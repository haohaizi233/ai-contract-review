# contract-kb — 企业知识产权合同模板知识库与智能审核引擎

基于 78 份真实企业合同模板知识库 + 14 阶段可组合审查流水线 + RAG 知识来源治理 + 受控审查 Agent + 统一标准合同（Canonical Contract）与双数据库工作单元（Unit of Work）的企业级模块化单体架构。

> 架构验收状态以 [docs/architecture/architecture-acceptance.md](docs/architecture/architecture-acceptance.md) 为准（含重构前后精确 AST/圈复杂度/安全/性能量化对比表与 Mermaid 架构图）。

## 快速启动

```powershell
# 1. 依赖与虚拟环境（Python ≥ 3.11）
pip install -r requirements-lock.txt

# 2. 导入模板知识库（解析→分类→条款/Schema 抽取）
python -m contract_kb.cli import

# 3. 运行全套六层测试金字塔（271 项测试：249 通过，0 失败，22 需生产凭据受控跳过）
pytest tests tools/wecom_wedrive_probe/tests -q

# 4. 运行真实测量评估（RAG Hit@K / MRR / NDCG@8 / 引用真实度 + 审核延迟 / 误漏报率）
python -m contract_kb.evaluation.review.evaluate

# 5. 启动企业级 API 服务（支持 /api/v1/* 标准契约与 /api/* 平滑兼容）
uvicorn contract_kb.api:app --host 127.0.0.1 --port 8000
```

## 六层自动化测试金字塔（Test Pyramid）

| 层级 | 路径 | 核心验证范围 | 测试数 |
|---|---|---|---|
| **L1: 单元测试** | `tests/unit/` | 14 阶段流水线独立输入输出契约、RAG 来源分级、受控 Agent 8 状态机、纯领域对账 | 24 项 |
| **L2: 集成测试** | `tests/integration/` | **真实 PostgreSQL 18.6** 隔离事务与 UoW、`/api/v1/*` 分页/取消/指标/错误信封 | 6 项 |
| **L3: 架构契约测试** | `tests/contract/` | **0 SCC 循环依赖、0 向 tools 反向依赖、0 裸 SQL 越界、Domain 纯净度、双存储接口 100% 对齐** | 5 项 |
| **L4: 安全测试** | `tests/security/` 及既有安全组 | Cryptographic Nonce 隔离信封、ZIP Bomb/XXE 内存放大防护、HMAC 时间戳防重放 | 13 项 |
| **L5: 端到端闭环** | `tests/e2e/` | 规则与排版标准审批 → 核心合规防绕过 → 14 阶段留痕与可复现哈希 → 租户隔离 → 审计覆盖 → 异步作业 | 1 项 |
| **L6: 性能基准** | `tests/performance/` | 检索 Hit@8 ≥ 0.80、MRR ≥ 0.70、引用真实度 1.0、单合同审核 P50 延迟 < 500ms 基准防劣化 | 2 项 |
| **基础业务测试** | `tests/` 顶层及微盘探针组 | 模板解析、五级分类、条款抽取、格式差异、双库合并、微盘权限诊断 | 220 项 |

## 核心架构视图与目录组织

