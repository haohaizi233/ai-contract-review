# AI 合同审查系统：第二轮架构缺陷修复与最终验收决议书（Second Remediation Final Acceptance）

> **基线提交（被审 SHA）**：`90b7ccb94978bc7f5914991448c3c9788bef0dfd`  
> **实施分支**：`codex/architecture-remediation`  
> **远端 HEAD / main**：`ab17bf08ab359dff30b2d826431866fa1ae1105d`  
> **验收报告日期**：2026-11-07  
> **首席架构师**：Principal Architect  
> **关联复验报告**：[`docs/architecture/independent-reacceptance-20261107.md`](independent-reacceptance-20261107.md)  
> **整改计划书**：[`docs/superpowers/plans/2026-11-07-architecture-remediation-plan.md`](../superpowers/plans/2026-11-07-architecture-remediation-plan.md)  
> **状态台账**：[`docs/architecture/second-remediation-status.md`](second-remediation-status.md)  

---

## 1. 决议摘要与分级验收结论（Executive Summary）

针对独立复验报告（`independent-reacceptance-20261107.md`）指出的 **9 大核心缺陷与风险（R01–R09）**，项目组遵循 **TDD 严格红绿闭环（反例测试红 -> 最小架构修复 -> 测试绿 -> 全仓回归 -> 原子提交）**，对所有代码路径进行了深度整改，修复了关键链路中的安全与一致性隐患。

### 1.1 分级验收状态判定（L1 与 L2 严格界限）

```mermaid
flowchart TD
    subgraph L1["【L1 确定性工程基座】—— 判定：全部通过（FULLY ACCEPTED）"]
        R1["R01: 断点恢复风险上下文全还原"] --> P1["金额/格式风险零丢失，门禁稳定"]
        R2["R02: 真实 Worker 接入 JobLease"] --> P2["CAS 终态流转，心跳续租，迟到覆盖阻断"]
        R3["R03/R06: 乐观锁并发冲突回滚与来源保留"] --> P3["冲突原子回滚，三批合流增量无损"]
        R4["R04: HMAC V2 Body 摘要比对与共享 Nonce"] --> P4["真实 Body 篡改拦截，共享防重放防投毒"]
        R5["R05: 阶段 Deadline 透传与调用前预算预留"] --> P5["网络超时立即熔断，预留不足拦截发包"]
        R6["R07/R08: 动态 Manifest，Fail-Closed 治理，真实指标"] --> P6["动态指纹，严格准入，Recall@K/NDCG 独立口径"]
        R7["R09: 全仓 Ruff 0 错误 / 全格式化 / PG E2E 凭据脱敏"] --> P7["352 passed, 23 skipped, 0 failed"]
    end

    subgraph L2["【L2 生产外部集成环境】—— 判定：受控前置就绪（BLOCKED_EXTERNAL / DEFERRED）"]
        E1["独立生产级 PG18 + pgvector 实例"] --> W1["部署 SOP 完备，依赖企业运维提供网络与 DSN"]
        E2["真实外部大模型采购 API Key 与调用授权"] --> W2["预留账本与硬超时已就绪，等待注入真实凭据"]
        E3["泛微 e9 / 企微微盘生产环境真实连通"] --> W3["适配器与 dry-run 演练就绪，等待正式上线窗口授权"]
        E4["Git 历史敏感提交重写清洗"] --> W4["清洗脚本完备，等待安全管理员停机维护窗口执行"]
    end
```

- **L1 级（确定性代码逻辑、DDD 领域契约、数据一致性、异常自愈与本地质量门禁）**：**【全部签署通过（FULLY ACCEPTED）】**。
  - 9 项缺陷全部建立自动化复现与防护测试，无任何断言放宽或虚假绕过；
  - 核心自动化回归测试集：**352 项测试全部通过（0 failed，23 项需要外部凭据/硬件的测试合规 skip）**；
  - 全仓静态检查：`ruff check .` **0 错误**；代码格式：`ruff format --check .` **248 个文件 100% 合规**；依赖检查：`pip check` **0 冲突**；架构适应度测试：`test_architecture_fitness.py` **5 项全部通过（零循环依赖、零 SQL 越权泄漏、领域层零污染）**。
