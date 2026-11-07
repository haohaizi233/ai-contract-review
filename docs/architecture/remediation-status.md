# 架构整改与分级验收状态台账（Remediation Status Ledger）

> [!WARNING]
> **状态勘误与撤回声明（2026-11-07 独立复验更新）**：
> 经 2026-11-07 独立架构复验（详见 [`independent-reacceptance-20261107.md`](independent-reacceptance-20261107.md)），此前将所有任务标为 COMPLETED 及“L1 FULLY ACCEPTED”与实际运行代码事实不符。已发现断点恢复丢失高风险（R01）、实际 Worker 未用租约栅栏致旧结果覆盖（R02）、同步忽略乐观锁冲突导致假成功（R03）、V2认证未校验真实body且未绑定共享nonce（R04）、deadline与预算未接主流程（R05）、旧CLI入口未迁移（R06）、Manifest静态（R07）、RAG未fail-closed与指标失真（R08）、全仓代码静态检查不达标（R09）等重大缺陷。
> 本台账特此撤回过早完成结论，恢复为客观真实状态，并开启第二轮缺陷整改（详见 [`second-remediation-status.md`](second-remediation-status.md)）。

- **基线提交（被审 SHA）**：`90b7ccb94978bc7f5914991448c3c9788bef0dfd`
- **实施分支**：`codex/architecture-remediation`
- **远端 HEAD / main**：`ab17bf08ab359dff30b2d826431866fa1ae1105d`
- **计划书**：[`docs/superpowers/plans/2026-11-07-architecture-remediation-plan.md`](../superpowers/plans/2026-11-07-architecture-remediation-plan.md)
- **独立复验报告**：[`docs/architecture/independent-reacceptance-20261107.md`](independent-reacceptance-20261107.md)
- **第二轮整改台账**：[`docs/architecture/second-remediation-status.md`](second-remediation-status.md)

---

## 任务执行状态总览（经复验勘误后）

| 任务编号 | 任务名称 | 关联缺陷 | 复验状态 | 关联提交 | 目标命令与结果 | 阻塞/缺陷关联 | 下一步动作 |
|---|---|---|---|---|---|---|---|
| **T00** | 冻结基线与建立隔离执行环境 | F13 | **PARTIAL** | `6a1ad50` | `git status`, `manifest.json` | 缺少远端CI证据 | 纳入第二轮治理 |
| **T01** | 修复同步/异步幂等的 owner 与请求内容绑定 | F01 | **PARTIAL** | `edf912c` | `pytest tests/security/test_idempotency_authorization.py` | 并发执行与双库需进一步复核 | 保持并通过回归 |
| **T02** | 人工覆写的事务、并发和审计原子化 | F02 | **COMPLETED** | `701566d` | `pytest tests/integration/test_human_override_atomicity.py` | 无 | 保持 |
| **T03** | 统一递归脱敏与审计入口保护 | F07 | **PARTIAL** | `d989151` | `pytest tests/security/test_redaction_boundaries.py` | 需确保所有异常入口完整脱敏 | 保持并通过回归 |
| **T04** | 认证签名 V2 与真实防重放 | F08 | **FAILED** | `c6e9c93` | `auth-api-repro.txt` 复现成功 | R04: 未校body，API未绑共享存储，提前消费nonce | Batch 4 重构修复 |
| **T05** | 授权条件下推数据库后再分页 | F10 | **COMPLETED** | `d254129` | `pytest tests/integration/test_authorized_pagination.py` | 基础测试通过 | 保持 |
| **T06** | 持久化作业租约、心跳与失联恢复 | F03 | **FAILED** | `d8b25e3` | `runner-repro.txt` 复现迟到覆盖 | R02: PersistentTaskRunner未接租约与CAS提交 | Batch 2 真实接入 |
| **T07** | 统一 deadline、可取消 IO 与最终提交保护 | F04 | **PARTIAL** | `55a3d90` | 同步stage直接执行后才查超时 | R05: deadline未传真实网络provider | Batch 5 严格接入 |
| **T08** | 调用前预算预留与并发硬约束 | F05 | **PARTIAL** | `dc13b80` | 预留组件存在但审核主线未接入 | R05: pipeline与provider未接入预算硬约束 | Batch 5 真正接线 |
| **T09** | 阶段强类型契约、快照和可复现 manifest | F09 | **FAILED** | `7b95b32` | `resume-repro.txt` 恢复后丢失高风险 | R01, R07: 恢复丢Findings/降级门禁; 静态manifest | Batch 1 & 6 核心修复 |
| **T10** | 稳定合同实体、来源合流和业务版本 | F06 | **PARTIAL** | `e54b7d0` | 单测通过但旧入口仍丢来源与版本 | R06: 旧入口未迁移，多源合流覆盖 | Batch 3 适配并闭环 |
| **T11** | 租户水位线、更新捕获和原子同步批次 | F06/F12 | **FAILED** | `adc8b45` | `sync-conflict-repro.txt` 假成功 | R03: save_canonical_version=False未报错反推进水位线 | Batch 3 回滚批次修复 |
| **T12** | RAG 准入 fail-closed、来源权限及证据定位 | F11 | **PARTIAL** | `8498226` | 未知类型与缺审批仍默认放行 | R08: governance.py 未实现严格 fail-closed | Batch 6 严格拦截 |
| **T13** | 独立 qrels、真实效果分层和成本口径 | F14 | **PARTIAL** | `f347882` | 独立函数存在但evaluate主入口仍用旧指标 | R08: evaluate.py 未接入真实Recall/NDCG | Batch 6 统一接入 |
| **T14** | 可选 Agent 边界与三个重点工具逐步接线 | F12 | **PARTIAL** | `c5503ec` | 工具仅增参数，未通过应用服务适配 | R06: 未完成CLI→应用服务→adapter迁移 | Batch 3 适配改造 |
| **T15** | 真实业务指标、临时文件生命周期与故障追踪 | F14 | **PARTIAL** | `b791a0a` | record_llm_call未在实际LLM调用触发 | R08: 观测指标未真正连接业务调用 | Batch 6 注入观测 |
| **T16** | 可复现依赖、全仓质量门禁及安全扫描闭环 | F13 | **FAILED** | `6852c60` | 全仓117项lint未过、39文件未format、未消费锁文件 | R09: 静态检查全面超标 | Batch 7 彻底清零 |
| **T17** | 真实 PG+pgvector、并发负载、部署与恢复演练 | 未验证项 | **FAILED** | `e31e7d3` | PG E2E缺少task插入直接断言、并发load未真正审核 | R09: 虚假通过断言与泄密隐患 | Batch 7 规范化 |
| **T18** | 文档修订、独立重验与最终交接 | 全部 F | **FAILED** | `10698b4` | 过早宣布L1全部通过，已撤回 | 存在未通过关键缺陷 | 本轮全部修复后重验 |
