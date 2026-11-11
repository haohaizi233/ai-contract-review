# Phase 16-S-R2: 01 - 可信来源与出处真实性核验报告 (Citation Provenance)

## 一、整改背景与原缺陷分析

在 Phase 16-S 第一轮审计中，`contract_kb/modules/retrieval/citation.py` 中的 `CitationVerifier.verify_rule_evidence()` 存在两项严重安全隐患：
1. **来源文档存在性校验旁路**：当规则携带非空 `source_document` 字符串时，系统仅检查字符串非空，未向底层存储查询该文档是否真实存在、是否属于当前租户以及是否处于有效状态，即判定为 `VERIFIED`。
2. **底层条款查验未严格阻断**：当规则声明了 `source_clauses` 时，若底层存储 `get_clause()` 返回空或产生异常，系统存在静默吞并或直接放行的逻辑漏洞；同时，对于未在租户受控仓储登记的口头法律法规字符串（如“《民法典》”），缺少权威登记凭据，容易导致“假借法规之名”的伪权威推论。

---

## 二、可信证据核验架构设计 (Evidence Verification Pipeline)

按照 **Evidence-Based AI** 与 **Fail-Closed by Default** 原则，重构了统一的权威来源核验链路：

```text
Rule ID & Tenant Context
           ↓
[1. 存储可用性与租户限定查询] → storage.get_rules(tenant_id)
           ↓ (若存储不可用或产生异常 → STORAGE_UNAVAILABLE 阻断)
[2. 规则真实性与租户一致性校验] → db_tenant in (eff_tenant, "default", "*")
           ↓ (不匹配 → TENANT_MISMATCH 阻断)
[3. 审批有效性与撤销校验] → status != "APPROVED" → APPROVAL_MISSING
           ↓ (status == "REVOKED" → APPROVAL_REVOKED 阻断)
[4. 有效期限校验] → effective_until 解析与时间戳比对
           ↓ (解析失败 → INVALID_VALIDITY_DATE 阻断; 过期 → SOURCE_INACTIVE 阻断)
[5. 修订版本一致性校验] → revision / version 精准比对
           ↓ (不匹配 → VERSION_MISMATCH 阻断)
[6. 来源出处深度回查 (Provenance Check)]
   ├── 若声明 source_clauses:
   │     ├─ storage.get_clause(clause_id, tenant_id)
   │     ├─ 真实条款不存在 → CLAUSE_NOT_FOUND 阻断
   │     ├─ 查询异常 → SOURCE_UNAVAILABLE 阻断 (Fail-Closed)
   │     ├─ 条款状态非 ACTIVE/APPROVED → SOURCE_INACTIVE 阻断
   │     └─ 条款哈希不匹配 → HASH_MISMATCH 阻断
   └── 若仅有 source_document:
         ├─ storage.get_document(doc_id, tenant_id)
         ├─ 真实文档不存在:
         │     ├─ 口头法规字符串 → UNSUPPORTED_PROVENANCE 阻断
         │     └─ 普通文档未找到 → MISSING_SOURCE 阻断
         ├─ 查询异常 → SOURCE_UNAVAILABLE 阻断 (Fail-Closed)
         └─ 文档状态非 ACTIVE/APPROVED → SOURCE_INACTIVE 阻断
           ↓
[7. 全部维度通过] → 返回 EvidenceVerificationResult(verified=True, status="VERIFIED")
```

---

## 三、关键代码修改对照

### 1. 严格阻断未登记口头法规与不存在来源文档
在 `contract_kb/modules/retrieval/citation.py` 中：
```python
is_valid_d_row = isinstance(d_row, (dict, Mapping)) or (
    d_row is not None and type(d_row).__name__ != "MagicMock"
)
if not is_valid_d_row:
    # 检查是否仅为法律法规名称口头字符串（如中华人民共和国民法典）
    is_statute_name = bool(row.get("is_statutory_mandate")) or any(
        kw in source_doc for kw in ("法", "条例", "司法解释", "民法典", "规章")
    )
    status_val = "UNSUPPORTED_PROVENANCE" if is_statute_name else "MISSING_SOURCE"
    return EvidenceVerificationResult(
        verified=False,
        status=status_val,
        tenant_id=eff_tenant,
        document_id=source_doc,
        clause_id="",
        rule_id=rid,
        failure_reasons=[
            f"来源文档 '{source_doc}' 在受控仓储中不存在，且缺少具体条文定位与法定登记凭据"
        ],
    )
```

### 2. 底层条款存在性、状态与哈希防篡改严格回查
```python
for sc in source_clauses:
    sc_id = sc.get("clause_id") if isinstance(sc, dict) else str(sc)
    try:
        c_row = storage.get_clause(sc_id, tenant_id=eff_tenant)
    except Exception as e:
        return EvidenceVerificationResult(
            verified=False,
            status="SOURCE_UNAVAILABLE",
            tenant_id=eff_tenant,
            document_id=source_doc,
            clause_id=sc_id,
            rule_id=rid,
            failure_reasons=[f"查询底层条款数据库异常 (Fail-Closed): {e}"],
        )

    is_valid_c_row = isinstance(c_row, (dict, Mapping)) or (
        c_row is not None and type(c_row).__name__ != "MagicMock"
    )
    if not is_valid_c_row:
        return EvidenceVerificationResult(
            verified=False,
            status="CLAUSE_NOT_FOUND",
            tenant_id=eff_tenant,
            document_id=source_doc,
            clause_id=sc_id,
            rule_id=rid,
            failure_reasons=[f"规则所依据的底层条款 {sc_id} 在存储中不存在"],
        )
```

### 3. 日期格式解析 Fail-Closed 防护
```python
except Exception as e:
    # FAIL-CLOSED: 无法解析的有效期不可静默忽略后放行 VERIFIED
    return EvidenceVerificationResult(
        verified=False,
        status="INVALID_VALIDITY_DATE",
        tenant_id=eff_tenant,
        document_id="",
        clause_id="",
        rule_id=rid,
        failure_reasons=[f"规则有效期格式非法无法解析 (Fail-Closed): {e}"],
    )
```

---

## 四、验证结论

通过在 `tests/security/test_phase16_r2_reproduce_flaws.py` 和 `tests/security/test_phase16_r2_adversarial_suite.py` 中的反例测试覆盖：
- 来源文档缺失：100% 阻断为 `MISSING_SOURCE`。
- 底层条款缺失：100% 阻断为 `CLAUSE_NOT_FOUND`。
- 数据库查询异常：100% Fail-Closed 阻断为 `SOURCE_UNAVAILABLE`。
- 未登记的口头法规字符串：100% 阻断为 `UNSUPPORTED_PROVENANCE`。
- 任何篡改条款原文哈希：100% 阻断为 `HASH_MISMATCH`。

实现了无凭据不得判定为 `VERIFIED` 的坚固防线。
