-- 003: 企业级统一合同模型（Canonical Contract）、14阶段审核流水线状态、人工覆盖审计、多租户规则隔离与异步任务表
-- 幂等脚本（IF NOT EXISTS / ADD COLUMN IF NOT EXISTS），可安全重复执行。

-- 1. 补齐 documents / rules / format_standards / review_tasks / review_issues 租户、幂等与来源区分列
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_source_hash_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_tenant_hash
    ON documents(tenant_id, source_hash);

ALTER TABLE rules ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE format_standards ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';

ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS rule_set_version TEXT;
ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS model_version TEXT;
ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS reproducibility_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_review_tasks_tenant_idem
    ON review_tasks(tenant_id, idempotency_key);

ALTER TABLE review_issues ADD COLUMN IF NOT EXISTS finding_source TEXT NOT NULL DEFAULT 'DETERMINISTIC';
ALTER TABLE review_issues ADD COLUMN IF NOT EXISTS check_status TEXT NOT NULL DEFAULT 'CHECKED';

-- 2. 14 阶段审核流水线执行状态持久化表（支持断点恢复、幂等重试与阶段耗时审计）
CREATE TABLE IF NOT EXISTS review_stage_states (
    task_id TEXT NOT NULL REFERENCES review_tasks(task_id) ON DELETE CASCADE,
    stage_name TEXT NOT NULL,
    stage_order INTEGER NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    duration_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    detail_json JSONB,
    error TEXT,
    PRIMARY KEY (task_id, stage_name)
);
CREATE INDEX IF NOT EXISTS idx_stage_states_task_order
    ON review_stage_states(task_id, stage_order);

-- 3. 人工复核覆盖不可篡改审计表（Human Override with Audit）
CREATE TABLE IF NOT EXISTS human_overrides (
    override_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES review_tasks(task_id) ON DELETE CASCADE,
    issue_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT,
    previous_state TEXT,
    new_state TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_human_overrides_task
    ON human_overrides(task_id, created_at);

-- 4. 企业统一标准合同模型表（隔离 e9 / tender / 微盘 / 上传合同异构表结构）
CREATE TABLE IF NOT EXISTS canonical_contracts (
    contract_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    contract_number TEXT,
    entity_prefix TEXT,
    title TEXT,
    business_category TEXT,
    contract_subtype TEXT,
    lifecycle_status TEXT NOT NULL DEFAULT 'DRAFT',
    source_system TEXT NOT NULL DEFAULT 'UPLOAD',
    source_document_id TEXT,
    source_hash TEXT,
    total_amount NUMERIC(18, 2),
    currency TEXT NOT NULL DEFAULT 'CNY',
    parties_json JSONB,
    amounts_json JSONB,
    source_mappings_json JSONB,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_canonical_contracts_tenant
    ON canonical_contracts(tenant_id, lifecycle_status);
CREATE INDEX IF NOT EXISTS idx_canonical_contracts_number
    ON canonical_contracts(tenant_id, contract_number);

-- 5. 轻量持久化异步任务队列表（支持幂等键、指数退避重试、取消、进度与死信诊断）
CREATE TABLE IF NOT EXISTS async_jobs (
    job_id TEXT PRIMARY KEY,
    idempotency_key TEXT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    payload_json JSONB,
    result_json JSONB,
    error_message TEXT,
    dead_letter_reason TEXT,
    progress INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    temp_file_path TEXT,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_async_jobs_poll
    ON async_jobs(status, next_run_at);
CREATE INDEX IF NOT EXISTS idx_async_jobs_tenant_idem
    ON async_jobs(tenant_id, idempotency_key);
