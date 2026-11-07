# Phase 11: RAG 可信知识治理、证据链完整性与企业级检索一致性专项审计报告

- **审计基线 Commit SHA**: `defadd67ce2b2defe7bcdd14af7a1dcc8bef2d9c`
- **修复分支**: `fix/phase11-governed-rag-integrity`
- **审计负责人**: Principal Software Architect & Security Architect
- **最高执行准则**: 
  > 合同知识的“存在”“成功导入”“可被检索”“被企业批准”“可以支持强制合规结论”是五个不同的状态，严禁通过字段默认值、隐式类型转换或 Prompt 文本将其混为一谈。

---

## 1. 缺陷清单与证据索引 (P0 & P1)

| 编号 | 严重度 | 缺陷位置 | 关键函数/行号 | 缺陷机制与复现表现 | 修复方案概要 |
|---|---|---|---|---|---|
| **P0-01** | CRITICAL | `contract_kb/storage.py` | `upsert_document()` (L579-L594)<br>`_index_clauses()` (L663-L667) | 导入时因文档为 `ACTIVE` 或类型为 `contract_template`，自动将 `PENDING_REVIEW` 条款提升为 `APPROVED`；索引时无条件将空/待审状态覆写为 `APPROVED`。已在 `test_reproduce_p0_01_sqlite_import_auto_approval` 中复现。 | 取消任何自动提升逻辑；`upsert_document` 保持原审批状态，缺失时默认为 `PENDING_REVIEW`；索引直接读取真实 `approval_status`。新增统一带审计的 `approve_knowledge_version` 命令。 |
| **P0-02** | CRITICAL | `contract_kb/storage_pg.py` | `_fts_search()` (L908, L861-862)<br>`search()` (L810-L830) | 在无向量表或向量不可用回退到 FTS 查询时，SQL 硬编码 `'TEMPLATE', 'APPROVED'` 伪造来源类型与审批状态，导致未审批历史案例可被伪装成权威模板。 | 消除 SQL 中硬编码字面量，真实关联 `clauses.knowledge_type` 与 `clauses.approval_status`，严格执行 `approved_only` 与租户预过滤。 |
| **P0-03** | CRITICAL | `contract_kb/modules/retrieval/governance.py` | `is_chunk_admissible()` (L130-L153)<br>`normalize_source_type()` (L94-L95) | 状态缺失时 `doc_status or "ACTIVE"` 默认放行；租户过滤在 hit 缺失 `tenant_id` 时漏判；将生命周期状态 `"ACTIVE"` 视作审批状态；将 `LAYER_D_RAG` 索引标签映射为权威 `TEMPLATE`。已在 `test_reproduce_p0_03_governance_missing_metadata_fail_closed` 中复现。 | 严格 Fail-Closed：缺失 `doc_status` 立即拒绝；指定租户时缺失 `tenant_id` 立即拒绝；审批状态仅允许 `APPROVED`；废弃将索引标签直接映射为权威模板的别名。 |
| **P0-04** | CRITICAL | `contract_kb/modules/retrieval/service.py` | `retrieve_for_review()` (L61, L70, L111-L117) | 权威检索通道以 `approved_only=False` 查询底层存储，仅依赖 Python 后置过滤（若数据库前置候选被未审批条款填满，将直接导致权威条款漏召回）；未核验通过的候选未从最终 bundle 中剥离。已在 `test_reproduce_p0_04_governed_retrieval_db_prefilter` 中复现。 | 数据库层必须执行 `approved_only=True` 预过滤；提供明确接口；核验失败的候选坚决剔除出权威提示词上下文。 |
| **P0-05** | CRITICAL | `contract_kb/modules/retrieval/citation.py` | `verify_retrieval_hits()` (L35-L42, L47-L65) | 当命中结果集为空时，虚报 `citation_correctness=1.0`；核验缺少 `tenant_id`、`document_id`、`source_hash`、`approval_status`、`document_status` 校验；`storage.get_clause()` 跨租户无屏障。已在 `test_reproduce_p0_05_citation_verifier_empty_hits` 中复现。 | 样本量为 0 时返回 0.0 或 `NO_EVIDENCE`；构建强类型 `EvidenceReference` 并进行多维度租户、版本、哈希、审批时效性核验；未通过者剔除。 |
| **P1-01** | HIGH | `contract_kb/api.py` | `/contract-kb/search` (L346-L362) | 对外 API 直接调用 `Storage.search()`，绕过 `GovernedRetrievalService`，未标注治理元数据与权威等级，且可能返回未获法务审批的原始片段。 | 统一通过 `GovernedRetrievalService` 接入，绑定认证主体租户与角色，限制普通用户仅能查看经审批的受控知识。 |
| **P1-02** | HIGH | `contract_kb/storage.py` vs `storage_pg.py` | 全文检索、降级逻辑、回滚与元数据字段差异 | SQLite 与 PostgreSQL 存在审批字段写入差异、中文 FTS 处理差异以及降级标志差异。 | 建立跨后端行为契约测试基准（Storage Parity Contract），保障安全过滤一致性。 |
| **P1-03** | HIGH | `contract_kb/clause_extractor.py` | `extract_clauses()` (L188, L192-L194, L211) | `"".join(buffer).strip()` 丢失段落边界；仅以前 80 字符哈希生成 12 位 Chunk ID 极易碰撞；硬编码 `is_company_standard=True`。已在 `test_reproduce_p1_03_clause_extractor_paragraph_breaks` 中复现。 | 保留原始段落换行与 AST 节点结构；区分原文与归一化摘要；改进稳定 ID 生成算法并保留旧 ID 映射兼容。 |
| **P1-04** | HIGH | `contract_kb/config.py`<br>`contract_kb/modules/retrieval/reranker.py` | `config.py` (L50)<br>`reranker.py` (L122) | `ALLOW_EXTERNAL_LLM` 环境变量默认值为 `"true"`，与默认离线零出境原则冲突；不同模块读取不同环境变量 (`CONTRACT_KB_ALLOW_EXTERNAL_LLM` vs `ALLOW_EXTERNAL_LLM`)。已在 `test_reproduce_p1_04_external_llm_authorization_default` 中复现。 | 统一配置项为 `CONTRACT_KB_ALLOW_EXTERNAL_LLM`，默认值严格设为 `"false"` (Fail-Closed)；跨模块统一守卫与 SSRF 防护。 |

