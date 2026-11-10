# 04 — 目标包结构与模块化单体架构蓝图（Target Package Architecture）

## 1. 架构定位与设计原则

本次 Phase 15 重构遵循 **Modular Monolith（模块化单体）+ Clean Architecture / DDD 分层** 原则，在不拆微服务、不更换 `contract_kb` 包名与 `setuptools` 构建体系、不改变任何业务行为与外部契约的前提下，将原本堆叠在 `contract_kb/` 根目录（35 个 `.py`）与 `tools/` 根目录（42 个 `.py`）的模块全面归位。

核心设计准则：
1. **成熟目录优先复用**：直接复用并充实已有的 `domain/`、`application/`、`modules/`、`infrastructure/`、`interfaces/`，不制造平行重复目录。
2. **单一规范实现（Single Canonical Implementation）**：每个核心类（如 `Storage`、`PgVectorStorage`、`ClauseRecord`、`LegalReviewAgent`）全局仅保留唯一规范实现文件；历史公共路径仅保留轻量重导出门面（Compatibility Facade）。
3. **内外职责隔离**：
   - `contract_kb/` 承载全部可复用生产业务代码、接口适配器与离线评测体系；
   - `tools/` 承载工程诊断、外部集成 CLI、历史数据修复、报表导出与运维脚本；
   - `scripts/` 承载仓库级架构静态审计与工程自动化脚本。

---

## 2. 重构前后目录树对比

### 2.1 重构前目录痛点（基线 `16bf8f8a`）

```text
ai-contract-review/
├── start_gui.py                  # 1,012 行，Tkinter UI + 进程管理 + 命令拼接混杂在仓库根目录
├── build_doc.py                  # 散落在仓库根目录的文档生成脚本
├── contract_kb/                  # 根目录堆积 35 个 .py 文件（解析/RAG/Agent/持久化/评测/API 混杂）
│   ├── agent_checkpoint.py
│   ├── agent_queue_manager.py
│   ├── api.py
│   ├── auth.py                   # 与 api.py 存在双向循环导入
│   ├── classifier.py
│   ├── clause_diff.py
│   ├── clause_extractor.py
│   ├── document_parser.py
│   ├── docx_parser.py
│   ├── embeddings*.py / rerankers.py / llm_review.py
│   ├── format_auditor.py / full_diff.py / table_aligner.py
│   ├── golden.py / retrieval_golden.py / synthetic.py / evaluate.py / benchmark_retrieval.py / ablation_study.py
│   ├── ingest.py / distill.py / rule_engine.py
│   ├── legal_agent.py / legal_agent_kb.py
│   └── storage.py / storage_pg.py / storage_errors.py
└── tools/                        # 根目录堆积 42 个 .py 文件
    ├── contract_diff_web.py      # 4,603 行超大文件（HTTP Server + 路由 + 业务服务 + HTML/CSS/JS 内联字符串）
    ├── material_catalog.py       # 被 Web 工作台直接依赖的业务目录服务却放在 tools/
    └── ... (其余 40 个数据库诊断、企微同步、Tender 导出、两库合并脚本平铺混放)
```

### 2.2 重构后目标目录树（当前实际结构）

