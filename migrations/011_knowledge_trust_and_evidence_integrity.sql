-- 011_knowledge_trust_and_evidence_integrity.sql
-- Phase 11: 知识审批独立可信审计与证据完整性约束
-- 明确知识生命周期与审批状态分离，记录完整的人工与系统审批留痕。

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
