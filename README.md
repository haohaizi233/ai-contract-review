# contract-kb — 企业知识产权合同模板知识库与审核引擎

基于 26 份真实 DOCX 合同模板构建的可追溯知识库 + 混合检索 RAG + 确定性规则审核引擎。

> 验收状态以 `reports/contract-kb/final-acceptance.md` 为准（严格区分：已实现 / 已通过 Mock /
> 已通过真实模型 / 已通过 PostgreSQL / 未验证）。

## 快速启动

```powershell
# 1. 依赖（Python ≥ 3.11）
pip install python-docx lxml pydantic numpy fastapi uvicorn python-multipart pytest httpx

# 2. 导入 26 份模板（解析→分类→条款/Schema 抽取）
python -m contract_kb.cli import

# 3. 生成知识蒸馏产物（taxonomy / 条款差异矩阵 / 字段 Schema / 规则候选）
python -m contract_kb.distill

# 4. 运行测试（200 项：解析/分类/条款/规则/检索/审核/租户隔离/LLM 链路/Embedding/格式标准/安全/API）
python -m pytest tests -q

# 5. 运行评估（RAG Recall@K / MRR / 引用正确性 + 审核命中率/误报/漏报）
python -m contract_kb.evaluate

# 6. 启动 API 服务
uvicorn contract_kb.api:app --host 127.0.0.1 --port 8000
```

模板源目录默认为工作区 `参考/`（可用环境变量 `CONTRACT_KB_TEMPLATE_DIR` 覆盖），全部路径相对项目根，无写死绝对路径。

## 关键配置（授权后启用）

| 环境变量 | 说明 |
|---|---|
| `CONTRACT_KB_LLM` | `none`（默认，语义审核标 NOT_CHECKED）/ `mock`（测试）/ `openai_compat`（授权端点，缺配置时 **fail-closed 报错**） |
| `CONTRACT_KB_LLM_BASE_URL` / `_API_KEY` / `_MODEL` | 授权 LLM 端点三元组（未授权禁止外发合同内容） |
| `CONTRACT_KB_EMBEDDING` | `local_hash`（默认离线）/ `st:<model>`（SentenceTransformer 真实语义向量）/ `cloud:<provider>`（需授权） |
| `CONTRACT_KB_TEST_PG_DSN` | 指向真实 PostgreSQL + pgvector 测试库时启用 PG 行为测试 |
| `CONTRACT_KB_REAL_CORPUS=1` | 受控环境真实语料回归（默认 skip，公开环境严禁启用） |
| `CONTRACT_KB_REAL_LLM_BASE_URL` / `_API_KEY` / `_MODEL` | 受控真实模型 E2E（`@pytest.mark.real_model`） |

更换 Embedding 模型/维度后必须 `storage.rebuild_index()`（索引版本化留痕；直接混用会
`EmbeddingConfigMismatch` fail-closed）。公司格式标准经 `format_standards` 表配置/审批/版本化，
**未审批的示例标准只作差异提示，不产生正式企业违规结论**（API：`/api/contract-kb/format-standards`）。

## 目录结构

```
contract_kb/            核心包
  config.py             配置（路径/限额/Embedding/LLM 开关/租户）
  docx_parser.py        OOXML 有序 AST 解析器（表格/样式/来源定位/特殊结构记录）
  classifier.py         分层分类（L1/L2 + 证据 + 冲突标记）
  clause_extractor.py   条款抽取/归类/归一化摘要/实体条件例外抽取
  ingest.py             安全校验 + 导入管线（失败不静默丢弃）
  distill.py            知识蒸馏（taxonomy/差异矩阵/字段 Schema/规则候选）
  storage.py            四层知识存储（SQLite+FTS5+向量）+ 混合检索 + 规则/任务/反馈
  embeddings.py         Embedding 抽象（本地哈希默认 / 云需显式授权）
  review_engine.py      审核链路（解析→分类→模板匹配→字段→规则→RAG→证据校验）
  golden.py             合成变异合同样本生成器（14 样本带预期标签）
  evaluate.py           RAG 与审核评估（真实测量）
  api.py                FastAPI 服务（导入/检索/比较/规则/审核任务/反馈）
tests/                  pytest 测试组（解析/分类/条款/Schema/规则/检索/审核/安全/API）
migrations/             pgvector 生产迁移脚本（本环境无 PG，未验证）
data/contract-kb/       导入结果 / AST / 知识层 / SQLite 库 / 评估 JSON
reports/contract-kb/    交付报告（11 份）
```

