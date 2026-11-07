-- 007_execution_checkpoints.sql
-- 流水线执行检查点与断点恢复 (T09 / F09 / ADR-001/002)

CREATE TABLE IF NOT EXISTS execution_checkpoints (
    task_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    stage_order INTEGER NOT NULL,
    input_hash TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    output_snapshot JSONB,
    status TEXT NOT NULL DEFAULT 'COMPLETED',
    lease_token TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, stage_name)
);

CREATE INDEX IF NOT EXISTS idx_exec_checkpoints_lookup 
    ON execution_checkpoints(task_id, stage_order);
