# Phase 11-R: 原文引文真实性核验与证据链加固报告

**文档编号**：04-CITATION-CHAIN-HARDENING  
**状态**：COMPLETED (VERIFIED)  
**涉及模块**：
- `contract_kb/modules/retrieval/citation.py`
- `tests/security/test_phase11_citation_integrity_strict.py`

---

## 一、 缺陷与真实性核验加固

### 1. P0-03: CitationVerifier 默认放行与证据哈希脱钩整改
- **根因分析**：
  原 `CitationVerifier.verify_retrieval_hits` 在获取文档状态与知识类型时，存在以下默认放行：
  ```python
  doc_status = str(row_dict.get("doc_status") or "ACTIVE").upper()
  ktype = str(row_dict.get("knowledge_type") or h.get("knowledge_type") or "TEMPLATE").upper()
  ```
  缺失对 `tenant_id` 严格校验，缺失对 `document_id` 强锚定，未校验 `source_hash` 防篡改哈希，未核验知识版本（`source_version`），且在未指定租户时易导致跨租户数据穿透。
- **加固策略（Strict Fail-Closed）**：
  1. **强类型证据核验结果契约**：
     ```python
     class EvidenceVerificationResult(BaseModel):
         verified: bool
         status: str
         tenant_id: str
         document_id: str
         clause_id: str
         document_version: str | None = None
         source_hash: str | None = None
         content_hash: str | None = None
         approval_record_id: str | None = None
         failure_reasons: list[str] = Field(default_factory=list)
     ```
  2. **Fail-Closed 校验维度**：
     - **租户隔离**：引文自身缺少 `tenant_id` 或引文声明租户与当前审核限定租户不一致，直接判定 `MISSING_TENANT` / `TENANT_MISMATCH`。
     - **条款存在性**：查验条款是否存在于该租户数据库，不存在直接返回 `MISSING_SOURCE`。
     - **文档一致性**：校验 `document_id` 与库内条款所属文档是否一致，防止引文张冠李戴（`DOCUMENT_MISMATCH`）。
     - **生命周期有效性**：必须明确且严格等于 `ACTIVE`，严禁使用任何缺省放行（`SOURCE_INACTIVE`）。
     - **防篡改来源哈希**：校验 `source_hash` 是否与数据库计算的原始文件哈希严格一致，发生篡改判定为 `HASH_MISMATCH`。
     - **版本一致性**：校验 `document_version` / `source_version` 是否一致，防止使用过时旧版本条款（`VERSION_MISMATCH`）。
     - **审批效力**：条款审批状态为 `REVOKED` 时判定为 `APPROVAL_REVOKED`；权威标准（`TEMPLATE` / `APPROVED_RULE`）状态非 `APPROVED` 判定为 `APPROVAL_MISSING`。
     - **原文精准核对**：引文摘录必须与数据库原文通过去空白规整比对精准匹配，不匹配判定为 `TEXT_MISMATCH`。

---

## 二、 自动化验证矩阵

| 测试用例名称 | 测试目标 | 实测结果 |
|:---|:---|:---|
| `test_citation_verifier_returns_typed_evidence_result` | 验证核验通过时返回强类型契约与完整元数据 | PASS |
| `test_citation_hash_mismatch_fails_closed` | 验证来源哈希不一致严格判定为 `HASH_MISMATCH` | PASS |
| `test_citation_revoked_source_fails_closed` | 验证法务撤销审批后证据核验判定为 `APPROVAL_REVOKED` | PASS |
| `test_citation_missing_fields_fails_closed` | 验证缺少租户、跨租户越权或文档不匹配时严格拒绝 | PASS |

---

## 三、 结论

CitationVerifier 证据核验器已全面升级为零信任、Fail-Closed 的强类型核验体系，彻底杜绝了默认字段放行和来源哈希与版本脱钩的安全漏洞。
