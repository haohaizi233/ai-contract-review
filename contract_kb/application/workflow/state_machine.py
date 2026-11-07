# -*- coding: utf-8 -*-
"""受控合同审核智能体确定性 8 状态工作流编排器（ControlledReviewAgentWorkflow）。

状态转换拓扑（显式有向无环状态机，严禁不受控自治跳转或直接修改规则库）：
1. DocumentAnalysis
2. ContractClassification
3. EvidenceRetrieval
4. RuleChecking
5. SemanticRiskAnalysis（超预算/未授权/超时优雅降级为 NOT_CHECKED，不阻断确定性结论）
6. FindingConsolidation
7. ReportGeneration
8. HumanReviewCoordination -> COMPLETED
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ... import config
from ...domain.models.review import (
    CheckStatus,
    Finding,
    FindingSource,
    PipelineContext,
)
from ...modules.rules import RuleRegistry
from ..pipeline.stages import (
    ClassifyStage,
    DeterministicValidationStage,
    ExtractFieldsStage,
    FinalizeReportStage,
    HumanReviewStage,
    LLMSemanticReviewStage,
    NormalizeStage,
    ParseStage,
    ReceiveStage,
    ResolveFindingsStage,
    RetrieveEvidenceStage,
    SelectTemplateStage,
    ValidateStage,
    VerifyEvidenceStage,
)
from .tool_executor import (
    ControlledToolExecutor,
    ToolInvocationError,
    ToolPermission,
    ToolSpec,
    WorkflowBudget,
)


class AgentWorkflowState(str, Enum):
    INIT = "INIT"
    DOCUMENT_ANALYSIS = "DocumentAnalysis"
    CONTRACT_CLASSIFICATION = "ContractClassification"
    EVIDENCE_RETRIEVAL = "EvidenceRetrieval"
    RULE_CHECKING = "RuleChecking"
    SEMANTIC_RISK_ANALYSIS = "SemanticRiskAnalysis"
    FINDING_CONSOLIDATION = "FindingConsolidation"
    REPORT_GENERATION = "ReportGeneration"
    HUMAN_REVIEW_COORDINATION = "HumanReviewCoordination"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"


ALLOWED_TRANSITIONS: dict[AgentWorkflowState, frozenset[AgentWorkflowState]] = {
    AgentWorkflowState.INIT: frozenset({AgentWorkflowState.DOCUMENT_ANALYSIS}),
    AgentWorkflowState.DOCUMENT_ANALYSIS: frozenset(
        {AgentWorkflowState.CONTRACT_CLASSIFICATION, AgentWorkflowState.ABORTED}
    ),
    AgentWorkflowState.CONTRACT_CLASSIFICATION: frozenset(
        {AgentWorkflowState.EVIDENCE_RETRIEVAL}
    ),
    AgentWorkflowState.EVIDENCE_RETRIEVAL: frozenset(
        {AgentWorkflowState.RULE_CHECKING}
    ),
    AgentWorkflowState.RULE_CHECKING: frozenset(
        {AgentWorkflowState.SEMANTIC_RISK_ANALYSIS}
    ),
    AgentWorkflowState.SEMANTIC_RISK_ANALYSIS: frozenset(
        {AgentWorkflowState.FINDING_CONSOLIDATION}
    ),
    AgentWorkflowState.FINDING_CONSOLIDATION: frozenset(
        {AgentWorkflowState.REPORT_GENERATION}
    ),
    AgentWorkflowState.REPORT_GENERATION: frozenset(
        {AgentWorkflowState.HUMAN_REVIEW_COORDINATION}
    ),
    AgentWorkflowState.HUMAN_REVIEW_COORDINATION: frozenset(
        {AgentWorkflowState.COMPLETED}
    ),
    AgentWorkflowState.COMPLETED: frozenset(),
    AgentWorkflowState.ABORTED: frozenset(),
}


class InvalidWorkflowTransitionError(RuntimeError):
    """非法状态机跳转异常。"""


# ---------- 强类型受控工具输入/输出 Pydantic Schema ----------


class _DocInput(BaseModel):
    docx_path: str


class _DocOutput(BaseModel):
    passed: bool
    clause_count: int = 0
    short_circuit_status: str | None = None
    reason: str | None = None


class _ClassifyInput(BaseModel):
    task_id: str


class _ClassifyOutput(BaseModel):
    category: str
    subtype: str
    confidence: float
    matched_template_id: str | None = None


class _RetrieveInput(BaseModel):
    subtype: str
    tenant_id: str | None = None


class _RetrieveOutput(BaseModel):
    retrieved_chunks: int
    citation_correctness: float


class _RuleCheckInput(BaseModel):
    mode: str
    custom_requirements: list[str] = Field(default_factory=list)


class _RuleCheckOutput(BaseModel):
    deterministic_findings: int
    rule_set_version: str


class _SemanticInput(BaseModel):
    mode: str
    sanitized_requirements: list[str] = Field(default_factory=list)


class _SemanticOutput(BaseModel):
    llm_status: str
    tokens_used: int = 0


class _ConsolidateInput(BaseModel):
    task_id: str


class _ConsolidateOutput(BaseModel):
    total_findings: int
    downgraded_references: int


class _ReportInput(BaseModel):
    task_id: str


class _ReportOutput(BaseModel):
    reproducibility_hash: str
    persisted_issues: int


class _HumanCoordInput(BaseModel):
    task_id: str


class _HumanCoordOutput(BaseModel):
    review_gate: str
    requires_human_count: int


@dataclass
class AgentWorkflowResult:
    task_id: str
    terminal_state: str
    status: str
    review_gate: str
    issues: list[dict[str, Any]]
    state_history: list[str] = field(default_factory=list)
    tool_audit_trail: list[dict[str, Any]] = field(default_factory=list)
    budget_ledger: dict[str, Any] = field(default_factory=dict)
    reproducibility_hash: str = ""
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "terminal_state": self.terminal_state,
            "status": self.status,
            "review_gate": self.review_gate,
            "issues": self.issues,
            "state_history": list(self.state_history),
            "tool_audit_trail": list(self.tool_audit_trail),
            "budget_ledger": dict(self.budget_ledger),
            "reproducibility_hash": self.reproducibility_hash,
            "reason": self.reason,
        }


class ControlledReviewAgentWorkflow:
    """企业级受控合同审核智能体状态机。"""

    def __init__(
        self,
        storage: Any,
        llm_provider: Any = None,
        llm_reviewer: Any = None,
        budget: WorkflowBudget | None = None,
        granted_permissions: set[ToolPermission] | None = None,
        registry: RuleRegistry | None = None,
    ):
        self.storage = storage
        self._llm_provider = llm_provider
        self._llm_reviewer = llm_reviewer
        self.budget = budget or WorkflowBudget()
        self.granted_permissions = granted_permissions
        self.registry = registry or RuleRegistry(include_defaults=True)

    @staticmethod
    def verify_transition(
        current: AgentWorkflowState, target: AgentWorkflowState
    ) -> None:
        allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
        if target not in allowed:
            raise InvalidWorkflowTransitionError(
                f"禁止非法状态机跳转：{current.value} -> {target.value}"
            )

    def _llm_status(self) -> dict[str, Any]:
        if self._llm_reviewer is not None:
            prov = getattr(self._llm_reviewer, "provider", None)
            return {
                "provider": getattr(prov, "name", "custom") if prov else "none",
                "configured": prov is not None,
            }
        prov = self._llm_provider
        return {
            "provider": getattr(prov, "name", "") if prov else "none",
            "configured": prov is not None,
        }

    def run(
        self,
        docx_path: str | Path,
        *,
        mode: str = "COMBINED",
        custom_requirements: list[str] | None = None,
        tenant_id: str | None = None,
        created_by: str = "agent_workflow",
        task_id: str | None = None,
    ) -> AgentWorkflowResult:
        effective_tenant = tenant_id if tenant_id is not None else config.TENANT_ID
        ctx = PipelineContext(
            docx_path=Path(docx_path),
            mode=mode,
            custom_requirements=list(custom_requirements or []),
            scope_tenant=tenant_id,
            effective_tenant=effective_tenant,
            created_by=created_by,
            task_id=task_id or uuid.uuid4().hex[:12],
            started_monotonic=time.monotonic(),
        )

        executor = ControlledToolExecutor(
            budget=self.budget, granted_permissions=self.granted_permissions
        )
        self._register_tools(executor, ctx)

        state = AgentWorkflowState.INIT
        history: list[str] = [state.value]

        def step_to(next_state: AgentWorkflowState) -> None:
            nonlocal state
            self.verify_transition(state, next_state)
            state = next_state
            history.append(state.value)

        # 1. DocumentAnalysis
        step_to(AgentWorkflowState.DOCUMENT_ANALYSIS)
        doc_out: _DocOutput = executor.invoke(  # type: ignore[assignment]
            "analyze_document", state.value, {"docx_path": str(docx_path)}
        )
        if not doc_out.passed:
            step_to(AgentWorkflowState.ABORTED)
            return AgentWorkflowResult(
                task_id=ctx.task_id,
                terminal_state=state.value,
                status=doc_out.short_circuit_status or "REJECTED",
                review_gate="BLOCK",
                issues=[],
                state_history=history,
                tool_audit_trail=[r.to_dict() for r in executor.audit_records],
                budget_ledger=executor.ledger.to_dict(),
                reason=doc_out.reason,
            )

        # 2. ContractClassification
        step_to(AgentWorkflowState.CONTRACT_CLASSIFICATION)
        executor.invoke(
            "classify_and_match_template", state.value, {"task_id": ctx.task_id}
        )

        # 3. EvidenceRetrieval
        step_to(AgentWorkflowState.EVIDENCE_RETRIEVAL)
        executor.invoke(
            "retrieve_governed_evidence",
            state.value,
            {"subtype": ctx.subtype, "tenant_id": ctx.scope_tenant},
        )

        # 4. RuleChecking
        step_to(AgentWorkflowState.RULE_CHECKING)
        executor.invoke(
            "run_deterministic_rules",
            state.value,
            {"mode": ctx.mode, "custom_requirements": ctx.custom_requirements},
        )

        # 5. SemanticRiskAnalysis（受预算、权限与超时门禁约束，失败诚实降级 NOT_CHECKED）
        step_to(AgentWorkflowState.SEMANTIC_RISK_ANALYSIS)
        try:
            executor.invoke(
                "run_semantic_risk_analysis",
                state.value,
                {
                    "mode": ctx.mode,
                    "sanitized_requirements": ctx.custom_requirements,
                },
            )
        except ToolInvocationError as exc:
            ctx.findings.append(
                Finding(
                    issue_id="",
                    rule_id="LLM_SEMANTIC_REVIEW",
                    severity="INFO",
                    category="NOT_CHECKED",
                    title=f"AI 语义审核受控降级（{exc.status.value}）",
                    description=(
                        f"受控智能体工作流拦截或降级了外部模型调用：{exc}；"
                        "已保留全部确定性规则结论并交由人工复核。"
                    ),
                    execution_mode="LLM_SEMANTIC",
                    finding_source=FindingSource.LLM_SEMANTIC.value,
                    requires_human_review=True,
                    check_status=CheckStatus.NOT_CHECKED.value,
                )
            )

        # 6. FindingConsolidation
        step_to(AgentWorkflowState.FINDING_CONSOLIDATION)
        executor.invoke(
            "consolidate_and_verify_findings", state.value, {"task_id": ctx.task_id}
        )

        # 7. ReportGeneration
        step_to(AgentWorkflowState.REPORT_GENERATION)
        rep_out: _ReportOutput = executor.invoke(  # type: ignore[assignment]
            "generate_and_persist_report", state.value, {"task_id": ctx.task_id}
        )

        # 8. HumanReviewCoordination
        step_to(AgentWorkflowState.HUMAN_REVIEW_COORDINATION)
        coord_out: _HumanCoordOutput = executor.invoke(  # type: ignore[assignment]
            "coordinate_human_review", state.value, {"task_id": ctx.task_id}
        )

        step_to(AgentWorkflowState.COMPLETED)
        return AgentWorkflowResult(
            task_id=ctx.task_id,
            terminal_state=state.value,
            status="COMPLETED",
            review_gate=coord_out.review_gate,
            issues=[f.to_dict() for f in ctx.findings],
            state_history=history,
            tool_audit_trail=[r.to_dict() for r in executor.audit_records],
            budget_ledger=executor.ledger.to_dict(),
            reproducibility_hash=rep_out.reproducibility_hash,
        )

    def _register_tools(
        self, executor: ControlledToolExecutor, ctx: PipelineContext
    ) -> None:
        def _handle_doc(_: _DocInput) -> _DocOutput:
            ReceiveStage(llm_status_fn=self._llm_status).execute(ctx)
            ValidateStage().execute(ctx)
            if ctx.short_circuit_status:
                return _DocOutput(
                    passed=False,
                    short_circuit_status=ctx.short_circuit_status,
                    reason=ctx.short_circuit_reason,
                )
            ParseStage().execute(ctx)
            if ctx.short_circuit_status:
                return _DocOutput(
                    passed=False,
                    short_circuit_status=ctx.short_circuit_status,
                    reason=ctx.short_circuit_reason,
                )
            norm = NormalizeStage().execute(ctx)
            ExtractFieldsStage().execute(ctx)
            return _DocOutput(passed=True, clause_count=int(norm["normalized_clauses"]))

        def _handle_classify(_: _ClassifyInput) -> _ClassifyOutput:
            ClassifyStage().execute(ctx)
            SelectTemplateStage(self.storage).execute(ctx)
            return _ClassifyOutput(
                category=ctx.category,
                subtype=ctx.subtype,
                confidence=float(ctx.classification_confidence),
                matched_template_id=(
                    ctx.matched_template["document_id"]
                    if ctx.matched_template
                    else None
                ),
            )

        def _handle_retrieve(_: _RetrieveInput) -> _RetrieveOutput:
            res = RetrieveEvidenceStage(self.storage).execute(ctx)
            return _RetrieveOutput(
                retrieved_chunks=int(res["retrieved_chunks"]),
                citation_correctness=float(res.get("citation_correctness", 1.0)),
            )

        def _handle_rules(_: _RuleCheckInput) -> _RuleCheckOutput:
            before = len(ctx.findings)
            DeterministicValidationStage(self.storage, registry=self.registry).execute(
                ctx
            )
            return _RuleCheckOutput(
                deterministic_findings=len(ctx.findings) - before,
                rule_set_version=ctx.rule_set_version,
            )

        def _handle_semantic(_: _SemanticInput) -> _SemanticOutput:
            res = LLMSemanticReviewStage(
                llm_provider=self._llm_provider, llm_reviewer=self._llm_reviewer
            ).execute(ctx)
            usage = getattr(ctx.llm_result, "raw_usage", {}) if ctx.llm_result else {}
            tokens = int(
                usage.get("total_tokens")
                or usage.get("tokens")
                or (
                    int(usage.get("prompt_tokens", 0))
                    + int(usage.get("completion_tokens", 0))
                )
            )
            return _SemanticOutput(
                llm_status=str(res.get("llm_status", "NOT_CHECKED")),
                tokens_used=tokens,
            )

        def _handle_consolidate(_: _ConsolidateInput) -> _ConsolidateOutput:
            ver = VerifyEvidenceStage().execute(ctx)
            ResolveFindingsStage().execute(ctx)
            return _ConsolidateOutput(
                total_findings=len(ctx.findings),
                downgraded_references=int(ver["downgraded_count"]),
            )

        def _handle_human_coord(_: _HumanCoordInput) -> _HumanCoordOutput:
            HumanReviewStage(self.storage).execute(ctx)
            req_cnt = sum(1 for f in ctx.findings if f.requires_human_review)
            return _HumanCoordOutput(
                review_gate=ctx.review_gate, requires_human_count=req_cnt
            )

        def _handle_report(_: _ReportInput) -> _ReportOutput:
            HumanReviewStage(self.storage).execute(ctx)
            rep = FinalizeReportStage(self.storage).execute(ctx)
            return _ReportOutput(
                reproducibility_hash=str(rep["reproducibility_hash"]),
                persisted_issues=int(rep["persisted_issues"]),
            )

        executor.register(
            ToolSpec(
                name="analyze_document",
                description="安全校验、DOCX 结构解析与字段抽取",
                input_schema=_DocInput,
                output_schema=_DocOutput,
                required_permission=ToolPermission.READ_DOCUMENT,
                handler=_handle_doc,  # type: ignore[arg-type]
                max_calls_per_run=1,
            )
        )
        executor.register(
            ToolSpec(
                name="classify_and_match_template",
                description="五级业务分类与现行标准模板匹配",
                input_schema=_ClassifyInput,
                output_schema=_ClassifyOutput,
                required_permission=ToolPermission.READ_KNOWLEDGE,
                handler=_handle_classify,  # type: ignore[arg-type]
                max_calls_per_run=1,
            )
        )
        executor.register(
            ToolSpec(
                name="retrieve_governed_evidence",
                description="受控混合知识检索（隔离权威标准与历史参考）",
                input_schema=_RetrieveInput,
                output_schema=_RetrieveOutput,
                required_permission=ToolPermission.READ_KNOWLEDGE,
                handler=_handle_retrieve,  # type: ignore[arg-type]
                max_calls_per_run=2,
            )
        )
        executor.register(
            ToolSpec(
                name="run_deterministic_rules",
                description="确定性规则策略注册表与已审批库表规则核验",
                input_schema=_RuleCheckInput,
                output_schema=_RuleCheckOutput,
                required_permission=ToolPermission.EXECUTE_RULES,
                handler=_handle_rules,  # type: ignore[arg-type]
                max_calls_per_run=1,
            )
        )
        executor.register(
            ToolSpec(
                name="run_semantic_risk_analysis",
                description="受控 LLM 语义风险与自定义要求分析",
                input_schema=_SemanticInput,
                output_schema=_SemanticOutput,
                required_permission=ToolPermission.INVOKE_LLM,
                handler=_handle_semantic,  # type: ignore[arg-type]
                timeout_seconds=45.0,
                max_calls_per_run=1,
            )
        )
        executor.register(
            ToolSpec(
                name="consolidate_and_verify_findings",
                description="原文定位真实性核验、认知护栏与冲突消解",
                input_schema=_ConsolidateInput,
                output_schema=_ConsolidateOutput,
                required_permission=ToolPermission.EXECUTE_RULES,
                handler=_handle_consolidate,  # type: ignore[arg-type]
                max_calls_per_run=1,
            )
        )
        executor.register(
            ToolSpec(
                name="generate_and_persist_report",
                description="计算可复现指纹并在 UoW 事务内持久化审核报告",
                input_schema=_ReportInput,
                output_schema=_ReportOutput,
                required_permission=ToolPermission.WRITE_REPORT,
                handler=_handle_report,  # type: ignore[arg-type]
                max_calls_per_run=1,
            )
        )
        executor.register(
            ToolSpec(
                name="coordinate_human_review",
                description="融合人工复核状态并输出放行门禁建议",
                input_schema=_HumanCoordInput,
                output_schema=_HumanCoordOutput,
                required_permission=ToolPermission.READ_KNOWLEDGE,
                handler=_handle_human_coord,  # type: ignore[arg-type]
                max_calls_per_run=1,
            )
        )
