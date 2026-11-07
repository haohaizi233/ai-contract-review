# 08 - Migration and Rollback Plan

## 1. 迁移概览

- **新增迁移脚本**: `migrations/011_knowledge_trust_and_evidence_integrity.sql`
- **目标数据库**: PostgreSQL 18 + pgvector / SQLite 3
- **SQL 语法解析器校验**: 通过 `pglast` 语法解析器 100% 静态验证。

```sql
-- 011_knowledge_trust_and_evidence_integrity.sql
CREATE TABLE IF NOT EXISTS knowledge_approval_records (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    knowledge_id TEXT NOT NULL,
    knowledge_type TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT 'v1',
    approver_id TEXT NOT NULL,
    approval_action TEXT NOT NULL,
    approval_reason TEXT,
    clause_ids JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_approvals_lookup
    ON knowledge_approval_records(tenant_id, knowledge_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_clauses_approval_ktype
    ON clauses(approval_status, knowledge_type);
```

---

## 2. 存量数据分级与 Dry Run 审计

生产数据库已部署专用的审计与干运行工具：
`scripts/audit_knowledge_approval_data.py`

### 存量数据分级策略：
- **类别 A（具备明确法务审批记录）**：保留 `APPROVED`，登记入 `knowledge_approval_records`；
- **类别 B（声明为标准模板但缺失审批证据）**：重置为 `PENDING_REVIEW`，排入法务待复核队列；
- **类别 C（非 ACTIVE 状态下的遗留 APPROVED 条款）**：置为 `REVOKED`，彻底阻断检索。

### 回滚方案：
1. **DML 回滚**：在执行数据修复前，自动创建快照备份文件：
   `cp data/contract_kb.db data/contract_kb.db.backup_<timestamp>`
2. **DDL 回滚 SQL**：
   ```sql
   DROP INDEX IF EXISTS idx_knowledge_approvals_lookup;
   DROP INDEX IF EXISTS idx_clauses_approval_ktype;
   DROP TABLE IF EXISTS knowledge_approval_records;
   ```
