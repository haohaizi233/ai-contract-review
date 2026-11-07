# Phase 11-R: 流水线 Stage 9/10/11 信任边界重构与真库核验报告

**文档编号**：05-STAGE-TRUST-BOUNDARIES  
**状态**：COMPLETED (VERIFIED)  
**涉及模块**：
- `contract_kb/application/pipeline/stages.py`
- `contract_kb/domain/models/review.py`
- `tests/security/test_phase11_stage_evidence_fail_closed.py`

---

## 一、 缺陷与整改分析

### 1. P0-04: 流水线阶段构造证据时伪造可信属性与缺乏真库核验
- **根因分析**：
  在原 `stages.py` 中：
  - Stage 9 (`RetrieveEvidenceStage`)：多处使用 `h.get("approved_status") or "APPROVED"`、`h.get("knowledge_type") or "TEMPLATE"`，在构造流水线上下文时自动补齐可信状态；
  - Stage 10 (`LLMSemanticReviewStage`)：在构造比对差异发现时同样使用 `top_ref.get("approved_status") or "APPROVED"`；
  - Stage 11 (`VerifyEvidenceStage`)：针对 `knowledge_references` 仅检查内存字典字段是否存在字符串 `"APPROVED"`，未回查存储底座，导致如果知识库条款在审核期间被法务紧急撤销（TOCTOU 漏洞），审查仍然会错误地给出强制性违规判定。
- **加固策略**：
  1. **清除默认属性伪造**：Stage 9 与 Stage 10 全面移除 `or "APPROVED"`、`or "TEMPLATE"` 与 `or ctx.scope_tenant`，缺失属性原样保留为空，严禁无中生有。
  2. **Stage 11 独立数据库权威回查**：
     - 在 `VerifyEvidenceStage` 中集成 `CitationVerifier`，若流水线上下文注入了 `storage_backend` 或阶段实例绑定了 `storage`，直接调用 `verify_evidence_item` 对 `knowledge_references` 中的每一项进行真实数据库回查。
     - **TOCTOU 撤销防御**：若回查发现条款状态已变为 `REVOKED`，该 Finding 立即判定为 `INSUFFICIENT_EVIDENCE`，标记 `requires_human_review = True`，并在 `description` 中明确追加 `（证据校验：引用的标准依据已被法务撤销失效，已降级为证据不足）`。
     - **非强制依据降级**：若引用来源为法律参考资料（`LEGAL_REFERENCE`）或历史案例（`HISTORICAL_CASE`），严禁驱动 `HIGH` 或 `CRITICAL` 的强制违规判定，自动降级为 `LOW` 提示人工复核。

---

## 二、 自动化验证矩阵

| 测试用例名称 | 测试目标 | 实测结果 |
|:---|:---|:---|
| `test_stage9_does_not_forge_approved_status` | 验证 Stage 9 构建 rag_context 绝不伪造 `APPROVED` 状态 | PASS |
| `test_stage11_toctou_revocation_downgrades_mandatory_finding` | 验证 TOCTOU 场景下被撤销的标准依据自动触发降级与人工复核要求 | PASS |

---

## 三、 结论

流水线 Stage 9/10/11 的零信任边界已完全确立，各阶段完全通过真实数据库回查核验引文状态，彻底消除了伪造可信凭证与并发撤销穿透的风险。
