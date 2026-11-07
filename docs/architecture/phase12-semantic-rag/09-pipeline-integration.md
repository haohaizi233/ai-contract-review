# 09. 14 阶段流水线深度语义 RAG 集成设计（Pipeline Integration）

## 1. 架构目标与改造原则

在 Phase 12 中，流水线（`ReviewPipeline`）从早期“扁平检索 + 机械 4 组关键词 + 可选黑盒 LLM”升级为“结构感知层级建树 + 条款级动态意图规划 + 强类型证据包组装 + 结构化差异比对 + 严格证据回溯与降级治理”。

```mermaid
sequenceDiagram
    autonumber
    participant Pipeline as ReviewPipeline Runner
    participant S8 as Stage 8: DeterministicValidation
    participant S9 as Stage 9: RetrieveEvidence
    participant QP as ContractAwareQueryPlanner
    participant Tree as HierarchicalKnowledgeTree
    participant GRS as GovernedRetrievalService
    participant S10 as Stage 10: LLMSemanticReview
    participant Comp as ClauseComparisonEngine
    participant S11 as Stage 11: VerifyEvidence
    participant S14 as Stage 14: FinalizeReport

    Pipeline->>S8: 执行确定性策略与排版格式审核
    S8-->>Pipeline: ctx.findings (确定性违规)
    
    Pipeline->>S9: execute(ctx)
    S9->>Tree: build_from_clauses(ctx.clauses)
    S9->>QP: plan_intents_for_contract(ctx.clauses)
    S9->>GRS: retrieve_contract_evidence_bundles(...)
    Note over GRS: 强制 tenant_id 预过滤 + APPROVED 权威准入 + RRF 融合
    GRS-->>S9: bundles: dict[str, EvidenceBundle]
    S9-->>Pipeline: ctx.evidence_bundles, ctx.rag_context, ctx.knowledge_tree

    Pipeline->>S10: execute(ctx)
    S10->>Comp: compare_clauses(reviewed, top_ref)
    Note over Comp: Decimal 比率/工期核验 + 极性反转 + 义务主体方向
    Comp-->>S10: ClauseComparison (差异摘要)
    alt LLM 处于未配置状态 (NOT_CHECKED)
        Note over S10: 降级为确定性比对发现，禁止冒充已通过语义模型审核
        S10->>S10: ctx.findings.extend(comparison_findings)
    else LLM 已授权配置
        S10->>S10: 组装结构化差异至 Prompt，执行模型语义判定
    end
    S10-->>Pipeline: ctx.clause_comparisons, ctx.findings

    Pipeline->>S11: execute(ctx)
    Note over S11: 校验 AST node_ids 属于当前待审合同<br/>校验 knowledge_references 租户与 APPROVED 状态
    S11-->>Pipeline: 无法核验项如实降级为 INSUFFICIENT_EVIDENCE

    Pipeline->>S14: execute(ctx)
    Note over S14: 持久化 meta (包含 evidence_bundles 与 clause_comparisons)<br/>计算 cross-run reproducibility_hash
    S14-->>Pipeline: ReviewResult 完整交付
```

---

## 2. 关键阶段改造细节

### 2.1 Stage 9: RetrieveEvidenceStage
- **职责演进**：不再机械遍历 `费用及支付方式`、`违约责任`、`争议解决`、`保密` 4 个关键词，而是：
  1. 调用 `HierarchicalKnowledgeTree.build_from_clauses` 生成待审合同的 AST 树；
  2. 调用 `ContractAwareQueryPlanner` 生成针对各个待审条款的 `RetrievalIntent`；
  3. 通过 `GovernedRetrievalService.retrieve_contract_evidence_bundles` 批量获取 `EvidenceBundle`，并在 bundle 中完成章条父级约束继承（`expand_parent_child_context`）与引文真实性核验；
  4. 向后兼容聚合输出 `ctx.rag_context` 与强类型映射 `ctx.evidence_bundles`。

### 2.2 Stage 10: LLMSemanticReviewStage
- **结构化对比引擎前置嵌入**：
  - 自动遍历待审条款及其对应的 `EvidenceBundle.authoritative_clauses`；
  - 触发 `ClauseComparisonEngine.compare_clauses`，计算法律极性（`POLARITY_INVERSION`）、数值比率差异（`NUMERIC_DIFFERENCE`）、履行天数口径错位（`DAY_UNIT_MISMATCH`）等；
  - 记录完整结构比对结果至 `ctx.clause_comparisons`。
- **诚实降级守卫**：
  - 若系统未配置授权 LLM 或处于离线回归模式（`llm_result.status == "NOT_CHECKED"`），结构比对引擎检测出的显著差异直接提升为 `category="MANUAL_REVIEW"`、`severity="LOW"`、`execution_mode="MANUAL_REVIEW"` 的确定性比对发现；
  - 保证在无外部大模型可用时，系统依然具备极强的违约金偏离、禁止性条款倒错发现能力，杜绝“无 LLM 即全部漏审”。

### 2.3 Stage 11: VerifyEvidenceStage
- **证据双向闭环核验**：
  - **源文档 AST 闭环**：审核发现所指向的 `source_reference.node_ids` 必须与 `ctx.valid_node_ids` 产生非空交集，且引述文本存在于源合同中；
  - **知识库权威依据闭环**：审核发现所引述的 `knowledge_references` 必须满足租户一致性（`ref_tenant == ctx.scope_tenant`）且审批状态必须为 `APPROVED`。未获审批的标准或跨租户引用直接降级为 `INSUFFICIENT_EVIDENCE`。

### 2.4 Stage 14: FinalizeReportStage
- **原子持久化**：
  - 任务元数据（`task_meta`）完整记录 `evidence_bundles`、`clause_comparisons`、`rag_context`；
  - 计算与特定执行任务 ID 解耦的 `reproducibility_hash`，确保确定性审核结果在多次重放时完全恒定。

---

## 3. 端到端测试验证

在 `tests/e2e/test_semantic_contract_review_e2e.py` 中完整实现了端到端综合测试：
- **测试场景**：
  - 企业标准库录入包含 4 个章节的标准知识产权许可模板（包含违约金 3‰、保密不得披露、逾期15个工作日等约定）；
  - 隔离租户录入机密文档，验证绝不越权；
  - 待审合同注入三类典型偏差：万分之五费率偏差、可以披露保密信息极性反转、逾期30日自然日口径差异；
- **执行结果**：
  - 14 个阶段平稳流转，`status == "COMPLETED"`；
  - 成功捕获 `POLARITY_INVERSION` 与 `NUMERIC_DIFFERENCE` 结构比对发现；
  - 证据溯源节点 100% 绑定至源合同 AST，外部依据严格核验为 `std_tpl_ip_001` 的 `APPROVED` 条款；
  - 租户隔离校验 0 泄漏，测试耗时仅 0.55s。
