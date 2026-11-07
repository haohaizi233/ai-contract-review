# ADR-002：统一持久化端口与基于 SAVEPOINT 的双后端工作单元（Unit of Work）

- **状态**：已采纳（Accepted）
- **日期**：2026-11-07
- **决策者**：首席软件架构师（Principal Architect）

## 1. 背景与问题

基线审计发现持久化层存在严重分裂与事务隐患：
1. `contract_kb/storage_pg.py`（`PgVectorStorage`）在生产代码中扇入为 **0**，API 与审核引擎硬编码实例化 SQLite `Storage`。
2. 9 处业务与接口代码直接访问 `storage.conn.execute(...)` 绕过仓储封装。
3. 审核结果落库（`review_tasks` + `review_issues` + `audit_log`）与文档入库（`documents` + `clauses` + `embeddings`）缺少跨步骤事务边界。
4. PostgreSQL 连接在 `autocommit=False` 模式下，普通查询方法内部直接调用 `self.conn.commit()` 会提前提交正在进行的外层业务事务；而查询抛错未回滚又会导致连接陷入 `InFailedSqlTransaction` 假死状态。

## 2. 决策内容

1. **统一工厂与 Fail-Closed 注入**：
   - 通过 [`create_storage()`](file:///d:/Users/Administrator/Desktop/AI合同/contract_kb/storage.py) 统一构建存储实例。当显式配置 `CONTRACT_KB_STORAGE_BACKEND=postgres` 或 `CONTRACT_KB_PG_DSN` 时，连接或迁移失败立即抛出 `StorageConfigError`，严禁静默回退至本地 SQLite 造成生产数据丢失假象。
2. **100% 仓储签名对齐**：
   - `Storage` 与 `PgVectorStorage` 对齐全部文档、规则、格式标准、审核任务、阶段状态、人工覆写、标准合同与异步任务方法签名，并在 `PgVectorStorage` 边界统一将 `TIMESTAMPTZ`（`datetime`）与 Epoch 秒（`float`）归一化。
3. **支持嵌套 `SAVEPOINT` 的 `UnitOfWork`**：
   - 引入 `_tx_depth` 事务深度计数与 `_commit()` / `_rollback()` 守卫：仅当 `_tx_depth == 0` 时仓储方法自动提交或回滚底层连接；当处于 `with storage.unit_of_work():` 作用域内（`_tx_depth > 0`）时，使用 SQL `SAVEPOINT sp_uow_<depth>` 管理提交与局部回滚。

## 3. 实证验证

- 在 SQLite 与真实本地 **PostgreSQL 18.6** 实例上通过集成测试验证了外层异常全量回滚、内层 `SAVEPOINT` 局部回滚以及事务内读写混合操作的一致性（见 [`tests/integration/test_uow_and_pg18_relational.py`](file:///d:/Users/Administrator/Desktop/AI合同/tests/integration/test_uow_and_pg18_relational.py)）。
