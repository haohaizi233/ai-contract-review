# -*- coding: utf-8 -*-
"""14 阶段可组合合同审核流水线（Review Pipeline Stages）。

阶段顺序：
1. Receive -> 2. Validate -> 3. Parse -> 4. Normalize -> 5. Classify ->
6. SelectTemplate -> 7. ExtractFields -> 8. DeterministicValidation ->
9. RetrieveEvidence -> 10. LLMSemanticReview -> 11. VerifyEvidence ->
12. ResolveFindings -> 13. HumanReview -> 14. FinalizeReport
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ...domain.models.canonical_contract import (
    CanonicalContract,
    ContractAmountSummary,
    ContractLifecycleStatus,
    ContractNumber,
    ContractParty,
    SourceSystemType,
)
from ...domain.models.review import (
    CheckStatus,
    EvidenceReference,
    Finding,
    FindingSource,
    PipelineContext,
)
from ...ingest import import_template_file, security_check_file
from ...modules.rules import (
    ConflictResolver,
    PolicyEvaluator,
    RuleRegistry,
)
from ...rule_engine import ReviewContext as DbRuleContext
from ...rule_engine import RuleEngine, RuleSpec
from .checkpoints import CheckpointCorruptedError
from .format_adapter import audit_format_from_record


class ReviewStage(ABC):
    """审核阶段统一抽象基类（每个阶段具备显式输入输出契约，可独立单测）。"""

    stage_name: str = "BASE"
    stage_order: int = 0
    input_contract: tuple[str, ...] = ()
    output_contract: tuple[str, ...] = ()
    is_stateless_rehydratable: bool = False

    @abstractmethod
    def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        """执行当前阶段，更新 PipelineContext 并返回阶段脱敏摘要字典。"""

    def rehydrate(self, ctx: PipelineContext, snapshot: dict[str, Any]) -> None:
        """从持久化快照中恢复该阶段的上下文输出。默认无需操作或由具体子类实现。"""
        pass


class ReceiveStage(ReviewStage):
    """Stage 1: 接收审核请求，初始化稳定 task_id、租户上下文与模型/规则基线标识。"""

    stage_name = "Receive"
    stage_order = 1
    input_contract = ("docx_path", "mode", "effective_tenant")
    output_contract = ("task_id", "started_monotonic")
    is_stateless_rehydratable = True

    def __init__(self, llm_status_fn: Any = None):
        self._llm_status_fn = llm_status_fn

    def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        ctx.docx_path = Path(ctx.docx_path)
        if not ctx.task_id:
            if ctx.idempotency_key:
                raw = f"{ctx.effective_tenant}:{ctx.idempotency_key}"
                ctx.task_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
            else:
                ctx.task_id = uuid.uuid4().hex[:12]
        if ctx.started_monotonic <= 0:
            ctx.started_monotonic = time.monotonic()
        if self._llm_status_fn is not None:
            ctx.llm_status_info = self._llm_status_fn()
            ctx.model_version = str(ctx.llm_status_info.get("provider") or "none")
        return {
            "task_id": ctx.task_id,
            "mode": ctx.mode,
            "tenant_id": ctx.effective_tenant,
            "model_version": ctx.model_version,
            "llm_status_info": ctx.llm_status_info,
        }

    def rehydrate(self, ctx: PipelineContext, snapshot: dict[str, Any]) -> None:
        if "task_id" in snapshot and not ctx.task_id:
            ctx.task_id = snapshot["task_id"]
        if "model_version" in snapshot:
            ctx.model_version = snapshot["model_version"]
        if "llm_status_info" in snapshot:
            ctx.llm_status_info = snapshot["llm_status_info"]


class ValidateStage(ReviewStage):
    """Stage 2: 导入文件安全与基本格式准入校验（ZIP Bomb / 路径穿越 / XXE / 扩展名 / 大小）。"""

    stage_name = "Validate"
    stage_order = 2
    input_contract = ("docx_path",)
    output_contract = ("security_result", "short_circuit_status")
    is_stateless_rehydratable = True

    def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        sec = security_check_file(ctx.docx_path)
        ctx.security_result = sec
        if not sec.passed:
            ctx.short_circuit_status = "REJECTED"
            ctx.short_circuit_reason = f"security_check_failed: {sec.rejected_reason}"
            return {"passed": False, "reason": ctx.short_circuit_reason}
        return {"passed": True, "size_bytes": getattr(sec, "size_bytes", 0)}

    def rehydrate(self, ctx: PipelineContext, snapshot: dict[str, Any]) -> None:
        if not snapshot.get("passed", True):
            ctx.short_circuit_status = "REJECTED"
            ctx.short_circuit_reason = snapshot.get("reason") or "security_check_failed"


class ParseStage(ReviewStage):
    """Stage 3: 安全 XML 解析 DOCX 文档结构（段落/表格/页眉页脚/条款树）。"""

    stage_name = "Parse"
    stage_order = 3
    input_contract = ("docx_path",)
    output_contract = ("parsed_record",)
    is_stateless_rehydratable = True

    def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        rec, err = import_template_file(ctx.docx_path)
        if rec is None:
            ctx.short_circuit_status = "PARSE_FAILED"
            ctx.short_circuit_reason = err or "parse_failed"
            return {"parsed": False, "reason": ctx.short_circuit_reason}
        ctx.parsed_record = rec
        return {
            "parsed": True,
            "document_id": rec.template_id,
            "sha256": rec.sha256[:16],
            "full_sha256": rec.sha256,
            "clause_count": len(rec.clauses or []),
        }

    def rehydrate(self, ctx: PipelineContext, snapshot: dict[str, Any]) -> None:
        if ctx.parsed_record is None:
            if ctx.docx_path and Path(ctx.docx_path).exists():
                rec, err = import_template_file(ctx.docx_path)
                if rec is None:
                    raise CheckpointCorruptedError(
                        f"Failed to re-parse document on rehydration: {err}"
                    )
                ctx.parsed_record = rec
            else:
                raise CheckpointCorruptedError(
                    "Cannot rehydrate ParseStage: parsed_record is missing and file does not exist"
                )


class NormalizeStage(ReviewStage):
    """Stage 4: 标准化条款列表、收集有效 AST 节点 ID 集合与计算复现种子。"""

    stage_name = "Normalize"
    stage_order = 4
    input_contract = ("parsed_record",)
    output_contract = ("clauses", "valid_node_ids", "reproducibility_seed")
    is_stateless_rehydratable = True

    def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        rec = ctx.parsed_record
        raw_clauses = rec.clauses or []
        if raw_clauses and isinstance(raw_clauses[0], dict):
            clauses = list(raw_clauses)
        else:
            clauses = [
                {
                    "clause_id": c["clause_id"],
                    "clause_number": c["clause_number"],
                    "article_title": c["article_title"],
                    "clause_category": c["clause_category"],
                    "clause_subcategory": c["clause_subcategory"],
                    "original_text": c["original_text"],
                    "source_node_ids": c["source_node_ids"],
                    "source_locator": c["source_locator"],
                }
                for c in raw_clauses
            ]
        ctx.clauses = clauses
        valid_ids: set[str] = set()
        for c in clauses:
            valid_ids.update(c.get("source_node_ids", []) or [])
        ctx.valid_node_ids = valid_ids
        ctx.reproducibility_seed = (
            f"{rec.sha256}|{ctx.mode}|{','.join(sorted(ctx.custom_requirements))}"
        )
        return {
            "normalized_clauses": len(clauses),
            "valid_node_count": len(valid_ids),
            "clauses": clauses,
            "valid_node_ids": list(valid_ids),
            "reproducibility_seed": ctx.reproducibility_seed,
        }

    def rehydrate(self, ctx: PipelineContext, snapshot: dict[str, Any]) -> None:
        if "clauses" in snapshot:
            ctx.clauses = list(snapshot["clauses"])
            ctx.valid_node_ids = set(snapshot.get("valid_node_ids", []))
            ctx.reproducibility_seed = str(snapshot.get("reproducibility_seed", ""))
        elif ctx.parsed_record is not None:
            self.execute(ctx)
        else:
            raise CheckpointCorruptedError(
                "Cannot rehydrate NormalizeStage: missing clauses in snapshot and parsed_record unavailable"
            )


class ClassifyStage(ReviewStage):
    """Stage 5: 合同五层业务分类评估与低置信度人工确认守卫。"""

    stage_name = "Classify"
    stage_order = 5
    input_contract = ("parsed_record",)
    output_contract = ("category", "subtype", "classification_confidence")
    is_stateless_rehydratable = True

    def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        rec = ctx.parsed_record
        ctx.category = rec.category or ""
        ctx.subtype = rec.subtype or ""
        ctx.classification_confidence = float(rec.classification_confidence or 0.0)
        ctx.classification_conflicts = list(rec.classification_conflicts or [])

        if ctx.classification_confidence < 0.7 or ctx.classification_conflicts:
            ctx.findings.append(
                Finding(
                    issue_id="",
                    rule_id=None,
                    severity="LOW",
                    category="CLASSIFICATION_UNCERTAIN",
                    title="合同业务类型识别不确定",
                    description=(
                        f"识别结果 {ctx.subtype} 置信度 {ctx.classification_confidence}，"
                        f"冲突：{ctx.classification_conflicts or '无'}，建议人工确认业务类型"
                    ),
                    actual_value={
                        "subtype": ctx.subtype,
                        "confidence": ctx.classification_confidence,
                    },
                    execution_mode="DETERMINISTIC",
                    finding_source=FindingSource.DETERMINISTIC.value,
                    requires_human_review=True,
                    check_status=CheckStatus.NEEDS_MANUAL_REVIEW.value,
                )
            )
        return {
            "category": ctx.category,
            "subtype": ctx.subtype,
            "confidence": ctx.classification_confidence,
            "conflicts": ctx.classification_conflicts,
            "findings": [f.to_dict() for f in ctx.findings],
        }

    def rehydrate(self, ctx: PipelineContext, snapshot: dict[str, Any]) -> None:
        if "category" not in snapshot or "subtype" not in snapshot:
            raise CheckpointCorruptedError(
                "ClassifyStage snapshot corrupted: missing category or subtype"
            )
        ctx.category = str(snapshot["category"])
        ctx.subtype = str(snapshot["subtype"])
        ctx.classification_confidence = float(snapshot.get("confidence", 0.0))
        ctx.classification_conflicts = list(snapshot.get("conflicts", []))
        if "findings" in snapshot:
            ctx.findings = [Finding.from_dict(f) for f in snapshot["findings"]]


class SelectTemplateStage(ReviewStage):
    """Stage 6: 在租户隔离边界内查找当前生效（ACTIVE）的标准模板版本。"""

    stage_name = "SelectTemplate"
    stage_order = 6
    input_contract = ("subtype", "scope_tenant")
    output_contract = ("matched_template",)

    def __init__(self, storage: Any):
        self.storage = storage

    def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        matched = self.storage.find_active_template(
            contract_subtype=ctx.subtype, tenant_id=ctx.scope_tenant
        )
        ctx.matched_template = matched
        if matched is None:
            ctx.findings.append(
                Finding(
                    issue_id="",
                    rule_id=None,
                    severity="MEDIUM",
                    category="TEMPLATE_NOT_FOUND",
                    title="未匹配到有效标准模板版本",
                    description=(
                        f"按业务类型 {ctx.subtype} 未在知识库找到对应标准模板，"
                        "无法执行模板差异比对；其他确定性检查继续执行"
                    ),
                    actual_value=ctx.subtype,
                    execution_mode="DETERMINISTIC",
                    finding_source=FindingSource.DETERMINISTIC.value,
                    requires_human_review=True,
                    check_status=CheckStatus.INSUFFICIENT_EVIDENCE.value,
                )
            )
        return {
            "matched_template": matched,
            "matched_template_id": matched["document_id"] if matched else None,
            "findings": [f.to_dict() for f in ctx.findings],
        }

    def rehydrate(self, ctx: PipelineContext, snapshot: dict[str, Any]) -> None:
        ctx.matched_template = snapshot.get("matched_template")
        if "findings" in snapshot:
            ctx.findings = [Finding.from_dict(f) for f in snapshot["findings"]]


class ExtractFieldsStage(ReviewStage):
    """Stage 7: 结构化抽取合同关键要素（合同编号、甲乙方主体、大小写金额、信用代码）。"""

    stage_name = "ExtractFields"
    stage_order = 7
    input_contract = ("clauses",)
    output_contract = ("extracted_fields",)
    is_stateless_rehydratable = True

    def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        full_text = "\n".join(c.get("original_text", "") for c in ctx.clauses)
        contract_no = None
        m_no = re.search(r"合同编号[:：]\s*([A-Za-z0-9\-_]+)", full_text)
        if m_no:
            contract_no = m_no.group(1).strip()

        party_a = None
        m_pa = re.search(
            r"甲方(?:[（(][^）)]*[）)])?\s*[:：]\s*([^\s\n，。；]{2,40})", full_text
        )
        if m_pa:
            party_a = m_pa.group(1).strip()

        party_b = None
        m_pb = re.search(
            r"乙方(?:[（(][^）)]*[）)])?\s*[:：]\s*([^\s\n，。；]{2,40})", full_text
        )
        if m_pb:
            party_b = m_pb.group(1).strip()

        amounts = re.findall(r"¥\s*([\d,]+(?:\.\d+)?)\s*元", full_text)
        ctx.extracted_fields = {
            "contract_number": contract_no,
            "party_a": party_a,
            "party_b": party_b,
            "detected_amounts_raw": amounts[:10],
        }
        return {
            "extracted_fields": dict(ctx.extracted_fields),
            "has_contract_number": bool(contract_no),
            "has_party_a": bool(party_a),
            "has_party_b": bool(party_b),
            "amount_mentions": len(amounts),
        }

    def rehydrate(self, ctx: PipelineContext, snapshot: dict[str, Any]) -> None:
        if "extracted_fields" not in snapshot:
            raise CheckpointCorruptedError(
                "ExtractFieldsStage snapshot corrupted: missing extracted_fields"
            )
        ctx.extracted_fields.update(snapshot["extracted_fields"])


class DeterministicValidationStage(ReviewStage):
    """Stage 8: 确定性规则策略执行 + 格式标准治理审核 + 已审批规则引擎 + 标准模板差异比对。"""

    stage_name = "DeterministicValidation"
    stage_order = 8
    input_contract = ("parsed_record", "clauses", "subtype")
    output_contract = ("rule_set_version", "findings")

    def __init__(
        self,
        storage: Any,
        registry: RuleRegistry | None = None,
        policy_evaluator: PolicyEvaluator | None = None,
    ):
        self.storage = storage
        self.registry = registry or RuleRegistry(include_defaults=True)
        self.policy_evaluator = policy_evaluator or PolicyEvaluator()

    def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        before_count = len(ctx.findings)

        # 1. 策略红线校验：拦截试图豁免 CORE_RULE_IDS 的自定义要求
        if ctx.mode in ("CUSTOM", "COMBINED") and ctx.custom_requirements:
            outcome = self.policy_evaluator.evaluate_custom_requirements(
                ctx.custom_requirements
            )
            ctx.findings.extend(outcome.blocked_override_findings)
            ctx.extracted_fields["_sanitized_custom_requirements"] = (
                outcome.sanitized_custom_requirements
            )
            ctx.extracted_fields["_blocked_custom_count"] = len(
                outcome.blocked_override_findings
            )
        else:
            ctx.extracted_fields["_sanitized_custom_requirements"] = []
            ctx.extracted_fields["_blocked_custom_count"] = 0

        # 2. 内置确定性规则策略执行（金额 Decimal、回填编号、主体一致性、必备条款等）
        ctx.findings.extend(
            self.registry.execute_all(ctx.parsed_record, ctx.clauses, ctx.subtype)
        )

        # 3. Word 格式审核（仅 ACTIVE 已审批标准可产生正式违规结论）
        ctx.findings.extend(self._run_format_audit(ctx))

        # 4. 数据库规则引擎执行（仅 APPROVED 强制执行，DRAFT 提示未审批）
        raw_rules = self.storage.get_rules(tenant_id=ctx.scope_tenant)
        ctx.rule_set_version = self.registry.compute_rule_set_version(raw_rules)
        ctx.findings.extend(self._run_db_rules(ctx, raw_rules))

        # 5. 标准模板条款级差异比对
        if ctx.mode in ("STANDARD", "COMBINED") and ctx.matched_template:
            ctx.findings.extend(self._diff_vs_template(ctx))

        return {
            "rule_set_version": ctx.rule_set_version,
            "deterministic_findings_added": len(ctx.findings) - before_count,
            "findings": [f.to_dict() for f in ctx.findings],
            "extracted_fields": dict(ctx.extracted_fields),
            "format_standard": ctx.format_standard,
        }

    def rehydrate(self, ctx: PipelineContext, snapshot: dict[str, Any]) -> None:
        if "findings" not in snapshot or "rule_set_version" not in snapshot:
            raise CheckpointCorruptedError(
                "DeterministicValidationStage snapshot corrupted or missing mandatory 'findings'/'rule_set_version'"
            )
        ctx.rule_set_version = str(snapshot["rule_set_version"])
        ctx.findings = [Finding.from_dict(f) for f in snapshot["findings"]]
        if "extracted_fields" in snapshot and isinstance(
            snapshot["extracted_fields"], dict
        ):
            ctx.extracted_fields.update(snapshot["extracted_fields"])
        if "format_standard" in snapshot:
            ctx.format_standard = snapshot["format_standard"]

    def _run_format_audit(self, ctx: PipelineContext) -> list[Finding]:
        out: list[Finding] = []
        std_rec = None
        get_active = getattr(self.storage, "get_active_format_standard", None)
        if get_active:
            try:
                std_rec = get_active(tenant_id=ctx.scope_tenant)
            except TypeError:
                std_rec = get_active()
        if std_rec is None:
            get_latest = getattr(self.storage, "get_format_standard", None)
            if get_latest:
                try:
                    std_rec = get_latest("company_format", tenant_id=ctx.scope_tenant)
                except TypeError:
                    std_rec = get_latest("company_format")
        ctx.format_standard = std_rec
        fmt = audit_format_from_record(ctx.parsed_record, standard_record=std_rec)
        std_meta = fmt.get("standard") or {}
        std_note = (
            f"（依据标准 {std_meta.get('standard_id')} v{std_meta.get('version')}，"
            f"状态 {std_meta.get('status')}）"
            if std_meta
            else ""
        )
        for fi in fmt["issues"]:
            if fi["issue_type"] == "NON_COMPLIANT" and fmt.get("binding"):
                sev = "MEDIUM" if fi.get("severity") == "MEDIUM" else "LOW"
                out.append(
                    Finding(
                        issue_id="",
                        rule_id=f"FORMAT:{fi['check']}",
                        severity=sev,
                        category="FORMAT_NON_COMPLIANT",
                        title=f"格式不符标准：{fi['check']}",
                        description=fi["description"] + std_note,
                        expected_value=fi.get("expected"),
                        actual_value=fi.get("actual"),
                        execution_mode="DETERMINISTIC",
                        finding_source=FindingSource.DETERMINISTIC.value,
                        requires_human_review=False,
                    )
                )
            elif fi["issue_type"] == "NON_COMPLIANT":
                out.append(
                    Finding(
                        issue_id="",
                        rule_id=f"FORMAT:{fi['check']}",
                        severity="LOW",
                        category="FORMAT_UNVERIFIED_STANDARD",
                        title=f"格式与示例标准差异（标准未审批）：{fi['check']}",
                        description=(
                            fi["description"]
                            + "。所依据格式标准未经审批（示例基线），仅提示差异，不构成正式企业违规结论"
                            + std_note
                        ),
                        expected_value=fi.get("expected"),
                        actual_value=fi.get("actual"),
                        execution_mode="DETERMINISTIC",
                        finding_source=FindingSource.DETERMINISTIC.value,
                        requires_human_review=True,
                        check_status=CheckStatus.NEEDS_MANUAL_REVIEW.value,
                    )
                )
            elif fi["issue_type"] == "TEMPLATE_DIFF":
                out.append(
                    Finding(
                        issue_id="",
                        rule_id=f"FORMAT:{fi['check']}",
                        severity="LOW",
                        category="FORMAT_TEMPLATE_DIFF",
                        title=f"格式与模板差异：{fi['check']}",
                        description=fi["description"],
                        expected_value=fi.get("expected"),
                        actual_value=fi.get("actual"),
                        execution_mode="DETERMINISTIC",
                        finding_source=FindingSource.DETERMINISTIC.value,
                        requires_human_review=True,
                        check_status=CheckStatus.NEEDS_MANUAL_REVIEW.value,
                    )
                )
            else:
                out.append(
                    Finding(
                        issue_id="",
                        rule_id=f"FORMAT:{fi['check']}",
                        severity="INFO",
                        category="FORMAT_UNREADABLE",
                        title=f"格式信息不可读：{fi['check']}",
                        description=fi["description"],
                        execution_mode="DETERMINISTIC",
                        finding_source=FindingSource.DETERMINISTIC.value,
                        requires_human_review=True,
                        check_status=CheckStatus.INSUFFICIENT_EVIDENCE.value,
                    )
                )
        return out

    def _run_db_rules(
        self, ctx: PipelineContext, raw_rules: list[dict[str, Any]]
    ) -> list[Finding]:
        out: list[Finding] = []
        specs: list[RuleSpec] = []
        skipped_bad: list[str] = []
        for r in raw_rules:
            try:
                specs.append(
                    RuleSpec(
                        rule_id=r["rule_id"],
                        version=r.get("version", 1),
                        name=r.get("name", ""),
                        rule_type=r["rule_type"],
                        execution_mode=r.get("execution_mode", "DETERMINISTIC"),
                        severity=r.get("severity", "MEDIUM"),
                        priority=r.get(
                            "priority", r.get("params", {}).get("priority", 100)
                        ),
                        contract_types=r.get("contract_types", ["*"]),
                        parameters=r.get("params", {}) or {},
                        exceptions=r.get("params", {}).get("exceptions", []) or [],
                        source_clauses=r.get("source_clauses", []),
                        status=r.get("status", "DRAFT"),
                        approved_by=r.get("approved_by"),
                        effective_at=r.get("effective_at"),
                        effective_until=r.get("effective_until")
                        or r.get("params", {}).get("effective_until"),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                skipped_bad.append(
                    f"{r.get('rule_id', '?')}: {type(exc).__name__}: {exc}"
                )
        if skipped_bad:
            self.storage.log_audit(
                "system",
                "rule_spec_invalid",
                "rule_engine",
                "; ".join(skipped_bad)[:500],
            )
        rule_ctx = DbRuleContext(
            document_id=ctx.parsed_record.template_id,
            subtype=ctx.subtype,
            category=ctx.category,
            clauses=ctx.clauses,
        )
        results = RuleEngine().execute(specs, rule_ctx)
        for res in results:
            self.storage.record_rule_execution(
                exec_id=f"{res.rule_id}:{res.rule_version}:{res.executed_at:.3f}:{uuid.uuid4().hex[:6]}",
                rule_id=res.rule_id,
                rule_version=res.rule_version,
                rule_type=res.rule_type,
                result=res.result,
                severity=res.severity,
                message=res.message,
                evidence=res.evidence,
                executed_at=res.executed_at,
            )
            if res.result == "FAIL":
                node_ids = res.evidence.get("node_ids", []) or []
                ev_text = ""
                if node_ids:
                    for c in ctx.clauses:
                        if set(node_ids) & set(c.get("source_node_ids", []) or []):
                            ev_text = c.get("original_text", "")[:200]
                            break
                out.append(
                    Finding(
                        issue_id="",
                        rule_id=res.rule_id,
                        severity=res.severity,
                        category="RULE_VIOLATION",
                        title=f"规则不通过：{res.rule_id}",
                        description=f"[v{res.rule_version}] {res.message}",
                        source_reference=(
                            EvidenceReference(
                                ctx.parsed_record.template_id,
                                node_ids,
                                res.evidence.get("clause_number"),
                                ev_text or res.message,
                            )
                            if node_ids
                            else None
                        ),
                        execution_mode="DETERMINISTIC",
                        finding_source=FindingSource.DETERMINISTIC.value,
                        requires_human_review=True,
                    )
                )
            elif res.result == "MANUAL_REQUIRED":
                out.append(
                    Finding(
                        issue_id="",
                        rule_id=res.rule_id,
                        severity="LOW",
                        category="MANUAL_REVIEW",
                        title=f"规则需人工复核：{res.rule_id}",
                        description=f"[v{res.rule_version}] {res.message}",
                        execution_mode="MANUAL_REVIEW",
                        finding_source=FindingSource.DETERMINISTIC.value,
                        requires_human_review=True,
                        check_status=CheckStatus.NEEDS_MANUAL_REVIEW.value,
                    )
                )
            elif res.result == "SKIPPED_DRAFT":
                out.append(
                    Finding(
                        issue_id="",
                        rule_id=res.rule_id,
                        severity="INFO",
                        category="RULE_PENDING_APPROVAL",
                        title=f"规则未审批，未强制执行：{res.rule_id}",
                        description=f"[v{res.rule_version}] {res.message}",
                        execution_mode="DETERMINISTIC",
                        finding_source=FindingSource.DETERMINISTIC.value,
                        requires_human_review=False,
                        check_status=CheckStatus.NOT_CHECKED.value,
                    )
                )
        return out

    def _diff_vs_template(self, ctx: PipelineContext) -> list[Finding]:
        out: list[Finding] = []
        matched = ctx.matched_template
        if not matched:
            return out
        tpl_clauses = self.storage.get_document_clauses(
            matched["document_id"], tenant_id=ctx.scope_tenant
        )
        tpl_by_cat: dict[str, list[dict[str, Any]]] = {}
        for t in tpl_clauses:
            tpl_by_cat.setdefault(t["clause_category"], []).append(t)
        cat_present = {c["clause_category"] for c in ctx.clauses}
        for cat, tlist in tpl_by_cat.items():
            if cat not in cat_present and cat in (
                "CONFIDENTIALITY",
                "LIABILITY",
                "DISPUTE_RESOLUTION",
            ):
                out.append(
                    Finding(
                        issue_id="",
                        rule_id="TEMPLATE_DIFF",
                        severity="MEDIUM",
                        category="CLAUSE_MISSING",
                        title=f"相对标准模板缺少条款类别：{cat}",
                        description=(
                            f"标准模板（{matched['source_file']}）含 {cat} 类条款，待审文档未发现"
                        ),
                        expected_value=cat,
                        actual_value=None,
                        knowledge_references=[
                            {
                                "document_id": matched["document_id"],
                                "text": tlist[0]["original_text"][:100],
                                "source_type": "TEMPLATE",
                            }
                        ],
                        execution_mode="DETERMINISTIC",
                        finding_source=FindingSource.DETERMINISTIC.value,
                        requires_human_review=True,
                        check_status=CheckStatus.INSUFFICIENT_EVIDENCE.value,
                    )
                )
        return out


class RetrieveEvidenceStage(ReviewStage):
    """Stage 9: 结构感知多粒度索引 + 条款驱动 Query Planning 混合证据检索（严格遵守租户与 ACTIVE 状态）。"""

    stage_name = "RetrieveEvidence"
    stage_order = 9
    input_contract = ("subtype", "scope_tenant")
    output_contract = ("rag_context",)

    def __init__(self, storage: Any):
        self.storage = storage

    def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        from ...modules.retrieval import GovernedRetrievalService
        from ...modules.retrieval.hierarchy import HierarchicalKnowledgeTree
        from ...modules.retrieval.query_planner import ContractAwareQueryPlanner

        # 1. 构建结构感知条款层级树
        doc_id = (
            ctx.parsed_record.template_id if ctx.parsed_record else "target_contract"
        )
        if ctx.clauses:
            tree = HierarchicalKnowledgeTree.build_from_clauses(
                document_id=doc_id,
                clauses=ctx.clauses,
                business_category=ctx.category,
                contract_subtype=ctx.subtype,
            )
        else:
            tree = HierarchicalKnowledgeTree(
                document_id=doc_id,
                business_category=ctx.category,
                contract_subtype=ctx.subtype,
            )
        ctx.knowledge_tree = tree

        # 2. 条款驱动动态 Query Planning 与 EvidenceBundle 检索
        svc = GovernedRetrievalService(self.storage)
        planner = ContractAwareQueryPlanner()
        bundles = svc.retrieve_contract_evidence_bundles(
            ctx.clauses or [],
            top_k_per_clause=2,
            tenant_id=ctx.scope_tenant,
            contract_category=ctx.category,
            contract_subtype=ctx.subtype,
            planner=planner,
            knowledge_tree=tree,
        )
        ctx.evidence_bundles = {
            k: (b.to_dict() if hasattr(b, "to_dict") else b) for k, b in bundles.items()
        }

        # 3. 聚合装配 rag_context 并保持全兼容
        rag_context: list[dict[str, Any]] = []
        seen_clauses: set[str] = set()
        verified_ratios: list[float] = []

        for b in bundles.values():
            if b.citation_summary is not None and b.citation_summary.total_checked > 0:
                verified_ratios.append(b.citation_summary.citation_correctness)
            for h in b.authoritative_clauses:
                cid = h.get("clause_id") or h.get("chunk_id") or ""
                if not cid or cid in seen_clauses:
                    continue
                seen_clauses.add(cid)
                query_text = (
                    b.intents[0].semantic_queries[0]
                    if (b.intents and b.intents[0].semantic_queries)
                    else (h.get("clause_category") or "合同条款")
                )
                rag_context.append(
                    {
                        "query": query_text,
                        "clause_id": cid,
                        "document_id": h.get("document_id", ""),
                        "clause_category": h.get("clause_category"),
                        "knowledge_type": h.get("knowledge_type", ""),
                        "approved_status": h.get("approved_status", ""),
                        "authority_rank": h.get("authority_rank", 2),
                        "can_drive_mandatory_finding": bool(
                            h.get("can_drive_mandatory_finding", False)
                        ),
                        "prompt_badge": h.get("prompt_badge", ""),
                        "text": (h.get("text") or h.get("original_text") or "")[:160],
                        "tenant_id": h.get("tenant_id", ""),
                        "source_hash": h.get("source_hash", ""),
                    }
                )

        # 兜底回退：若针对性条款未召回任何上下文，执行基础关键字兜底
        if not rag_context:
            for kw in ("费用及支付方式", "违约责任", "争议解决", "保密"):
                legacy_bundle = svc.retrieve_for_review(
                    kw,
                    top_k=3,
                    business_category=ctx.category,
                    contract_subtype=ctx.subtype,
                    tenant_id=ctx.scope_tenant,
                    include_historical_cases=False,
                )
                if (
                    legacy_bundle.citation_summary is not None
                    and legacy_bundle.citation_summary.total_checked > 0
                ):
                    verified_ratios.append(
                        legacy_bundle.citation_summary.citation_correctness
                    )
                for h in legacy_bundle.authoritative_hits[:2]:
                    cid = h["clause_id"]
                    if cid in seen_clauses:
                        continue
                    seen_clauses.add(cid)
                    rag_context.append(
                        {
                            "query": kw,
                            "clause_id": cid,
                            "document_id": h["document_id"],
                            "clause_category": h.get("clause_category"),
                            "knowledge_type": h.get("knowledge_type", ""),
                            "approved_status": h.get("approved_status", ""),
                            "authority_rank": h.get("authority_rank", 2),
                            "can_drive_mandatory_finding": bool(
                                h.get("can_drive_mandatory_finding", False)
                            ),
                            "prompt_badge": h.get("prompt_badge", ""),
                            "text": h["text"][:160],
                            "tenant_id": h.get("tenant_id", ""),
                            "source_hash": h.get("source_hash", ""),
                        }
                    )

        ctx.rag_context = rag_context
        avg_cit = (
            round(sum(verified_ratios) / len(verified_ratios), 4)
            if (verified_ratios and len(rag_context) > 0)
            else 0.0
        )
        return {
            "rag_context": list(ctx.rag_context),
            "evidence_bundles": dict(ctx.evidence_bundles),
            "retrieved_chunks": len(rag_context),
            "citation_correctness": avg_cit,
        }

    def rehydrate(self, ctx: PipelineContext, snapshot: dict[str, Any]) -> None:
        if "rag_context" in snapshot:
            ctx.rag_context = list(snapshot["rag_context"])
        if "evidence_bundles" in snapshot:
            ctx.evidence_bundles = dict(snapshot["evidence_bundles"])


class LLMSemanticReviewStage(ReviewStage):
    """Stage 10: 受控 LLM 语义审核（未授权时如实标注 NOT_CHECKED，绝不伪造已审）。"""

    stage_name = "LLMSemanticReview"
    stage_order = 10
    input_contract = ("clauses", "rag_context")
    output_contract = ("llm_result", "findings")

    def __init__(
        self,
        llm_provider: Any = None,
        llm_reviewer: Any = None,
        budget_service: Any = None,
    ):
        self._llm_provider = llm_provider
        self._llm_reviewer = llm_reviewer
        self._budget_service = budget_service

    def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        from ...llm_review import LLMFailure, LLMReviewResult, LLMSemanticReviewer

        llm = self._llm_reviewer or LLMSemanticReviewer(provider=self._llm_provider)
        contract_text = "\n".join(c.get("original_text", "") for c in ctx.clauses)
        sanitized_reqs: list[str] = (
            ctx.extracted_fields.get("_sanitized_custom_requirements", [])
            if ctx.mode in ("CUSTOM", "COMBINED")
            else []
        )
        blocked_count = int(ctx.extracted_fields.get("_blocked_custom_count", 0))

        # 被策略层拦截的自定义要求单独生成 CUSTOM_REQUIREMENT 结论留痕，绝不送入 LLM
        if blocked_count > 0 and ctx.mode in ("CUSTOM", "COMBINED"):
            ctx.findings.append(
                Finding(
                    issue_id="",
                    rule_id="CUSTOM_REQUIREMENT",
                    severity="MEDIUM",
                    category="POLICY_GUARD",
                    title="自定义要求因试图豁免核心规则被拦截",
                    description="包含试图跳过企业强制核心规则的要求，已由策略层拦截并转人工复核。",
                    execution_mode="DETERMINISTIC",
                    finding_source=FindingSource.DETERMINISTIC.value,
                    requires_human_review=True,
                    check_status=CheckStatus.NEEDS_MANUAL_REVIEW.value,
                )
            )

        # 检查 ExecutionDeadline 剩余时间
        remaining_timeout: float | None = None
        if hasattr(ctx, "get_deadline"):
            deadline = ctx.get_deadline()
            if deadline is not None:
                remaining_timeout = deadline.remaining_seconds()
                if remaining_timeout <= 0:
                    ctx.findings.append(
                        Finding(
                            issue_id="",
                            rule_id="LLM_SEMANTIC_REVIEW",
                            severity="INFO",
                            category="NOT_CHECKED",
                            title="LLM 语义审核超时未执行",
                            description=f"流水线截止时间已耗尽 (剩余 {remaining_timeout:.3f}s)，未发起网络调用",
                            execution_mode="LLM_SEMANTIC",
                            finding_source=FindingSource.LLM_SEMANTIC.value,
                            requires_human_review=True,
                            check_status=CheckStatus.NOT_CHECKED.value,
                        )
                    )
                    ctx.llm_result = LLMReviewResult(
                        status="FAILED", failure=LLMFailure.TIMEOUT.value
                    )
                    return {
                        "llm_status": "FAILED",
                        "semantic_findings": 0,
                        "failure": LLMFailure.TIMEOUT.value,
                        "findings": [f.to_dict() for f in ctx.findings],
                    }

        # 调用前预算预留检查 (Pre-invocation Budget Reservation)
        budget_svc = self._budget_service or getattr(ctx, "budget_service", None)
        reservation = None
        max_output_tokens = 4000
        if budget_svc is not None:
            from ...application.workflow.budget import (
                BudgetLimitExceededError,
                TokenUsage,
            )

            est_input_tokens = max(100, len(contract_text) // 2 + 500)
            try:
                reservation = budget_svc.reserve_budget(
                    input_tokens=est_input_tokens,
                    max_output_tokens=max_output_tokens,
                )
            except BudgetLimitExceededError as exc:
                try:
                    from ...infrastructure.observability import GLOBAL_METRICS

                    GLOBAL_METRICS._counters["budget_exhaustions"] += 1
                except Exception:
                    pass
                ctx.findings.append(
                    Finding(
                        issue_id="",
                        rule_id="LLM_BUDGET_EXCEEDED",
                        severity="INFO",
                        category="BUDGET_EXCEEDED",
                        title="LLM 预算不足，未执行语义审核",
                        description=f"预算账本拦截：{exc}",
                        execution_mode="LLM_SEMANTIC",
                        finding_source=FindingSource.LLM_SEMANTIC.value,
                        requires_human_review=True,
                        check_status=CheckStatus.NOT_CHECKED.value,
                    )
                )
                ctx.llm_result = LLMReviewResult(
                    status="NOT_CHECKED",
                    failure="BUDGET_EXCEEDED",
                )
                return {
                    "llm_status": "NOT_CHECKED",
                    "semantic_findings": 0,
                    "failure": "BUDGET_EXCEEDED",
                    "findings": [f.to_dict() for f in ctx.findings],
                }

        # 结构化条款差异比对（Structured Clause Comparison）
        from ...modules.retrieval.comparison import ClauseComparisonEngine

        engine = ClauseComparisonEngine()
        comparisons: list[dict[str, Any]] = []
        comparison_findings: list[Finding] = []

        bundles = ctx.evidence_bundles or {}
        for c in ctx.clauses:
            cid = str(c.get("clause_id") or "")
            b = bundles.get(cid)
            if not b:
                continue
            auth_clauses = (
                b.get("authoritative_clauses", [])
                if isinstance(b, dict)
                else getattr(b, "authoritative_clauses", [])
            )
            if not auth_clauses:
                continue
            top_ref = auth_clauses[0]
            comp = engine.compare_clauses(c, top_ref)
            comparisons.append(comp.model_dump())

            if comp.conclusion not in ("MATCH", "STRUCTURAL_DIFFERENCE"):
                kref = {
                    "document_id": top_ref.get("document_id"),
                    "clause_id": top_ref.get("clause_id"),
                    "clause_number": top_ref.get("clause_number"),
                    "text": (top_ref.get("original_text") or top_ref.get("text") or "")[
                        :120
                    ],
                    "source_type": top_ref.get("knowledge_type", ""),
                    "tenant_id": top_ref.get("tenant_id") or ctx.scope_tenant,
                    "approved_status": top_ref.get("approved_status") or "APPROVED",
                }
                node_ids = list(c.get("source_node_ids") or [])
                ref_evidence = (
                    EvidenceReference(
                        document_id=ctx.parsed_record.template_id
                        if ctx.parsed_record
                        else "",
                        node_ids=node_ids,
                        clause_number=c.get("clause_number"),
                        original_text=str(c.get("original_text") or "")[:120],
                        exact_quote=str(c.get("original_text") or "")[:120],
                    )
                    if node_ids
                    else None
                )
                desc = (
                    "；".join(comp.summary_findings)
                    if comp.summary_findings
                    else f"条款与企业标准存在结构性差异：{comp.conclusion}"
                )
                comparison_findings.append(
                    Finding(
                        issue_id="",
                        rule_id=f"COMPARISON:{comp.conclusion}",
                        severity="LOW",
                        category="MANUAL_REVIEW",
                        title=f"标准条款差异比对：{comp.conclusion}",
                        description=desc,
                        source_reference=ref_evidence,
                        knowledge_references=[kref],
                        execution_mode="MANUAL_REVIEW",
                        finding_source=FindingSource.DETERMINISTIC.value,
                        requires_human_review=True,
                        check_status=CheckStatus.NEEDS_MANUAL_REVIEW.value,
                    )
                )

        ctx.clause_comparisons = comparisons

        try:
            llm_result = llm.review(
                contract_text,
                ctx.rag_context,
                sanitized_reqs,
                timeout=remaining_timeout,
                max_tokens=max_output_tokens,
            )
        except TypeError:
            llm_result = llm.review(contract_text, ctx.rag_context, sanitized_reqs)
        except TimeoutError:
            llm_result = LLMReviewResult(
                status="FAILED", failure=LLMFailure.TIMEOUT.value
            )
        except Exception:
            llm_result = LLMReviewResult(
                status="FAILED", failure=LLMFailure.UNKNOWN.value
            )
        ctx.llm_result = llm_result

        # 核销或处理预算
        if reservation is not None and budget_svc is not None:
            from ...application.workflow.budget import TokenUsage

            if llm_result.status == "CHECKED":
                raw_usage = getattr(llm_result, "raw_usage", {}) or {}
                inp = (
                    raw_usage.get("prompt_tokens")
                    or raw_usage.get("input_tokens")
                    or reservation.input_tokens
                )
                out = (
                    raw_usage.get("completion_tokens")
                    or raw_usage.get("output_tokens")
                    or 100
                )
                try:
                    budget_svc.settle_budget(
                        reservation.reservation_id,
                        TokenUsage(input_tokens=int(inp), output_tokens=int(out)),
                    )
                except Exception:
                    budget_svc.mark_uncertain(reservation.reservation_id)
            elif llm_result.failure == LLMFailure.TIMEOUT.value:
                budget_svc.mark_uncertain(reservation.reservation_id)
            else:
                budget_svc.release_budget(reservation.reservation_id)

        if llm_result.status == "NOT_CHECKED":
            ctx.findings.append(
                Finding(
                    issue_id="",
                    rule_id="LLM_SEMANTIC_REVIEW",
                    severity="INFO",
                    category="NOT_CHECKED",
                    title="LLM 语义审核未执行",
                    description=(
                        "当前环境未配置授权 LLM 提供商。依据数据治理要求，"
                        "未经配置不得向外部大模型传输合同内容。语义级审查标记为 NOT_CHECKED，"
                        "本次结果仅包含确定性规则检查。"
                    ),
                    execution_mode="LLM_SEMANTIC",
                    finding_source=FindingSource.LLM_SEMANTIC.value,
                    requires_human_review=True,
                    check_status=CheckStatus.NOT_CHECKED.value,
                )
            )
            for req in sanitized_reqs:
                ctx.findings.append(
                    Finding(
                        issue_id="",
                        rule_id="CUSTOM_REQUIREMENT",
                        severity="LOW",
                        category="MANUAL_REVIEW",
                        title=f"自定义要求未分析：{req[:30]}",
                        description=(
                            f"要求『{req}』需 LLM 语义分析，当前未配置 LLM，"
                            "标记 NOT_CHECKED，需人工复核。"
                        ),
                        execution_mode="MANUAL_REVIEW",
                        finding_source=FindingSource.DETERMINISTIC.value,
                        requires_human_review=True,
                        check_status=CheckStatus.NOT_CHECKED.value,
                    )
                )
            # LLM 未配置时，结构化差异分析器直接产出确定性待复核发现
            ctx.findings.extend(comparison_findings)
            return {
                "llm_status": "NOT_CHECKED",
                "semantic_findings": 0,
                "comparison_findings": len(comparison_findings),
                "findings": [f.to_dict() for f in ctx.findings],
            }

        for f in llm_result.findings:
            ctx.findings.append(
                Finding(
                    issue_id="",
                    rule_id=f"LLM:{f.category}",
                    severity=f.severity,
                    category=f.category,
                    title=f.title,
                    description=f.description,
                    source_reference=(
                        EvidenceReference(
                            ctx.parsed_record.template_id if ctx.parsed_record else "",
                            [],
                            None,
                            f.original_text,
                        )
                        if f.original_text
                        else None
                    ),
                    suggestion=f.suggestion,
                    execution_mode="LLM_SEMANTIC",
                    finding_source=FindingSource.LLM_SEMANTIC.value,
                    requires_human_review=f.requires_human_review,
                    confidence=f.confidence,
                    check_status=(
                        CheckStatus.NEEDS_MANUAL_REVIEW.value
                        if f.requires_human_review
                        else CheckStatus.CHECKED.value
                    ),
                )
            )
        for cr in llm_result.custom_requirement_results:
            verdict = cr.get("verdict", "不确定")
            ctx.findings.append(
                Finding(
                    issue_id="",
                    rule_id="CUSTOM_REQUIREMENT",
                    severity="LOW" if verdict != "不满足" else "MEDIUM",
                    category="CUSTOM_REQUIREMENT_RESULT",
                    title=f"自定义要求结论：{verdict}",
                    description=(
                        f"要求『{cr.get('requirement', '')}』→ {verdict}；"
                        f"证据：{cr.get('evidence', '')}"
                    ),
                    execution_mode="LLM_SEMANTIC",
                    finding_source=FindingSource.LLM_SEMANTIC.value,
                    requires_human_review=True,
                    confidence=cr.get("confidence"),
                    check_status=CheckStatus.NEEDS_MANUAL_REVIEW.value,
                )
            )
        if llm_result.failure:
            ctx.findings.append(
                Finding(
                    issue_id="",
                    rule_id="LLM_SEMANTIC_REVIEW",
                    severity="INFO",
                    category="NOT_CHECKED",
                    title=f"LLM 审核部分失败：{llm_result.failure}",
                    description=f"失败类型 {llm_result.failure}，已降级处理，不伪造结论。",
                    execution_mode="LLM_SEMANTIC",
                    finding_source=FindingSource.LLM_SEMANTIC.value,
                    requires_human_review=True,
                    check_status=CheckStatus.NEEDS_MANUAL_REVIEW.value,
                )
            )
        return {
            "llm_status": llm_result.status,
            "semantic_findings": len(llm_result.findings),
            "failure": llm_result.failure,
            "findings": [f.to_dict() for f in ctx.findings],
        }

    def rehydrate(self, ctx: PipelineContext, snapshot: dict[str, Any]) -> None:
        if "findings" in snapshot:
            ctx.findings = [Finding.from_dict(f) for f in snapshot["findings"]]
        if "clause_comparisons" in snapshot:
            ctx.clause_comparisons = list(snapshot["clause_comparisons"])


class VerifyEvidenceStage(ReviewStage):
    """Stage 11: 原文证据与 AST 节点真实性核验（包含数据库权威回查与 TOCTOU 撤销防御）。"""

    stage_name = "VerifyEvidence"
    stage_order = 11
    input_contract = ("findings", "valid_node_ids", "clauses")
    output_contract = ("findings",)
    is_stateless_rehydratable = True

    def __init__(self, storage: Any | None = None, verifier: Any | None = None):
        self.storage = storage
        if verifier is None:
            from ...modules.retrieval.citation import CitationVerifier

            self.verifier = CitationVerifier()
        else:
            self.verifier = verifier

    def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        downgraded = 0
        bound_from_quote = 0
        for f in ctx.findings:
            ref = f.source_reference
            if ref is not None:
                # 若仅有原文引述尚无 node_ids（如 LLM 发现），尝试反查真实条款节点
                if not ref.node_ids and ref.original_text:
                    quote = ref.original_text.strip()
                    matched_clause = next(
                        (
                            c
                            for c in ctx.clauses
                            if quote and quote in c.get("original_text", "")
                        ),
                        None,
                    )
                    if matched_clause and matched_clause.get("source_node_ids"):
                        ref.node_ids = list(matched_clause["source_node_ids"])
                        ref.clause_number = matched_clause.get("clause_number")
                        ref.exact_quote = quote
                        f.citation_status = "VERIFIED"
                        bound_from_quote += 1
                    else:
                        f.source_reference = None
                        f.check_status = CheckStatus.INSUFFICIENT_EVIDENCE.value
                        f.requires_human_review = True
                        f.citation_status = "CITATION_UNVERIFIED"
                        downgraded += 1
                elif ref.node_ids and not (set(ref.node_ids) & ctx.valid_node_ids):
                    f.check_status = CheckStatus.INSUFFICIENT_EVIDENCE.value
                    f.requires_human_review = True
                    f.citation_status = "CITATION_UNVERIFIED"
                    f.description += (
                        "（证据校验：引用节点未能在解析结果中核实，已降级为证据不足）"
                    )
                    downgraded += 1
                elif not ref.node_ids:
                    f.source_reference = None
                    f.citation_status = "CITATION_UNVERIFIED"

            # 严格核验知识库引用（knowledge_references）：通过真实数据库独立回查权威状态（防 TOCTOU）
            if f.knowledge_references:
                eff_storage = self.storage or getattr(ctx, "storage_backend", None)
                for kref in f.knowledge_references:
                    if eff_storage is not None:
                        res = self.verifier.verify_evidence_item(
                            eff_storage, kref, expected_tenant_id=ctx.scope_tenant
                        )
                        if not res.verified:
                            f.check_status = CheckStatus.INSUFFICIENT_EVIDENCE.value
                            f.requires_human_review = True
                            f.citation_status = "CITATION_UNVERIFIED"
                            if res.status == "APPROVAL_REVOKED":
                                f.description += "（证据校验：引用的标准依据已被法务撤销失效，已降级为证据不足）"
                            else:
                                f.description += f"（证据校验：知识库引用权威核验未通过[{res.status}]，已降级为证据不足）"
                            if f.severity in ("HIGH", "CRITICAL"):
                                f.severity = "LOW"
                            downgraded += 1
                            break
                        # 检查来源是否允许驱动强制结论
                        ktype = str(
                            kref.get("knowledge_type") or kref.get("source_type") or ""
                        ).upper()
                        if (
                            ktype in ("LEGAL_REFERENCE", "HISTORICAL_CASE")
                            or kref.get("can_drive_mandatory_finding") is False
                        ):
                            if f.severity in ("HIGH", "CRITICAL"):
                                f.severity = "LOW"
                                f.requires_human_review = True
                                f.check_status = CheckStatus.NEEDS_MANUAL_REVIEW.value
                                f.description += "（参考依据不具备企业强制基准效力，已降级并提示人工复核）"
                    else:
                        ref_tenant = kref.get("tenant_id")
                        if (
                            ref_tenant
                            and ctx.scope_tenant
                            and ref_tenant != ctx.scope_tenant
                        ):
                            f.check_status = CheckStatus.INSUFFICIENT_EVIDENCE.value
                            f.requires_human_review = True
                            f.citation_status = "CITATION_UNVERIFIED"
                            f.description += (
                                "（证据校验：知识库引用跨租户越权，已降级）"
                            )
                            downgraded += 1
                            break
                        st = kref.get("approved_status")
                        if st != "APPROVED":
                            f.check_status = CheckStatus.INSUFFICIENT_EVIDENCE.value
                            f.requires_human_review = True
                            f.citation_status = "CITATION_UNVERIFIED"
                            if f.severity in ("HIGH", "CRITICAL"):
                                f.severity = "LOW"
                            downgraded += 1
                            break
        return {
            "downgraded_count": downgraded,
            "bound_from_quote": bound_from_quote,
            "findings": [f.to_dict() for f in ctx.findings],
        }

    def rehydrate(self, ctx: PipelineContext, snapshot: dict[str, Any]) -> None:
        if "findings" in snapshot:
            ctx.findings = [Finding.from_dict(f) for f in snapshot["findings"]]
        if "clause_comparisons" in snapshot:
            ctx.clause_comparisons = list(snapshot["clause_comparisons"])


class ResolveFindingsStage(ReviewStage):
    """Stage 12: 规则与语义发现冲突消解、认知边界守卫与确定性 issue_id 生成。"""

    stage_name = "ResolveFindings"
    stage_order = 12
    input_contract = ("findings", "reproducibility_seed", "task_id")
    output_contract = ("findings",)
    is_stateless_rehydratable = True

    def __init__(self, resolver: ConflictResolver | None = None):
        self.resolver = resolver or ConflictResolver()

    def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        before = len(ctx.findings)
        per_task_seed = f"{ctx.task_id}|{ctx.reproducibility_seed}|{ctx.rule_set_version}|{ctx.model_version}"
        ctx.findings = self.resolver.resolve(ctx.findings, per_task_seed)
        return {
            "before_count": before,
            "resolved_count": len(ctx.findings),
            "findings": [f.to_dict() for f in ctx.findings],
        }

    def rehydrate(self, ctx: PipelineContext, snapshot: dict[str, Any]) -> None:
        if "findings" in snapshot:
            ctx.findings = [Finding.from_dict(f) for f in snapshot["findings"]]
        if "clause_comparisons" in snapshot:
            ctx.clause_comparisons = list(snapshot["clause_comparisons"])


class HumanReviewStage(ReviewStage):
    """Stage 13: 人工复核门禁判定与既有人工反馈合并（重试/恢复时不丢失人工批注）。"""

    stage_name = "HumanReview"
    stage_order = 13
    input_contract = ("findings", "task_id")
    output_contract = ("review_gate", "findings")

    def __init__(self, storage: Any):
        self.storage = storage

    def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        # 若该 task_id 已存在人工覆盖记录，恢复其人工状态
        existing_issues = {
            row["issue_id"]: row for row in self.storage.get_issues(ctx.task_id)
        }
        for f in ctx.findings:
            ex = existing_issues.get(f.issue_id)
            if ex and ex.get("human_state") and ex["human_state"] != "PENDING":
                f.human_state = ex["human_state"]
                f.human_note = ex.get("human_note")
                f.finding_source = FindingSource.HUMAN.value

        has_high_blocking = any(
            f.severity == "HIGH"
            and f.human_state not in ("IGNORE", "FALSE_POSITIVE")
            and f.check_status == CheckStatus.CHECKED.value
            for f in ctx.findings
        )
        needs_human = any(
            f.requires_human_review and f.human_state == "PENDING" for f in ctx.findings
        )
        if has_high_blocking:
            ctx.review_gate = "BLOCKED_BY_HIGH_RISK"
        elif needs_human:
            ctx.review_gate = "HUMAN_REVIEW_REQUIRED"
        else:
            ctx.review_gate = "PASS"
        return {
            "review_gate": ctx.review_gate,
            "needs_human": needs_human,
            "findings": [f.to_dict() for f in ctx.findings],
        }

    def rehydrate(self, ctx: PipelineContext, snapshot: dict[str, Any]) -> None:
        ctx.review_gate = str(snapshot.get("review_gate", ctx.review_gate))
        if "findings" in snapshot:
            ctx.findings = [Finding.from_dict(f) for f in snapshot["findings"]]


class FinalizeReportStage(ReviewStage):
    """Stage 14: 计算跨运行可复现性哈希，并在单个原子工作单元（UoW）内持久化任务与全部问题。"""

    stage_name = "FinalizeReport"
    stage_order = 14
    input_contract = ("findings", "parsed_record", "task_id")
    output_contract = ("reproducibility_hash",)

    def __init__(self, storage: Any):
        self.storage = storage

    def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        # 剥离内部临时键
        public_fields = {
            k: v for k, v in ctx.extracted_fields.items() if not k.startswith("_")
        }
        ctx.extracted_fields = public_fields

        # 计算与具体 task_id 无关的可复现性指纹（同合同+同规则版本+同模型版本恒定一致）
        canonical_items = [
            {
                "rule_id": f.rule_id,
                "severity": f.severity,
                "category": f.category,
                "title": f.title,
                "expected_value": f.expected_value,
                "actual_value": f.actual_value,
                "finding_source": f.finding_source,
                "check_status": f.check_status,
            }
            for f in ctx.findings
        ]
        repro_payload = json.dumps(
            {
                "sha256": ctx.parsed_record.sha256,
                "mode": ctx.mode,
                "custom_requirements": sorted(ctx.custom_requirements),
                "rule_set_version": ctx.rule_set_version,
                "model_version": ctx.model_version,
                "findings": canonical_items,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        repro_hash = hashlib.sha256(repro_payload.encode("utf-8")).hexdigest()[:16]

        issue_dicts = [f.to_dict() for f in ctx.findings]
        breakdown: dict[str, int] = {}
        for f in ctx.findings:
            breakdown[f.finding_source] = breakdown.get(f.finding_source, 0) + 1

        task_meta = {
            "source_file": ctx.parsed_record.source_file,
            "rule_set_version": ctx.rule_set_version,
            "model_version": ctx.model_version,
            "file_hash": ctx.parsed_record.sha256
            if ctx.parsed_record
            else getattr(ctx, "file_hash", ""),
            "category": ctx.category,
            "subtype": ctx.subtype,
            "classification_confidence": ctx.classification_confidence,
            "matched_template_id": (
                ctx.matched_template["document_id"] if ctx.matched_template else None
            ),
            "llm": ctx.llm_status_info,
            "rag_context": ctx.rag_context,
            "evidence_bundles": ctx.evidence_bundles,
            "clause_comparisons": ctx.clause_comparisons,
            "review_gate": ctx.review_gate,
            "finding_breakdown": breakdown,
            "extracted_fields": public_fields,
            "reproducibility_hash": repro_hash,
        }

        uow_fn = getattr(self.storage, "unit_of_work", None)
        if uow_fn is not None:
            with uow_fn():
                self._persist(ctx, task_meta, issue_dicts, repro_hash)
        else:
            self._persist(ctx, task_meta, issue_dicts, repro_hash)

        ctx.extracted_fields["_reproducibility_hash"] = repro_hash
        ctx.extracted_fields["_finding_breakdown"] = breakdown
        return {
            "reproducibility_hash": repro_hash,
            "persisted_issues": len(issue_dicts),
        }

    def _persist(
        self,
        ctx: PipelineContext,
        task_meta: dict[str, Any],
        issue_dicts: list[dict[str, Any]],
        repro_hash: str,
    ) -> None:
        try:
            self.storage.save_task(
                ctx.task_id,
                ctx.parsed_record.template_id,
                ctx.mode,
                "COMPLETED",
                task_meta,
                tenant_id=ctx.effective_tenant,
                created_by=ctx.created_by,
                idempotency_key=ctx.idempotency_key,
                rule_set_version=ctx.rule_set_version,
                model_version=ctx.model_version,
                reproducibility_hash=repro_hash,
            )
        except TypeError:
            self.storage.save_task(
                ctx.task_id,
                ctx.parsed_record.template_id,
                ctx.mode,
                "COMPLETED",
                task_meta,
                tenant_id=ctx.effective_tenant,
                created_by=ctx.created_by,
            )
        self.storage.save_issues(ctx.task_id, issue_dicts)
        upsert_cc = getattr(self.storage, "upsert_canonical_contract", None)
        if upsert_cc is not None:
            upsert_cc(self._build_canonical_dict(ctx))

    @staticmethod
    def _build_canonical_dict(ctx: PipelineContext) -> dict[str, Any]:
        fields = ctx.extracted_fields
        parties = [
            ContractParty(role=role, name=str(fields[key]))
            for role, key in (("PARTY_A", "party_a"), ("PARTY_B", "party_b"))
            if fields.get(key)
        ]
        raw_amts = fields.get("detected_amounts_raw") or []
        first_amt = str(raw_amts[0]).replace(",", "") if raw_amts else None
        status = (
            ContractLifecycleStatus.APPROVED.value
            if ctx.review_gate == "PASS"
            else ContractLifecycleStatus.PENDING_MODIFICATION.value
        )
        cid_hash = hashlib.sha256(
            f"{ctx.effective_tenant}:{ctx.parsed_record.sha256}".encode("utf-8")
        ).hexdigest()[:14]
        return CanonicalContract(
            contract_id=f"cc_{cid_hash}",
            tenant_id=ctx.effective_tenant,
            contract_number=ContractNumber.parse(fields.get("contract_number")),
            title=str(ctx.parsed_record.source_file or ""),
            business_category=ctx.category,
            contract_subtype=ctx.subtype,
            lifecycle_status=status,
            source_system=SourceSystemType.UPLOAD.value,
            source_document_id=str(ctx.parsed_record.template_id or ""),
            source_hash=str(ctx.parsed_record.sha256 or ""),
            parties=parties,
            amounts=ContractAmountSummary.from_values(total=first_amt),
        ).to_dict()
