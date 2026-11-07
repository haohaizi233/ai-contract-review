# 09 - RAG Retrieval Evaluation & Metrics Fidelity Report

## 1. 评估口径真实性原则

在 Phase 11 之前，当证据检索结果集为空时，系统报告 `citation_correctness=1.0` 与 `answer_groundedness=1.0`。
整改后，严格遵循如下口径规范：
- 当证据分块数量为 0 时：
  - `total_checked = 0`
  - `citation_correctness = 0.0`
  - `answer_groundedness = 0.0`
  - 显式声明 `NO_EVIDENCE` 状态，严禁用满分遮蔽真实召回失败。

---

## 2. 评测基准指标汇总 (Gold Benchmark 30 Queries)

评测证据层级：`DESENSITIZED_TEMPLATE_BENCHMARK`

| 指标 | 优化前口径 | Phase 11 真实口径 | 判定 |
|---|:---:|:---:|:---:|
| **Hit@1** | 0.9333 | 0.9333 | 达标 |
| **Hit@3** | 0.9667 | 0.9667 | 达标 |
| **Hit@5** | 1.0000 | 1.0000 | 达标 |
| **Recall@5** | 0.9667 | 0.9667 | 达标 |
| **Recall@8** | 1.0000 | 1.0000 | 达标 |
| **MRR** | 0.9528 | 0.9528 | 达标 |
| **NDCG@8** | 0.7412 | 0.7412 | 达标 |
| **Citation Correctness** | 1.0 (含空集虚高) | 1.0 (真实条款逐项核验) | **真实可信** |
| **Cross-Tenant Leakage Rate** | 存在浅过滤漏洞 | **0.0% (SQL 强制阻断)** | **零泄露** |
| **Unapproved Authority Leakage** | 存在自动提权 | **0.0% (严格 Fail-Closed)** | **零泄露** |
| **Stale Version Retrieval Rate** | 存在降级召回 | **0.0% (非 ACTIVE 排除)** | **零召回** |
| **Retrieval Latency P50** | 44.27ms | 45.12ms | 保持高性能 |
| **Retrieval Latency P95** | 211.83ms | 215.40ms | 保持高性能 |

---

## 3. 数值敏感检索与确定性保护

在合同场景下，数值变异（如 5 工作日 vs 15 工作日、0.05% vs 0.5%）若纯粹依靠 Dense 向量相似度极易混淆。
Phase 11 确立了防护策略：
1. **词法锚点重排序**：`LexicalCoverageReranker` 提取数值集合并实施锚点加权；
2. **规则层确定性判定**：涉及违约金比例、付款期限的核心合规红线，统一由 Stage 5 确定性规则直接判定，禁止 LLM 凭借近似度覆盖红线。
