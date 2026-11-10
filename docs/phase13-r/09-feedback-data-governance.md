# Phase 13-R7: 人工复核状态机与反馈数据治理报告 (`09-feedback-data-governance.md`)

## 1. 7 态人工复核状态机 (`HumanReviewStatus`)

在 `contract_kb/domain/models/review_identity.py` 中定义了严格的 7 态状态机：
1. `PENDING_AI_REVIEW`（待 AI 审查）
2. `AI_REVIEW_COMPLETED`（大模型审查完成）
3. `RULE_FALLBACK_COMPLETED`（确定性规则兜底完成）
4. `HUMAN_ACCEPTED`（法务人工采纳）
5. `HUMAN_MODIFIED`（法务人工修改批注）
6. `HUMAN_REJECTED`（法务人工驳回 AI 建议）
7. `ESCALATED_TO_COUNSEL`（提级至资深法务/总法务裁决）

通过 `HumanReviewStatus.can_transition_to(target)` 强制约束状态流转：
- 未完成 AI 或规则初审（仍处于 `PENDING_AI_REVIEW`）的条款，仅允许直接提级（`ESCALATED_TO_COUNSEL`），禁止直接标记为 `HUMAN_ACCEPTED`。
- Web 工作台新增 `/api/diff/human_decision` 接口，支持会话级人工裁决持久化与 SSE 实时同步。

## 2. 敏感信息脱敏 (`sanitize_sensitive_data`)

在 `contract_kb/legal_agent_kb.py` 中实现 `sanitize_sensitive_data(text)`，在任何人工反馈落盘前自动脱敏：
- 18 位中国居民身份证号（`ID_CARD_RE`） -> `[REDACTED_ID_CARD]`
- 16–19 位银行卡/对公账号（`BANK_CARD_RE`） -> `[REDACTED_BANK_CARD]`
- 11 位中国大陆手机号（`PHONE_RE`） -> `[REDACTED_PHONE]`
- 电子邮箱（`EMAIL_RE`） -> `[REDACTED_EMAIL]`
- 企业主体名称（`COMPANY_RE`） -> `[REDACTED_COMPANY]`

## 3. 四区物理隔离与防评估集污染 (`FeedbackGovernanceStore`)

在 `contract_kb/legal_agent_kb.py` 中实现 4 目录物理隔离存储：
- `raw_feedback/`：原始脱敏后的人工反馈记录（默认 `approved_for_training=False`）。
- `reviewed_feedback/`：经知识管理员复核后的反馈记录。
- `approved_training_samples/`：获批用于 Few-Shot / 微调的训练集样本。
- `evaluation_holdout/`：黄金评测保留集样本。
- **防泄漏硬约束**：`approve_feedback(..., split="train" | "eval")` 在写入前计算 `sample_hash` 并检查对侧目录；若样本已存在于 `evaluation_holdout`，则拒绝进入 `approved_training_samples`（反之亦然），抛出 `ValueError` 防止评测集污染（Data Contamination）。

## 4. 验证测试证据

- `tests/contract/test_feedback_governance.py`（3 项测试全部通过）：验证状态机合法/非法转移、PII 脱敏无残留、以及训练集与评测集互斥防污染。
