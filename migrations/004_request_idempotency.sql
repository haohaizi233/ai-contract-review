-- 004: 请求级幂等预约与所有权强绑定（Request Idempotency with Tenant, Actor, Operation and Key binding）
-- 遵循 fail-closed 与唯一性约束，防止跨操作人泄露与重复并发竞争。

CREATE TABLE IF NOT EXISTS request_idempotency (
    tenant_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'IN_PROGRESS',
    resource_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, actor_id, operation, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_req_idem_lookup
    ON request_idempotency (tenant_id, resource_id);