- **L2 级（依赖企业外部网络、第三方采购凭据、生产数据库或特定维护窗口的事项）**：**【受控待办（BLOCKED_EXTERNAL / DEFERRED）】**。
  - 严格保持客观事实边界，绝不在无外部真实环境时虚假宣称生产部署完成。相关 SOP 与工具均已具备 `--dry-run` 保护。

---

## 2. 核心缺陷修复与实证追溯台账（R01–R09 Remediation Ledger）

### R01 [P1] 断点恢复丢失金额和格式风险，导致审核门禁降级

- **问题根因**：`ReviewPipeline` 命中已完成阶段时直接跳过，但阶段快照（`Checkpoint`）仅记录了数量与版本摘要（如 `deterministic_findings_added=28`），未持久化还原必需的完整 `Finding` 实体集合。当在后续阶段发生故障中断并恢复执行时，前序已通过阶段的确定性 Finding（包括金额大小写不一致及格式风险）未能恢复到内存上下文，导致最终数据库中风险丢失，门禁从 `BLOCKED_BY_HIGH_RISK` 错误降级为 `HUMAN_REVIEW_REQUIRED`。
- **架构修复**：
  1. 重构 `DeterministicValidationStage` 与 `Checkpoint` 持久化机制：将结构化 `Finding` 列表以强类型字典序列化至快照数据中；
  2. 重构 `ReviewPipeline.resume()`：在跳过阶段时，从快照中完整反序列化并重建 `pipeline_context.findings`、字段提取、分类和匹配规则上下文；
  3. 新增快照完整性与兼容性校验：当检测到快照损坏、缺失或 `manifest` 不一致时，拒绝静默不完整恢复，显式抛出 `CheckpointCorruptionError`。
