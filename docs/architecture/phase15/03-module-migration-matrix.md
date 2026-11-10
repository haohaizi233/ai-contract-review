# 03 — 全量模块迁移矩阵（Phase 15-A / Batch 0）

## 1. `contract_kb/` 根目录 35 个模块迁移矩阵

| # | 旧路径 (Old Path) | 新规范路径 (New Path) | 类别 | 核心公开符号 (Public Symbols) | 已知调用方 | 运行时路径依赖 | 保留根目录兼容入口 | 迁移批次 |
|---|---|---|---|---|---|---|---|---|
| 1 | `contract_kb/__init__.py` | `contract_kb/__init__.py` | `COMPATIBILITY_ENTRYPOINT` | `__version__` | 全仓 | 无 | 是（包根） | 保持 |
| 2 | `contract_kb/config.py` | `contract_kb/config.py` | `COMPATIBILITY_ENTRYPOINT` | `PROJECT_ROOT`, `DATA_DIR`, `DB_PATH`, `ensure_dirs` 等 | 全仓 | `Path(__file__).resolve().parent.parent` | 是（全局配置） | 保持 |
| 3 | `contract_kb/api.py` | `contract_kb/interfaces/http/app.py` + `contract_kb/api.py` | `INTERFACE` | `app`, `get_storage`, `get_runner`, `KnowledgeApprovalReq` | `tests/*`, `uvicorn` | 无 | **是**（轻量门面与补丁锚点） | Batch 8 |
| 4 | `contract_kb/auth.py` | `contract_kb/interfaces/http/auth.py` | `INTERFACE` | `Principal`, `TrustedHeaderAuth`, `compute_auth_signature_v2`, `authorize`, `check_tenant`, `enforce_input_limits` | `api.py`, `tests/security/*` | 无 | 否（统一更新至 `interfaces.http.auth`） | Batch 8 |
| 5 | `contract_kb/cli.py` | `contract_kb/interfaces/cli/main.py` + `contract_kb/cli.py` | `INTERFACE` | `main` | `ci.yml`, `start_gui.py`, `README.md` | 无 | **是**（轻量门面） | Batch 8 |
| 6 | `contract_kb/review_engine.py` | `contract_kb/application/pipeline/review_engine.py` + `contract_kb/review_engine.py` | `APPLICATION` | `SourceReference`, `Issue`, `ReviewEngine`, `audit_format_from_rec` | `api.py`, `evaluate.py`, `task_queue`, `tests/*` | 无 | **是**（轻量门面） | Batch 8 |
| 7 | `contract_kb/ablation_study.py` | `contract_kb/evaluation/benchmarks/ablation_study.py` | `EVALUATION` | `ComponentCallTracker`, `AblationConfig`, `MockDenseEmbedding`, `MockCrossEncoderReranker`, `evaluate_configuration`, `run_full_ablation_study` | `tests/unit/*`, `tests/integration/*` | 无 | 否 | Batch 1 |
| 8 | `contract_kb/benchmark_retrieval.py` | `contract_kb/evaluation/retrieval/benchmark_retrieval.py` | `EVALUATION` | `build_bench_storage`, `run_benchmark`, `run` | `ci.yml`, `start_gui.py`, `tests/test_format_diff.py` | 无 | 否 | Batch 1 |
| 9 | `contract_kb/evaluate.py` | `contract_kb/evaluation/review/evaluate.py` | `EVALUATION` | `determine_evaluation_tier`, `eval_rag`, `eval_review`, `run` | `start_gui.py`, `README.md`, `tests/*` | 无 | 否 | Batch 1 |
| 10 | `contract_kb/golden.py` | `contract_kb/evaluation/datasets/golden.py` | `EVALUATION` | `generate_all`, `MUTATIONS` | `tests/conftest.py`, `tests/integration/*`, `tests/security/*` | 无 | 否 | Batch 1 |
| 11 | `contract_kb/retrieval_golden.py` | `contract_kb/evaluation/datasets/retrieval_golden.py` | `EVALUATION` | `GoldenQuery`, `GoldenDoc`, `GOLDEN_DOCS`, `GOLDEN_QUERIES`, `HARD_NEGATIVE_QUERIES`, `ndcg_at_k`, `judge_hits` | `ablation_study`, `benchmark_retrieval`, `tests/*` | 无 | 否 | Batch 1 |
| 12 | `contract_kb/synthetic.py` | `contract_kb/evaluation/datasets/synthetic.py` | `EVALUATION` | `generate_corpus`, `SYNTH_TEMPLATES` | `ci.yml`, `tests/conftest.py`, `tests/*` | `config.PROJECT_ROOT / "tests/fixtures/synth_corpus"` | 否 | Batch 1 |
| 13 | `contract_kb/docx_parser.py` | `contract_kb/modules/document_processing/docx_parser.py` | `BUSINESS_MODULE` | `ASTNode`, `ParsedDocument`, `sha256_file`, `parse_docx` | `document_parser`, `ingestion`, `tests/*` | 无 | 否 | Batch 2 |
| 14 | `contract_kb/document_parser.py` | `contract_kb/modules/document_processing/document_parser.py` | `BUSINESS_MODULE` | `parse_doc`, `parse_pdf`, `parse_document` | `ingestion` | 无 | 否 | Batch 2 |
| 15 | `contract_kb/clause_extractor.py` | `contract_kb/modules/document_processing/clause_extractor.py` | `BUSINESS_MODULE` | `ClauseRecord`, `extract_clauses`, `extract_table_schemas` | `ingestion`, `tests/*` | 无 | 否 | Batch 2 |
| 16 | `contract_kb/classifier.py` | `contract_kb/modules/document_processing/classifier.py` | `BUSINESS_MODULE` | `Classification`, `classify`, `SUBTYPE_CN_NAMES` | `ingestion`, `full_diff` | 无 | 否 | Batch 2 |
| 17 | `contract_kb/ingest.py` | `contract_kb/modules/document_processing/ingestion.py` | `BUSINESS_MODULE` | `SecurityCheckResult`, `ImportedDoc`, `security_check_file`, `import_template_file`, `scan_and_import` | `api`, `cli`, `stages`, `full_diff`, `web`, `tests/*` | 无 | 否 | Batch 2 |
| 18 | `contract_kb/clause_diff.py` | `contract_kb/modules/contract_comparison/clause_diff.py` | `BUSINESS_MODULE` | `ClauseDelta`, `diff_clauses`, `diff_clause_sets` | `api`, `distill`, `full_diff`, `tests/*` | 无 | 否 | Batch 2 |
| 19 | `contract_kb/full_diff.py` | `contract_kb/modules/contract_comparison/full_diff.py` + `contract_kb/full_diff.py` | `BUSINESS_MODULE` | `escape_html`, `generate_word_diff_html`, `build_full_contract_diff` | `web`, `diff_contract`, `tests/*` | 无 | **是**（核心入口要求） | Batch 2 |
| 20 | `contract_kb/table_aligner.py` | `contract_kb/modules/contract_comparison/table_aligner.py` | `BUSINESS_MODULE` | `ExtractedTable`, `extract_tables_from_nodes`, `compute_table_similarity`, `align_contract_tables`, `build_table_diff_block` | `full_diff`, `tests/*` | 无 | 否 | Batch 2 |
| 21 | `contract_kb/format_auditor.py` | `contract_kb/modules/contract_comparison/format_auditor.py` | `BUSINESS_MODULE` | `FormatIssue`, `FormatAuditor`, `audit_format`, `BASELINE_STANDARDS` | `format_adapter`, `tests/*` | 无 | 否 | Batch 2 |
| 22 | `contract_kb/rule_engine.py` | `contract_kb/modules/rules/engine.py` | `BUSINESS_MODULE` | `RuleSpec`, `RuleExecution`, `ReviewContext`, `RuleEngine` | `stages.py`, `tests/test_rule_engine.py` | 无 | 否 | Batch 2 |
| 23 | `contract_kb/distill.py` | `contract_kb/modules/templates/distill.py` | `BUSINESS_MODULE` | `build_taxonomy`, `build_clause_diff`, `build_field_schema`, `build_rule_candidates`, `run` | `tests/test_knowledge.py`, `tests/test_review.py`, `tests/test_compare_semantics.py` | 无 | 否 | Batch 2 |
| 24 | `contract_kb/embeddings_base.py` | `contract_kb/application/ports/embedding.py` + `contract_kb/infrastructure/ai/embedding_providers.py` | `APPLICATION` / `INFRASTRUCTURE` | `EmbeddingProvider`, `LocalHashEmbedding`, `CloudEmbedding`, `_tokenize` | `embeddings`, `storage`, `tests/*` | 无 | 否 | Batch 3 |
| 25 | `contract_kb/embeddings_real.py` | `contract_kb/infrastructure/ai/embedding_providers.py` | `INFRASTRUCTURE` | `SentenceTransformerEmbedding`, `get_embedding_provider_by_spec` | `benchmark_retrieval`, `tests/*` | 无 | 否 | Batch 3 |
| 26 | `contract_kb/embeddings.py` | `contract_kb/infrastructure/ai/embedding_providers.py` | `INFRASTRUCTURE` | `get_embedding_provider` | `storage`, `storage_pg`, `tests/*` | 无 | 否 | Batch 3 |
| 27 | `contract_kb/rerankers.py` | `contract_kb/application/ports/reranker.py` + `contract_kb/infrastructure/ai/reranker_providers.py` | `APPLICATION` / `INFRASTRUCTURE` | `Reranker`, `LexicalReranker`, `CrossEncoderReranker`, `get_reranker` | `ablation_study`, `benchmark_retrieval` | 无 | 否 | Batch 3 |
| 28 | `contract_kb/llm_review.py` | `contract_kb/application/ports/llm.py` + `contract_kb/infrastructure/ai/llm_providers.py` | `APPLICATION` / `INFRASTRUCTURE` | `LLMFailure`, `LLMFinding`, `LLMReviewResult`, `LLMNotConfigured`, `LLMProvider`, `MockLLMProvider`, `OpenAICompatProvider`, `LLMSemanticReviewer`, `LLMConfigError`, `sanitize_untrusted_contract_text`, `build_prompt`, `get_llm_provider` | `runner`, `stages`, `legal_agent`, `review_engine`, `tests/*` | 无 | 否 | Batch 3 |
| 29 | `contract_kb/agent_checkpoint.py` | `contract_kb/infrastructure/checkpoints/storage.py` | `INFRASTRUCTURE` | `CHECKPOINT_DIR`, `CheckpointVerificationResult`, `CheckpointStorage`, `compute_annotations_fingerprint` | `review_sessions`, `tests/*` | `config.DATA_DIR / "agent_checkpoints"` | 否 | Batch 4 |
| 30 | `contract_kb/legal_agent_kb.py` | `contract_kb/application/agents/knowledge_adapter.py` | `APPLICATION` | `GovernedLegalRule`, `FeedbackSampleRecord`, `GOVERNED_RULES_STORE`, `FEEDBACK_BASE_DIR`, `retrieve_governed_rules`, `retrieve_legal_rules`, `sanitize_sensitive_data`, `record_user_feedback`, `export_sft_dataset` | `legal_review_agent`, `web`, `tests/*` | `config.DATA_DIR.parent / "ai_feedback"` | 否 | Batch 4 |
| 31 | `contract_kb/legal_agent.py` | `contract_kb/application/agents/legal_review_agent.py` + `contract_kb/legal_agent.py` | `APPLICATION` | `CircuitBreaker`, `LegalReviewAgent`, `get_legal_agent` | `review_sessions`, `tests/*` | `config.DATA_DIR / "ai_agent_cache.json"` | **是**（核心入口要求） | Batch 4 |
| 32 | `contract_kb/agent_queue_manager.py` | `contract_kb/application/agents/review_sessions.py` | `APPLICATION` | `AgentReviewSession`, `AgentReviewManager`, `get_agent_manager`, `CHECKPOINT_DIR` | `web`, `tests/*` | 无 | 否 | Batch 4 |
| 33 | `contract_kb/storage_errors.py` | `contract_kb/infrastructure/persistence/errors.py` | `INFRASTRUCTURE` | `EmbeddingConfigMismatch`, `StorageConfigError`, `OptimisticLockConflictError` | `api`, `sqlite_storage`, `postgres_storage`, `tests/*` | 无 | 否 | Batch 5 |
| 34 | `contract_kb/storage.py` | `contract_kb/infrastructure/persistence/sqlite_storage.py` + `contract_kb/storage.py` | `INFRASTRUCTURE` | `Storage`, `create_storage` | 全仓 (`62` 处引用) | `config.DB_PATH` | **是**（核心入口要求） | Batch 5 |
| 35 | `contract_kb/storage_pg.py` | `contract_kb/infrastructure/persistence/postgres_storage.py` + `contract_kb/storage_pg.py` | `INFRASTRUCTURE` | `PgVectorStorage`, `StoragePG` | `sqlite_storage`, `tests/*` | `config.PROJECT_ROOT / "migrations"` | **是**（核心入口要求） | Batch 5 |

