-- 013_template_id_and_classification.sql
-- 模板多版本链与分类元数据刷新支持
ALTER TABLE documents ADD COLUMN IF NOT EXISTS template_id TEXT;
UPDATE documents SET template_id = document_id WHERE template_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_docs_template_tenant ON documents(template_id, tenant_id);
