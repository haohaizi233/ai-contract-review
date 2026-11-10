# Phase 14-R: SQLite 与 PostgreSQL 知识契约一致性与回滚治理 (04-sqlite-pg-parity-and-rollback-governance)

## 一、双库行为割裂缺陷审计 (DEFECT-P0-05)

在前期版本中，系统同时支持 SQLite 与 PostgreSQL，但在底层数据写入与知识受控检索治理时存在不一致：
1. **知识源类型契约不匹配**：
   - 在 SQLite (`contract_kb/storage.py`) 中，导入合同模板时条款的 `knowledge_type` 默认为 `"TEMPLATE"`；
   - 在 PostgreSQL (`contract_kb/storage_pg.py`) 中，历史代码第 478 行却硬编码写入了 `"LAYER_A_RAW_CLAUSE"`；
   - **后果**：在受控检索治理策略 `KnowledgeGovernancePolicy` 中，只认可 `"TEMPLATE"`、`"APPROVED_RULE"` 与 `"HISTORICAL_CASE"`；PostgreSQL 写入的 `"LAYER_A_RAW_CLAUSE"` 被判为未知类型，导致即使法务在 PG 中正式审批通过了模板条款，受控检索也无法召回任何权威标准，造成 SQLite 与 PostgreSQL 在相同业务逻辑下出现截然不同的审核行为。
2. **版本回滚与未审批阻断门禁不严**：
   在执行 `rollback_to_version` 回滚至历史某版本时，若历史该版本下存在未审批通过（`PENDING_REVIEW`）或审批状态为空的异常条款，旧逻辑未能对其进行彻底拦截，存在将未受控草稿条款误激活为生效条款的风险。

---

## 二、整改与治理对齐

### 2.1 统一写入契约与向后兼容规范化

1. **统一 PostgreSQL 写入契约**：
   在 `contract_kb/storage_pg.py` 中，修正条款插入与向量嵌入逻辑，将 `knowledge_type` 统一设置为：
   ```python
   ktype = (
       "TEMPLATE" if doc.get("document_type") == "contract_template" else "HISTORICAL_CASE"
   )
   ```
2. **治理策略别名兼容机制**：
   在 `contract_kb/modules/retrieval/governance.py` 的 `normalize_source_type` 中，显式建立类型别名映射：
   ```python
   alias_map = {
       "LAYER_A_RAW_CLAUSE": KnowledgeSourceType.TEMPLATE,
       "CANONICAL_TEMPLATE": KnowledgeSourceType.TEMPLATE,
       "ENTERPRISE_STANDARD": KnowledgeSourceType.TEMPLATE,
   }
   ```
   确保历史存量 PostgreSQL 数据库中的老数据无需执行破坏性数据更新，也能被受控治理引擎正确识别为 `TEMPLATE`。

### 2.2 严格版版本回滚审批阻断

在 `contract_kb/storage.py` 与 `contract_kb/storage_pg.py` 的 `rollback_to_version` 逻辑中实施前置与后置双向审批校验：
- 回滚目标版本下的所有条款，必须经过正式法务审批（`approval_status == 'APPROVED'`）；
- 若目标版本包含任何 `PENDING_REVIEW`、`REJECTED` 或 `approval_status IS NULL` 的条款，回滚事务立即中止回滚，并抛出 `ValueError("目标版本存在未审批条款，拒绝激活！")`；
- 严密保护审计流水，记录回滚操作人、时间戳与版本变动记录。

---

## 三、一致性测试证据

1. **双库检索契约对齐测试**：
   - 测试文件：`tests/contract/test_pg_sqlite_governed_retrieval_parity.py`
   - 验证内容：`LAYER_A_RAW_CLAUSE` 成功解析为有效 `KnowledgeTrustDescriptor`，其 `source_type` 为 `TEMPLATE`，且经过 `annotate_hit` 后 `can_drive_mandatory_finding == True`。
2. **模板链与回滚强校验测试**：
   - 测试文件：`tests/contract/test_rollback_template_chain_validation.py`
   - 7 项测试全部通过，覆盖：
     - 正常回滚到已审批历史版本；
     - 尝试回滚到包含未审批条款的版本被严格阻断；
     - 撤销后审批状态失效拦截；
     - 跨租户回滚隔离。
