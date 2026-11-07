# 合同审核评估报告（contract-review-evaluation）

- 测试样本量：14（合成变异合同）
- 全标签命中样本：14
- 微平均 Precision：1.0，Recall：1.0，F1：1.0
- TP=24，FP=0，FN=0

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

## 逐样本结果（逐标签 TP/FN）

| 样本 | 预期标签 | 逐标签结果 | 样本结果 | 耗时(s) |
|---|---|---|---|---|
| mut_01_missing_contract_no | ['CONTRACT_NUMBER_BACKFILL', 'FIELD_REQUIRED'] | FIELD_REQUIRED=TP; CONTRACT_NUMBER_BACKFILL=TP | PASS | 0.68 |
| mut_02_party_name_change | ['PARTY_ATTACHMENT_CONSISTENCY', 'PARTY_MISMATCH'] | PARTY_ATTACHMENT_CONSISTENCY=TP; PARTY_MISMATCH=TP | PASS | 0.664 |
| mut_03_service_fee_change | ['AMOUNT_MISMATCH'] | AMOUNT_MISMATCH=TP | PASS | 0.622 |
| mut_04_official_fee_change | ['AMOUNT_MISMATCH'] | AMOUNT_MISMATCH=TP | PASS | 0.621 |
| mut_05_payment_deadline | ['CLAUSE_VARIATION', 'PAYMENT_DEADLINE'] | CLAUSE_VARIATION=TP; PAYMENT_DEADLINE=TP | PASS | 0.626 |
| mut_06_missing_confidentiality | ['CLAUSE_MISSING', 'CLAUSE_REQUIRED_CONFIDENTIALITY'] | CLAUSE_MISSING=TP; CLAUSE_REQUIRED_CONFIDENTIALITY=TP | PASS | 0.619 |
| mut_07_dispute_change | ['CLAUSE_VARIATION', 'DISPUTE_JURISDICTION_STANDARD'] | CLAUSE_VARIATION=TP; DISPUTE_JURISDICTION_STANDARD=TP | PASS | 0.604 |
| mut_08_amount_case_mismatch | ['AMOUNT_CASE_CONSISTENCY', 'AMOUNT_MISMATCH'] | AMOUNT_MISMATCH=TP; AMOUNT_CASE_CONSISTENCY=TP | PASS | 0.605 |
| mut_09_patent_no_change | ['FIELD_FORMAT', 'IDENTIFIER_FORMAT_CHECK'] | FIELD_FORMAT=TP; IDENTIFIER_FORMAT_CHECK=TP | PASS | 0.673 |
| mut_10_contradictory_clause | ['MANUAL_REVIEW', 'SEMANTIC_RISK'] | SEMANTIC_RISK=TP; MANUAL_REVIEW=TP | PASS | 0.689 |
| mut_11_late_fee_change | ['CLAUSE_VARIATION', 'LATE_FEE_RATE'] | CLAUSE_VARIATION=TP; LATE_FEE_RATE=TP | PASS | 0.659 |
| mut_12_missing_liability | ['CLAUSE_MISSING', 'CLAUSE_REQUIRED_LIABILITY'] | CLAUSE_MISSING=TP; CLAUSE_REQUIRED_LIABILITY=TP | PASS | 0.602 |
| mut_13_invoice_variant | ['INVOICE_TERM_VARIANT', 'INVOICE_TYPE_CONSISTENCY'] | INVOICE_TERM_VARIANT=TP; INVOICE_TYPE_CONSISTENCY=TP | PASS | 0.633 |
| mut_14_no_issue_baseline | 无问题 | - | PASS | 0.641 |

## 误报/漏报分析

- 无错误基准样本未报出任何 HIGH/MEDIUM 问题 → 本轮零误报。
- 无漏报。

## 说明

- 确定性规则覆盖：金额一致性、必备条款、管辖条款、发票条款、违约金、付款期限、标识格式、主体一致性、矛盾启发式；
- 语义级错误在无 LLM 环境下标记 NOT_CHECKED/NEEDS_MANUAL_REVIEW，不伪报；
- 所有检出问题均带 source_reference（node_ids + 原文），可在原文档定位。