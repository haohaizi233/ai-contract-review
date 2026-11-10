# Phase 13-R4: 可信 RAG 与知识版本治理统一报告 (`06-trusted-rag-integration.md`)

## 1. 现状问题与风险分析 (P1-A)

前次实现中，`contract_kb/legal_agent_kb.py` 中的本地知识规则字典 `LEGAL_KNOWLEDGE_BASE` 仅为平铺字符串字典，未包含版本标识、审批状态（`approval_status`）、生效时间及引用核验。这导致：
1. 若某条历史规则已被撤销（`REVOKED`）或处于草稿待审（`PENDING_REVIEW`）状态，法务 Agent 仍会无差别检索并引用。
2. Agent 生成的 `citation_ids` 与 `evidence_verification_status` 缺乏与可信规则库 `rule_id@version` 的严格核验机制。

## 2. 统一治理模型 (`GovernedLegalRule`)

在 `contract_kb/legal_agent_kb.py` 中实现受治理规则实体 `GovernedLegalRule`：
- `rule_id`: 唯一规则标识（如 `RULE-PAY-001`）
- `clause_type`: 适用条款类型
- `version`: 语义化版本号（如 `v2.1`）
- `approval_status`: `"APPROVED" | "REVOKED" | "PENDING_REVIEW" | "EXPIRED"`
- `tenant_id`: 租户隔离标识
- `effective_from`: 生效日期
- `statutory_basis`: 法律法规依据（《民法典》等）
- `enterprise_redline`: 企业内控红线
- `negotiation_bottom_line`: 谈判妥协底线

## 3. 检索过滤与引用核验 (Fail-Closed)

1. **`retrieve_governed_rules(clause_type, tenant_id, require_approved=True)`**：
   - 默认强制过滤 `approval_status != "APPROVED"` 的规则。
   - 被标记为 `REVOKED`（如历史旧规则 `RULE-PAY-OLD-000`）的规则在检索阶段即被拦截，绝不进入 Prompt 上下文或兜底规则匹配。
2. **Evidence & Citation Verification**：
   - `LegalAgentReviewEngine.review_clause` 在构造 `LegalAgentReviewResult` 时，收集本次命中的已审批规则 `citation_ids`（格式为 `rule_id@version`）。
   - 若命中至少一条 `APPROVED` 规则且通过校验，标记 `evidence_verification_status = "VERIFIED"`；若无已审批规则支撑，标记为 `"UNVERIFIED"`。

## 4. 验证测试证据

- 测试文件：`tests/integration/test_legal_agent_trusted_rag.py`
  - `test_case_c_revoked_rule_not_cited_approved_rule_verified`：验证 `RULE-PAY-OLD-000` (`REVOKED`) 被严格剔除，仅 `APPROVED` 规则进入上下文与 `citation_ids`，且 `evidence_verification_status == "VERIFIED"`。
