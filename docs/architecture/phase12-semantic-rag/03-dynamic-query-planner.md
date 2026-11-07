# Phase 12-B: 动态 Query Planner 设计与防爆炸技术规范

- **状态**: IMPLEMENTED & TESTED
- **核心组件**: [`contract_kb/modules/retrieval/query_planner.py`](file:///d:/Users/Administrator/Desktop/AI合同/contract_kb/modules/retrieval/query_planner.py)
- **测试用例**: [`tests/unit/test_dynamic_query_planner.py`](file:///d:/Users/Administrator/Desktop/AI合同/tests/unit/test_dynamic_query_planner.py)

---

## 1. 架构动机与问题定义
在 Phase 12 之前，`RetrieveEvidenceStage` 始终机械遍历 4 个硬编码关键词（`费用及支付方式`、`违约责任`、`争议解决`、`保密`）。这种做法完全忽视了待审合同本身的真实法律约定，如：
- 待审条款：“甲方逾期付款超过30日，乙方有权单方解除合同，但应提前5日书面通知甲方。”
- 固定关键词检索无法提取“30日逾期”、“单方解除权限制”与“5日通知期”等关键风险要素。

## 2. 两级查询规划设计 (Two-Level Planning)

### Level 1: 确定性 Query Planning（零外部依赖，100% 规则驱动）
- **主体识别（`legal_subject`）**：精准区分“甲方”、“乙方”、“第三方”、“双方”。
- **行为抽取（`action`）**：单方解除、交付成果、支付款项、开具发票、保密义务、争议仲裁等。
- **条件与例外（`conditions` / `exceptions`）**：提取触发前提与免责例外。
- **强约束提取（`numeric_constraints`）**：
  - 金额：`type="MONEY", unit="YUAN"`
  - 工期：`type="WORKDAYS", unit="WORKDAY"`
  - 自然日：`type="CALENDAR_DAYS", unit="DAY"`
  - 比率：`type="PERCENTAGE"` / `type="RATE"`
- **跨条款引用（`referenced_clauses`）**：识别“依照第十二条”等跨条引用，自动追加 `CROSS_CLAUSE_REFERENCE_CHECK` 专用意图。

### Level 2: LLM-Assisted Query Expansion（受控拓展，严格事实锚定）
- 必须满足两道门禁：`CONTRACT_KB_ALLOW_EXTERNAL_LLM == True` 且提供了经过认证的 LLM Provider。
- 输出必须严格通过 Pydantic `RetrievalIntent` 校验。
- 拒绝任何凭空捏造未在原文出现的金额、天数与法条。
- 模型不可用或响应超时时，自动平滑降级为 Level 1。

## 3. 防爆炸预算控制策略（Anti-Explosion Budgeting）

为防止单份合同膨胀出成百上千次网络检索，`QueryPlanningBudget` 实施了严格的资源门限控制：

| 约束项 | 默认参数值 | 作用与机制 |
|---|---|---|
| **单合同最大意图数 (`max_total_queries`)** | `16` | 严格截断意图总数，防止向量数据库请求雪崩 |
| **单条款最大意图数 (`max_intents_per_clause`)** | `2` | 限制单个条款拆解粒度 |
| **条款风险过滤 (`priority_cutoff`)** | `4` | 优先按风险（违约/解除/争议=P1, 费用/IP=P2）处理，自动忽略 P5 样板 |
| **意图签名去重 (`enable_deduplication`)** | `True` | 基于 `(intent_type, lexical_queries, legal_subject)` 签名合并相同证据请求 |
| **单查询超时上限 (`per_query_timeout_seconds`)** | `3.0s` | 避免慢查询拖垮整个流水线 |

## 4. 性能与可扩展性测试结果
在 `tests/unit/test_dynamic_query_planner.py` 中分别对 10、50、100、300 条款的合成合同进行了压力测试：
- **300 条款大合同**：意图规划执行时间仅 **8.5 ms**（远低于 200 ms SLA 基准线）；
- **最终意图生成总数**：恒定被预算控制在 **≤ 16**，零网络/算力爆炸。
