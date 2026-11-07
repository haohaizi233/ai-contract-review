# -*- coding: utf-8 -*-
"""知识源分类、信任边界与生命周期治理策略（Knowledge Governance Policy）。

明确区分四类企业知识资产的权威等级与检索准入条件：
1. `TEMPLATE`：经法务审定的企业现行标准合同模板条款（`ACTIVE` + `APPROVED`，权威依据）；
2. `APPROVED_RULE`：经审批生效的确定性/语义审核规则（`APPROVED`，权威依据）；
3. `HISTORICAL_CASE`：历史已签合同与审批案卷（**仅供参考先例**，严禁作为强制违规判定标准）；
4. `LEGAL_REFERENCE`：外部法律法规与实务指引参考（辅助解释参考，不单独构成企业内控红线）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class KnowledgeSourceType(str, Enum):
    TEMPLATE = "TEMPLATE"
    APPROVED_RULE = "APPROVED_RULE"
    HISTORICAL_CASE = "HISTORICAL_CASE"
    LEGAL_REFERENCE = "LEGAL_REFERENCE"


class KnowledgeLifecycleStatus(str, Enum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"
    DELETED = "DELETED"


@dataclass(frozen=True)
class KnowledgeTrustDescriptor:
    source_type: KnowledgeSourceType
    authority_rank: int  # 数值越小权威度越高
    can_drive_mandatory_finding: bool
    require_approved_status: bool
    prompt_badge: str


TRUST_MATRIX: dict[KnowledgeSourceType, KnowledgeTrustDescriptor] = {
    KnowledgeSourceType.APPROVED_RULE: KnowledgeTrustDescriptor(
        source_type=KnowledgeSourceType.APPROVED_RULE,
        authority_rank=1,
        can_drive_mandatory_finding=True,
        require_approved_status=True,
        prompt_badge="[企业已审批强制规则-权威依据]",
    ),
    KnowledgeSourceType.TEMPLATE: KnowledgeTrustDescriptor(
        source_type=KnowledgeSourceType.TEMPLATE,
        authority_rank=2,
        can_drive_mandatory_finding=True,
        require_approved_status=True,
        prompt_badge="[企业现行标准模板条款-权威基准]",
    ),
    KnowledgeSourceType.LEGAL_REFERENCE: KnowledgeTrustDescriptor(
        source_type=KnowledgeSourceType.LEGAL_REFERENCE,
        authority_rank=3,
        can_drive_mandatory_finding=False,
        require_approved_status=True,
        prompt_badge="[外部法规参考-辅助说明]",
    ),
    KnowledgeSourceType.HISTORICAL_CASE: KnowledgeTrustDescriptor(
        source_type=KnowledgeSourceType.HISTORICAL_CASE,
        authority_rank=4,
        can_drive_mandatory_finding=False,
        require_approved_status=False,
        prompt_badge="[历史合同先例-仅供参考，严禁作为强制违规标准]",
    ),
}


class KnowledgeGovernancePolicy:
    """检索结果准入过滤与权威等级标注器。"""

    BLOCKED_DOC_STATUSES = frozenset(
        {
            KnowledgeLifecycleStatus.DRAFT.value,
            KnowledgeLifecycleStatus.SUPERSEDED.value,
            KnowledgeLifecycleStatus.ARCHIVED.value,
            KnowledgeLifecycleStatus.DELETED.value,
        }
    )

    @classmethod
    def normalize_source_type(cls, raw: str | None) -> KnowledgeSourceType:
        if not raw:
            return KnowledgeSourceType.TEMPLATE
        up = raw.strip().upper()
        alias_map = {
            "TEMPLATE": KnowledgeSourceType.TEMPLATE,
            "RULE": KnowledgeSourceType.APPROVED_RULE,
            "APPROVED_RULE": KnowledgeSourceType.APPROVED_RULE,
            "CASE": KnowledgeSourceType.HISTORICAL_CASE,
            "HISTORICAL_CASE": KnowledgeSourceType.HISTORICAL_CASE,
            "HISTORY": KnowledgeSourceType.HISTORICAL_CASE,
            "LEGAL": KnowledgeSourceType.LEGAL_REFERENCE,
            "LEGAL_REFERENCE": KnowledgeSourceType.LEGAL_REFERENCE,
        }
        return alias_map.get(up, KnowledgeSourceType.TEMPLATE)

    @classmethod
    def descriptor_for(cls, raw_type: str | None) -> KnowledgeTrustDescriptor:
        stype = cls.normalize_source_type(raw_type)
        return TRUST_MATRIX[stype]

    @classmethod
    def is_chunk_admissible(
        cls,
        hit: dict[str, Any],
        *,
        allow_historical_cases: bool = False,
        expected_tenant_id: str | None = None,
    ) -> bool:
        """校验检索分块是否满足状态、审批与租户边界要求。"""
        doc_status = str(hit.get("doc_status") or hit.get("status") or "ACTIVE").upper()
        if doc_status in cls.BLOCKED_DOC_STATUSES:
            return False

        if expected_tenant_id is not None and hit.get("tenant_id") is not None:
            if hit.get("tenant_id") != expected_tenant_id:
                return False

        desc = cls.descriptor_for(hit.get("knowledge_type"))
        if (
            desc.source_type == KnowledgeSourceType.HISTORICAL_CASE
            and not allow_historical_cases
        ):
            return False

        if desc.source_type == KnowledgeSourceType.APPROVED_RULE:
            approved_status = str(hit.get("approved_status") or "APPROVED").upper()
            if approved_status != "APPROVED":
                return False
        elif desc.require_approved_status:
            approved_status = str(hit.get("approved_status") or "APPROVED").upper()
            if approved_status in {"DRAFT", "REJECTED", "REVOKED"}:
                return False

        return True

    @classmethod
    def annotate_hit(cls, hit: dict[str, Any]) -> dict[str, Any]:
        """为通过治理校验的检索结果注入权威等级与提示词标识。"""
        desc = cls.descriptor_for(hit.get("knowledge_type"))
        enriched = dict(hit)
        enriched["knowledge_type"] = desc.source_type.value
        enriched["authority_rank"] = desc.authority_rank
        enriched["can_drive_mandatory_finding"] = desc.can_drive_mandatory_finding
        enriched["prompt_badge"] = desc.prompt_badge
        return enriched
