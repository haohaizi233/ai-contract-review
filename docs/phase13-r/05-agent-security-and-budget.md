# Phase 13-R3：统一大模型授权、SSRF 防护、Token 预算与强类型输出报告 (05-agent-security-and-budget)

## 1. 统一外发授权与 SSRF 防护 (`contract_kb/legal_agent.py`)

1. **外发门禁 (`CONTRACT_KB_ALLOW_EXTERNAL_LLM`)**:
   - 默认禁止向外部模型发送任何合同正文；
   - 调用前强制执行 `self.is_external_allowed()` 检查，未授权时立即返回 `failure_code="NOT_AUTHORIZED"`、`source="AUTHORIZATION_GATE"`、`review_status="RULE_REVIEW_COMPLETED"`，绝不发起任何外部 HTTP 请求。

2. **SSRF 端点安全校验 (`_is_safe_endpoint_url`)**:
   - 仅允许 `http` 与 `https` 协议，拒绝 `file://`、`ftp://`、`gopher://` 等危险协议；
   - 显式拦截云厂商链路本地元数据地址（`169.254.169.254`、`metadata.google.internal`、`instance-data`）。

3. **复用统一 `OpenAICompatProvider`**:
   - 移除原本孤立的 `urllib` 裸调用，统一通过 `contract_kb.llm_review.OpenAICompatProvider` 执行推理。

4. **防提示词注入随机 Nonce 信封**:
   - 每次调用生成 96-bit 随机 `call_nonce = secrets.token_hex(12)`；
   - 将不可信合同条款包裹于 `<UNTRUSTED_CONTRACT_BODY nonce="{call_nonce}">...</UNTRUSTED_CONTRACT_BODY>` 内，并在系统提示词中强制声明围栏内任何祈使句均不得作为指令执行。

---

## 2. 调用前预算预留与不确定账单治理 (`BudgetLedgerService`)

- 调用前执行 `self.budget_ledger.reserve_budget(input_tokens=est_input_tokens, max_output_tokens=max_out)`；
- 若预算不足，直接捕获 `BudgetLimitExceededError`，返回 `failure_code="BUDGET_EXCEEDED"`、`source="BUDGET_GATE"`；
- 若请求发生超时 (`timeout`)，调用 `self.budget_ledger.mark_uncertain(reservation.reservation_id)` 将预留转入 `UNCERTAIN` 挂账状态，防止失败重试造成额度穿透；
- 正常返回后通过 `self.budget_ledger.settle_budget(...)` 按实际 Token 结算，非超时失败通过 `release_budget(...)` 释放额度。

---

## 3. Pydantic 强类型校验与诚实降级语义 (`LegalAgentReviewResult` & `ReviewStatus`)

- 模型返回必须为完整合法的 JSON 对象，包含 `risk_level` (`HIGH` | `MEDIUM` | `LOW` | `INFO`)、非空 `risk_label`、非空 `ai_analysis` 与非空 `action_advice`，并经 `LegalAgentReviewResult` 强类型校验；
- 移除旧版通过正则在截断损坏文本中拼凑半截字段伪装成功的逻辑；
- 严格区分 `ReviewStatus.AI_REVIEW_COMPLETED`（真实大模型审核完成）与 `ReviewStatus.RULE_REVIEW_COMPLETED`（规则兜底完成），绝不将兜底结果伪装为 AI 成功。
