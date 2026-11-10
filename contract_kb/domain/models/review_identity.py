# -*- coding: utf-8 -*-
"""合同审查任务身份、执行指纹与研判结论强类型模型 - Phase 13R2 & 13R3。

包含：
1. ReviewSessionIdentity: 审查会话租户与用户归属标识；
2. ReviewExecutionFingerprint: 12 维防篡改、防串线执行指纹；
3. LegalAgentReviewResult: 严谨的 Pydantic 结构化法务 Agent 输出契约；
4. ReviewStatus / HumanReviewStatus: 明确区分 AI 状态与人工最终裁决状态机。
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class ReviewStatus(str, Enum):
    """AI 研判执行状态（严禁将规则兜底伪装成 AI 成功）。"""

    AI_REVIEW_COMPLETED = "AI_REVIEW_COMPLETED"
    RULE_REVIEW_COMPLETED = "RULE_REVIEW_COMPLETED"
    AI_REVIEW_FAILED = "AI_REVIEW_FAILED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
    NOT_CHECKED = "NOT_CHECKED"


class HumanReviewStatus(str, Enum):
    """人工审阅闭环 7 态生命周期状态机。"""

    PENDING = "PENDING"
    AI_REVIEWING = "AI_REVIEWING"
    AI_REVIEWED = "AI_REVIEWED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    HUMAN_ACCEPTED = "HUMAN_ACCEPTED"
    HUMAN_REJECTED = "HUMAN_REJECTED"
    HUMAN_MODIFIED = "HUMAN_MODIFIED"

    def can_transition_to(self, target: HumanReviewStatus) -> bool:
        """校验状态流转合法性（严禁非法倒流或越级流转）。"""
        valid_transitions = {
            HumanReviewStatus.PENDING: {
                HumanReviewStatus.AI_REVIEWING,
                HumanReviewStatus.HUMAN_REVIEW_REQUIRED,
                HumanReviewStatus.HUMAN_ACCEPTED,
                HumanReviewStatus.HUMAN_REJECTED,
                HumanReviewStatus.HUMAN_MODIFIED,
            },
            HumanReviewStatus.AI_REVIEWING: {
                HumanReviewStatus.AI_REVIEWED,
                HumanReviewStatus.HUMAN_REVIEW_REQUIRED,
            },
            HumanReviewStatus.AI_REVIEWED: {
                HumanReviewStatus.HUMAN_REVIEW_REQUIRED,
                HumanReviewStatus.HUMAN_ACCEPTED,
                HumanReviewStatus.HUMAN_REJECTED,
                HumanReviewStatus.HUMAN_MODIFIED,
            },
            HumanReviewStatus.HUMAN_REVIEW_REQUIRED: {
                HumanReviewStatus.HUMAN_ACCEPTED,
                HumanReviewStatus.HUMAN_REJECTED,
                HumanReviewStatus.HUMAN_MODIFIED,
            },
            HumanReviewStatus.HUMAN_ACCEPTED: {
                HumanReviewStatus.HUMAN_MODIFIED,
                HumanReviewStatus.HUMAN_REJECTED,
            },
            HumanReviewStatus.HUMAN_REJECTED: {
                HumanReviewStatus.HUMAN_ACCEPTED,
                HumanReviewStatus.HUMAN_MODIFIED,
            },
            HumanReviewStatus.HUMAN_MODIFIED: {
                HumanReviewStatus.HUMAN_ACCEPTED,
                HumanReviewStatus.HUMAN_REJECTED,
            },
        }
        return target in valid_transitions.get(self, set())


@dataclass(frozen=True)
class ReviewSessionIdentity:
    """审查会话归属标识。"""

    session_id: str
    tenant_id: str
    user_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
        }


@dataclass
class ReviewSessionContext:
    """审查会话统一上下文（绑定租户、用户、文档哈希与母本版本，保证生命周期一致性）。"""

    session_id: str
    tenant_id: str
    user_id: str
    doc_name: str
    file_sha256: str = ""
    template_id: str = ""
    template_version: str = "v1"
    created_at: float = 0.0
    updated_at: float = 0.0
    expires_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewSessionContext:
        import time

        now = time.time()
        return cls(
            session_id=str(data.get("session_id") or "default_session"),
            tenant_id=str(data.get("tenant_id") or "default"),
            user_id=str(data.get("user_id") or "anonymous"),
            doc_name=str(data.get("doc_name") or ""),
            file_sha256=str(data.get("file_sha256") or ""),
            template_id=str(data.get("template_id") or ""),
            template_version=str(data.get("template_version") or "v1"),
            created_at=float(data.get("created_at") or now),
            updated_at=float(data.get("updated_at") or now),
            expires_at=float(data.get("expires_at") or (now + 86400 * 7)),
        )


@dataclass(frozen=True)
class ReviewExecutionFingerprint:
    """12 维原子执行指纹（确保文件名相同但内容变化、规则/模型变更时不误复用检查点）。"""

    tenant_id: str
    user_id: str
    file_sha256: str
    template_id: str
    template_content_hash: str
    template_version: str
    rule_set_version: str
    kb_snapshot_version: str
    agent_prompt_version: str
    model_config_version: str
    diff_engine_version: str
    annotations_fingerprint: str

    def compute_hash(self) -> str:
        """计算唯一防碰撞 SHA256 哈希。"""
        raw = (
            f"{self.tenant_id}|{self.user_id}|{self.file_sha256}|{self.template_id}|"
            f"{self.template_content_hash}|{self.template_version}|{self.rule_set_version}|"
            f"{self.kb_snapshot_version}|{self.agent_prompt_version}|{self.model_config_version}|"
            f"{self.diff_engine_version}|{self.annotations_fingerprint}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, str]:
        d = asdict(self)
        d["fingerprint_hash"] = self.compute_hash()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewExecutionFingerprint:
        return cls(
            tenant_id=str(data.get("tenant_id") or "default"),
            user_id=str(data.get("user_id") or "anonymous"),
            file_sha256=str(data.get("file_sha256") or ""),
            template_id=str(data.get("template_id") or ""),
            template_content_hash=str(data.get("template_content_hash") or ""),
            template_version=str(data.get("template_version") or "v1"),
            rule_set_version=str(data.get("rule_set_version") or "v1"),
            kb_snapshot_version=str(data.get("kb_snapshot_version") or "v1"),
            agent_prompt_version=str(data.get("agent_prompt_version") or "v1"),
            model_config_version=str(data.get("model_config_version") or "v1"),
            diff_engine_version=str(data.get("diff_engine_version") or "v1"),
            annotations_fingerprint=str(data.get("annotations_fingerprint") or ""),
        )


class LegalAgentReviewResult(BaseModel):
    """法务智能体研判结论严格结构化契约。"""

    risk_level: str = Field(pattern="^(HIGH|MEDIUM|LOW|INFO)$")
    risk_label: str = Field(min_length=1, max_length=64)
    ai_analysis: str = Field(min_length=5)
    action_advice: str = Field(min_length=5)
    evidence_references: list[dict[str, Any]] = Field(default_factory=list)
    review_status: ReviewStatus = ReviewStatus.AI_REVIEW_COMPLETED
    requires_human_review: bool = True
    provider: str = ""
    model: str = ""
    usage: dict[str, Any] = Field(default_factory=dict)
    failure_reason: str | None = None
    elapsed_sec: float = 0.0
    from_cache: bool = False
    reasoning: str = ""
