# Phase 11-R: 知识审批与 CAS 乐观并发控制加固报告

**文档编号**：03-APPROVAL-AND-CAS-HARDENING  
**状态**：COMPLETED (VERIFIED)  
**涉及模块**：
- `contract_kb/storage.py`
- `contract_kb/storage_pg.py`
- `contract_kb/storage_errors.py`
- `contract_kb/api.py`
- `migrations/012_knowledge_approval_optimistic_locking.sql`
- `tests/security/test_phase11_import_approval_bypass.py`
- `tests/security/test_phase11_approval_cas.py`

---

## 一、 核心安全缺陷与修复

### 1. P0-01: 数据导入绕过法务审批漏洞彻底封堵
- **根因分析**：
  原实现中 `Storage.upsert_document` 与 `PgVectorStorage.upsert_document` 允许外部入参携带 `approval_status="APPROVED"` 并直接落库入表，且在重导入或 SQLite `INSERT OR REPLACE` 时会覆盖历史状态，破坏了法律审批边界。
- **加固策略**：
  1. **零信任准入边界**：数据导入仅产生知识候选，绝对不授予知识权威。普通 `upsert_document` 将所有新条款的初始审批状态强制锁定为 `PENDING_REVIEW`，忽略外部传入的 `approval_status`。
  2. **状态防篡改冲突策略**：将插入语义从 `INSERT OR REPLACE` 调整为 `INSERT INTO ... ON CONFLICT(clause_id) DO UPDATE SET ...`，排除对 `approval_status` 的更新。若某一历史条款已被法务审批（`APPROVED`）或被撤销（`REVOKED`），重新导入该文档不会覆盖或洗白既有审批状态。
  3. **独立授权历史迁移命令**：提供显式受控的 `authorized_bulk_import_approved(doc, tenant, approver_id, approval_reason)`。该命令必须校验审批人身份 `approver_id` 与审批理由 `approval_reason`，并严格在事务中向 `knowledge_approval_records` 插入审计凭单。

### 2. P0-02: 知识库版本 CAS 乐观锁并发控制落地
- **根因分析**：
  原方法签名声明了 `expected_revision` 形参，但在 SQLite 与 PostgreSQL 存储层均未参与 SQL 条件过滤与版本递增，导致并发审批、撤销或修改时存在静默状态覆盖风险。
- **加固策略**：
  1. **Schema 增强**：在 `documents` 与 `clauses` 增加 `revision INTEGER NOT NULL DEFAULT 1` 物理版本号，并发布数据库迁移 `migrations/012_knowledge_approval_optimistic_locking.sql`。
  2. **原子 CAS 更新与冲突检测**：
     - 在调用 `approve_knowledge_version` 或 `revoke_knowledge_version` 时读取当前文档修订号 `current_rev`。
     - 若传入 `expected_revision` 且与 `current_rev` 不符，直接抛出 `OptimisticLockConflictError`。
     - 在事务中执行原子条件递增：
       ```sql
       UPDATE documents SET revision = revision + 1
       WHERE document_id = :did AND tenant_id = :tid AND revision = :current_rev;
       ```
       若影响行数为 0，立即触发冲突回滚并报错。
     - 同一事务中将所涉及条款的 `revision` 同步递增为新版本号 `new_revision`，并在 `knowledge_approval_records` 中记录 `version = 'rev_{new_revision}'`。
  3. **对外 API 状态码规范**：
     在 `/api/v1/contract-kb/documents/{document_id}/approve` 与 `/revoke` 接口中，捕获 `OptimisticLockConflictError` 并返回 **HTTP 409 Conflict**，携带详细冲突说明，杜绝静默覆盖。

---

## 二、 自动化验证与测试矩阵

| 测试用例名称 | 测试目标 | 实测结果 |
|:---|:---|:---|
| `test_import_cannot_grant_approval` | 验证普通导入携带 `APPROVED` 被强制重置为 `PENDING_REVIEW` | PASS |
| `test_reimport_cannot_restore_revoked_approval` | 验证条款撤销后重导入不会篡改 `REVOKED` 状态 | PASS |
| `test_cross_tenant_import_isolation` | 验证同文本多租户导入生命周期与审批独立隔离 | PASS |
| `test_rebuild_index_preserves_approval_status` | 验证向量/文本索引重建不修改审批状态 | PASS |
| `test_authorized_bulk_import_creates_audit_record` | 验证授权历史导入命令生成正式审计单据 | PASS |
| `test_approve_with_mismatched_expected_revision_fails` | 验证修订号不一致触发 `OptimisticLockConflictError` | PASS |
| `test_approve_and_revoke_atomic_revision_increment` | 验证多次审批/撤销修订号原子递增，旧版本无法重放 | PASS |
| `test_api_approval_cas_conflict_returns_409` | 验证 HTTP API 返回 409 Conflict 与 403 权限控制 | PASS |

---

## 三、 结论

P0-01 与 P0-02 缺陷已在 SQLite 和 PostgreSQL 双引擎中彻底修复，并通过全部针对性安全回归测试，知识审批与并发修改具备防绕过、可审计与防覆盖的强一致性保证。
