# 架构整改与分级验收状态台账（Remediation Status Ledger）

- **基线提交（被审 SHA）**：`4eabf0edf21aceb046e9600c0b661e93c3fd019a`
- **实施分支**：`codex/architecture-remediation`
- **远端 HEAD / main**：`ab17bf08ab359dff30b2d826431866fa1ae1105d`
- **计划书**：[`docs/superpowers/plans/2026-11-07-architecture-remediation-plan.md`](../superpowers/plans/2026-11-07-architecture-remediation-plan.md)
- **独立验收报告**：[`docs/architecture/independent-acceptance-20261107.md`](independent-acceptance-20261107.md)

---

## 任务执行状态总览

| 任务编号 | 任务名称 | 关联缺陷 | 状态 | 关联提交 | 目标命令与结果 | 阻塞/外部依赖 | 下一步动作 |
|---|---|---|---|---|---|---|---|
| **T00** | 冻结基线与建立隔离执行环境 | F13 | **COMPLETED** | `6a1ad50` | `git status`, `manifest.json` | 无 | 进入 T01 |
| **T01** | 修复同步/异步幂等的 owner 与请求内容绑定 | F01 | **COMPLETED** | 待提交 (`fix: bind idempotency to owner and request fingerprint`) | `pytest tests/security/test_idempotency_authorization.py tests/integration/test_idempotency_concurrency.py` (4 passed) | 无 | 进入 T02 |
| **T02** | 人工覆写的事务、并发和审计原子化 | F02 | **IN_PROGRESS** | - | `pytest tests/integration/test_human_override_atomicity.py` | 无 | 先写失败断言测试 |
| **T03** | 统一递归脱敏与审计入口保护 | F07 | **NOT_STARTED** | - | `pytest tests/security/test_redaction_boundaries.py` | T02 | - |
| **T04** | 认证签名 V2 与真实防重放 | F08 | **NOT_STARTED** | - | `pytest tests/security/test_auth_replay_v2.py` | T03、网关协同 | - |
| **T05** | 授权条件下推数据库后再分页 | F10 | **NOT_STARTED** | - | `pytest tests/integration/test_authorized_pagination.py` | T01 | - |
| **T06** | 持久化作业租约、心跳与失联恢复 | F03 | **NOT_STARTED** | - | `pytest tests/integration/test_job_lease_recovery.py` | T01, T02 | - |
| **T07** | 统一 deadline、可取消 IO 与最终提交保护 | F04 | **NOT_STARTED** | - | `pytest tests/unit/test_execution_deadline.py` | T06 | - |
| **T08** | 调用前预算预留与并发硬约束 | F05 | **NOT_STARTED** | - | `pytest tests/unit/test_budget_reservations.py` | T07 | - |
| **T09** | 阶段强类型契约、快照和可复现 manifest | F09 | **NOT_STARTED** | - | `pytest tests/contract/test_stage_schemas.py` | T01, T06-T08 | - |
| **T10** | 稳定合同实体、来源合流和业务版本 | F06 | **NOT_STARTED** | - | `pytest tests/unit/test_canonical_merge.py` | T02, T09 | - |
| **T11** | 租户水位线、更新捕获和原子同步批次 | F06/F12 | **NOT_STARTED** | - | `pytest tests/integration/test_incremental_sync_recovery.py` | T06, T10 | - |
| **T12** | RAG 准入 fail-closed、来源权限及证据定位 | F11 | **NOT_STARTED** | - | `pytest tests/security/test_knowledge_governance_fail_closed.py` | T01, T09 | - |
| **T13** | 独立 qrels、真实效果分层和成本口径 | F14 | **NOT_STARTED** | - | `pytest tests/unit/test_evaluation_metrics.py` | T09, T12 | - |
| **T14** | 可选 Agent 边界与三个重点工具逐步接线 | F12 | **NOT_STARTED** | - | `pytest tests/contract/test_legacy_cli_compatibility.py` | T08, T11, T12 | - |
| **T15** | 真实业务指标、临时文件生命周期与故障追踪 | F14 | **NOT_STARTED** | - | `pytest tests/integration/test_observability_wiring.py` | T03, T06-T09 | - |
| **T16** | 可复现依赖、全仓质量门禁及安全扫描闭环 | F13 | **NOT_STARTED** | - | `ruff check`, `pip check`, CI | M1/M2 | - |
| **T17** | 真实 PG+pgvector、并发负载、部署与恢复演练 | 未验证项 | **NOT_STARTED** | - | `test_pgvector_pipeline_e2e.py` | 独立测试基础设施 | - |
| **T18** | 文档修订、独立重验与最终交接 | 全部 F | **NOT_STARTED** | - | `architecture-acceptance.md` | 全部任务完成 | - |
