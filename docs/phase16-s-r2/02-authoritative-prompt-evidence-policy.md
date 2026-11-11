# Phase 16-S-R2: 02 - 权威提示词依据与降级策略报告 (Authoritative Prompt Policy)

## 一、整改背景与原缺陷分析

在第一轮整改中，系统在构建 LLM 提示词上下文时存在安全降级与越权风险：
1. **APPROVAL 状态直接冒充 VERIFIED**：在调用大模型前，仅凭知识库中规则的 `status == "APPROVED"` 属性，就统一打上 `[企业已审批风控基准·优先适用]` 权威标签，直接塞入系统提示词。这导致即使来源已失效或出处未查证，大模型仍将其作为企业最高意志优先遵循。
2. **缺乏推理后双向验证 (Post-Inference Reverse Check)**：大模型可能在输出中引用未经验证的规则或虚构法定强制条款，而原系统未对模型输出进行反向引用合规检查，直接将结论写入高信度缓存。

---

## 二、双轨制证据分级体系与提示词构建

为彻底杜绝伪权威凭据进入模型推理，建立了双轨制证据分级模型：

```text
检索到的规则 / 条款
       ↓
CitationVerifier 执行全维度深度核验
       ├── 核验通过 (status == "VERIFIED") 
       │     ↓
       │  放入 【企业已审批生效风控基准】 (Verified Legal Evidence)
       │  标注: [企业已审批风控基准·优先适用]
       │  赋予: 权威法律约束力与最高优先级
       │
       └── 核验未通过 (如 MISSING_SOURCE, APPROVAL_MISSING 等)
             ↓
          放入 【非权威参考资料】 (Unverified Reference Context)
          标注: [非权威参考·不可作为确证依据]
          声明: 仅供背景比对，严禁作为确定性法律风控结论的基础依据
```

### 数据结构保障：
在 `contract_kb/application/agents/knowledge_adapter.py` 中定义专门证据容器：
```python
@dataclass
class VerifiedLegalEvidence:
    rule: GovernedLegalRule
    verification_result: EvidenceVerificationResult
    is_authoritative: bool = False

@dataclass
class LegalEvidenceBundle:
    authoritative_rules: list[VerifiedLegalEvidence] = field(default_factory=list)
    unverified_references: list[GovernedLegalRule] = field(default_factory=list)
    verification_map: dict[str, EvidenceVerificationResult] = field(default_factory=dict)
```

---

## 三、推理后反向证据边界检查 (Post-Inference Reverse Evidence Boundary)

在 `contract_kb/application/agents/legal_review_agent.py` 中，增加了模型推理完成后的逆向安全审查网关：

```python
# 逆向审查：模型是否引用了未经验证的规则或虚构法定强制性
has_unverified_rule_citation = False
for ref in evidence_refs:
    if ref.get("verification_status") != "VERIFIED":
        rid = ref.get("rule_id") or ""
        rname = ref.get("rule_name") or ""
        if (rid and rid in ai_text) or (rname and rname in ai_text):
            has_unverified_rule_citation = True
            break

if has_unverified_rule_citation:
    status = ReviewStatus.NEEDS_HUMAN_REVIEW
    ai_text = (
        "【安全防线拦截警报】模型推导结论引用了未经验证或来源缺失的企业规则，"
        "已自动降级为需要人工法务复核。\n" + ai_text
    )
    # 阻断旧缓存写入
    skip_cache_write = True
```

---

## 四、验证结论

通过 `test_unverified_rule_not_used_as_authoritative_prompt_context` 与对抗用例测试验证：
1. 未通过 `CitationVerifier` 的规则绝不出现在 `[企业已审批风控基准·优先适用]` 区块。
2. 只有 `VERIFIED` 规则才能进入权威上下文。
3. 一旦模型违规引用未验证规则，系统即时将审核状态置为 `NEEDS_HUMAN_REVIEW`，并禁止写入缓存，彻底消除了越权与幻觉扩散风险。
