# pgvector 生产拓扑迁移脚本（供 DBA 在目标 PostgreSQL 环境执行）
# 说明：本工作区无 PostgreSQL 实例，此脚本按生产拓扑交付，【未在本环境验证】。
# 本地开发/测试使用 SQLite（contract_kb/storage.py），Schema 逻辑等价。

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    source_file TEXT,
    source_hash TEXT UNIQUE,
    source_version TEXT,
    tenant_id TEXT,
    business_category TEXT,
    contract_subtype TEXT,
    document_type TEXT,
    imported_at TIMESTAMPTZ DEFAULT now(),
    status TEXT DEFAULT 'ACTIVE',
    raw_meta JSONB
);

CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    document_id TEXT REFERENCES documents(document_id) ON DELETE CASCADE,
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
    document_id TEXT REFERENCES documents(document_id) ON DELETE CASCADE,
    clause_number TEXT,
    clause_category TEXT,
    clause_subcategory TEXT,
    original_text TEXT,
    normalized_summary TEXT,
    is_company_standard BOOLEAN,
    approval_status TEXT,
    source_node_ids JSONB,
    knowledge_type TEXT,
    effective_time TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_clauses_doc ON clauses(document_id);
CREATE INDEX IF NOT EXISTS idx_clauses_cat ON clauses(clause_category);

-- Layer D：语义 RAG（向量 + 全文）
CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id TEXT PRIMARY KEY,
    document_id TEXT REFERENCES documents(document_id) ON DELETE CASCADE,
    clause_id TEXT,
    business_category TEXT,
    contract_subtype TEXT,
    knowledge_type TEXT,
    approved_status TEXT,
    effective_time TIMESTAMPTZ,
    source_hash TEXT,
    text TEXT,
    -- 维度按所用 Embedding 模型调整（本地哈希 256；中文 BERT 系常见 768/1024）
    vec vector(256)
);
CREATE INDEX IF NOT EXISTS idx_emb_vec ON embeddings
    USING ivfflat (vec vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_emb_filter ON embeddings(business_category, contract_subtype);

CREATE INDEX IF NOT EXISTS idx_clauses_fts ON clauses
    USING gin (to_tsvector('simple', coalesce(original_text,'') || ' ' || coalesce(normalized_summary,'')));

CREATE TABLE IF NOT EXISTS rules (
    rule_id TEXT, version INTEGER, name TEXT, rule_type TEXT,
    execution_mode TEXT, severity TEXT, contract_types JSONB,
    params JSONB, source_clauses JSONB, status TEXT,
    approved_by TEXT, effective_at TIMESTAMPTZ,
    PRIMARY KEY (rule_id, version)
);

CREATE TABLE IF NOT EXISTS review_tasks (
    task_id TEXT PRIMARY KEY,
    document_id TEXT,
    mode TEXT,
    status TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    meta JSONB
);

CREATE TABLE IF NOT EXISTS review_issues (
    issue_id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES review_tasks(task_id) ON DELETE CASCADE,
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

CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    at TIMESTAMPTZ DEFAULT now(),
    actor TEXT,
    action TEXT,
    target TEXT,
    detail TEXT
);

-- 租户/权限隔离（检索必须带 tenant_id 过滤）
CREATE INDEX IF NOT EXISTS idx_docs_tenant ON documents(tenant_id, status);