## 2. `tools/` 根目录 42 个脚本与仓库根目录 2 个脚本迁移矩阵

| 旧路径 | 新规范路径 | 类别 | 保留薄兼容包装器 | 迁移批次 |
|---|---|---|---|---|
| `tools/contract_diff_web.py` | `contract_kb/interfaces/web/{app,server,routes/*,services/*}` | `INTERFACE` | **是** (`tools/contract_diff_web.py`) | Batch 6 |
| `start_gui.py` | `contract_kb/interfaces/desktop/{app,main_window,process_runner,commands}.py` | `INTERFACE` | **是** (`start_gui.py`) | Batch 7 |
| `build_doc.py` | `tools/reports/build_doc.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/material_catalog.py` | `contract_kb/modules/templates/material_catalog.py` + `tools/templates/material_catalog.py` | `BUSINESS_MODULE` | **是** (`tools/material_catalog.py`) | Batch 2 |
| `tools/check_db_tables.py` | `tools/diagnostics/check_db_tables.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/dump_all_col_comments.py` | `tools/diagnostics/dump_all_col_comments.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/dump_all_db_templates.py` | `tools/diagnostics/dump_all_db_templates.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/dump_db_enums.py` | `tools/diagnostics/dump_db_enums.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/dump_dict_tables.py` | `tools/diagnostics/dump_dict_tables.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/dump_sample.py` | `tools/diagnostics/dump_sample.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/inspect_matter.py` | `tools/diagnostics/inspect_matter.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/query_e9_workflows.py` | `tools/diagnostics/query_e9_workflows.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/analyze_templates.py` | `tools/templates/analyze_templates.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/build_template_5th_layer_rules.py` | `tools/templates/build_template_5th_layer_rules.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/download_db_templates.py` | `tools/templates/download_db_templates.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/list_ref_files.py` | `tools/templates/list_ref_files.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/match_templates.py` | `tools/templates/match_templates.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/read_rules_docx.py` | `tools/templates/read_rules_docx.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/read_sample_templates.py` | `tools/templates/read_sample_templates.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/template_5th_layer_data.py` | `tools/templates/template_5th_layer_data.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/build_knowledge_graph_html.py` | `tools/reports/build_knowledge_graph_html.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/compile_b64_to_png.py` | `tools/reports/compile_b64_to_png.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/export_contract_mappings_excel.py` | `tools/reports/export_contract_mappings_excel.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/export_merge_supplement.py` | `tools/reports/export_merge_supplement.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/export_mubu_files.py` | `tools/reports/export_mubu_files.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/export_prefixes.py` | `tools/reports/export_prefixes.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/generate_classification_ledger.py` | `tools/reports/generate_classification_ledger.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/sample_prefixes.py` | `tools/reports/sample_prefixes.py` | `DEVELOPMENT_TOOL` | 否 | Batch 1 |
| `tools/sync_wecom_cli.py` | `tools/integrations/wecom/sync_wecom_cli.py` | `MAINTENANCE_SCRIPT` | **是** (`tools/sync_wecom_cli.py`) | Batch 7 |
| `tools/sync_wecom_wedrive.py` | `tools/integrations/wecom/sync_wecom_wedrive.py` | `MAINTENANCE_SCRIPT` | **是** (`tools/sync_wecom_wedrive.py`) | Batch 7 |
| `tools/tender_attach_export.py` | `tools/integrations/tender/tender_attach_export.py` | `MAINTENANCE_SCRIPT` | 否 | Batch 7 |
| `tools/tender_case_export.py` | `tools/integrations/tender/tender_case_export.py` | `MAINTENANCE_SCRIPT` | **是** (`tools/tender_case_export.py`) | Batch 7 |
| `tools/tender_case_quality.py` | `tools/integrations/tender/tender_case_quality.py` | `MAINTENANCE_SCRIPT` | 否 | Batch 7 |
| `tools/tender_case_web.py` | `tools/integrations/tender/tender_case_web.py` | `MAINTENANCE_SCRIPT` | 否 | Batch 7 |
| `tools/tender_fullsync.py` | `tools/integrations/tender/tender_fullsync.py` | `MAINTENANCE_SCRIPT` | 否 | Batch 7 |
| `tools/tender_sync_web.py` | `tools/integrations/tender/tender_sync_web.py` | `MAINTENANCE_SCRIPT` | 否 | Batch 7 |
| `tools/tender_unify_merge.py` | `tools/integrations/tender/tender_unify_merge.py` | `MAINTENANCE_SCRIPT` | 否 | Batch 7 |
| `tools/contract_merge_repair.py` | `tools/data_ops/contract_merge_repair.py` | `MAINTENANCE_SCRIPT` | 否 | Batch 7 |
| `tools/cross_verify_and_merge.py` | `tools/data_ops/cross_verify_and_merge.py` | `MAINTENANCE_SCRIPT` | **是** (`tools/cross_verify_and_merge.py`) | Batch 7 |
| `tools/live_db_merge.py` | `tools/data_ops/live_db_merge.py` | `MAINTENANCE_SCRIPT` | **是** (`tools/live_db_merge.py`) | Batch 7 |
| `tools/recover_e9_ledger_assets.py` | `tools/data_ops/recover_e9_ledger_assets.py` | `MAINTENANCE_SCRIPT` | 否 | Batch 7 |
| `tools/reorganize_cases_by_category.py` | `tools/data_ops/reorganize_cases_by_category.py` | `MAINTENANCE_SCRIPT` | 否 | Batch 7 |
| `tools/validate_contract_merge_repair.py` | `tools/data_ops/validate_contract_merge_repair.py` | `MAINTENANCE_SCRIPT` | 否 | Batch 7 |
| `tools/diff_contract.py` | `tools/maintenance/diff_contract.py` | `MAINTENANCE_SCRIPT` | **是** (`tools/diff_contract.py`) | Batch 7 |
