# Phase 14-R 基线审计与真实缺陷台账 (00-baseline-and-defect-ledger)

- **审计基线 Commit**: `16bf8f8a26a356bcf1708e54cf8e89e483e73071`
- **整改分支**: `fix/phase14-cross-boundary-integrity`
- **审计日期**: 2026-11-10
- **团队角色**: Principal Software Architect, Senior Python / FastAPI Engineer, Application Security Engineer, RAG / LLM Engineer, PostgreSQL / SQLite Engineer, Senior QA / SDET Engineer, LegalTech Domain Engineer

---

## 1. 真实调用图与双链比对 (Architecture Call Graph)

### 1.1 主审核链 (FastAPI / `contract_kb/api.py`) vs Web 工作台审核链 (`tools/contract_diff_web.py`)

```text
[FastAPI 主审核链]
Client Request (X-User / X-Roles / X-Tenant + HMAC-SHA256 V2 Signature + Nonce)
  ↓ TrustedHeaderAuth (严格认证，拒绝自报伪造，防重放)
Principal (tenant_id, user_id, roles)
  ↓ RateLimiter & InputSanitizer
ReviewPipeline.run(doc, tenant_id)
  ↓ LoadTemplateStage (Tenant Isolated)
  ↓ AlignTableStage (TableAligner)
  ↓ DiffGenerationStage (FullDiff with SHA-256)
  ↓ RetrievalStage (GovernedRetrievalService - 仅检索 APPROVED 知识)
  ↓ VerifyEvidenceStage (CitationVerifier - 强校验 source_hash 与版本)
  ↓ LLMReviewStage (BudgetLedgerService 记账 + 熔断容灾)
  ↓ CheckpointStage (ReviewExecutionFingerprint 12 因子 + HMAC)
Database (PostgreSQL / SQLite 事务一致)

======================= 差异与风险对比 =======================

[Web 工作台审核链 - 历史实现缺陷]
Client Request (Query ?tenant_id=... or Header X-Tenant-ID: ...)
  ↓ DiffVisualizerHandler._get_request_identity() [CRITICAL: 无签名无鉴权，客户端任意自报身份！]
Session Directory (SESSION_CACHE_DIR / clean_id) [CRITICAL: 无租户隔离，平铺存储！]
  ↓ Document Upload (25MB 限制但无格式魔数防护)
  ↓ Template Selection (优先检索但无租户归属校验)
  ↓ execute_current_diff (file_hash 缺失时降级为 doc_name 哈希！)
  ↓ AgentReviewManager (subscribe_events 允许跨租户窃听 SSE 流！)
  ↓ LegalReviewAgent (从内存静态字典 GOVERNED_RULES_STORE 读规则，脱离数据库正式审批状态！)
  ↓ CitationVerifier (缺少 req_hash 或 req_ver 时直接跳过校验！)
  ↓ Checkpoint (指纹仅取前 40 字符；校验失败后通过 legacy_doc_id 绕过强校验！)
  ↓ Feedback & Decision (无参数校验，直接改内存卡片状态，无状态机流转防护与审计日志！)
```

---

## 2. 缺陷台账清单 (Defect Ledger)

### P0 缺陷清单 (阻断级)

| Defect ID | Severity | Module / Source File | Code Location | Trigger Conditions | Impact | Fix Strategy | Status | Verification Evidence |
|---|---|---|---|---|---|---|---|---|
| **DEFECT-P0-01** | **P0** | `tools/contract_diff_web.py` | `_get_request_identity()`, `get_session_dir()` | 客户端在 Header 或 Query 中传入任意 `tenant_id` 或 `user_id` | 身份伪造、横向越权、越权访问他租户会话与合同数据、CORS 通配符允许恶意站点跨域攻击 | 区分 LOCAL_DEV 与 ENTERPRISE 模式；对接 `TrustedHeaderAuth` 与 HMAC-SHA256 V2 签名；对 `session_dir` 实施安全 slug 与租户物理隔离；增加 CSRF / Origin 校验 | **VERIFIED_FIXED** | `tests/security/test_web_identity_authorization.py` (5 passed) |
| **DEFECT-P0-02** | **P0** | `tools/contract_diff_web.py`, 前端交互 | `/api/diff/upload`, `/api/diff/agent_stream` | 上传后服务端生成新 `session_id`，但前端 EventSource 仍订阅旧/默认会话 | 上传会话 ≠ SSE 会话 ≠ 控制会话；用户 A 上传合同 A，SSE 却收到其他会话或收不到事件；任务状态失联 | 建立统一 `ReviewSessionContext`，保证上传返回会话与 SSE 订阅、暂停、取消等操作强制一致；前端收拢 `ReviewApiClient` 并透明注入会话头 | **VERIFIED_FIXED** | `tools/contract_diff_web.py` 前后端集成闭环，全量回归测试通过 |
| **DEFECT-P0-03** | **P0** | `contract_kb/agent_queue_manager.py`, `agent_checkpoint.py` | `compute_annotations_fingerprint()`, `start_review()`, `_compute_checkpoint_signature()` | 1. 合同在 40 字符后篡改金额或责任主体；2. 强校验失败触发 legacy_doc_id 回退；3. 签名仅为未加盐 SHA256 | 检查点恢复了不匹配的审查结果；篡改检查点无法检测；同名不同内容合同复用旧结果 | `annotations_fingerprint` 纳入全量标准/待审文本与元数据哈希；移除 `legacy_doc_id` 越权后门；引入 HMAC-SHA256 签名防篡改；严格绑定 12 维真实指纹 | **VERIFIED_FIXED** | `tests/contract/test_review_fingerprint_real_inputs.py` (2 passed) |
| **DEFECT-P0-04** | **P0** | `contract_kb/legal_agent_kb.py`, `legal_agent.py`, `citation.py` | `retrieve_governed_rules()`, `verify_evidence_item()` | 1. 数据库中规则被 REVOKED，但 Agent 仍从静态内存字典加载 APPROVED 规则；2. 引用缺失 `source_hash` 时被放行 | 审查依据脱节，废止规则继续产生法律建议；虚假引用未被识别；缺少数据库权威回查 | 对接 `GovernedRetrievalService` 数据库审批回查；废除生产代码直接使用静态字典；`CitationVerifier` 实行数据库权威回查与严格校验 | **VERIFIED_FIXED** | `tests/security/test_phase11_adversarial_suite.py`, `tests/security/test_phase11_stage_evidence_fail_closed.py` |
| **DEFECT-P0-05** | **P0** | `contract_kb/storage_pg.py` vs `storage.py` | `storage_pg.py:478`, `storage.py:791` | 在 PostgreSQL 导入模板合同条款，默认 `knowledge_type='LAYER_A_RAW_CLAUSE'`，而 SQLite 默认为 `'TEMPLATE'` | PostgreSQL 经正式审批后，`GovernedRetrievalService` 因知识类型不匹配无法召回模板条款，SQLite 与 PostgreSQL 检索语义完全分裂 | 统一知识类型契约与来源层级规范；迁移历史 PG 条款来源映射；统一 `rollback_to_version` 对全部 `PENDING_REVIEW` 条款的拦截阻断 | **VERIFIED_FIXED** | `tests/contract/test_pg_sqlite_governed_retrieval_parity.py`, `tests/contract/test_rollback_template_chain_validation.py` |

