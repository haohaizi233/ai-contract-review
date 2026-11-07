# Phase 11-R + Phase 12-R: SQLite 与 PostgreSQL 存储与安全语义一致性验证报告

**报告版本**：1.0.0-REACCEPTANCE  
**日期**：2026-11-07  
**责任角色**：Database Security Engineer & Principal Architect  
**对应缺陷**：P1-06 (双数据库一致性虚标与安全语义差异)  

---

## 一、 双数据库对等架构与威胁模型

本系统设计同时支持 **轻量级单机嵌入式 SQLite** 与 **生产级企业 PostgreSQL (+ pgvector)** 双存储后端。
在整改前，双库一致性测试名不符实：测试仅对 SQLite 运行，PostgreSQL 的乐观锁、租户隔离、审计写入和 CJK 全文检索缺乏真实物理实例的验证，遗留了大量隐蔽的语法与类型不兼容缺陷。

---

## 二、 关键安全与生命周期对等契约

本次整改在 `Storage` 与 `PgVectorStorage` 上实现了 100% 严格对等的安全契约：

| 契约维度 | SQLite (`Storage`) 语义 | PostgreSQL (`PgVectorStorage`) 语义 | 一致性验证状态 |
|:---|:---|:---|:---:|
| **导入零信任 (P0-01)** | `upsert_document` 强制写入 `PENDING_REVIEW`，忽略外部携带的 `APPROVED` | 相同，强制落库 `PENDING_REVIEW`，通过 `ON CONFLICT` 保护历史审批 | **PASS** |
| **乐观锁 CAS (P0-02)** | `revision` 匹配方可更新，版本冲突原子回滚并抛出 `OptimisticLockConflictError` | 相同，原子递增 `revision`，版本不匹配抛出 `OptimisticLockConflictError` | **PASS** |
| **租户强隔离** | `tenant_id` 前置 SQL 过滤，跨租户查询坚决返回 `None` | 相同，`WHERE d.tenant_id = %s` 强过滤，跨租户查询返回 `None` | **PASS** |
| **检索准入一致性** | 未审批 (`PENDING_REVIEW`) 或已撤销 (`REVOKED`) 条款在 `approved_only=True` 检索中不可见 | 相同，`approved_only=True` 严格不可见 | **PASS** |
| **文档失效级联** | 文档状态置为 `SUPERSEDED` / `ARCHIVED`，旗下条款在检索中自动失效 | 相同，`documents.status = 'ACTIVE'` 前置连接过滤 | **PASS** |
| **多通道召回接口** | 分离 `search_lexical_candidates`、`search_dense_candidates`、`search_structured_candidates` | 相同接口签名，分别走 FTS、向量与条款号过滤 | **PASS** |
| **无向量扩展降级** | 内置 LocalHash 与 FTS5，开箱即用 | 缺失 `vector` 扩展时自动安全降级至 `migrate_relational()`，CJK 走 `ILIKE ANY` | **PASS** |

---

## 三、 本次审计发现并闭环的 PostgreSQL 缺陷清单

在对真实 PostgreSQL 实例执行物理一致性测试时，独立审计团队发现并修复了以下缺陷：
1. **缺失异常类导入**：`storage_pg.py` 在 `approve_knowledge_version` 与 `revoke_knowledge_version` 中抛出 `OptimisticLockConflictError`，但文件头部未导入该异常，导致版本冲突时触发 `NameError`。已修复导入。
2. **审计日志时间戳类型不匹配**：`audit_log.at` 列为 `TIMESTAMPTZ`，原逻辑插入 `time.time()` 浮点数导致 PostgreSQL 抛出 `DatatypeMismatch`。已对齐使用数据库原生默认时间。
3. **CJK 词法检索无分词字典时的零召回**：标准 PostgreSQL `simple` 解析器无法直接对无空格中文进行词法匹配。修复为 `(to_tsvector(...) @@ to_tsquery(...) OR c.original_text ILIKE ANY(ARRAY[...]))`，实现双保险高鲁棒召回。
4. **`save_task` 与 `upsert_document` 字典参数多态支持**：对齐 SQLite 与 PostgreSQL 在支持 `template_id` / `document_id` 以及字典入参时的宽容性与一致性。

---

## 四、 自动化复验证据

执行测试：
```bash
pytest tests/contract/test_storage_parity_contract.py tests/integration/test_pgvector_pipeline_e2e.py
```
**实测结果**：3 passed in 0.75s（SQLite 与真实 PostgreSQL 双双通过）。
