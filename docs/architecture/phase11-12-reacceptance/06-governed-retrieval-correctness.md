# Phase 11-R + Phase 12-R: 受控检索正确性与多通道召回治理报告

**报告版本**：1.0.0-REACCEPTANCE  
**日期**：2026-11-07  
**责任角色**：RAG/Information Retrieval Engineer & QA Auditor  
**对应缺陷**：P1-01 (权威知识分类混淆), P1-04 (多路召回通道混淆), P1-05 (困难语义与数值比对脆弱)  

---

## 一、 权威知识分类隔离机制 (P1-01)

### 1. 缺陷背景
在整改前，`retrieve_authoritative` 仅以 `approved_only=True` 进行筛选。由于《民法典》法条参考 (`LEGAL_REFERENCE`) 也具备合法审批流，导致外部辅助参考资料泄露进入“权威企业强制标准集合”，破坏了审核基准的权威唯一性。

### 2. 治理与修复方案
在 `KnowledgeGovernancePolicy.is_chunk_admissible` 中引入两道严格的 Fail-Closed 门禁：
1. **`authoritative_only: bool = True` 门禁**：核验分块元数据的 `can_drive_mandatory_finding` 必须为 `True`（仅对 `TEMPLATE` 与 `APPROVED_RULE` 开放）；
2. **`target_knowledge_types` 严格白名单**：在意图级别强制要求仅允许 `{"TEMPLATE", "APPROVED_RULE"}` 候选，彻底将 `LEGAL_REFERENCE`（法律参考）与 `HISTORICAL_CASE`（历史先例）阻隔在权威违规判别上下文之外。

---

## 二、 独立多路召回通道物理分离 (P1-04)

### 1. 缺陷背景
原检索系统在执行意图检索时，每个通道（词法 Channel A、语义 Channel B、编号 Channel D）均调用通用的 `storage.search()`，而该方法内部隐式执行了 FTS 与向量的混合与 RRF 融合，导致通道职责重叠、重复执行向量推理并造成大量冗余计算。

### 2. 架构重构
在 SQLite (`Storage`) 与 PostgreSQL (`PgVectorStorage`) 底层分别实现了真正解耦的单路检索接口：
- `search_lexical_candidates(...)`：仅走 FTS5 / PG FTS 词法全文索引，不触发向量计算；
- `search_dense_candidates(...)`：仅走语义向量索引，不触发词法搜索；
- `search_structured_candidates(...)`：仅走条款编号与元数据精确索引。

在 `GovernedRetrievalService.retrieve_for_intent` 中，分别向对应通道发起单路召回，并在顶层通过 Reciprocal Rank Fusion (RRF, $k=60$) 执行纯净的多路结果融合。

---

## 三、 困难法律语义与结构化比对强化 (P1-05)

### 1. 法律义务主体倒错检出
针对保密协议与知识产权归属中的非对称单向义务倒错，新增 `check_asymmetric_party_reversal` 状态机：
- 识别“甲方披露/乙方保密”与“乙方披露/甲方保密”的对立关系；
- 识别“知识产权归甲方”与“知识产权归乙方”的主体倒错；
- 检出时标记为 `SUBJECT_DIFFERENCE`，生成不可忽略的人工复核告警。

### 2. 但书例外条件不对称检出
识别“除非经事先书面同意，否则不得...”等带前提例外的条款与“在任何情况下均不得...”绝对禁止条款之间的不对称性，产生 `EXCEPTION_DIFFERENCE` 差异并标记 `requires_human_review=True`。

### 3. 高精度数值比对
引入金额绝对值的 Decimal 解析比对（如 10,000 元 vs 100,000 元），与现有的万分比/千分比（0.05% vs 0.5%）、工作日 vs 自然日统一构成六层结构化防线。
