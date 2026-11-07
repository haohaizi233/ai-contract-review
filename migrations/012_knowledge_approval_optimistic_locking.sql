-- 012_knowledge_approval_optimistic_locking.sql
-- Phase 11-R: 知识审批乐观锁与并发冲突控制 (CAS)
-- 为 documents 与 clauses 引入 revision 版本列，支持无锁/乐观并发校验

ALTER TABLE documents ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 1;
ALTER TABLE clauses ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS idx_documents_tenant_revision
    ON documents(tenant_id, document_id, revision);
