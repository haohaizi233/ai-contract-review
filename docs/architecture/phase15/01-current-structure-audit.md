# 01 — 当前工程目录结构与模块职责审计报告（Phase 15-A 基线）

## 1. 审计范围与基线信息

- **审计分支**：`refactor/phase15-python-package-architecture`
- **基线 Commit SHA**：`16bf8f8a26a356bcf1708e54cf8e89e483e73071`（`origin/main`）
- **扫描范围**：`*.py`、`pyproject.toml`、`requirements-lock.txt`、`.github/workflows/ci.yml`、`start_gui.bat`、`tools/deploy/*.ps1`、`README.md`、`docs/`、`tests/`
- **自动审计工具**：`scripts/audit_architecture.py`

## 2. 重构前量化基线指标

| 指标项 | 重构前基线值 | 问题描述 |
|---|---|---|
| 全仓 Python 文件总数 | `271` | 含 `contract_kb/` (100)、`tools/` (50)、`scripts/` (3)、`tests/` (116)、仓库根目录 (2) |
| 全仓 Python 总行数 | `73,932` | 平均单文件 `272.8` 行 |
| 最大 Python 单文件 | `tools/contract_diff_web.py` (`4,603` 行) | 同时内嵌 HTTP Server、路由、业务编排、HTML/CSS/JS 与会话管理 |
| `contract_kb/` 根目录 `.py` 文件数 | `35` | 文档解析、合同比对、法务 Agent、检查点、模型适配、数据库、评估脚本与公共入口混放 |
| `tools/` 根目录 `.py` 文件数 | `42` | 生产级 Web 工作台、素材目录服务与一次性数据库诊断、E9/Tender 同步、导出脚本混杂 |
| 仓库根目录 `.py` 文件数 | `2` | `start_gui.py` (`1,012` 行单文件 Tkinter + 进程管理)、`build_doc.py` (`428` 行文档生成工具) |
| 模块间内部依赖边数 | `498` | 跨层直接引用旧扁平模块 |
| 包内循环依赖（Tarjan SCC） | `1` 组 (`contract_kb.api <-> contract_kb.auth`) | `auth.py` 在运行时反向 `import contract_kb.api` 获取默认 `get_storage()` |

## 3. 重构前目录树快照

