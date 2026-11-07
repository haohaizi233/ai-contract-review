# 07 - SQLite & PostgreSQL Storage Parity Report

## 1. 跨存储后端契约一致性对比

Phase 11 全面消除了 SQLite (`Storage`) 与 PostgreSQL (`PgVectorStorage`) 之间的安全与语义差异，建立了统一的行为契约基准：

| 治理与检索行为维度 | SQLite (`Storage`) | PostgreSQL (`PgVectorStorage`) | 一致性状态 | 契约对齐说明 |
|---|---|---|:---:|---|
| **默认审批状态** | `PENDING_REVIEW` | `PENDING_REVIEW` | **一致** | 新增文档无论模板或 ACTIVE，一律保持待审批 |
| **审批写入口** | `approve_knowledge_version` | `approve_knowledge_version` | **一致** | 统一参数，乐观租户校验，审计日志留痕 |
| **撤销写入口** | `revoke_knowledge_version` | `revoke_knowledge_version` | **一致** | 统一置为 `REVOKED`，同步更新向量表 |
| **无向量降级表现** | FTS5 OR 查询 (带词频扩展) | tsquery + tsvector 降级 | **一致** | PG 降级查询彻底删除 `'TEMPLATE', 'APPROVED'` 伪造字面量，真实关联 `clauses.approval_status` 并标记 `degradation_reason='NO_EMBEDDINGS_TABLE'` |
| **SQL 前置租户隔离** | `AND d.tenant_id=?` | `AND d.tenant_id=%s` | **一致** | 跨租户查询底层 SQL 零命中，不依赖 Python 后过滤 |
| **非 ACTIVE 文档阻断** | `AND d.status='ACTIVE'` | `AND d.status='ACTIVE'` | **一致** | `DRAFT`, `SUPERSEDED`, `ARCHIVED` 文档在底层 SQL 自动屏蔽 |
| **中文 FTS 分词策略** | 2-gram 词扩展 + FTS5 MATCH | 字符 2-gram 词扩展 + simple 分词 | **一致** | 针对法律专业术语、标的金额、条款编号执行相同的词元切分 |

---

## 2. 行为契约测试验证

通过专项契约测试 `tests/contract/test_storage_parity_contract.py`：
- `test_sqlite_storage_contract_lifecycle_and_approval`
- `test_sqlite_storage_non_active_document_blocked`
验证了生命周期变更、审批状态流转、租户隔离、撤销即时阻断在底层存储的一致性保证。