---

## 2. 失败复现验证（Test Evidence）

通过自动化测试 `tests/security/test_phase11_reproduce_flaws.py`，全部 6 项核心缺陷在修复前稳定复现失败：

```text
FAILED tests/security/test_phase11_reproduce_flaws.py::test_reproduce_p0_01_sqlite_import_auto_approval
E   AssertionError: Flaw P0-01 reproduced: clause was elevated to APPROVED
E   assert 'APPROVED' == 'PENDING_REVIEW'

FAILED tests/security/test_phase11_reproduce_flaws.py::test_reproduce_p0_03_governance_missing_metadata_fail_closed
E   AssertionError: Flaw P0-03 reproduced: missing doc_status was accepted (defaulted to ACTIVE)
E   assert True is False

FAILED tests/security/test_phase11_reproduce_flaws.py::test_reproduce_p0_04_governed_retrieval_db_prefilter
E   AssertionError: Flaw P0-04 reproduced: authoritative search passed approved_only=False
E   assert False is True

FAILED tests/security/test_phase11_reproduce_flaws.py::test_reproduce_p0_05_citation_verifier_empty_hits
E   AssertionError: Flaw P0-05 reproduced: empty hits returned citation_correctness=1.0
E   assert 1.0 == 0.0

FAILED tests/security/test_phase11_reproduce_flaws.py::test_reproduce_p1_03_clause_extractor_paragraph_breaks
E   AssertionError: Flaw P1-03 reproduced: paragraph breaks lost: '第一条 乙方应在五个工作日内完成交付。但经甲方书面同意可以延期。'

FAILED tests/security/test_phase11_reproduce_flaws.py::test_reproduce_p1_04_external_llm_authorization_default
E   AssertionError: Flaw P1-04 reproduced: ALLOW_EXTERNAL_LLM defaults to True
E   assert True is False
```

---

## 3. 架构整改实施计划 (ADR 路线图)

1. **Phase 11-B: 审批状态与知识治理数据模型重设计与修复 (P0-01, P0-02, P0-03)**
   - 彻底废除自动提权；
   - 建立五维模型：Document Lifecycle, Approval Workflow State, Knowledge Source Type, Authority Capability, Effective Version；
   - 提供具备审计跟踪、乐观锁、幂等性的统一审批写接口 `approve_knowledge_version`；
   - 修复 SQLite / PostgreSQL 数据库写入与降级查询。
2. **Phase 11-C: 受控检索收口与 SQL 前置过滤 (P0-04, P1-01)**
   - 数据库底层必须实现强制 pre-filter；
   - 对外 API 统一走 `GovernedRetrievalService`；
   - 严格租户与审批约束，严禁未授权或未审批条款泄露。
3. **Phase 11-D: 证据链完整性与 CitationVerifier 重构 (P0-05)**
   - 强类型 `EvidenceReference`；
   - 多维引文核验与失效剔除；
   - 修正零样本指标统计口径。
4. **Phase 11-E: Chunking 保真性与稳定 ID (P1-03)**
   - 保留段落换行与 AST 节点映射；
   - 建立高鲁棒性 Chunk ID 并保留向后兼容映射。
5. **Phase 11-F: 双存储一致性与出境授权默认安全 (P1-02, P1-04)**
   - SQLite 与 PostgreSQL 行为对齐；
   - 统一配置项并默认禁用外部传输。
6. **Phase 11-G: 综合验收与交付**
   - 完整回归、对抗性测试矩阵、静态分析及架构交付文档。
