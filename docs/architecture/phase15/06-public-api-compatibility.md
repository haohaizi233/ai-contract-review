# 06 — 公共 API 与向后兼容性保障（Public API & Compatibility）

## 1. `contract_kb/` 根目录收敛与兼容门面设计

重构后，`contract_kb/` 根目录从 **35 个 `.py` 文件** 收敛至 **9 个文件**（`__init__.py` + 8 个稳定公共入口/门面）：

| 根目录文件 | 行数 | 角色定位 | 委托/重导出的规范实现模块 |
|---|---:|---|---|
| `contract_kb/__init__.py` | 4 | 包根定义 | 暴露 `__version__` |
| `contract_kb/config.py` | 150 | 全局配置中心 | 提供 `PROJECT_ROOT`、`DATA_DIR`、`DB_PATH`、模型与安全门禁配置 |
| `contract_kb/api.py` | 940 | FastAPI 主入口 | 组装 `interfaces.http`、`application.pipeline`、`infrastructure.persistence` |
| `contract_kb/cli.py` | 24 | CLI 兼容门面 | `contract_kb.interfaces.cli.main` |
| `contract_kb/review_engine.py` | 202 | 审核引擎门面 | `contract_kb.application.pipeline.ReviewPipeline` |
| `contract_kb/storage.py` | 41 | SQLite 存储门面 | `contract_kb.infrastructure.persistence.sqlite_storage` |
| `contract_kb/storage_pg.py` | 27 | PG 存储门面 | `contract_kb.infrastructure.persistence.postgres_storage` |
| `contract_kb/legal_agent.py` | 29 | 法务 Agent 门面 | `contract_kb.application.agents.legal_review_agent` |
| `contract_kb/full_diff.py` | 30 | 全文 Diff 门面 | `contract_kb.modules.contract_comparison.full_diff` |

---

## 2. 核心子包职责与导出边界

### 2.1 文档处理模块 (`contract_kb.modules.document_processing`)
- `docx_parser.py`：OOXML 段落、表格、样式与修订痕迹解析（`ParsedDocument`, `ASTNode`, `parse_docx`, `sha256_file`）。
- `document_parser.py`：DOCX / PDF / DOC 多格式统一解析调度（`parse_document`, `parse_pdf`, `parse_doc`）。
- `clause_extractor.py`：合同章节与条款边界识别及表格 Schema 抽取（唯一 `ClauseRecord` 定义）。
- `classifier.py`：五级合同分类与子类型识别（`Classification`, `classify`, `SUBTYPE_CN_NAMES`）。
- `ingestion.py`：文件安全门禁校验（ZIP Bomb / 宏 / 加密拦截）与批量扫描入库编排（`SecurityCheckResult`, `ImportedDoc`, `security_check_file`, `import_template_file`, `scan_and_import`）。

### 2.2 合同比对与格式审核 (`contract_kb.modules.contract_comparison`)
- `clause_diff.py`：条款集合对齐与数值/期限/比例差异检测（`ClauseDelta`, `diff_clauses`, `diff_clause_sets`）。
- `full_diff.py`：整版双栏原格式 HTML 对照、词级高亮与法务审阅批注生成（`build_full_contract_diff`, `generate_word_diff_html`）。
- `table_aligner.py`：复杂嵌套表格与合并单元格二维对齐（`ExtractedTable`, `align_contract_tables`, `build_table_diff_block`）。
- `format_auditor.py`：页面边距、字体字号、行距与签章区版式合规审核（`FormatIssue`, `FormatAuditor`, `audit_format`）。

### 2.3 AI 端口与适配器 (`contract_kb.application.ports` + `contract_kb.infrastructure.ai`)
- 抽象端口：
  - `application/ports/embedding.py`：`EmbeddingProvider`
  - `application/ports/reranker.py`：`Reranker`
  - `application/ports/llm.py`：`LLMProvider`, `LLMFailure`, `LLMFinding`, `LLMReviewResult`, `LLMNotConfigured`, `LLMConfigError`
- 基础设施实现：
  - `infrastructure/ai/embedding_providers.py`：`LocalHashEmbedding`, `CloudEmbedding`, `SentenceTransformerEmbedding`, `get_embedding_provider`, `get_embedding_provider_by_spec`
  - `infrastructure/ai/reranker_providers.py`：`LexicalReranker`, `CrossEncoderReranker`, `get_reranker`
  - `infrastructure/ai/llm_providers.py`：`MockLLMProvider`, `OpenAICompatProvider`, `LLMSemanticReviewer`, `get_llm_provider`, `sanitize_untrusted_contract_text`

### 2.4 法务 Agent 与断点检查点 (`contract_kb.application.agents` + `contract_kb.infrastructure.checkpoints`)
- `application/agents/legal_review_agent.py`：`LegalReviewAgent`, `CircuitBreaker`, `get_legal_agent`
- `application/agents/review_sessions.py`：`AgentReviewSession`, `AgentReviewManager`, `get_agent_manager`
- `application/agents/knowledge_adapter.py`：`GovernedLegalRule`, `FeedbackSampleRecord`, `retrieve_governed_rules`, `record_user_feedback`, `export_sft_dataset`
- `infrastructure/checkpoints/storage.py`：`CheckpointStorage`, `CheckpointVerificationResult`, `compute_annotations_fingerprint`

---

## 3. `unittest.mock.patch` 拦截目标对齐

在模块搬迁过程中，所有针对具体实现模块内部依赖的 `unittest.mock.patch` 字符串目标已同步更新至真实执行模块：
- `tests/test_legal_agent_resilience.py`：更新为 `contract_kb.application.agents.legal_review_agent._load_dotenv` 与 `contract_kb.application.agents.review_sessions.get_legal_agent`。
- `tests/contract/test_feedback_governance.py`：更新为 `contract_kb.application.agents.knowledge_adapter.FEEDBACK_BASE_DIR` 与 `contract_kb.application.agents.legal_review_agent.CACHE_FILE`。
- 对于 `contract_kb.api.get_storage` 等公共入口级 Patch，`TrustedHeaderAuth` 通过 `storage=lambda: get_storage()` 动态绑定，确保舊 Patch 路径继续 100% 生效。