## 知识库分层

| 层 | 内容 | 存储 |
|---|---|---|
| Layer A 原始模板 | 文档哈希/AST 节点/条款原文/来源定位 | `data/contract-kb/ast/` + `layer_a_raw.json` + documents/nodes/clauses 表 |
| Layer B 结构化业务 | 字段 Schema/合同类型/实体/费用结构 | `field_schema.json` + `taxonomy.json` + fields 表 |
| Layer C 企业规则 | 规则候选（DRAFT）→ 审批后生效 | `rule_candidates.json` + rules 表（含审批工作流） |
| Layer D 语义 RAG | 可检索条款文本/摘要 | embeddings 表（FTS5 + 向量 + RRF 融合 + rerank） |

AI 生成的归一化摘要均带 `summary_method` 标记（deterministic_rule_v1），与原文分层保存，不冒充原始事实。

## 数据库说明（如实标注）

- 本地/测试：**SQLite**（FTS5 全文 + numpy 向量），全部测试真实执行；
- 生产：**PostgreSQL + pgvector**，迁移脚本 `migrations/001_init_pgvector.sql` + `002_embedding_meta_task_tenant_format_std.sql`；
  行为（迁移/导入/检索/回滚/维度校验/租户）**已在真实 PostgreSQL + pgvector 实例上验证**
  （本地 PG 18 + pgvector 0.8.7；CI `test-pg` job 使用 pgvector/pgvector:pg16 服务容器）；
  向量检索为 **SQL 相似度排序 + LIMIT**（`ORDER BY vec <=> %s::vector LIMIT n`），不全量载入内存；
- 同一 `Storage` 接口可替换 PgVector 实现；Embedding 维度/提供方变更必须 `rebuild_index()`（版本化）。

## 回滚说明

| 场景 | 操作 |
|---|---|
| 导入错文件 | `storage.delete_document(document_id)`（级联删条款/索引 + 写审计） |
| 索引损坏/换 Embedding | `storage.rebuild_index()` 全量重建（幂等） |
| 模板更新回退 | 文档按 `source_hash` 版本化保留，`storage.rollback_to_version(doc_id, old_hash)` 重新激活旧版 |
| 数据库整体回滚 | 删除 `data/contract-kb/contract_kb.db` 后重跑 `cli import` + `distill`（导入幂等） |
| 规则回滚 | 规则按 (rule_id, version) 存储；新版本建新 version，旧版本保留可回指 |

## 安全与合规要点

- 未配置的云 Embedding/LLM 一律拒绝外发合同内容（显式配置 + 授权后方可启用）；
- 上传校验扩展名/魔数/大小/ZIP 条目数/压缩比/路径穿越（均有测试）；
- 解析仅读 OOXML 文本层，不执行宏/外部链接；合同正文作为数据处理，防提示词注入；
- 规则候选全部 DRAFT，未经审批不得作为企业强制规则；人工反馈不自动改规则（需审批+版本）；
- 金额校验一律 Decimal。

## 测试与验收

`reports/contract-kb/final-acceptance.md` 为本轮验收报告：验证状态矩阵（已实现 / 已通过 Mock /
已通过真实模型 / 已通过 PostgreSQL / 未验证）、逐项修复对照、测试结果、差异文件与剩余阻塞问题。
LLM 受控 E2E 的脱敏运行证据见 `reports/contract-kb/llm-e2e-evidence.json`（不含合同原文/密钥/完整端点）。

CI（`.github/workflows/ci.yml`）：`lint-and-type`（Ruff lint/format + mypy，无 `|| true`）、
`security-gate`（敏感扫描 **fail-closed**：配置缺失/工具失败即阻断）、`test-synthetic`（全量 pytest + 迁移 + 基准）、
`test-pg`（真实 PostgreSQL + pgvector 服务容器集成测试）、`real-corpus-regression`
（`workflow_dispatch` + self-hosted + environment: controlled，日志脱敏）。
