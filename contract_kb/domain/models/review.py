# -*- coding: utf-8 -*-
"""合同审核核心领域模型与契约（零外部框架/数据库依赖）。

定义统一的：
- FindingSource（DETERMINISTIC | LLM_SEMANTIC | HUMAN）
- EvidenceReference / Finding（含低置信度与缺证据禁止输出确定性法律结论守卫）
- StageStatus / StageExecutionRecord
- ReviewContext / ReviewResult
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class FindingSource(str, Enum):
    """审核结论来源分类（规则引擎结论、LLM 语义结论、人工复核结论严格区分）。"""

    DETERMINISTIC = "DETERMINISTIC"
    LLM_SEMANTIC = "LLM_SEMANTIC"
    HUMAN = "HUMAN"


class CheckStatus(str, Enum):
    CHECKED = "CHECKED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"
    NOT_CHECKED = "NOT_CHECKED"


class StageStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


LOW_CONFIDENCE_THRESHOLD = 0.75


def deterministic_issue_id(
    seed: str, rule_id: str | None, category: str, idx: int
) -> str:
    """基于复现种子生成稳定确定性的 10 位十六进制 issue_id。"""
    raw = f"{seed}|{rule_id or 'NONE'}|{category}|{idx}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


@dataclass
class EvidenceReference:
    """原文证据与知识来源引用定位。"""

    document_id: str
    node_ids: list[str] = field(default_factory=list)
    clause_number: str | None = None
    original_text: str = ""
    section: str = ""
    clause_id: str = ""
    exact_quote: str = ""
    char_offset_or_locator: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "node_ids": list(self.node_ids),
            "clause_number": self.clause_number,
            "original_text": self.original_text,
            "section": self.section,
            "clause_id": self.clause_id,
            "exact_quote": self.exact_quote,
            "char_offset_or_locator": self.char_offset_or_locator,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EvidenceReference:
        return cls(
            document_id=str(d.get("document_id", "")),
            node_ids=list(d.get("node_ids", []) or []),
            clause_number=d.get("clause_number"),
            original_text=str(d.get("original_text", "")),
            section=str(d.get("section", "")),
            clause_id=str(d.get("clause_id", "")),
            exact_quote=str(d.get("exact_quote", "")),
            char_offset_or_locator=d.get("char_offset_or_locator"),
        )



@dataclass
class Finding:
    """统一的审核风险发现领域实体。"""

    issue_id: str
    rule_id: str | None
    severity: str
    category: str
    title: str = ""
    description: str = ""
    expected_value: Any = None
    actual_value: Any = None
    source_reference: EvidenceReference | None = None
    knowledge_references: list[dict[str, Any]] = field(default_factory=list)
    suggestion: str = ""
    execution_mode: str = "DETERMINISTIC"
    finding_source: str = FindingSource.DETERMINISTIC.value
    requires_human_review: bool = False
    confidence: float | None = None
    check_status: str = CheckStatus.CHECKED.value
    citation_status: str = "VERIFIED"
    human_state: str = "PENDING"
    human_note: str | None = None

    def enforce_epistemic_guard(self) -> "Finding":
        """认知边界守卫：低置信度或证据不足时禁止输出确定性法律违规结论。"""
        if self.execution_mode == "LLM_SEMANTIC":
            self.finding_source = FindingSource.LLM_SEMANTIC.value
        elif self.human_state not in ("PENDING", "", None):
            self.finding_source = FindingSource.HUMAN.value

        # 1. 低置信度强制要求人工复核，不得作为确定性定论自动放行/阻断
        if self.confidence is not None and self.confidence < LOW_CONFIDENCE_THRESHOLD:
            self.requires_human_review = True
            if self.check_status == CheckStatus.CHECKED.value:
                self.check_status = CheckStatus.NEEDS_MANUAL_REVIEW.value

        # 2. LLM 语义发现若缺失原文摘录证据，降级为证据不足并强制人工复核
        if (
            self.finding_source == FindingSource.LLM_SEMANTIC.value
            and self.category not in ("NOT_CHECKED", "CUSTOM_REQUIREMENT_RESULT")
            and (
                self.source_reference is None
                or not self.source_reference.original_text.strip()
            )
        ):
            self.requires_human_review = True
            self.check_status = CheckStatus.INSUFFICIENT_EVIDENCE.value

        # 3. 证据不足或未检查项不得伪装为无需人工关注的正式违规结论
        if self.check_status in (
            CheckStatus.INSUFFICIENT_EVIDENCE.value,
            CheckStatus.NEEDS_MANUAL_REVIEW.value,
        ) and self.severity in ("HIGH", "MEDIUM"):
            self.requires_human_review = True

        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "expected_value": self.expected_value,
            "actual_value": self.actual_value,
            "source_reference": (
                self.source_reference.to_dict()
                if self.source_reference is not None
                else None
            ),
            "knowledge_references": list(self.knowledge_references),
            "suggestion": self.suggestion,
            "execution_mode": self.execution_mode,
            "finding_source": self.finding_source,
            "requires_human_review": self.requires_human_review,
            "confidence": self.confidence,
            "check_status": self.check_status,
            "citation_status": self.citation_status,
            "human_state": self.human_state,
            "human_note": self.human_note,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Finding:
        ref_data = d.get("source_reference")
        ref: EvidenceReference | None = None
        if isinstance(ref_data, dict):
            ref = EvidenceReference.from_dict(ref_data)
        elif isinstance(ref_data, EvidenceReference):
            ref = ref_data

        return cls(
            issue_id=str(d.get("issue_id", "")),
            rule_id=d.get("rule_id"),
            severity=str(d.get("severity", "LOW")),
            category=str(d.get("category", "")),
            title=str(d.get("title", "")),
            description=str(d.get("description", "")),
            expected_value=d.get("expected_value"),
            actual_value=d.get("actual_value"),
            source_reference=ref,
            knowledge_references=list(d.get("knowledge_references", []) or []),
            suggestion=str(d.get("suggestion", "")),
            execution_mode=str(d.get("execution_mode", "DETERMINISTIC")),
            finding_source=str(d.get("finding_source", FindingSource.DETERMINISTIC.value)),
            requires_human_review=bool(d.get("requires_human_review", False)),
            confidence=d.get("confidence"),
            check_status=str(d.get("check_status", CheckStatus.CHECKED.value)),
            citation_status=str(d.get("citation_status", "VERIFIED")),
            human_state=str(d.get("human_state", "PENDING")),
            human_note=d.get("human_note"),
        )



@dataclass
class StageExecutionRecord:
    """审核流水线单阶段状态留痕契约。"""

    task_id: str
    stage_name: str
    stage_order: int
    status: str = StageStatus.PENDING.value
    attempt: int = 1
    started_at: float = 0.0
    finished_at: float = 0.0
    duration_ms: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineContext:
    """14 阶段可组合审核流水线统一上下文（ReviewContext）。"""

    docx_path: Path
    mode: str = "STANDARD"
    custom_requirements: list[str] = field(default_factory=list)
    scope_tenant: str | None = None
    effective_tenant: str = "default"
    created_by: str = "system"
    task_id: str = ""
    idempotency_key: str | None = None
    timeout_seconds: float = 120.0
    started_monotonic: float = 0.0
    cancel_requested: bool = False

    # 版本快照与可复现性指纹
    rule_set_version: str = "builtin-v1"
    model_version: str = "none"
    reproducibility_seed: str = ""

    # 各阶段结构化产物
    short_circuit_status: str | None = None
    short_circuit_reason: str | None = None
    security_result: Any = None
    parsed_record: Any = None
    clauses: list[dict[str, Any]] = field(default_factory=list)
    valid_node_ids: set[str] = field(default_factory=set)
    category: str = ""
    subtype: str = ""
    classification_confidence: float = 0.0
    classification_conflicts: list[Any] = field(default_factory=list)
    matched_template: dict[str, Any] | None = None
    extracted_fields: dict[str, Any] = field(default_factory=dict)
    format_standard: dict[str, Any] | None = None
    rag_context: list[dict[str, Any]] = field(default_factory=list)
    llm_status_info: dict[str, Any] = field(default_factory=dict)
    llm_result: Any = None
    findings: list[Finding] = field(default_factory=list)
    review_gate: str = "PASS"
    stage_records: list[StageExecutionRecord] = field(default_factory=list)
    resume_completed_stages: set[str] = field(default_factory=set)
    stage_checkpoints: dict[str, Any] = field(default_factory=dict)
    deadline: Any = None
    cancel_token: Any = None

    def get_deadline(self) -> Any:
        if self.deadline is None:
            from ...application.pipeline.deadline import ExecutionDeadline

            self.deadline = ExecutionDeadline(
                timeout_seconds=self.timeout_seconds,
                started_monotonic=(
                    self.started_monotonic if self.started_monotonic > 0 else None
                ),
            )
        return self.deadline

    def get_cancel_token(self) -> Any:
        if self.cancel_token is None:
            from ...application.pipeline.deadline import CancelToken

            self.cancel_token = CancelToken(check_fn=lambda: self.cancel_requested)
        return self.cancel_token


@dataclass
class ReviewResult:
    """审核任务最终输出契约（100% 兼容既有返回字典结构，并扩展流水线可观测与溯源元数据）。"""

    task_id: str
    status: str
    document_id: str | None = None
    source_file: str | None = None
    mode: str = "STANDARD"
    category: str | None = None
    subtype: str | None = None
    classification_confidence: float | None = None
    matched_template_id: str | None = None
    tenant_id: str = "default"
    created_by: str = "system"
    llm: dict[str, Any] = field(default_factory=dict)
    rag_context: list[dict[str, Any]] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None
    rule_set_version: str = "builtin-v1"
    model_version: str = "none"
    reproducibility_hash: str = ""
    review_gate: str = "PASS"
    finding_breakdown: dict[str, int] = field(default_factory=dict)
    extracted_fields: dict[str, Any] = field(default_factory=dict)
    stages: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        if self.status in ("REJECTED", "PARSE_FAILED", "CANCELLED", "TIMED_OUT"):
            out: dict[str, Any] = {
                "task_id": self.task_id,
                "status": self.status,
                "reason": self.reason or "",
                "issues": list(self.issues),
            }
            if self.stages:
                out["stages"] = list(self.stages)
            return out

        return {
            "task_id": self.task_id,
            "document_id": self.document_id,
            "source_file": self.source_file,
            "mode": self.mode,
            "category": self.category,
            "subtype": self.subtype,
            "classification_confidence": self.classification_confidence,
            "matched_template_id": self.matched_template_id,
            "status": self.status,
            "issue_count": len(self.issues),
            "tenant_id": self.tenant_id,
            "created_by": self.created_by,
            "llm": dict(self.llm),
            "rag_context": list(self.rag_context),
            "issues": list(self.issues),
            "rule_set_version": self.rule_set_version,
            "model_version": self.model_version,
            "reproducibility_hash": self.reproducibility_hash,
            "review_gate": self.review_gate,
            "finding_breakdown": dict(self.finding_breakdown),
            "extracted_fields": dict(self.extracted_fields),
            "stages": list(self.stages),
        }
