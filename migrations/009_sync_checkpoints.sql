-- 009_sync_checkpoints.sql
-- 外部系统增量同步水位线与原子批次历史 (T11 / F06/F12)

CREATE TABLE IF NOT EXISTS sync_watermarks (
    tenant_id TEXT NOT NULL,
    source_system TEXT NOT NULL,
    source_scope TEXT NOT NULL,
    last_updated_at DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    last_external_id TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, source_system, source_scope)
);

CREATE TABLE IF NOT EXISTS sync_batch_history (
    batch_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    source_system TEXT NOT NULL,
    source_scope TEXT NOT NULL,
    record_count INTEGER NOT NULL DEFAULT 0,
    cursor_snapshot JSONB,
    status TEXT NOT NULL DEFAULT 'COMMITTED',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
