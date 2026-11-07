-- 005: 认证签名防重放 Nonce 持久化表（Auth Signature Replay Prevention Nonces）
-- 支持在多实例与重启后持久化记录 Nonce，防止重放攻击。

CREATE TABLE IF NOT EXISTS auth_replay_nonces (
    nonce TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_auth_nonces_expiry
    ON auth_replay_nonces(expires_at);