```text
ai-contract-review/
├── build_doc.py                        # [DEVELOPMENT_TOOL] 428L 根目录孤立 PDF 渲染脚本（导入即读文件）
├── start_gui.py                        # [INTERFACE] 1012L 桌面 Tkinter 控制台单文件实现
├── start_gui.bat                       # Windows 启动入口
├── pyproject.toml                      # setuptools 构建配置
├── contract_kb/                        # 根目录堆积 35 个 .py 文件
│   ├── __init__.py
│   ├── config.py
│   ├── api.py                          # [INTERFACE] 942L FastAPI 入口与全量路由实现
│   ├── auth.py                         # [INTERFACE] 365L HTTP 认证与 RBAC
│   ├── cli.py                          # [INTERFACE] 175L CLI 入口
│   ├── review_engine.py                # [APPLICATION] 202L 审核引擎门面
│   ├── docx_parser.py                  # [BUSINESS_MODULE] 463L OOXML 解析
│   ├── document_parser.py              # [BUSINESS_MODULE] 644L 多格式文档解析
│   ├── clause_extractor.py             # [BUSINESS_MODULE] 380L 条款与表格结构提取
│   ├── classifier.py                   # [BUSINESS_MODULE] 899L 合同五级分类器
│   ├── ingest.py                       # [BUSINESS_MODULE] 293L 模板安全校验与导入编排
│   ├── clause_diff.py                  # [BUSINESS_MODULE] 218L 条款级差异比对
│   ├── full_diff.py                    # [BUSINESS_MODULE] 813L 整版全文对齐与批注生成
│   ├── table_aligner.py                # [BUSINESS_MODULE] 664L 表格结构对齐
│   ├── format_auditor.py               # [BUSINESS_MODULE] 438L 版式规范审查
│   ├── rule_engine.py                  # [BUSINESS_MODULE] 471L 声明式规则执行器
│   ├── distill.py                      # [BUSINESS_MODULE] 788L 78套模板五级知识蒸馏
│   ├── legal_agent.py                  # [APPLICATION] 675L 法务 AI 智能体审查引擎
│   ├── legal_agent_kb.py               # [APPLICATION] 439L 法务智能体规则检索与反馈脱敏治理
│   ├── agent_queue_manager.py          # [APPLICATION] 867L 多租户审查会话与看门狗队列
│   ├── agent_checkpoint.py             # [INFRASTRUCTURE] 182L 8项指纹检查点持久化
│   ├── embeddings.py                   # [INFRASTRUCTURE] 28L Embedding 工厂门面
│   ├── embeddings_base.py              # [INFRASTRUCTURE] 69L Embedding 端口与离线哈希实现
│   ├── embeddings_real.py              # [INFRASTRUCTURE] 65L SentenceTransformer 实现
│   ├── rerankers.py                    # [INFRASTRUCTURE] 76L 词法与 Cross-Encoder 重排器
│   ├── llm_review.py                   # [INFRASTRUCTURE] 460L LLM 端口、防注入信封与 OpenAI 兼容实现
│   ├── storage.py                      # [INFRASTRUCTURE] 3275L SQLite 仓储与 create_storage 工厂
│   ├── storage_pg.py                   # [INFRASTRUCTURE] 3412L PostgreSQL + pgvector 仓储实现
│   ├── storage_errors.py               # [INFRASTRUCTURE] 16L 存储异常定义
│   ├── ablation_study.py               # [EVALUATION] 525L 检索与重排消融实验
│   ├── benchmark_retrieval.py          # [EVALUATION] 220L 检索基准评测
│   ├── evaluate.py                     # [EVALUATION] 591L 检索与端到端审核评估
│   ├── golden.py                       # [EVALUATION] 212L 30份黄金变异样本生成
│   ├── retrieval_golden.py             # [EVALUATION] 283L 检索黄金查询集与 NDCG 计算
│   ├── synthetic.py                    # [EVALUATION] 319L 合成语料生成器
│   ├── domain/                         # 已建 DDD 领域层 (models, value_objects, events, services)
│   ├── application/                    # 已建部分应用层 (pipeline, workflow, integrations, security, ports)
│   ├── modules/                        # 已建部分业务模块 (retrieval, rules, templates, cases, integrations)
│   ├── infrastructure/                 # 已建部分基础设施 (file_storage, observability, persistence/uow, task_queue)
│   └── interfaces/                     # 已建部分接口层 (http/middleware, http/schemas)
├── tools/                              # 根目录堆积 42 个 .py 文件 + 2 个子目录 (diagnostics, wecom_wedrive_probe)
├── scripts/                            # 3 个运维审计与导出脚本
└── tests/                              # 116 个测试文件 (unit, integration, contract, security, e2e, performance)
```

## 4. 旧模块分类清单（按 9 大架构类别）

### 4.1 `DOMAIN`（领域层）
- 现有 `contract_kb/domain/models/*`、`value_objects/*`、`events/*`、`services/*` 职责清晰，零外部 I/O 依赖，保持不变并补充 `domain/exceptions/`。

### 4.2 `APPLICATION`（应用编排与智能体层）
- 已归位：`application/pipeline/*`、`application/workflow/*`、`application/integrations/*`、`application/security/*`、`application/ports/persistence.py`
- 待归位：
  - `contract_kb/legal_agent.py` → `contract_kb/application/agents/legal_review_agent.py`
  - `contract_kb/agent_queue_manager.py` → `contract_kb/application/agents/review_sessions.py`
  - `contract_kb/legal_agent_kb.py` → `contract_kb/application/agents/knowledge_adapter.py`
  - Embedding / LLM / Reranker 抽象端口 → `contract_kb/application/ports/{embedding,llm,reranker}.py`