```text
ai-contract-review/
├── contract_kb/
│   ├── __init__.py
│   ├── config.py                 # 全局路径、环境与安全阈值配置
│   ├── api.py                    # FastAPI 主入口（兼容 uvicorn contract_kb.api:app）
│   ├── cli.py                    # CLI 兼容门面（委托至 interfaces.cli.main）
│   ├── review_engine.py          # 审核引擎门面（委托至 application.pipeline.ReviewPipeline）
│   ├── storage.py                # 存储兼容门面（重导出 sqlite_storage）
│   ├── storage_pg.py             # PG 存储兼容门面（重导出 postgres_storage）
│   ├── legal_agent.py            # 法务 Agent 兼容门面（重导出 legal_review_agent）
│   ├── full_diff.py              # 全文 Diff 兼容门面（重导出 contract_comparison.full_diff）
│   │
│   ├── domain/                   # 核心领域层（零外部框架与基础设施依赖）
│   │   ├── models/               # canonical_contract.py, review.py
│   │   ├── value_objects/        # amounts.py, contract_number.py, deadline.py
│   │   └── ports/                # storage_port.py
│   │
│   ├── application/              # 应用编排与用例层
│   │   ├── pipeline/             # runner.py, stages.py, format_adapter.py, deadline.py
│   │   ├── agents/               # legal_review_agent.py, review_sessions.py, knowledge_adapter.py
│   │   ├── workflow/             # agent_state_machine.py
│   │   ├── security/             # idempotency.py
│   │   └── ports/                # embedding.py, llm.py, reranker.py
│   │
│   ├── modules/                  # 领域业务模块层（Bounded Contexts）
│   │   ├── document_processing/  # docx_parser.py, document_parser.py, clause_extractor.py, classifier.py, ingestion.py
│   │   ├── contract_comparison/  # clause_diff.py, full_diff.py, table_aligner.py, format_auditor.py
│   │   ├── retrieval/            # service.py, authority.py, citation_verifier.py
│   │   ├── rules/                # engine.py, registry.py, redline_guard.py, conflict_resolver.py, governance.py
│   │   ├── templates/            # distill.py, catalog_data.py, material_catalog.py
│   │   ├── cases/                # reconciliation.py, safe_cache.py
│   │   └── integrations/         # adapters.py
│   │
│   ├── infrastructure/           # 基础设施适配层
│   │   ├── persistence/          # sqlite_storage.py, postgres_storage.py, errors.py, uow.py
│   │   ├── ai/                   # embedding_providers.py, llm_providers.py, reranker_providers.py
│   │   ├── checkpoints/          # storage.py
│   │   ├── task_queue/           # runner.py
│   │   └── observability/        # logging_metrics.py
│   │
│   ├── interfaces/               # 外部交互接口层
│   │   ├── http/                 # auth.py, middleware.py, schemas.py
│   │   ├── web/                  # app.py, server.py, routes/*, services/*, templates/index.html, static/{css,js}/*
│   │   ├── desktop/              # app.py, main_window.py, process_runner.py, commands.py, widgets/cards.py
│   │   └── cli/                  # main.py
│   │
│   └── evaluation/               # 离线评测与基准实验层
│       ├── datasets/             # golden.py, retrieval_golden.py, synthetic.py
│       ├── retrieval/            # benchmark_retrieval.py
│       ├── review/               # evaluate.py
│       └── benchmarks/           # ablation_study.py
│
├── tools/                        # 工程工具与运维脚本体系（根目录仅保留 8 个薄兼容包装器）
│   ├── diagnostics/              # 数据库表结构、枚举、字段注释与微盘诊断脚本
│   ├── templates/                # 模板分析、匹配、五级规则构建与 OSS 模板下载脚本
│   ├── integrations/
│   │   ├── wecom/                # sync_wecom_cli.py, sync_wecom_wedrive.py
│   │   └── tender/               # tender_fullsync.py, tender_case_export.py, tender_case_quality.py 等
│   ├── data_ops/                 # contract_merge_repair.py, cross_verify_and_merge.py, live_db_merge.py 等
│   ├── reports/                  # build_knowledge_graph_html.py, export_contract_mappings_excel.py, build_doc.py 等
│   └── maintenance/              # diff_contract.py
│
├── scripts/
│   └── audit_architecture.py     # 可重复执行的 AST 静态架构审计工具
│
├── tests/
│   ├── architecture/             # Phase 15 新增：分层依赖、循环导入、包边界与公共入口自动化架构测试
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── security/
│   ├── e2e/
│   └── performance/
│
├── migrations/
├── docs/architecture/phase15/
├── pyproject.toml
├── start_gui.py                  # 31 行薄兼容入口（委托至 contract_kb.interfaces.desktop.app:main）
└── start_gui.bat
```

---

## 3. 开发者导航指南（Developer Navigation Map）

| 当开发者需要修改…… | 唯一规范目录 / 模块位置 |
|---|---|
| **DOCX / PDF / DOC 文档解析与条款抽取** | `contract_kb/modules/document_processing/` |
| **条款级 Diff、双栏全文比对、表格对齐、格式审核** | `contract_kb/modules/contract_comparison/` |
| **RAG 权威检索、来源分级治理、引用核验** | `contract_kb/modules/retrieval/` |
| **确定性审核规则引擎、红线守卫、人工覆写** | `contract_kb/modules/rules/` |
| **模板分类路由规则、知识蒸馏、标准素材库** | `contract_kb/modules/templates/` |
| **14 阶段审核流水线编排** | `contract_kb/application/pipeline/` |
| **法务审查 Agent、SSE 队列管理、反馈治理** | `contract_kb/application/agents/` |
| **Embedding / Reranker / LLM 端口与适配器** | `contract_kb/application/ports/` + `contract_kb/infrastructure/ai/` |
| **SQLite / PostgreSQL 持久化与事务工作单元** | `contract_kb/infrastructure/persistence/` |
| **FastAPI HTTP 接口与 HMAC-V2 鉴权** | `contract_kb/interfaces/http/` + `contract_kb/api.py` |
| **合同比对可视化 Web 工作台（路由/服务/前端资源）** | `contract_kb/interfaces/web/` |
| **Windows 桌面运维控制台（Tkinter GUI）** | `contract_kb/interfaces/desktop/` |
| **企业微信/微盘同步与 Tender 系统同步脚本** | `tools/integrations/wecom/`、`tools/integrations/tender/` |
| **RAG 与端到端审核质量评测基准** | `contract_kb/evaluation/` |
