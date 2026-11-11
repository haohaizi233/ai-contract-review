# Phase 16-S 基线审计报告 (00-baseline-audit.md)

## 一、代码基线与审查范围

- **基线 Commit SHA**: `3e015d2402f7432918ca956ee60842a6657292e7`
- **目标分支**: `fix/phase16-trust-boundary-security`
- **审计定位**: 首席软件架构师、应用安全工程师、Python 后端架构师、AI/RAG 安全工程师、SDET / QA 负责人、法务合规工程师
- **核心原则**: Security by Construction + Evidence-Based AI + Fail-Closed Governance

## 二、端到端调用关系与信任边界再绘制

```text
HTTP / Web / SSE 请求
  ↓
[入口认证与请求完整性门禁]
  ├─ 模式判断: LOCAL_DEV (仅 127.0.0.1) vs ENTERPRISE (强制签名)
  ├─ 密钥有效性: 缺密钥/空值 → Fail-Closed (拒绝服务)
  ├─ 规范化参数: Method, Canonical Path, Sorted Query, Principal Headers
  ├─ 请求正文: 真实读取 Body 字节，校验 SHA256(RawBody) == X-Content-SHA256
  ├─ 时效性校验: |Now - X-Auth-Timestamp| <= 300s，防未来/过去过期
  ├─ 防重放校验: 原子 Nonce Store (内存 + 数据库)，拒绝重复 Nonce
  └─ 签名核验: HMAC-SHA256 V2 比较 (hmac.compare_digest)
  ↓
[身份主体与会话所有权绑定]
  ├─ AuthenticatedPrincipal(user_id, tenant_id, roles, auth_method)
  ├─ Session 强绑定: (tenant_id, user_id, session_id)
  ├─ 越权拦截: 跨租户 / 跨用户 / 匿名冒领 → 403 阻断
  └─ 文件隔离: 租户目录/会话目录物理隔离，杜绝全局 CURRENT_SESSION_FILE 兜底
  ↓
[Diff 比对与 Agent 审查引擎]
  ├─ 差异条款 (Clause Delta) 提取
  └─ 任务启动与看门狗代际令牌隔离
  ↓
[受控知识检索与审批证据链 (Governed RAG)]
  ├─ 显式注入 Tenant ID, Storage, Template Version, Rule Version
  ├─ 权威性门禁: 仅当前租户数据库内 status='APPROVED' 规则具备权威效力
  ├─ 静态字典去权威化: GOVERNED_RULES_STORE 仅作未审批兜底/非权威参考，严禁标记为 APPROVED 企业红线
  ├─ 证据核验: CitationVerifier 强类型校验来源哈希、版本与审批状态
  └─ Fail-Closed: 数据库故障/未审批/已撤销 → INSUFFICIENT_EVIDENCE / REFERENCE_ONLY
  ↓
[LLM 研判与 TOCTOU 审批撤销复核]
  ├─ 防注入信封隔离 (<UNTRUSTED_CONTRACT_BODY nonce="...">)
  ├─ 结构化输出校验 (Pydantic Schema)
  └─ T4 落地前复核: 若规则在推理期间被撤销，阻断权威结论落地
  ↓
[人工裁决与反馈数据治理]
  ├─ 人工裁决留痕 (HumanReviewStatus 状态机流转与审计踪迹)
  ├─ 反馈身份强制服务端覆盖: 客户端自报 tenant_id/reviewer_id 无效
  ├─ 反馈与训练审批物理分离: 普通反馈仅写入待审队列，严禁客户端 self-approve
  ├─ 训练集导出: 仅限 Admin 角色、强制租户隔离、严格数据脱敏与 Holdout 隔离
  └─ Checkpoint 恢复: 生产环境禁用固定密钥，HMAC 防篡改签名严格核验
```

## 三、识别的绕过路径清单

1. **Web 工作台 HMAC 验签空密钥跳过** (`contract_kb/interfaces/web/app.py`):
   在企业模式下，若 `CONTRACT_KB_AUTH_SHARED_KEY` 未配置，代码 `if key:` 为假，跳过签名对比，直接信任客户端请求。
2. **时间戳、Nonce 与正文摘要缺失真实校验** (`contract_kb/interfaces/web/app.py`):
   虽然接收了 `X-Auth-Timestamp`、`X-Auth-Nonce` 与 `X-Content-SHA256`，但未校验时间窗口、未消费 Nonce、未比对请求正文原始字节哈希。
3. **SSE 订阅与会话跨用户越权** (`contract_kb/application/agents/review_sessions.py`):
   `get_or_create_session` 与 `subscribe_events` 仅核验 `tenant_id`，未核验 `user_id`；`get_session` 存在 `user_id != "anonymous"` 绕过分支。
4. **法务 Agent 静态规则绕过知识审批** (`contract_kb/application/agents/legal_review_agent.py` & `knowledge_adapter.py`):
   `retrieve_governed_rules` 在数据库无命中（包括已全部撤销）时，静默回退至静态 `GOVERNED_RULES_STORE` 并将其赋予 `APPROVED` 权威效力。
5. **用户反馈接口身份伪造与训练样本自审批** (`contract_kb/interfaces/web/routes/feedback.py`):
   使用 `setdefault` 未强制覆盖客户端身份字段；`record_user_feedback` 信任客户端提交的 `is_approved=True`，直接将样本写入已审批训练集。
6. **SFT 导出接口无鉴权无隔离** (`contract_kb/interfaces/web/routes/feedback.py`):
   未经过任何认证授权即可调用，跨租户混合导出全部样本。
7. **检查点 HMAC 生产默认密钥固定** (`contract_kb/infrastructure/checkpoints/storage.py`):
   生产环境下默认回退到公开字符串 `"contract_kb_checkpoint_integrity_secret_key_v14"`。
8. **全局默认文档回退泄露** (`contract_kb/interfaces/web/services/session_files.py` & `app.py` & `reviews.py`):
   会话缺失时回退到 `CURRENT_SESSION_FILE`；企业模式未认证直接 GET `/` 内嵌活动合同比对数据。
