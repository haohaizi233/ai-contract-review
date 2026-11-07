# Phase 11-R + Phase 12-R: 检索调用预算控制与端到端调用追踪报告

**报告版本**：1.0.0-REACCEPTANCE  
**日期**：2026-11-07  
**责任角色**：Principal Software Architect & Performance Engineer  
**对应缺陷**：P1-02 (动态检索预算失控与重复规划), P1-03 (LLM 扩展虚假能力声明)  

---

## 一、 端到端真实检索调用预算 (P1-02)

### 1. 缺陷背景
原检索系统虽然在规划器中定义了 `QueryPlanningBudget`，但在进入检索执行阶段（`retrieve_contract_evidence_bundles`）后存在重大缺陷：
1. **重复意图规划**：先调用 `plan_intents_for_contract` 选出待审条款，随后在 `retrieve_for_clause` 中再次从头计算确定性意图，单份合同造成两倍规划开销；
2. **缺乏运行时熔断**：未对底层存储搜索 (`storage.search()`)、向量生成和重排调用设定端到端预算配额与硬性截止时间，遇到恶意或大型合同时容易耗尽系统资源；
3. **无调用追踪元数据**：调用方无法审计实际消耗的检索开销。

### 2. 治理与重构实现
1. **真实调用追踪与熔断实体 (`RetrievalExecutionBudget`)**：
   - `max_total_intents`: 最大允许执行意图总数（默认 24）；
   - `max_search_calls`: 最大允许底层存储查询次数（默认 60）；
   - `deadline_seconds`: 超时截止保护（默认 10.0 秒）；
   - `actual_search_calls`: 真实记录底层通道实际触发次数；
   - `is_budget_exhausted`: 预算耗尽时自动熔断，阻断后续搜索调用。
2. **消除重复规划（意图复用）**：
   在合同级批量规划完成后，将 `prioritized_intents` 按 `source_clause_id` 聚类建立索引映射字典，直接传入 `retrieve_for_clause(pre_planned_intents=...)`，杜绝重复计算。
3. **注入强类型调用指标**：
   每个 `EvidenceBundle` 均记录其归属的 `execution_budget` 执行快照，透明公开 `actual_search_calls` 与耗时。

---

## 二、 LLM Query Expansion 真实支持与显式标记 (P1-03)

### 1. 缺陷背景
原 `expand_intents_with_llm` 仅包含空桩代码，恒定返回 `base_intents`，并在外部声称支持 Level 2 LLM 辅助拓展，存在虚假能力声明风险。

### 2. 治理策略
践行**“有即诚实支持，无即显式声明”**原则：
- 在 `RetrievalIntent` 模型中显式引入 `llm_expansion_status: str | None` 字段；
- 若未显式开启 `CONTRACT_KB_ALLOW_EXTERNAL_LLM` 或未注入 `llm_provider`，明确将状态标记为：
  `DISABLED_NO_AUTHORIZED_LLM`
  严禁静默假装已执行；
- 当环境获合法授权并提供 LLM 提供者时，发起结构化意图拆解，成功生成增补意图（标记为 `generation_method="llm_assisted_v1"` 与 `llm_expansion_status="SUCCESS"`）；若 LLM 响应不合规，如实标记为 `FALLBACK_INVALID_LLM_OUTPUT`。