```text
ai-contract-review/
├── contract_kb/
│   ├── domain/                  # 核心领域层（纯净零外部依赖）
│   │   ├── models/              # CanonicalContract 标准合同聚合根、ReviewResult、Finding
│   │   ├── value_objects/       # ContractNumber、Decimal 精确金额、ExecutionDeadline/CancelToken
│   │   └── ports/               # 领域仓储端口契约
│   ├── application/             # 应用编排层
│   │   ├── pipeline/            # 14 阶段可组合审查流水线（ReviewPipeline / ReviewStage）
│   │   ├── agents/              # 法务审查 Agent、SSE 队列会话管理、知识库治理适配器
│   │   ├── workflow/            # 受控审查 Agent 8 状态机（ControlledReviewAgentWorkflow / Budget）
│   │   ├── security/            # 幂等键与安全门禁服务
│   │   └── ports/               # Embedding / Reranker / LLM 抽象端口
│   ├── modules/                 # 领域业务模块层（Bounded Contexts）
│   │   ├── document_processing/ # DOCX/PDF/DOC 解析、条款提取、五级业务分类、受控导入
│   │   ├── contract_comparison/ # 逐条款 Diff、全文双栏 Diff、表格对齐、排版格式审核
│   │   ├── retrieval/           # RAG 知识来源分级治理、RRF 融合、重排、引用核验
│   │   ├── rules/               # 策略红线守卫、规则引擎、冲突消解、带审计人工覆写
│   │   ├── templates/           # 模板知识蒸馏、五级分类路由规则、标准素材目录
│   │   ├── cases/               # 历史案卷交叉对账纯函数、SHA-256 安全缓存
│   │   └── integrations/        # 泛微 e9、新系统 tender、MinIO、企微微盘只读防腐适配器
│   ├── infrastructure/          # 基础设施适配层
│   │   ├── persistence/         # SQLite / PostgreSQL+pgvector 双引擎存储与 UnitOfWork
│   │   ├── ai/                  # Embedding / Reranker / LLM 具体 Provider 实现
│   │   ├── checkpoints/         # Agent 断点续传与防篡改快照存储
│   │   ├── task_queue/          # 轻量持久化异步执行器（PersistentTaskRunner）
│   │   └── observability/       # 脱敏日志与指标收集器（MetricsCollector）
│   ├── interfaces/              # 外部交互接口层
│   │   ├── http/                # FastAPI OpenAPI v1 模式、HMAC-V2 鉴权、中间件与错误信封
│   │   ├── web/                 # 合同比对与 AI 批注可视化工作台（Server / Routes / Services / 静态资源）
│   │   ├── desktop/             # Tkinter 桌面自动化运维控制台（MainWindow / ProcessRunner / Commands）
│   │   └── cli/                 # 命令行入口（import / refresh / inventory）
│   └── evaluation/              # 离线评测与基准度量层（生产代码零反向依赖）
│       ├── datasets/            # 黄金查询集、合成模板语料与变异样本生成
│       ├── retrieval/           # RAG 检索基准评测
│       ├── review/              # 端到端审核质量与延迟评估
│       └── benchmarks/          # 多路检索与重排消融实验
├── tools/                       # 工程辅助与运维脚本（按职责分包 + 根目录保留 8 个薄兼容入口）
│   ├── diagnostics/             # 数据库结构与微盘诊断工具
│   ├── templates/               # 模板规则分析与下载工具
│   ├── integrations/            # 企微/微盘同步 CLI (wecom/) 与新系统同步工具 (tender/)
│   ├── data_ops/                # 历史案卷合并、修复与全量复验工具
│   ├── reports/                 # 知识图谱 HTML、Excel 映射表与报告生成工具
│   └── maintenance/             # 单合同命令行微观比对工具
├── scripts/                     # 架构静态审计与自动化工程脚本
└── tests/                       # 自动化测试套件（unit / integration / contract / architecture / security / e2e / performance）
```

## 关键配置（授权后启用）

| 环境变量 | 说明 |
|---|---|
| `CONTRACT_KB_LLM` | `none`（默认，语义审核标 NOT_CHECKED）/ `mock`（测试）/ `openai_compat`（授权端点，缺配置时 **fail-closed 报错**） |
| `CONTRACT_KB_LLM_BASE_URL` / `_API_KEY` / `_MODEL` | 授权 LLM 端点三元组（未授权禁止外发合同内容） |
| `CONTRACT_KB_EMBEDDING` | `local_hash`（默认离线）/ `st:<model>`（SentenceTransformer 真实语义向量）/ `cloud:<provider>`（需授权） |
| `CONTRACT_KB_STORAGE_BACKEND` | `sqlite`（默认本地）/ `postgres`（生产关系库与向量库） |
| `CONTRACT_KB_PG_DSN` | 指向真实 PostgreSQL + pgvector 测试库或生产库 |
| `CONTRACT_KB_ALLOW_EXTERNAL_LLM` | 外部大模型外发开关（默认 `false`，防数据违规出境） |
| `CONTRACT_KB_ENABLE_JOB_WORKER` | 是否在 API 进程内自启异步任务执行器后台线程池（默认 `0`） |

## 关键架构决策与迁移治理文档

- [目标架构设计与领域边界](docs/architecture/target-architecture.md)
- [14 阶段审核流水线设计契约](docs/architecture/review-pipeline.md)
- [双库数据架构与工作单元](docs/architecture/data-architecture.md)
- [RAG 来源分级与受控 Agent 架构](docs/architecture/rag-agent-architecture.md)
- [安全威胁模型与治理清单](docs/architecture/security-threat-model.md)
- [渐进式迁移、兼容期治理与高危操作授权方案](docs/architecture/migration-plan.md)
- [全景架构重构验收报告](docs/architecture/architecture-acceptance.md)
- 架构决策记录（ADRs）：
  - [ADR-001：模块化单体架构决策](docs/architecture/architecture-decisions/ADR-001-modular-monolith.md)
  - [ADR-002：双数据库持久化与工作单元](docs/architecture/architecture-decisions/ADR-002-persistence-and-uow.md)
  - [ADR-003：受控审查 Agent 有限状态机](docs/architecture/architecture-decisions/ADR-003-controlled-agent-state-machine.md)
  - [ADR-004：基于关系库持久化的轻量异步执行器](docs/architecture/architecture-decisions/ADR-004-lightweight-async-task-runner.md)
