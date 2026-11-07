-- 008_contract_source_history.sql
-- 稳定合同实体版本历史与来源映射表 (T10 / F06 / ADR-002)

CREATE TABLE IF NOT EXISTS canonical_contract_versions (
    contract_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    tenant_id TEXT NOT NULL,
    snapshot JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (contract_id, version)
);

CREATE INDEX IF NOT EXISTS idx_canonical_versions_lookup 
    ON canonical_contract_versions(contract_id, version DESC);

CREATE TABLE IF NOT EXISTS contract_source_mappings (
    tenant_id TEXT NOT NULL,
    source_system TEXT NOT NULL,
    external_id TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    external_code TEXT,
    source_hash TEXT,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, source_system, external_id)
);

CREATE INDEX IF NOT EXISTS idx_source_mappings_contract 
    ON contract_source_mappings(contract_id);