---

### P1 缺陷清单 (关键级)

| Defect ID | Severity | Module / Source File | Code Location | Trigger Conditions | Impact | Fix Strategy | Status | Verification Evidence |
|---|---|---|---|---|---|---|---|---|
| **DEFECT-P1-01** | **P1** | `tools/contract_diff_web.py`, `contract_kb/legal_agent_kb.py` | `/api/diff/feedback`, `/api/diff/human_decision` | 1. 前端调用 `/api/diff/feedback` 仅传 1 个参数导致 TypeError 崩溃；2. 前端调用 `/api/diff/human_decision` 直接修改状态，绕过状态机与审计 | 审核反馈功能硬报错；人工审核状态倒流、越级流转；缺少合法审计留痕 | 修复方法签名对齐；接入 `HumanReviewStatus.can_transition_to()` 校验；持久化结构化人工审核审计记录 | **VERIFIED_FIXED** | `tests/integration/test_agent_human_feedback_e2e.py` (2 passed) |
| **DEFECT-P1-02** | **P1** | `tools/sync_wecom_wedrive.py` | `sync_file():436` | 执行微盘同名文件路径更新时，SQL `UPDATE documents SET source_file=? WHERE source_hash=?` 缺少 `tenant_id` 过滤 | 相同哈希的公共模板跨租户篡改其他租户的文档文件名与路径 | 增加 `tenant_id` 强制隔离条件；限制破坏性同步开关的权限与降级兜底 | **VERIFIED_FIXED** | `tools/sync_wecom_wedrive.py:435` 代码审查与全量测试 |
| **DEFECT-P1-03** | **P1** | `contract_kb/document_parser.py` | `parse_pdf():228` | 上传混合型 PDF（第 1 页有文字，第 2 页为纯扫描图片） | 全文文本长度 >= 25 字符，跳过扫描页识别，导致第 2 页关键条款完全漏审且不提示 `OCR_REQUIRED` | 改为逐页提取与状态标记；只要存在未识别页面即标记 `OCR_REQUIRED` 并创建占位节点，防止漏审 | **VERIFIED_FIXED** | `contract_kb/document_parser.py:270` 代码审查与单元测试 |
| **DEFECT-P1-04** | **P1** | `tools/contract_diff_web.py` | `generate_rendered_html():4509` | 合同内容包含 `</script><script>alert(1)</script>` | 字符串替换注入导致破坏 `<script>` 闭合，构成 XSS 漏洞 | 将 JSON 序列化中的 `<`、`>`、`&` 字符转义为 `\u003c`、`\u003e`、`\u0026`，杜绝脚本逃逸 | **VERIFIED_FIXED** | `tests/security/test_web_html_bootstrap_xss.py` (1 passed) |
| **DEFECT-P1-05** | **P1** | `contract_kb/legal_agent.py` | `_is_safe_endpoint_url():65` | 配置自定义 `base_url` 为 `http://127.0.0.1:8000` 或内网私有 IP（`10.0.0.0/8` 等） | 仅拦截云元数据 IP，未拦截回环与 RFC1918 私网地址，导致 SSRF 内部服务探测风险 | 增强 URL 与 IP 解析校验，严密拦截 IPv4/IPv6 回环、私网、链路本地与 DNS 重绑定地址，仅允许公网或受控白名单内网网关 | **VERIFIED_FIXED** | `contract_kb/legal_agent.py:65` 代码审查与回归测试 |
