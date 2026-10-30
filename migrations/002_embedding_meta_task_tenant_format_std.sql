-- 002: Embedding 索引版本管理 + 审核任务租户/创建者 + 公司格式标准（可审批/可版本化）
-- 说明：
--   * review_tasks 增加 tenant_id / created_by，API 层强制租户与资源所有权检查；
--   * embedding_index_meta 记录索引提供方/维度/版本（换模型必须 rebuild_index，禁止混用）；
--   * format_standards 保存公司格式标准的版本与审批状态（未审批标准不得产生正式违规结论）。
-- 与 001 一样幂等（IF NOT EXISTS / ADD COLUMN IF NOT EXISTS），可重复执行。

ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS created_by TEXT;

CREATE TABLE IF NOT EXISTS embedding_index_meta (
    version BIGSERIAL PRIMARY KEY,
    provider TEXT,
    dim INTEGER,
    model_spec TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL DEFAULT 'ACTIVE'
);
CREATE INDEX IF NOT EXISTS idx_emb_meta_status ON embedding_index_meta(status, version);

CREATE TABLE IF NOT EXISTS format_standards (
    standard_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    params JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'DRAFT',
    created_by TEXT,
    approved_by TEXT,
    approved_at TIMESTAMPTZ,
    effective_at TIMESTAMPTZ,
    note TEXT,
    PRIMARY KEY (standard_id, version)
);
