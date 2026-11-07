-- 006_job_leases.sql
-- 持久化异步任务租约与崩溃恢复字段 (T06 / F03 / ADR-004)

ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS worker_id TEXT;
ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS lease_token TEXT;
ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ DEFAULT now();
ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ DEFAULT now();

CREATE INDEX IF NOT EXISTS idx_async_jobs_lease_recovery 
    ON async_jobs(status, lease_until, next_run_at);
