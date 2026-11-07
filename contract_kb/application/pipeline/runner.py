# -*- coding: utf-8 -*-
"""14 阶段审核流水线编排器（ReviewPipeline Runner）：
支持幂等键短路、阶段状态持久化、失败断点恢复、超时控制与取消信号响应。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ... import config
from ...domain.models.review import (
    PipelineContext,
    ReviewResult,
    StageExecutionRecord,
    StageStatus,
)
from ...modules.rules import RuleRegistry
from .stages import (
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
    ReviewStage,
    SelectTemplateStage,
    ValidateStage,
    VerifyEvidenceStage,
)


class ReviewPipeline:
    """企业级可组合合同审核流水线。"""

    def __init__(
        self,
        storage: Any,
        llm_provider: Any = None,
        llm_reviewer: Any = None,
        llm_status_fn: Any = None,
        registry: RuleRegistry | None = None,
        stages: list[ReviewStage] | None = None,
    ):
        self.storage = storage
        self.registry = registry or RuleRegistry(include_defaults=True)
        self.stages: list[ReviewStage] = stages or [
            ReceiveStage(llm_status_fn=llm_status_fn),
            ValidateStage(),
            ParseStage(),
            NormalizeStage(),
            ClassifyStage(),
            SelectTemplateStage(storage),
            ExtractFieldsStage(),
            DeterministicValidationStage(storage, registry=self.registry),
            RetrieveEvidenceStage(storage),
            LLMSemanticReviewStage(
                llm_provider=llm_provider, llm_reviewer=llm_reviewer
            ),
            VerifyEvidenceStage(),
            ResolveFindingsStage(),
            HumanReviewStage(storage),
            FinalizeReportStage(storage),
        ]

    def _resolve_idempotency_and_attempts(
        self,
        idempotency_key: str | None,
        effective_tenant: str,
        task_id: str | None,
    ) -> tuple[ReviewResult | None, str, dict[str, int]]:
        if idempotency_key:
            find_idem = getattr(self.storage, "find_task_by_idempotency_key", None)
            if find_idem is not None:
                existing = find_idem(idempotency_key, tenant_id=effective_tenant)
                if existing and existing.get("status") == "COMPLETED":
                    return self._rebuild_result_from_persisted(existing), "", {}
                if existing and not task_id:
                    task_id = existing["task_id"]

        prior_attempts: dict[str, int] = {}
        get_stages = getattr(self.storage, "get_stage_states", None)
        if task_id and get_stages is not None:
            for st in get_stages(task_id):
                prior_attempts[st["stage_name"]] = int(st.get("attempt", 1))
        return None, (task_id or ""), prior_attempts

    def _execute_single_stage(
        self, stage: ReviewStage, ctx: PipelineContext, attempt: int
    ) -> ReviewResult | None:
        if self._check_cancelled(ctx):
            self._record_terminal_task(ctx, "CANCELLED", "cancelled_by_caller")
            return ReviewResult(
                task_id=ctx.task_id,
                status="CANCELLED",
                reason="review_task_cancelled",
                stages=[r.to_dict() for r in ctx.stage_records],
            )

        elapsed = time.monotonic() - ctx.started_monotonic
        if elapsed > ctx.timeout_seconds:
            rec_timeout = StageExecutionRecord(
                task_id=ctx.task_id or "pending",
                stage_name=stage.stage_name,
                stage_order=stage.stage_order,
                status=StageStatus.TIMED_OUT.value,
                attempt=attempt,
                started_at=time.time(),
                finished_at=time.time(),
                duration_ms=0.0,
                error=f"timeout_exceeded ({elapsed:.3f}s > {ctx.timeout_seconds}s)",
            )
            ctx.stage_records.append(rec_timeout)
            self._persist_stage(ctx, rec_timeout)
            self._record_terminal_task(ctx, "TIMED_OUT", rec_timeout.error or "")
            return ReviewResult(
                task_id=ctx.task_id,
                status="TIMED_OUT",
                reason=rec_timeout.error,
                stages=[r.to_dict() for r in ctx.stage_records],
            )

        t0_wall = time.time()
        t0_mono = time.monotonic()
        stage_rec = StageExecutionRecord(
            task_id=ctx.task_id,
            stage_name=stage.stage_name,
            stage_order=stage.stage_order,
            status=StageStatus.RUNNING.value,
            attempt=attempt,
            started_at=t0_wall,
        )

        try:
            detail = stage.execute(ctx)
            stage_rec.task_id = ctx.task_id
            if stage.stage_order == 1 and ctx.task_id:
                self._ensure_running_task_row(ctx)
            stage_rec.finished_at = time.time()
            stage_rec.duration_ms = round((time.monotonic() - t0_mono) * 1000.0, 3)
            stage_rec.status = StageStatus.COMPLETED.value
            stage_rec.detail = detail or {}
            ctx.stage_records.append(stage_rec)
            self._persist_stage(ctx, stage_rec)
        except Exception as exc:
            stage_rec.task_id = ctx.task_id or "failed"
            stage_rec.finished_at = time.time()
            stage_rec.duration_ms = round((time.monotonic() - t0_mono) * 1000.0, 3)
            stage_rec.status = StageStatus.FAILED.value
            stage_rec.error = f"{type(exc).__name__}: {exc}"
            ctx.stage_records.append(stage_rec)
            if ctx.task_id:
                self._persist_stage(ctx, stage_rec)
                self._record_terminal_task(ctx, "FAILED", stage_rec.error)
            raise

        if ctx.short_circuit_status:
            self._record_terminal_task(
                ctx, ctx.short_circuit_status, ctx.short_circuit_reason or ""
            )
            return ReviewResult(
                task_id=ctx.task_id,
                status=ctx.short_circuit_status,
                reason=ctx.short_circuit_reason,
                stages=[r.to_dict() for r in ctx.stage_records],
            )
        return None

    def run(
        self,
        docx_path: str | Path,
        mode: str = "STANDARD",
        custom_requirements: list[str] | None = None,
        tenant_id: str | None = None,
        created_by: str = "system",
        task_id: str | None = None,
        idempotency_key: str | None = None,
        timeout_seconds: float = 120.0,
        cancel_requested: bool = False,
    ) -> ReviewResult:
        effective_tenant = tenant_id if tenant_id is not None else config.TENANT_ID
        cached, resolved_task_id, prior_attempts = (
            self._resolve_idempotency_and_attempts(
                idempotency_key, effective_tenant, task_id
            )
        )
        if cached is not None:
            return cached

        ctx = PipelineContext(
            docx_path=Path(docx_path),
            mode=mode,
            custom_requirements=list(custom_requirements or []),
            scope_tenant=tenant_id,
            effective_tenant=effective_tenant,
            created_by=created_by,
            task_id=resolved_task_id,
            idempotency_key=idempotency_key,
            timeout_seconds=float(timeout_seconds),
            started_monotonic=time.monotonic(),
            cancel_requested=bool(cancel_requested),
        )

        for stage in self.stages:
            attempt = prior_attempts.get(stage.stage_name, 0) + 1
            early = self._execute_single_stage(stage, ctx, attempt)
            if early is not None:
                return early

        repro_hash = str(ctx.extracted_fields.pop("_reproducibility_hash", ""))
        breakdown = dict(ctx.extracted_fields.pop("_finding_breakdown", {}))
        return ReviewResult(
            task_id=ctx.task_id,
            status="COMPLETED",
            document_id=ctx.parsed_record.template_id,
            source_file=ctx.parsed_record.source_file,
            mode=ctx.mode,
            category=ctx.category,
            subtype=ctx.subtype,
            classification_confidence=ctx.classification_confidence,
            matched_template_id=(
                ctx.matched_template["document_id"] if ctx.matched_template else None
            ),
            tenant_id=ctx.effective_tenant,
            created_by=ctx.created_by,
            llm=ctx.llm_status_info,
            rag_context=ctx.rag_context,
            issues=[f.to_dict() for f in ctx.findings],
            rule_set_version=ctx.rule_set_version,
            model_version=ctx.model_version,
            reproducibility_hash=repro_hash,
            review_gate=ctx.review_gate,
            finding_breakdown=breakdown,
            extracted_fields=ctx.extracted_fields,
            stages=[r.to_dict() for r in ctx.stage_records],
        )

    def _check_cancelled(self, ctx: PipelineContext) -> bool:
        if ctx.cancel_requested:
            return True
        if ctx.task_id:
            is_cancelled = getattr(self.storage, "is_task_cancelled", None)
            if is_cancelled is not None and is_cancelled(ctx.task_id):
                ctx.cancel_requested = True
                return True
        return False

    def _ensure_running_task_row(self, ctx: PipelineContext) -> None:
        get_task = getattr(self.storage, "get_task", None)
        if get_task is not None and get_task(ctx.task_id) is not None:
            return
        save_task = getattr(self.storage, "save_task", None)
        if save_task is None:
            return
        try:
            save_task(
                ctx.task_id,
                "",
                ctx.mode,
                "RUNNING",
                {"source_file": ctx.docx_path.name},
                tenant_id=ctx.effective_tenant,
                created_by=ctx.created_by,
                idempotency_key=ctx.idempotency_key,
            )
        except TypeError:
            save_task(
                ctx.task_id,
                "",
                ctx.mode,
                "RUNNING",
                {"source_file": ctx.docx_path.name},
                tenant_id=ctx.effective_tenant,
                created_by=ctx.created_by,
            )

    def _persist_stage(self, ctx: PipelineContext, rec: StageExecutionRecord) -> None:
        save_stage = getattr(self.storage, "save_stage_state", None)
        if save_stage is None or not ctx.task_id:
            return
        save_stage(
            task_id=ctx.task_id,
            stage_name=rec.stage_name,
            stage_order=rec.stage_order,
            status=rec.status,
            attempt=rec.attempt,
            started_at=rec.started_at,
            finished_at=rec.finished_at,
            duration_ms=rec.duration_ms,
            detail=rec.detail,
            error=rec.error,
        )

    def _record_terminal_task(
        self, ctx: PipelineContext, status: str, reason: str
    ) -> None:
        save_task = getattr(self.storage, "save_task", None)
        if save_task is None or not ctx.task_id:
            return
        doc_id = ctx.parsed_record.template_id if ctx.parsed_record else ""
        try:
            save_task(
                ctx.task_id,
                doc_id,
                ctx.mode,
                status,
                {"reason": reason, "source_file": ctx.docx_path.name},
                tenant_id=ctx.effective_tenant,
                created_by=ctx.created_by,
                idempotency_key=ctx.idempotency_key,
            )
        except TypeError:
            save_task(
                ctx.task_id,
                doc_id,
                ctx.mode,
                status,
                {"reason": reason, "source_file": ctx.docx_path.name},
                tenant_id=ctx.effective_tenant,
                created_by=ctx.created_by,
            )

    def _rebuild_result_from_persisted(self, task_row: dict[str, Any]) -> ReviewResult:
        task_id = task_row["task_id"]
        meta = task_row.get("meta") or {}
        issues_raw = self.storage.get_issues(task_id)
        normalized_issues: list[dict[str, Any]] = []
        for it in issues_raw:
            d = dict(it)
            if "source_ref" in d and "source_reference" not in d:
                d["source_reference"] = d.pop("source_ref")
            if "knowledge_refs" in d and "knowledge_references" not in d:
                d["knowledge_references"] = d.pop("knowledge_refs") or []
            d["requires_human_review"] = bool(d.get("requires_human_review"))
            normalized_issues.append(d)
        get_stages = getattr(self.storage, "get_stage_states", None)
        stages = get_stages(task_id) if get_stages else []
        return ReviewResult(
            task_id=task_id,
            status=task_row["status"],
            document_id=task_row.get("document_id"),
            source_file=meta.get("source_file"),
            mode=task_row.get("mode", "STANDARD"),
            category=meta.get("category"),
            subtype=meta.get("subtype"),
            classification_confidence=meta.get("classification_confidence"),
            matched_template_id=meta.get("matched_template_id"),
            tenant_id=task_row.get("tenant_id", "default"),
            created_by=task_row.get("created_by", "system"),
            llm=meta.get("llm", {}),
            rag_context=meta.get("rag_context", []),
            issues=normalized_issues,
            rule_set_version=task_row.get("rule_set_version") or "builtin-v1",
            model_version=task_row.get("model_version") or "none",
            reproducibility_hash=(
                task_row.get("reproducibility_hash")
                or meta.get("reproducibility_hash", "")
            ),
            review_gate=meta.get("review_gate", "PASS"),
            finding_breakdown=meta.get("finding_breakdown", {}),
            extracted_fields=meta.get("extracted_fields", {}),
            stages=stages,
        )
