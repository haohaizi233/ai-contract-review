# Phase 13-R11: 生产就绪度检查清单 (`13-production-readiness.md`)

## 1. 生产环境安全与可信门禁核对表

| 检查域 | 检查项 | 当前状态 | 代码落点 |
| :--- | :--- | :--- | :--- |
| **数据真实性** | 合同对照与表格重建无任何硬编码演示企业名、金额、地址 | **已就绪 (PASS)** | `contract_kb/full_diff.py`, `contract_kb/table_aligner.py` |
| **结构可追溯** | 差异块绑定 `standard_source_node_ids` 与 `reviewed_source_node_ids` | **已就绪 (PASS)** | `contract_kb/domain/models/source_node.py`, `contract_kb/full_diff.py` |
| **检查点安全** | 12 因子 `ReviewExecutionFingerprint` + 8 道校验门禁 + 原子写盘 | **已就绪 (PASS)** | `contract_kb/domain/models/review_identity.py`, `contract_kb/agent_checkpoint.py` |
| **出境与网络控制** | 默认 `CONTRACT_KB_ALLOW_EXTERNAL_LLM=false`；SSRF 私网/元数据 IP 拦截 | **已就绪 (PASS)** | `contract_kb/legal_agent.py` |
| **Token 预算治理** | 调用前 `reserve_budget`、成功后 `settle_budget`、异常 `mark_uncertain` | **已就绪 (PASS)** | `contract_kb/legal_agent.py`, `contract_kb/modules/review/budget_ledger.py` |
| **状态诚实性** | 严格区分 `AI_REVIEW_COMPLETED` 与 `RULE_REVIEW_COMPLETED` | **已就绪 (PASS)** | `contract_kb/domain/models/review_identity.py`, `contract_kb/legal_agent.py` |
| **多租户与多会话** | 会话级独立目录 `SESSION_CACHE_DIR / session_id`、跨租户 `PermissionError` | **已就绪 (PASS)** | `tools/contract_diff_web.py`, `contract_kb/agent_queue_manager.py` |
| **反馈与训练隔离** | 7 态人工状态机、PII 正则脱敏、训练集与评测集物理隔离防污染 | **已就绪 (PASS)** | `contract_kb/legal_agent_kb.py` |
| **生产外部设施** | 生产级 PostgreSQL 高可用实例与企业内网私有化 LLM 网关连接验收 | **待生产环境联调** | 详见 `docs/phase13-r/production-deployment.md` |