### 4.3 `BUSINESS_MODULE`（核心业务模块）
- 已归位：`modules/retrieval/*`、`modules/rules/{governance,strategies}.py`、`modules/templates/catalog_data.py`、`modules/cases/reconciliation.py`、`modules/integrations/adapters.py`
- 待归位：
  - 文档处理 (`docx_parser.py`, `document_parser.py`, `clause_extractor.py`, `classifier.py`, `ingest.py`) → `contract_kb/modules/document_processing/`
  - 合同比对与格式审查 (`clause_diff.py`, `full_diff.py`, `table_aligner.py`, `format_auditor.py`) → `contract_kb/modules/contract_comparison/`
  - 规则执行器 (`rule_engine.py`) → `contract_kb/modules/rules/engine.py`
  - 模板知识蒸馏与素材目录服务 (`contract_kb/distill.py`, `tools/material_catalog.py`) → `contract_kb/modules/templates/{distill,material_catalog}.py`

### 4.4 `INFRASTRUCTURE`（基础设施适配层）
- 已归位：`infrastructure/file_storage/retention.py`、`infrastructure/observability/{metrics,redaction}.py`、`infrastructure/persistence/uow.py`、`infrastructure/task_queue/runner.py`
- 待归位：
  - 检查点存储 (`agent_checkpoint.py`) → `contract_kb/infrastructure/checkpoints/storage.py`
  - AI 模型提供方 (`embeddings.py`, `embeddings_base.py`, `embeddings_real.py`, `rerankers.py`, `llm_review.py`) → `contract_kb/infrastructure/ai/{embedding_providers,llm_providers,reranker_providers}.py`
  - 数据库存储 (`storage.py`, `storage_pg.py`, `storage_errors.py`) → `contract_kb/infrastructure/persistence/{sqlite_storage,postgres_storage,errors}.py`

### 4.5 `INTERFACE`（用户与外部接口层）
- HTTP 接口：`contract_kb/auth.py` → `contract_kb/interfaces/http/auth.py`；`contract_kb/api.py` 核心路由收敛至 `contract_kb/interfaces/http/`
- CLI 接口：`contract_kb/cli.py` 核心实现收敛至 `contract_kb/interfaces/cli/main.py`
- Web 审阅工作台：`tools/contract_diff_web.py` (`4,603` 行) 拆解至 `contract_kb/interfaces/web/`（含 `app.py`, `server.py`, `routes/`, `services/`, `templates/index.html`, `static/css/`, `static/js/`）
- 桌面 GUI 控制台：`start_gui.py` (`1,012` 行) 拆解至 `contract_kb/interfaces/desktop/`（含 `app.py`, `main_window.py`, `process_runner.py`, `commands.py`, `widgets/`）

### 4.6 `EVALUATION`（评测与基准实验层）
- `ablation_study.py` → `contract_kb/evaluation/benchmarks/ablation_study.py`
- `benchmark_retrieval.py` → `contract_kb/evaluation/retrieval/benchmark_retrieval.py`
- `evaluate.py` → `contract_kb/evaluation/review/evaluate.py`
- `golden.py`, `retrieval_golden.py`, `synthetic.py` → `contract_kb/evaluation/datasets/{golden,retrieval_golden,synthetic}.py`

### 4.7 `DEVELOPMENT_TOOL` / `MAINTENANCE_SCRIPT` / `COMPATIBILITY_ENTRYPOINT`
- `tools/` 下 42 个脚本按职责归入 `tools/diagnostics/`、`tools/templates/`、`tools/integrations/wecom/`、`tools/integrations/tender/`、`tools/data_ops/`、`tools/reports/`、`tools/maintenance/`；
- 仓库根目录 `build_doc.py` 迁入 `tools/reports/build_doc.py`；
- 仅对外部测试、CLI 与 GUI 强依赖的稳定入口保留薄兼容包装器。
