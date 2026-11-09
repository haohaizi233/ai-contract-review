# 合同审核评估报告（contract-review-evaluation）

- **评测证据层级（Evaluation Tier）**：`DESENSITIZED_TEMPLATE_BENCHMARK`
- 测试样本量：14，全标签命中样本：14
- 微平均 Precision：1.0，Recall：1.0，F1：1.0
- 误报率（FPR）：0.0，漏报率（FNR）：0.0
- 原文依据度（AnswerGroundedness）：1.0
- 审核延迟：Mean=11668.81ms, P50=11771.52ms, P95=12119.23ms, P99=12172.46ms
- Token 与单份成本：Tokens=0, CostPerContract=$0.000000

## 逐标签指标

| 标签 | TP | FP | FN | Precision | Recall |
|---|---|---|---|---|---|
| AMOUNT_CASE_CONSISTENCY | 1 | 0 | 0 | 1.0 | 1.0 |
| AMOUNT_MISMATCH | 3 | 0 | 0 | 1.0 | 1.0 |
| CLAUSE_MISSING | 2 | 0 | 0 | 1.0 | 1.0 |
| CLAUSE_REQUIRED_CONFIDENTIALITY | 1 | 0 | 0 | 1.0 | 1.0 |
| CLAUSE_REQUIRED_LIABILITY | 1 | 0 | 0 | 1.0 | 1.0 |
| CLAUSE_VARIATION | 3 | 0 | 0 | 1.0 | 1.0 |
| CONTRACT_NUMBER_BACKFILL | 1 | 0 | 0 | 1.0 | 1.0 |
| DISPUTE_JURISDICTION_STANDARD | 1 | 0 | 0 | 1.0 | 1.0 |
| FIELD_FORMAT | 1 | 0 | 0 | 1.0 | 1.0 |
| FIELD_REQUIRED | 1 | 0 | 0 | 1.0 | 1.0 |
| IDENTIFIER_FORMAT_CHECK | 1 | 0 | 0 | 1.0 | 1.0 |
| INVOICE_TERM_VARIANT | 1 | 0 | 0 | 1.0 | 1.0 |
| INVOICE_TYPE_CONSISTENCY | 1 | 0 | 0 | 1.0 | 1.0 |
| LATE_FEE_RATE | 1 | 0 | 0 | 1.0 | 1.0 |
| MANUAL_REVIEW | 1 | 0 | 0 | 1.0 | 1.0 |
| PARTY_ATTACHMENT_CONSISTENCY | 1 | 0 | 0 | 1.0 | 1.0 |
| PARTY_MISMATCH | 1 | 0 | 0 | 1.0 | 1.0 |
| PAYMENT_DEADLINE | 1 | 0 | 0 | 1.0 | 1.0 |
| SEMANTIC_RISK | 1 | 0 | 0 | 1.0 | 1.0 |