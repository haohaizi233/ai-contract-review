# Phase 16-S-R1: 缺陷红态复现与根因分析报告

## 一、复现测试概览

所有缺陷均已通过 `tests/security/test_phase16_r1_reproduce_flaws.py` 完成确定性红态复现，执行结果如下：

```bash
python -m pytest tests/security/test_phase16_r1_reproduce_flaws.py -v
============================== 8 passed in 1.57s ==============================
```

> **说明**：此处的复现测试通过断言当前生产代码中存在的缺陷行为（例如：断言 `from_cache is True` 当数据库报错时、断言 `is_approved=True` 写入了 `reviewed_events.jsonl` 等），精确锁定缺陷的存在性。

---

## 二、八大缺陷详细复现与根因定位

### 1. P0-R1-01: Web 后台 Worker 未显式注入 Storage

- **源码定位**: `contract_kb/application/agents/review_sessions.py:458-461`
- **复现测试**: `test_reproduce_p0_r1_01_worker_lacks_storage_injection`
- **复现现象**: Worker 启动后调用 `agent.review_clause` 时，`kwargs["storage"]` 为 `None`，且 `doc_context["storage"]` 为 `None`。
- **根因分析**:
  `AgentReviewManager.start_document_review()` 缺少 Storage 依赖注入；`AgentReviewSession` 亦无存储句柄或工厂。导致真实后台线程在调用 Agent 时，Agent 只能在 `storage=None` 模式下运行，无法检索租户数据库中的自定义已审批规则。
- **整改方案**:
  在 `AgentReviewManager` 与 `AgentReviewSession` 中引入受控的 Storage Provider / Repository Factory，启动任务时显式绑定当前租户 Storage 并传递给 Worker。

---

### 2. P0-R1-02: APPROVED 直接映射为 VERIFIED，无证据核验

- **源码定位**: `contract_kb/application/agents/legal_review_agent.py:523-525`
- **复现测试**: `test_reproduce_p0_r1_02_approved_directly_mapped_to_verified`
- **复现现象**: 即使规则没有有效源文档、没有原文或 CitationVerifier 未通过，`verification_status` 直接被赋值为 `"VERIFIED"`。
- **根因分析**:
  代码直接使用了三元表达式：
  `"verification_status": "VERIFIED" if r.approval_status == "APPROVED" else "UNVERIFIED"`
  混淆了“审批状态 (Approval Status)”与“引文核验状态 (Citation Verification Status)”。
- **整改方案**:
  拆分两个独立维度；接入现有 `CitationVerifier` 或规则证据核验适配器，严格核对租户一致性、源文档存在性、哈希一致性、生命周期状态与原文匹配度，仅核验通过才允许标为 `VERIFIED`，否则标为 `UNVERIFIED` 或 `INSUFFICIENT_PROVENANCE`。

---

### 3. P0-R1-03: 缓存核验数据库异常时吞异常放行

- **源码定位**: `contract_kb/application/agents/legal_review_agent.py:410-430`
- **复现测试**: `test_reproduce_p0_r1_03_cache_swallows_db_exception`
- **复现现象**: 当 `storage.get_rules(...)` 抛出 `RuntimeError("Database connection down")` 时，`res.get("from_cache")` 依然返回 `True`。
- **根因分析**:
  `cache_valid = True` 初始化为 `True`，数据库检查被 `except Exception: pass` 捕获，当数据库异常时 `cache_valid` 未被置为 `False`，导致直接命中并返回旧缓存。
- **整改方案**:
  遵循默认 Fail-Closed 原则。一旦数据库检查抛出异常或无法确认有效性，必须立即置 `cache_valid = False`，拒绝直接返回缓存，并安全回退或重新进行权威核验。

---

### 4. P0-R1-04: TOCTOU 审批撤销复核吞异常且继续写缓存

