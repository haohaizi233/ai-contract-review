-- 010_knowledge_governance.sql
-- 知识治理元数据与审批状态强制字段 (T12 / F11)

ALTER TABLE clauses ADD COLUMN IF NOT EXISTS governance_status TEXT NOT NULL DEFAULT 'PENDING_REVIEW';
ALTER TABLE clauses ADD COLUMN IF NOT EXISTS reviewed_by TEXT;
ALTER TABLE clauses ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_clauses_governance 
    ON clauses(knowledge_type, approval_status, governance_status);
