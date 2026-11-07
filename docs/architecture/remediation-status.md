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
| **T01** | 修复同步/异步幂等的 owner 与请求内容绑定 | F01 | **COMPLETED** | `edf912c` | `pytest tests/security/test_idempotency_authorization.py tests/integration/test_idempotency_concurrency.py` (4 passed) | 无 | 进入 T02 |
| **T02** | 人工覆写的事务、并发和审计原子化 | F02 | **COMPLETED** | `701566d` | `pytest tests/integration/test_human_override_atomicity.py` (5 passed) | 无 | 进入 T03 |
| **T03** | 统一递归脱敏与审计入口保护 | F07 | **COMPLETED** | `d989151` | `pytest tests/security/test_redaction_boundaries.py` (6 passed) | 无 | 进入 T04 |
| **T04** | 认证签名 V2 与真实防重放 | F08 | **COMPLETED** | `c6e9c93` | `pytest tests/security/test_auth_replay_v2.py` (4 passed) | 无 | 进入 T05 |
| **T05** | 授权条件下推数据库后再分页 | F10 | **COMPLETED** | `d254129` | `pytest tests/integration/test_authorized_pagination.py` (2 passed) | 无 | 进入 T06 |
| **T06** | 持久化作业租约、心跳与失联恢复 | F03 | **COMPLETED** | `d8b25e3` | `pytest tests/integration/test_job_lease_recovery.py tests/e2e/test_worker_process_crash.py` (4 passed) | T01, T02 | 进入 T07 |
| **T07** | 统一 deadline、可取消 IO 与最终提交保护 | F04 | **COMPLETED** | `55a3d90` | `pytest tests/unit/test_execution_deadline.py tests/integration/test_deadline_finalization.py` (5 passed) | T06 | 进入 T08 |
| **T08** | 调用前预算预留与并发硬约束 | F05 | **COMPLETED** | `dc13b80` | `pytest tests/unit/test_budget_reservations.py tests/integration/test_provider_budget_limits.py` (5 passed) | T07 | 进入 T09 |
| **T09** | 阶段强类型契约、快照和可复现 manifest | F09 | **COMPLETED** | `7b95b32` | `pytest tests/contract/test_stage_schemas.py tests/integration/test_checkpoint_resume.py tests/unit/test_execution_manifest.py` (6 passed) | T01, T06-T08 | 进入 T10 |
| **T10** | 稳定合同实体、来源合流和业务版本 | F06 | **COMPLETED** | `e54b7d0` | `pytest tests/unit/test_canonical_merge.py tests/integration/test_canonical_versions.py` (5 passed) | T02, T09 | 进入 T11 |
| **T11** | 租户水位线、更新捕获和原子同步批次 | F06/F12 | **COMPLETED** | `adc8b45` | `pytest tests/integration/test_incremental_sync_recovery.py` (3 passed) | T06, T10 | 进入 T12 |
| **T12** | RAG 准入 fail-closed、来源权限及证据定位 | F11 | **COMPLETED** | `8498226` | `pytest tests/security/test_knowledge_governance_fail_closed.py tests/integration/test_citation_verification.py` (6 passed) | T01, T09 | 进入 T13 |
| **T13** | 独立 qrels、真实效果分层和成本口径 | F14 | **COMPLETED** | `f347882` | `pytest tests/unit/test_evaluation_metrics.py` (8 passed) | T09, T12 | 进入 T14 |
| **T14** | 可选 Agent 边界与三个重点工具逐步接线 | F12 | **COMPLETED** | `c5503ec` | `pytest tests/contract/test_legacy_cli_compatibility.py tests/integration/test_integration_entrypoints.py` (6 passed) | T08, T11, T12 | 进入 T15 |
| **T15** | 真实业务指标、临时文件生命周期与故障追踪 | F14 | **COMPLETED** | `b791a0a` | `pytest tests/integration/test_observability_wiring.py tests/security/test_upload_retention.py` (8 passed) | T03, T06-T09 | 进入 T16 |
| **T16** | 可复现依赖、全仓质量门禁及安全扫描闭环 | F13 | **COMPLETED** | 待提交 | `ruff check`, `ruff format`, `pip check`, `mypy`, `pytest` | M1/M2 | 进入 T17 |
| **T17** | 真实 PG+pgvector、并发负载、部署与恢复演练 | 未验证项 | **NOT_STARTED** | - | `test_pgvector_pipeline_e2e.py` | 独立测试基础设施 | - |
| **T18** | 文档修订、独立重验与最终交接 | 全部 F | **NOT_STARTED** | - | `architecture-acceptance.md` | 全部任务完成 | - |
