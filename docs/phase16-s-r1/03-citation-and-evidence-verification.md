# Phase 16-S-R1: 03 - 证据与引用真实核验体系（禁止 APPROVED 冒充 VERIFIED）

## 一、缺陷根本原因诊断 (P0-R1-02)

上一轮 Phase 16-S 实现中，存在如下语义与安全缺陷：

```python
"verification_status": (
    "VERIFIED"
    if rule.approval_status == "APPROVED"
    else "UNVERIFIED"
)
```

**根因分析**：
“规则处于 APPROVED 审批状态”仅代表该规则在管理流程上具有准入资格，根本不等于“该条批注引用的依据原文真实无误、出处哈希一致且来源条款有效”。
直接把 `APPROVED` 映射为 `VERIFIED`，绕过了真正的证据链核验（Citation Verification），导致无出处的空头规则、跨租户规则、超出有效期的规则或出处条款已被废止的规则，均能伪装成最高置信度的 `VERIFIED` 凭证输出给企业法务。

---

## 二、真实引用与证据核验链路设计

本次整改在 `contract_kb/modules/retrieval/citation.py` 中实现了深度的规则与引文核验引擎 `CitationVerifier.verify_rule_evidence()`：

```text
规则引用 (GovernedLegalRule / Rule Item)
                    ↓
[第1道防线] 租户归属与越权防范 (Tenant Isolation)
    → 仅允许当前租户专有规则或公共法定基准 (default/*)
    → 跨其他租户直接拦截：TENANT_MISMATCH
                    ↓
[第2道防线] 审批状态真实性 (Approval Lifecycle)
    → 数据库权威状态必须为 APPROVED
    → REVOKED 立即失效：APPROVAL_REVOKED
    → DRAFT / PENDING 阻断：APPROVAL_MISSING
                    ↓
[第3道防线] 有效期与截止窗口核验 (Temporal Validity)
    → 支持 float 时间戳与 ISO-8601 标准日期解析
    → 超过截止期判定失效：SOURCE_INACTIVE
                    ↓
[第4道防线] 规则版本与修订比对 (Revision Matching)
    → 严格核对引用 revision 与数据库权威 version
    → 版本漂移直接拦截：VERSION_MISMATCH
                    ↓
[第5道防线] 来源出处与母本条款穿透核验 (Provenance Verification)
    → 必须具备非空 source_document 或 source_clauses
    → 缺失来源元数据：INSUFFICIENT_PROVENANCE
    → 若关联底层标准条款，穿透核查条款 ACTIVE/APPROVED 状态
                    ↓
             核验全部通过：VERIFIED
```

### 完整核验状态码定义

| 状态码 | 含义 | 安全处置策略 |
|---|---|---|
| `VERIFIED` | 权威出处完备、租户匹配、审批有效且条款在有效期内 | 允许作为高置信度法务批注凭证 |
| `INSUFFICIENT_PROVENANCE` | 虽通过审批，但无出处引文或来源条款列表为空 | 降级处理，严禁显示为认证依据 |
| `APPROVAL_REVOKED` | 依据规则已被法务管理员撤销作废 | 阻断并转人工复核 |
| `APPROVAL_MISSING` | 依据规则仅处于草稿或待审状态 | 拒绝采信 |
| `SOURCE_INACTIVE` | 规则超出有效截止期或源条款已失效 | 提示过时并降级 |
| `VERSION_MISMATCH` | 依据规则版本号与数据库当前版本不符 | 触发规则失效告警 |
| `TENANT_MISMATCH` | 试图引用其他企业租户的私有风控规则 | 严格安全阻断 |
| `STORAGE_UNAVAILABLE` | 数据库不可达或未注入受信任知识库 | Fail-Closed 降级 |

---

## 三、代码改动定位

1. **`contract_kb/modules/retrieval/citation.py`**：
   - 增加 `verify_rule_evidence` 核心核验函数；
   - 适配 `verify_evidence_item` 自动路由至规则核验；
   - 完善 ISO 日期与修订版本格式兼容性。
2. **`contract_kb/application/agents/legal_review_agent.py`**：
   - `review_clause()` 内部将每一条召回的 `governed_rules` 送入 `CitationVerifier().verify_rule_evidence()` 执行真实证据核验；
   - `build_high_precision_fallback()` 同样接入 `verify_rule_evidence`，彻底杜绝无证据直接标记 `VERIFIED` 的行为。

---

## 四、安全测试验证

在 `tests/security/test_phase16_r1_citation_verification.py` 中覆盖全部异常分支：
- `test_verify_rule_evidence_tenant_isolation`: 跨租户调用报 `TENANT_MISMATCH`。
- `test_verify_rule_evidence_revoked_or_draft_status`: 废止规则报 `APPROVAL_REVOKED`。
- `test_verify_rule_evidence_expired_date`: 过期规则报 `SOURCE_INACTIVE`。
- `test_verify_rule_evidence_insufficient_provenance`: 缺来源报 `INSUFFICIENT_PROVENANCE`。
- `test_verify_rule_evidence_genuine_approved_with_provenance`: 出处完备报 `VERIFIED`。
- `test_legal_review_agent_fallback_marks_insufficient_provenance`: Agent 降级严格按出处打标。