- **源码定位**: `contract_kb/application/agents/legal_review_agent.py:724-758`
- **复现测试**: `test_reproduce_p0_r1_04_toctou_swallows_db_exception`
- **复现现象**: 模型推理后执行 TOCTOU 撤销复核时，若 `storage.get_rules` 发生异常，代码吞异常后仍执行 `self._save_cache()` 并返回 `AI_REVIEW_COMPLETED`。
- **根因分析**:
  `except Exception: pass` 静默忽略了复核故障，未阻断结论持久化与缓存落盘。
- **整改方案**:
  TOCTOU 异常时必须 Fail-Closed：阻断缓存写入，标记结论不可用，将审核状态降级为 `NEEDS_HUMAN_REVIEW`。

---

### 5. P1-R1-05: 服务层允许直接在 payload 中自审批训练样本

- **源码定位**: `contract_kb/application/agents/knowledge_adapter.py:374-378`
- **复现测试**: `test_reproduce_p1_r1_05_service_layer_allows_self_approval`
- **复现现象**: 直接调用 `record_user_feedback({"task_id": "...", "is_approved": True})`，该记录被立即写入 `reviewed_feedback/reviewed_events.jsonl`。
- **根因分析**:
  虽然 HTTP Route 层面做了覆盖，但在服务层 `record_user_feedback` 内部仍然从 `payload.get("is_approved")` 读取布尔值。
- **整改方案**:
  严格拆分 `submit_feedback()` (仅允许记录为待审) 与 `approve_feedback_for_training()` (需提供已认证审批人 Principal、租户校验与管理员角色)。`record_user_feedback` 默认不放行训练审批。

---

### 6. P1-R1-06: 自审批测试断言错误与假阳性

- **源码定位**: `tests/security/test_feedback_approval_authorization.py:43-67`
- **复现测试**: `test_reproduce_p1_r1_06_false_positive_self_approval_test`
- **复现现象**: 原测试通过断言 `unique_doc not in content` 获得 PASS，但实际上 `FeedbackSampleRecord` 压根没有 `doc_id` 字段，且真实的 `task_id` 已经进入了已审批文件！
- **根因分析**:
  测试未断言真实落盘标识 `task_id`，导致即使安全防线被突破，测试依然假阳性通过。
- **整改方案**:
  重构测试断言：解析 JSONL 的每行 JSON，根据 `task_id`、`annotation_id` 定位目标记录，严格断言其不在 `reviewed_events.jsonl` 中且未被导出到训练集。

---

### 7. P1-R1-07: Nonce Store 依赖隐式 sys.modules 猜测数据库

- **源码定位**: `contract_kb/interfaces/http/auth.py:121-125, 466-470`
- **复现测试**: `test_reproduce_p1_r1_07_nonce_store_implicit_sys_modules`
- **复现现象**: 在生产模式下，当 `contract_kb.api` 未被加载到 `sys.modules` 且 `storage=None` 时，抛出 `PermissionError("生产环境下持久化 Nonce 存储不可用")`。
- **根因分析**:
  依赖全局模块导入猜测 Storage，缺少显式的 NonceStore 注入与工厂支持。
- **整改方案**:
  定义 `NonceStoreProtocol`，在 Web 服务启动与认证中间件初始化时显式注入 Storage / NonceStore 实例。

---

### 8. P1-R1-08: 未认证企业首页输出敏感内部元数据

- **源码定位**: `contract_kb/interfaces/web/app.py:132-141`
- **复现测试**: `test_reproduce_p1_r1_08_enterprise_homepage_metadata_leak`
- **复现现象**: `generate_rendered_html(is_enterprise=True)` 仍然执行 `mgr.search_cases(...)` 并将案例与统计信息内嵌在 HTML 中。
- **根因分析**:
  上轮修复仅将 `data_json` 设为 `null`，忽略了 `allTemplates`、`refTemplates` 与 `material_data` 中内嵌的真实业务数据。
- **整改方案**:
  企业模式下对未认证请求返回安全的应用外壳，将敏感占位符初始化为空数组/空对象，必须通过携带有效认证凭据的 API 动态加载租户专属数据。
