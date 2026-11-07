# 第二轮缺陷修复与分级验收状态台账（Second Remediation Status Ledger）

> **基线提交（被审 SHA）**：`90b7ccb94978bc7f5914991448c3c9788bef0dfd`  
> **实施分支**：`codex/architecture-remediation`  
> **远端 HEAD / main**：`ab17bf08ab359dff30b2d826431866fa1ae1105d`  
> **关联复验报告**：[`independent-reacceptance-20261107.md`](independent-reacceptance-20261107.md)  
> **历史台账勘误**：[`remediation-status.md`](remediation-status.md)  
> **建账时间**：2026-11-07  

---

## 修复批次与缺陷对应表

| 批次编号 | 目标缺陷 | 严重级别 | 核心问题概述 | 状态 | 验证用例 / 证据 | 修复提交 (SHA) |
|---|---|---|---|---|---|---|
| **Batch 1** | **R01** | P1 | 断点恢复丢失金额和格式风险，导致审核门禁降级 | **COMPLETED** | `tests/integration/test_checkpoint_resume_findings_preservation.py` (3 passed, 保持7项Findings与BLOCKED_BY_HIGH_RISK) | `241f08a` |
| **Batch 2** | **R02** | P1 | PersistentTaskRunner 未接租约心跳与 CAS 终态，迟到 worker 覆盖最新结果 | **COMPLETED** | `tests/integration/test_task_runner_fenced_leases.py` (4 passed, 迟到worker被阻断且长任务心跳续租) | `ead56f5` |
| **Batch 3** | **R03 / R06** | P1 | 同步忽略乐观锁冲突导致假成功与跳水；重点集成工具未走应用适配器 | **COMPLETED** | `test_sync_service_optimistic_conflict.py`, `test_multi_source_incremental_sync.py` (全部通过，回滚冲突且三批合流保留来源) | `bbe5ede` |
| **Batch 4** | **R04** | P1 | V2 认证未校验真实 body，API 未绑共享存储，提前消费 nonce 导致投毒 | **COMPLETED** | `tests/security/test_auth_v2_body_digest_and_shared_nonce.py` (3 passed, 真实body核验阻断篡改且先验签后消费) | `4b13928` |
| **Batch 5** | **R05** | P1 | 阶段 deadline 未传至底层 HTTP provider；预算未接主审核链路与硬限额 | **COMPLETED** | `tests/integration/test_provider_deadline_and_budget_enforcement.py` (6 passed, 截止时间透传快速超时，预算耗尽拦截且精准核销与UNCERTAIN挂账) | `1220a3a` |
| **Batch 6** | **R07 / R08** | P2 | Manifest 与 Stage Schema 静态无约束；RAG 准入未 fail-closed；评估指标口径失真 | **COMPLETED** | `tests/security/test_governance_fail_closed_strict.py`, `tests/unit/test_evaluate_script_metrics.py`, `tests/unit/test_execution_manifest_dynamic.py` (全部通过，动态多维指纹，严格准入阻断缺失/未知状态，Hit@K与Recall@K及真实IDCG口径分离) | `86f4b96` |
| **Batch 7** | **R09** | P2 | 全仓 117 项 Ruff 告警与 39 个文件格式问题；PG E2E 缺任务插入与 DSN 脱敏；Lockfile 消费 | **COMPLETED** | `ruff check .` (0 error), `ruff format --check .` (248 files formatted), `tests/integration/test_pgvector_pipeline_e2e.py` (DSN脱敏与任务插入核验通过), 全仓 352 passed, 23 skipped | `cab8fa6` |

---

## 执行纪律

1. **绝对遵循 TDD 闭环**：每一批次必须先将复验反例转化为可自动运行的测试用例（红），确认其能稳定重现缺陷，再进行最小化修复，最后通过测试（绿）并做全量回归。
2. **严禁破坏原有契约与数据**：保留所有既有公共 API、ReviewEngine 接口以及 CLI 兼容入口。
3. **分批原子提交**：每个批次独立提交，提交信息清晰指明修复问题，不混入无关文件格式化。
4. **拒绝虚假声称**：未在真实代码路径上运行的逻辑不得声明为已解决；区分 L1 确定性闭环与 L2 生产待办事项。
