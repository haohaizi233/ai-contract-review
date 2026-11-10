# -*- coding: utf-8 -*-
"""知识源分类、信任边界与生命周期治理策略（Knowledge Governance Policy）。

明确区分五类独立维度的企业知识资产状态：
1. Document Lifecycle（文档生命周期）：DRAFT, ACTIVE, SUPERSEDED, ARCHIVED, DELETED
2. Approval Workflow State（审批状态）：PENDING_REVIEW, APPROVED, REJECTED, REVOKED
3. Knowledge Source Type（知识类型）：TEMPLATE, APPROVED_RULE, LEGAL_REFERENCE, HISTORICAL_CASE
4. Authority Capability（权威能力）：can_drive_mandatory_finding, prompt_badge 等
5. Effective Version（时态版本）：source_version, effective_from, source_hash 等

核心原则：
- 严格 Fail-Closed：缺失元数据坚决阻断；
- 审批状态绝不可由文档状态 (ACTIVE)、文件名或模板类型自动推断；
- 历史案例与非强制参考法规严禁作为强制违规依据。
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


class DocumentLifecycleStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"
    DELETED = "DELETED"


# 保留别名以保证旧代码兼容性
KnowledgeLifecycleStatus = DocumentLifecycleStatus


class ApprovalStatus(str, Enum):
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"


@dataclass(frozen=True)
class KnowledgeTrustDescriptor:
    source_type: KnowledgeSourceType
    authority_rank: int  # 数值越小权威度越高 (1: 强制规则, 2: 现行标准模板, 3: 法规参考, 4: 历史案例)
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
    """检索结果准入过滤与权威等级标注器（Strict Fail-Closed 零信任边界）。"""

    BLOCKED_DOC_STATUSES = frozenset(
        {
            DocumentLifecycleStatus.DRAFT.value,
            DocumentLifecycleStatus.SUPERSEDED.value,
            DocumentLifecycleStatus.ARCHIVED.value,
            DocumentLifecycleStatus.DELETED.value,
        }
    )

    VALID_DOC_STATUSES = frozenset(
        {
            DocumentLifecycleStatus.DRAFT.value,
            DocumentLifecycleStatus.ACTIVE.value,
            DocumentLifecycleStatus.SUPERSEDED.value,
            DocumentLifecycleStatus.ARCHIVED.value,
            DocumentLifecycleStatus.DELETED.value,
        }
    )

    @classmethod
    def normalize_source_type(cls, raw: str | None) -> KnowledgeSourceType | None:
        if not raw or not str(raw).strip():
            return None
        up = str(raw).strip().upper()
        alias_map = {
            "TEMPLATE": KnowledgeSourceType.TEMPLATE,
            "RULE": KnowledgeSourceType.APPROVED_RULE,
            "APPROVED_RULE": KnowledgeSourceType.APPROVED_RULE,
            "CASE": KnowledgeSourceType.HISTORICAL_CASE,
            "HISTORICAL_CASE": KnowledgeSourceType.HISTORICAL_CASE,
            "HISTORY": KnowledgeSourceType.HISTORICAL_CASE,
            "LEGAL": KnowledgeSourceType.LEGAL_REFERENCE,
            "LEGAL_REFERENCE": KnowledgeSourceType.LEGAL_REFERENCE,
            "LAYER_A_RAW_CLAUSE": KnowledgeSourceType.TEMPLATE,  # 向后兼容存量 PostgreSQL 原始模板条款映射
        }
        return alias_map.get(up)

    @classmethod
    def descriptor_for(cls, raw_type: str | None) -> KnowledgeTrustDescriptor | None:
        stype = cls.normalize_source_type(raw_type)
        if stype is None:
            return None
        return TRUST_MATRIX.get(stype)

    @classmethod
    def is_chunk_admissible(
        cls,
        hit: dict[str, Any],
        *,
        allow_historical_cases: bool = False,
        expected_tenant_id: str | None = None,
        authoritative_only: bool = False,
        target_knowledge_types: list[str] | set[str] | None = None,
    ) -> bool:
        """校验检索分块是否满足状态、审批与租户边界要求（Strict Fail-Closed）。"""
        raw_ktype = hit.get("knowledge_type")
        if not raw_ktype or not str(raw_ktype).strip():
            return False

        desc = cls.descriptor_for(raw_ktype)
        if desc is None:
            return False

        # 0. 权威性与目标知识类型过滤 (Fail-Closed)
        if authoritative_only and not desc.can_drive_mandatory_finding:
            return False

        if target_knowledge_types is not None:
            allowed_types = {str(t).strip().upper() for t in target_knowledge_types}
            if desc.source_type.value not in allowed_types:
                return False

        # 1. 文档生命周期校验：必须明确存在且必须为 ACTIVE，严禁缺省默认放行 (Fail-Closed)
        raw_doc_status = (
            hit.get("doc_status") or hit.get("document_status") or hit.get("status")
        )
        if not raw_doc_status or not str(raw_doc_status).strip():
            return False
        doc_status = str(raw_doc_status).strip().upper()
        if (
            doc_status not in cls.VALID_DOC_STATUSES
            or doc_status != DocumentLifecycleStatus.ACTIVE.value
        ):
            return False

        # 2. 租户隔离校验：若声明 expected_tenant_id，hit 必须包含明确且一致的 tenant_id (Fail-Closed)
        if expected_tenant_id is not None:
            hit_tenant = hit.get("tenant_id")
            if (
                not hit_tenant
                or str(hit_tenant).strip() != str(expected_tenant_id).strip()
            ):
                return False

        # 3. 历史合同通道控制
        if (
            desc.source_type == KnowledgeSourceType.HISTORICAL_CASE
            and not allow_historical_cases
        ):
            return False

        # 4. 审批状态核验：权威知识项（TEMPLATE / APPROVED_RULE / LEGAL_REFERENCE）必须明确为 APPROVED
        # 严禁缺省、严禁 UNKNOWN、严禁将生命周期状态 "ACTIVE" 视为审批状态
        if desc.require_approved_status:
            raw_approved = hit.get("approved_status") or hit.get("approval_status")
            if not raw_approved or not str(raw_approved).strip():
                return False
            approved_status = str(raw_approved).strip().upper()
            if approved_status != ApprovalStatus.APPROVED.value:
                return False

        return True

    @classmethod
    def annotate_hit(cls, hit: dict[str, Any]) -> dict[str, Any]:
        """为通过治理校验的检索结果注入权威等级与提示词标识。"""
        desc = cls.descriptor_for(hit.get("knowledge_type"))
        enriched = dict(hit)
        if desc is None:
            enriched["can_drive_mandatory_finding"] = False
            enriched["authority_rank"] = 99
            enriched["prompt_badge"] = "[未经验证资产-严禁作为基准]"
            return enriched

        raw_approved = (
            str(hit.get("approved_status") or hit.get("approval_status") or "")
            .strip()
            .upper()
        )
        is_approved = raw_approved == ApprovalStatus.APPROVED.value
        can_drive = desc.can_drive_mandatory_finding and is_approved

        enriched["knowledge_type"] = desc.source_type.value
        enriched["authority_rank"] = desc.authority_rank
        enriched["can_drive_mandatory_finding"] = can_drive
        enriched["prompt_badge"] = desc.prompt_badge
        enriched["evidence_admissible"] = True
        return enriched