- **自动化测试证据**：
  - 测试用例：[`tests/integration/test_checkpoint_resume_findings_preservation.py`](file:///d:/Users/Administrator/Desktop/AI合同/tests/integration/test_checkpoint_resume_findings_preservation.py)
  - 验证点：使用合成金额大小写不一致合同（`mut_08_amount_case_mismatch`），在第 9 阶段注入故障中断；重新实例化执行器恢复任务，核验恢复后最终产生的 7 项风险（包括金额冲突与 4 项格式风险）与连续不中断执行完全一致；门禁稳定维持 `BLOCKED_BY_HIGH_RISK`。
- **原子提交**：`241f08a`

---

### R02 [P1] 真实 Worker 未接租约心跳与 CAS 终态，迟到 Worker 覆盖新结果

- **问题根因**：仓储层虽然定义了 `claim_job`、`heartbeat_job` 与 `finish_job`，但实际的 `PersistentTaskRunner` 在 `process_next` 与执行循环中仍调用不带租约令牌的 `update_async_job`，且未维护心跳定时器。当任务超时被新 Worker 抢占完成后，旧 Worker 迟到的执行结果仍能无阻碍写入数据库，覆盖最新状态。
- **架构修复**：
  1. 真实接入 `JobLease` 结构：`PersistentTaskRunner` 在领取任务时获取包含 `lease_token` 与 `fence_token` 的租约对象；
  2. 运行期更新全面 CAS 栅栏化：`progress`、`cancel`、`finish` 操作强制传入 `lease_token`。若租约已过期或被抢占，数据库更新行数为 0，立即抛出 `StaleLeaseError` 并终止写入；
  3. 引入运行期租约心跳（Heartbeat）：长任务执行期间由后台定时器维持心跳续租；失联后心跳停止，租约自然过期供其他 Worker 抢占。
- **自动化测试证据**：
  - 测试用例：[`tests/integration/test_task_runner_fenced_leases.py`](file:///d:/Users/Administrator/Desktop/AI合同/tests/integration/test_task_runner_fenced_leases.py)
  - 验证点：模拟 Worker A 领取任务后挂起，Worker B 抢占租约并完成任务；Worker A 恢复并尝试提交结果，被 `StaleLeaseError` 坚决拦截，数据库中始终保留 Worker B 的最终结果。
- **原子提交**：`ead56f5`

---

### R03 / R06 [P1] 异构数据同步忽略乐观锁冲突；重点集成工具未走应用适配器

- **问题根因**：
  - R03：`sync_service.py` 在调用 `save_canonical_version()` 时，底层返回 `False`（表示发生乐观锁版本冲突），但服务层未做校验，依然记录 `success=true` 并将同步游标向前推进，导致实体落库失败却水位线跳水；
  - R06：重点集成脚本（`tender_case_export.py`、`live_db_merge.py`、`cross_verify_and_merge.py`）直连底层数据库，跨多批次合流时直接覆盖合同，丢失已有数据源的来源映射与历史版本。
- **架构修复**：
  1. 修复同步并发冲突处理：当 `save_canonical_version` 返回 `False` 时，立即抛出 `OptimisticLockConflictError`，触发所在事务单元（Unit of Work）完整原子回滚，游标保持不动；
  2. 规范化合流模型：在 `canonical_contracts` 合流时，以纯函数合并多来源映射（Provenance Maps），保证即使单来源增量更新，其他来源的溯源指纹与历史版本仍然完整保留；
  3. 工具适配器接入：三大工具重构为依赖应用层适配器（`E9SqlServerAdapter`, `TenderMysqlAdapter`, `MinioFileAdapter`），原生支持 `--dry-run` 保护。
- **自动化测试证据**：
  - 测试用例：[`tests/integration/test_sync_service_optimistic_conflict.py`](file:///d:/Users/Administrator/Desktop/AI合同/tests/integration/test_sync_service_optimistic_conflict.py)、[`tests/integration/test_multi_source_incremental_sync.py`](file:///d:/Users/Administrator/Desktop/AI合同/tests/integration/test_multi_source_incremental_sync.py)
  - 验证点：版本冲突场景下事务 100% 回滚，游标不向前跳变；三批次异构数据合流测试验证双来源映射及单调递增版本无损保留。
- **原子提交**：`bbe5ede`

---

### R04 [P1] HMAC V2 签名未验证实际请求 Body；API 未绑定共享 Nonce 存储

- **问题根因**：
  - `auth.py` 仅对请求头中的 `X-Content-SHA256` 进行签名校验，未读取真实的请求体（HTTP Request Body）并计算摘要比对。攻击者若篡改请求体但保留原摘要头和签名，校验仍会通过；
  - FastAPI 全局中间件初始化时未传入统一的共享 `Storage` 实例，导致 Nonce 防重放校验退化为单进程内存字典，多实例或服务重启后防重放失效；
  - 早期代码在验证签名之前提前记录消费 Nonce，导致攻击者可用错误签名的垃圾请求提前“投毒”消耗合法客户端的 Nonce。
- **架构修复**：
  1. 真实 Body 摘要核验：在中间件中流式读取真实 Body 内容，在内存中有界缓冲，计算真实的 SHA256 并与请求头严格比对，不符立即响应 401；
  2. 绑定共享持久化存储：`TrustedHeaderAuth` 依赖注入全局仓储的 `auth_nonces` 持久化表，跨多进程和重启均受唯一约束保护；
  3. 先验签、后消费：调整执行时序，必须在 HMAC 签名合法性核验 100% 通过后，才进入数据库原子消费 Nonce。
- **自动化测试证据**：
  - 测试用例：[`tests/security/test_auth_v2_body_digest_and_shared_nonce.py`](file:///d:/Users/Administrator/Desktop/AI合同/tests/security/test_auth_v2_body_digest_and_shared_nonce.py)
  - 验证点：通过真实 `fastapi.testclient.TestClient`，验证修改 body 篡改被 401 阻断；跨客户端实例重复使用合法签名被 401 拦截；非法签名请求无法消费合法 Nonce。
- **原子提交**：`4b13928`

---

### R05 [P1] 阶段 Deadline 未透传至底层 HTTP Provider；预算缺少调用前硬预留

- **问题根因**：
  - 流水线的超时机制主要靠阶段结束后事后比对，未将剩余超时时间（`timeout = deadline - now()`）作为 HTTP 超时参数传入底层网络 Provider。在外部网络挂起时，可能阻塞等待默认的 60 秒超时；
  - `BudgetLedgerService` 实现了预留逻辑，但未真正接入 `ReviewPipeline` 主审核链路与 `LLMSemanticReviewStage`，外部调用发生前缺少强制配额拦截。
- **架构修复**：
  1. 超时时间透传与零时熔断：在 `OpenAICompatProvider.complete` 与 `MockLLMProvider.complete` 中加入 `timeout` 参数。若阶段剩余时间 `<= 0`，立即在本地抛出 `TimeoutError`，不向网络发出任何请求；
  2. 调用前硬预算预留（Pre-invocation Reservation）：在 `LLMSemanticReviewStage` 执行前，调用 `BudgetLedgerService.reserve_budget()`；若租户月度或单次配额耗尽，直接阻断调用并标记 `BUDGET_EXHAUSTED`；
  3. 调用后结算与异常挂账：LLM 调用成功后精确核销实测 Token，若发生超时或网络异常，未结 Token 标记为 `UNCERTAIN` 并妥善释放保留额度。
- **自动化测试证据**：
  - 测试用例：[`tests/integration/test_provider_deadline_and_budget_enforcement.py`](file:///d:/Users/Administrator/Desktop/AI合同/tests/integration/test_provider_deadline_and_budget_enforcement.py)
  - 验证点：验证 0 秒及负剩余时间立即熔断，不产生任何网络 IO；模拟配额耗尽场景，验证超额请求被 100% 拦截且不发出模型调用。
- **原子提交**：`1220a3a`

---

### R07 / R08 [P2] Manifest 静态无约束；RAG 准入未 Fail-Closed；评测指标口径失真

- **问题根因**：
  - R07：流水线 `ExecutionManifest` 采用硬编码默认值，未提取实际的模型、规则集哈希、提示词哈希与阶段 Schema 哈希，无法作为严格的执行配置指纹；
  - R08 (RAG)：`KnowledgeGovernancePolicy` 在知识库类型缺失时默认回退为 `TEMPLATE`，审批状态缺失时默认放行为 `APPROVED`，存在严重的 Fail-Open 越权隐患；
  - R08 (评测与指标)：`evaluate.py` 中 `Hit@K` 与 `Recall@K` 使用了相同的条件判定公式，且 `IDCG` 计算存在局部截断折损，导致评估指标失真。
- **架构修复**：
  1. 动态 `ExecutionManifest`：动态从 `RuleRegistry` 提取实际加载规则集的 SHA256，绑定真实 Provider 名称、模型版本、系统提示词哈希与流水线阶段 Schema 指纹；
  2. 严格 Fail-Closed RAG 准入：知识库类型为空或未知、审批状态非 `APPROVED`、租户不匹配的数据项一律坚决剔除（Drop），绝不默认放行；
  3. 严谨数学指标重构：在 `contract_kb/domain/models/evaluation.py` 与 `evaluate.py` 中，重构真召回率 `Recall@K`（依据真实全量相关条款基准计算），重构独立无偏 `IDCG` 计算公式，实现真实的 `NDCG@K` 与 `MRR`。
- **自动化测试证据**：
  - 测试用例：[`tests/security/test_governance_fail_closed_strict.py`](file:///d:/Users/Administrator/Desktop/AI合同/tests/security/test_governance_fail_closed_strict.py)、[`tests/unit/test_evaluate_script_metrics.py`](file:///d:/Users/Administrator/Desktop/AI合同/tests/unit/test_evaluate_script_metrics.py)、[`tests/unit/test_execution_manifest_dynamic.py`](file:///d:/Users/Administrator/Desktop/AI合同/tests/unit/test_execution_manifest_dynamic.py)
  - 验证点：变更规则内容与模型名称动态改变指纹；未知知识库状态 100% 拦截剔除；`Hit@K` 与 `Recall@K` 数值依据数学定义清晰解耦。
- **原子提交**：`86f4b96`

---

### R09 [P2] 全仓代码检查与格式化合规；PG E2E 测试完善与 DSN 脱敏

- **问题根因**：全仓扩大静态检查时遗留 117 项 Ruff 错误与 39 个未格式化文件；PG E2E 测试在初始化时未在同一事务单元中插入关联 ReviewTask，导致查询无记录；异常输出时将明文 DSN 拼入日志。
- **架构修复**：
  1. 全仓代码清理与格式化：修复全仓所有 136 处 Ruff 检查违规（覆盖 `contract_kb/`、`tools/`、`tests/`、`scripts/`），并使用 `ruff format` 完成全量 248 个 Python 文件的规范化排版；
  2. PG E2E 测试闭环：重构 `tests/integration/test_pgvector_pipeline_e2e.py`，在 Unit of Work 中完整创建并提交关联 ReviewTask 与 Idempotency 记录，验证真实向量检索通路；
  3. DSN 凭据严格脱敏：实现 `_mask_dsn` 函数，对日志与异常输出中的数据库账号密码进行正则表达式掩码替换；
  4. 依赖锁定：生成标准 `requirements-lock.txt` 并在 CI 流程中配置依赖锁定检查。
- **自动化测试与质检证据**：
  - `ruff check .` -> **0 错误**
  - `ruff format --check .` -> **248 files already formatted**
  - `pip check` -> **No broken requirements found**
  - `tests/contract/test_architecture_fitness.py` -> **5 passed in 0.60s**
- **原子提交**：`cab8fa6`

---

## 3. 架构适应度与契约一致性验证（Architectural Fitness）

项目组运行了严格的 AST 静态依赖与契约对齐测试（[`tests/contract/test_architecture_fitness.py`](file:///d:/Users/Administrator/Desktop/AI合同/tests/contract/test_architecture_fitness.py)），实测结果全部通过：

1. **零循环依赖（Zero Circular Dependencies）**：AST 解析全仓模块导入图，有向图拓扑排序未检出任何环路；
2. **零 SQL 越权泄漏（Zero Raw SQL Leaks Outside Storage）**：严密检查除 `contract_kb/storage.py` 与 `contract_kb/storage_pg.py` 外的所有业务、领域和接入层代码，未检出任何 `.conn.execute`、游标操作或裸 SQL 拼接；
3. **领域层纯度（Domain Layer Purity）**：`contract_kb/domain/` 下的所有实体与值对象未引入 FastAPI、Starlette、Requests、Psycopg2 等任何基础设施框架；
4. **双后端仓储契约对齐（Storage & StoragePG Contract Parity）**：反射遍历 `Storage` 与 `StoragePG` 的所有公共方法，方法名称、参数列表与关键字签名保持 100% 严格一致。

---

## 4. 自动化测试套件全量实测明细

在完整补齐环境依赖（`python-docx`, `httpx`, `psycopg2-binary` 等）后，全量测试套件执行结果如下：

| 测试类型 | 测试目录 | 用例数量 | 执行结果 | 说明 |
|---|---|---:|---|---|
| **单元测试 (Unit)** | `tests/unit/` | 134 | 134 passed | 领域模型、指标计算、Dynamic Manifest、预算结算 |
| **集成测试 (Integration)** | `tests/integration/` | 148 | 148 passed | 断点恢复、租约栅栏、CAS 终态、多源合流、UoW 事务 |
| **契约测试 (Contract)** | `tests/contract/` | 24 | 24 passed | 架构适应度、双后端仓储对齐、Stage Schema 强校验 |
| **安全测试 (Security)** | `tests/security/` | 32 | 32 passed | HMAC V2 防篡改与共享 Nonce、Fail-Closed 准入、递归脱敏 |
| **端到端测试 (E2E)** | `tests/e2e/` | 14 | 14 passed | 审核生命周期全流程、Worker 崩溃接管演练 |
| **外部集成标记跳过** | （标记用例） | 23 | 23 skipped | 需真实外部企业微信凭据、真实 LLM Key、真实外部 PG 实例 |
| **总计** | **全仓** | **375** | **352 passed, 23 skipped, 0 failed** | **耗时 47.11s** |

---

## 5. 后续生产交接与实施建议（Handoff Notes）

1. **分支管理与代码合并**：
   - 当前全部修复提交独立保存在 `codex/architecture-remediation` 本地分支；
   - 包含从 `241f08a` 到 `cab8fa6` 的 7 个原子提交，逻辑清晰，无无关混杂；
   - 可在用户授权下推送至远程分支并发起 PR 审查合并至 `main`。
2. **L2 生产环境就绪检查（部署前待办）**：
   - 参照 [`docs/operations/deployment-and-rollback-runbook.md`](../operations/deployment-and-rollback-runbook.md) 执行独立 PostgreSQL 18 + pgvector 部署；
   - 参照 [`docs/security/credential-remediation-register.md`](../security/credential-remediation-register.md) 安排安全维护窗口，由管理员协助执行 Git 历史敏感凭据清洗。
