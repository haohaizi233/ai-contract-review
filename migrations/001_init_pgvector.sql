-- pgvector 生产拓扑迁移脚本（供 DBA 在目标 PostgreSQL 环境执行）
-- 说明：本工作区无 PostgreSQL 实例；SQL 语法已用 pglast 解析器校验（tests/test_pg_migration.py），
--       运行时行为未在真实 PG 实例验证（见最终验收报告"未验证清单"）。
-- 本地开发/测试使用 SQLite（contract_kb/storage.py），接口经 storage_pg.PgVectorStorage 对齐。

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    source_file TEXT,
    source_hash TEXT UNIQUE,
    source_version TEXT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    business_category TEXT,
    contract_subtype TEXT,
    document_type TEXT,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    raw_meta JSONB
);

CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    node_type TEXT,
    ord INTEGER,
    text TEXT,
    section TEXT,
    clause_number TEXT,
    table_index INTEGER,
    row_index INTEGER,
    column_index INTEGER,
    source_locator JSONB,
    extraction_status TEXT
);
CREATE INDEX IF NOT EXISTS idx_nodes_doc ON nodes(document_id);

CREATE TABLE IF NOT EXISTS clauses (
    clause_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    clause_number TEXT,
    clause_category TEXT,
    clause_subcategory TEXT,
    original_text TEXT NOT NULL,
    normalized_summary TEXT,
    is_company_standard BOOLEAN NOT NULL DEFAULT FALSE,
    approval_status TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
    source_node_ids JSONB,
    knowledge_type TEXT,
    effective_time TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_clauses_doc ON clauses(document_id);
CREATE INDEX IF NOT EXISTS idx_clauses_cat ON clauses(clause_category);

CREATE TABLE IF NOT EXISTS fields (
    field_key TEXT NOT NULL,
    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    field_name TEXT,
    data_type TEXT,
    required_candidate BOOLEAN,
    required_approved BOOLEAN,
    meta JSONB,
    PRIMARY KEY (field_key, document_id)
);

-- Layer D：语义 RAG（向量 + 全文）
CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    clause_id TEXT,
    business_category TEXT,
    contract_subtype TEXT,
    knowledge_type TEXT,
    approved_status TEXT,
    effective_time TIMESTAMPTZ,
    source_hash TEXT,
    text TEXT,
    -- 维度按所用 Embedding 模型调整（本地哈希 256；BGE-M3 为 1024）
    vec vector(256) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_emb_vec ON embeddings
    USING ivfflat (vec vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_emb_filter ON embeddings(business_category, contract_subtype);
CREATE INDEX IF NOT EXISTS idx_emb_tenant ON embeddings(document_id, approved_status);

CREATE INDEX IF NOT EXISTS idx_clauses_fts ON clauses
    USING gin (to_tsvector('simple', coalesce(original_text,'') || ' ' || coalesce(normalized_summary,'')));

CREATE TABLE IF NOT EXISTS rules (
    rule_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    name TEXT,
    rule_type TEXT,
    execution_mode TEXT,
    severity TEXT,
    priority INTEGER NOT NULL DEFAULT 100,
    contract_types JSONB,
    params JSONB,
    source_clauses JSONB,
    status TEXT NOT NULL DEFAULT 'DRAFT',
    approved_by TEXT,
    effective_at TIMESTAMPTZ,
    effective_until TIMESTAMPTZ,
    PRIMARY KEY (rule_id, version)
);

CREATE TABLE IF NOT EXISTS rule_executions (
    exec_id TEXT PRIMARY KEY,
    rule_id TEXT,
    rule_version INTEGER,
    rule_type TEXT,
    result TEXT,
    severity TEXT,
    message TEXT,
    evidence JSONB,
    executed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS review_tasks (
    task_id TEXT PRIMARY KEY,
    document_id TEXT,
    mode TEXT,
    status TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    meta JSONB
);

CREATE TABLE IF NOT EXISTS review_issues (
    issue_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES review_tasks(task_id) ON DELETE CASCADE,
    rule_id TEXT,
    severity TEXT,
    category TEXT,
    title TEXT,
    description TEXT,
    expected_value JSONB,
    actual_value JSONB,
    source_ref JSONB,
    knowledge_refs JSONB,
    suggestion TEXT,
    execution_mode TEXT,
    requires_human_review BOOLEAN,
    confidence REAL,
    human_state TEXT DEFAULT 'PENDING',
    human_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_issues_task ON review_issues(task_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor TEXT,
    action TEXT,
    target TEXT,
    detail TEXT
);

-- 租户/权限隔离（检索必须带 tenant_id 过滤）
CREATE INDEX IF NOT EXISTS idx_docs_tenant ON documents(tenant_id, status);
