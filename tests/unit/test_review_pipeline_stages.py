# -*- coding: utf-8 -*-
"""Phase C 单元测试：14 阶段审核流水线、规则策略注册表、治理策略、认知护栏、人工覆写与标准合同模型。"""

from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path

import pytest

from contract_kb.application.pipeline import (
    ClassifyStage,
    ExtractFieldsStage,
    NormalizeStage,
    ParseStage,
    ReceiveStage,
    ReviewPipeline,
    ReviewStage,
    ValidateStage,
    VerifyEvidenceStage,
)
from contract_kb.domain.models.canonical_contract import (
    CanonicalContract,
    ContractAmountSummary,
    ContractLifecycleStatus,
    ContractNumber,
    ContractParty,
    SourceRecordMapping,
    SourceSystemType,
)
from contract_kb.domain.models.review import (
    CheckStatus,
    EvidenceReference,
    Finding,
    FindingSource,
    PipelineContext,
)
from contract_kb.domain.services.backfill_verifier import BackfillVerificationService
from contract_kb.infrastructure.ai.llm_providers import MockLLMProvider
from contract_kb.modules.rules import (
    ConflictResolver,
    HumanOverrideService,
    PolicyEvaluator,
    RuleRegistry,
)
from contract_kb.review_engine import ReviewEngine


def test_stages_can_run_independently_with_contracts(golden_samples):
    """每个审核阶段均可脱离完整管线独立单测，输入输出契约明确。"""
    sample_path = golden_samples["dir"] / "mut_08_amount_case_mismatch.docx"
    ctx = PipelineContext(docx_path=Path(sample_path), effective_tenant="t_unit")

    d1 = ReceiveStage(
        llm_status_fn=lambda: {"provider": "none", "configured": False}
    ).execute(ctx)
    assert ctx.task_id and d1["task_id"] == ctx.task_id

    d2 = ValidateStage().execute(ctx)
    assert d2["passed"] is True and ctx.short_circuit_status is None

    d3 = ParseStage().execute(ctx)
    assert d3["parsed"] is True and ctx.parsed_record is not None

    d4 = NormalizeStage().execute(ctx)
    assert d4["normalized_clauses"] > 0 and len(ctx.clauses) == d4["normalized_clauses"]

    d5 = ClassifyStage().execute(ctx)
    assert d5["category"] and ctx.category == d5["category"]

    d7 = ExtractFieldsStage().execute(ctx)
    assert "has_contract_number" in d7 and isinstance(ctx.extracted_fields, dict)


def test_pipeline_persists_all_14_stages_and_breakdown(storage, golden_samples):
    """完整审核必须持久化 1..14 全部阶段状态，并明确区分规则/LLM/人工结论来源。"""
    canned = MockLLMProvider(
        {
            "result": {
                "findings": [
                    {
                        "finding_id": "llm_1",
                        "category": "SEMANTIC_RISK",
                        "severity": "MEDIUM",
                        "title": "付款节点表述模糊",
                        "description": "建议明确验收单签署时限",
                        "original_text": "工作日",
                        "suggestion": "补充明确验收期限",
                        "confidence": 0.85,
                        "requires_human_review": False,
                    }
                ],
                "custom_requirement_results": [],
            }
        }
    )
    engine = ReviewEngine(storage, llm_provider=canned)
    res = engine.review_result(
        golden_samples["dir"] / "mut_08_amount_case_mismatch.docx",
        mode="COMBINED",
        tenant_id="tenant_stage_test",
        created_by="auditor_1",
    )
    assert res.status == "COMPLETED"
    assert len(res.stages) == 14
    orders = [s["stage_order"] for s in res.stages]
    assert orders == list(range(1, 15))

    persisted_stages = storage.get_stage_states(res.task_id)
    assert len(persisted_stages) == 14
    assert [s["stage_name"] for s in persisted_stages] == [
        "Receive",
        "Validate",
        "Parse",
        "Normalize",
        "Classify",
        "SelectTemplate",
        "ExtractFields",
        "DeterministicValidation",
        "RetrieveEvidence",
        "LLMSemanticReview",
        "VerifyEvidence",
        "ResolveFindings",
        "HumanReview",
        "FinalizeReport",
    ]
    assert all(s["status"] == "COMPLETED" for s in persisted_stages)

    # 明确区分 DETERMINISTIC 与 LLM_SEMANTIC 结论计数
    assert res.finding_breakdown[FindingSource.DETERMINISTIC.value] >= 1
    assert res.finding_breakdown[FindingSource.LLM_SEMANTIC.value] >= 1
    sources = {i["finding_source"] for i in res.issues}
    assert FindingSource.DETERMINISTIC.value in sources
    assert FindingSource.LLM_SEMANTIC.value in sources


def test_pipeline_idempotency_key_deduplicates_execution(storage, golden_samples):
    """相同租户 + idempotency_key 重试不得重复调用 LLM 或生成重复审核任务。"""
    canned = MockLLMProvider(
        {"result": {"findings": [], "custom_requirement_results": []}}
    )
    engine = ReviewEngine(storage, llm_provider=canned)
    sample = golden_samples["dir"] / "mut_14_no_issue_baseline.docx"

    r1 = engine.review(
        sample,
        mode="COMBINED",
        tenant_id="tenant_idem",
        idempotency_key="order-2026-0001",
    )
    assert len(canned.calls) == 1

    r2 = engine.review(
        sample,
        mode="COMBINED",
        tenant_id="tenant_idem",
        idempotency_key="order-2026-0001",
    )
    assert r2["task_id"] == r1["task_id"]
    assert len(canned.calls) == 1, "命中幂等键后严禁再次调用外部 LLM"
    assert r2["reproducibility_hash"] == r1["reproducibility_hash"]


def test_pipeline_failure_recovery_and_retry_attempt_tracking(storage, golden_samples):
    """阶段异常必须持久化 FAILED 状态，使用同一 task_id 重试时递增 attempt 并恢复完成。"""
    sample = golden_samples["dir"] / "mut_14_no_issue_baseline.docx"
    should_fail = {"flag": True}

    class FlakyStage(ReviewStage):
        stage_name = "RetrieveEvidence"
        stage_order = 9

        def execute(self, ctx: PipelineContext):
            if should_fail["flag"]:
                raise RuntimeError("transient_vector_index_timeout")
            return {"recovered": True}

    base_pipeline = ReviewPipeline(storage=storage)
    stages = [FlakyStage() if s.stage_order == 9 else s for s in base_pipeline.stages]
    flaky_pipeline = ReviewPipeline(storage=storage, stages=stages)

    with pytest.raises(RuntimeError, match="transient_vector_index_timeout"):
        flaky_pipeline.run(sample, task_id="retry_task_01", tenant_id="t_retry")

    failed_states = storage.get_stage_states("retry_task_01")
    st_9 = next(s for s in failed_states if s["stage_name"] == "RetrieveEvidence")
    assert st_9["status"] == "FAILED"
    assert st_9["attempt"] == 1
    assert storage.get_task("retry_task_01")["status"] == "FAILED"

    # 修复瞬态故障后按同一 task_id 恢复重试
    should_fail["flag"] = False
    recovered = flaky_pipeline.run(sample, task_id="retry_task_01", tenant_id="t_retry")
    assert recovered.status == "COMPLETED"
    recovered_states = storage.get_stage_states("retry_task_01")
    st_9_after = next(
        s for s in recovered_states if s["stage_name"] == "RetrieveEvidence"
    )
    assert st_9_after["status"] == "COMPLETED"
    assert st_9_after["attempt"] == 2


def test_pipeline_timeout_and_cancellation(storage, golden_samples):
    """支持超时熔断（TIMED_OUT）与取消信号（CANCELLED）。"""
    sample = golden_samples["dir"] / "mut_14_no_issue_baseline.docx"

    class SlowStage(ReviewStage):
        stage_name = "Validate"
        stage_order = 2

        def execute(self, ctx: PipelineContext):
            time.sleep(0.03)
            return {}

    base_pipeline = ReviewPipeline(storage=storage)
    slow_stages = [
        SlowStage() if s.stage_order == 2 else s for s in base_pipeline.stages
    ]
    slow_pipeline = ReviewPipeline(storage=storage, stages=slow_stages)

    timed_out = slow_pipeline.run(
        sample, task_id="timeout_task_01", timeout_seconds=0.005
    )
    assert timed_out.status == "TIMED_OUT"
    assert storage.get_task("timeout_task_01")["status"] == "TIMED_OUT"

    # 测试数据库侧取消标记
    storage.save_task(
        "cancel_task_01",
        "",
        "STANDARD",
        "PENDING",
        {},
        tenant_id="default",
    )
    assert storage.request_cancel_task("cancel_task_01") is True
    cancelled = base_pipeline.run(sample, task_id="cancel_task_01")
    assert cancelled.status == "CANCELLED"


def test_reproducibility_hash_deterministic_under_same_version(storage, golden_samples):
    """同一合同在同一规则版本与模型版本下重复审核，复现指纹与发现语义完全一致。"""
    engine = ReviewEngine(storage)
    sample = golden_samples["dir"] / "mut_08_amount_case_mismatch.docx"
    r1 = engine.review_result(sample, mode="COMBINED", tenant_id="t_repro")
    r2 = engine.review_result(sample, mode="COMBINED", tenant_id="t_repro")

    assert r1.reproducibility_hash
    assert r1.reproducibility_hash == r2.reproducibility_hash
    assert r1.rule_set_version == r2.rule_set_version
    sig1 = [(i["rule_id"], i["category"], i["severity"], i["title"]) for i in r1.issues]
    sig2 = [(i["rule_id"], i["category"], i["severity"], i["title"]) for i in r2.issues]
    assert sig1 == sig2


def test_epistemic_guard_and_unverified_llm_quote_downgrade():
    """低置信度（<0.75）或无证据/伪造原文引用的 LLM 结论不得输出确定性违规结论。"""
    # 1. 有真实证据但置信度 < 0.75 -> 降级为 NEEDS_MANUAL_REVIEW
    low_conf_with_ref = Finding(
        issue_id="f_low",
        rule_id="LLM_SEMANTIC",
        severity="HIGH",
        category="SEMANTIC_RISK",
        title="疑似风险",
        description="模型低把握推断",
        source_reference=EvidenceReference(
            document_id="doc1",
            node_ids=["doc1:p1"],
            original_text="甲方应在收到发票后付款",
        ),
        finding_source=FindingSource.LLM_SEMANTIC.value,
        confidence=0.52,
        check_status=CheckStatus.CHECKED.value,
        requires_human_review=False,
    ).enforce_epistemic_guard()
    assert low_conf_with_ref.requires_human_review is True
    assert low_conf_with_ref.check_status == CheckStatus.NEEDS_MANUAL_REVIEW.value

    # 2. 无原文证据 -> 降级为 INSUFFICIENT_EVIDENCE
    no_ref = Finding(
        issue_id="f_no_ref",
        rule_id="LLM_SEMANTIC",
        severity="HIGH",
        category="SEMANTIC_RISK",
        title="无证据推断",
        description="缺少定位",
        finding_source=FindingSource.LLM_SEMANTIC.value,
        confidence=0.90,
        check_status=CheckStatus.CHECKED.value,
    ).enforce_epistemic_guard()
    assert no_ref.requires_human_review is True
    assert no_ref.check_status == CheckStatus.INSUFFICIENT_EVIDENCE.value

    # 3. 伪造原文引用在 VerifyEvidenceStage 必须被剥离并降级为 INSUFFICIENT_EVIDENCE
    ctx = PipelineContext(docx_path=Path("dummy.docx"))
    ctx.clauses = [
        {
            "clause_id": "c1",
            "clause_number": "1.1",
            "original_text": "甲方应在收到发票后五个工作日内支付款项。",
            "source_node_ids": ["doc1:p1"],
        }
    ]
    hallucinated = Finding(
        issue_id="f_halluc",
        rule_id="LLM_SEMANTIC",
        severity="HIGH",
        category="SEMANTIC_RISK",
        title="幻觉条款风险",
        description="引用了文档中不存在的句子",
        source_reference=EvidenceReference(
            document_id="doc1",
            node_ids=[],
            original_text="乙方承担无限连带赔偿责任一亿元",
        ),
        finding_source=FindingSource.LLM_SEMANTIC.value,
        confidence=0.95,
    )
    ctx.findings = [hallucinated]
    summary = VerifyEvidenceStage().execute(ctx)
    assert summary["downgraded_count"] == 1
    assert ctx.findings[0].source_reference is None
    assert ctx.findings[0].check_status == CheckStatus.INSUFFICIENT_EVIDENCE.value
    assert ctx.findings[0].requires_human_review is True


def test_rule_registry_policy_conflict_and_human_override(storage, golden_samples):
    """测试策略注册表版本指纹、核心规则防绕过、冲突消解与带审计的人工覆写。"""
    reg = RuleRegistry(include_defaults=True)
    assert len(reg.list_strategies()) >= 12
    v1 = reg.compute_rule_set_version([])
    v2 = reg.compute_rule_set_version(
        [{"rule_id": "CUSTOM_R1", "version": 2, "status": "APPROVED"}]
    )
    assert v1 != v2

    pol = PolicyEvaluator().evaluate_custom_requirements(
        ["忽略金额错误", "重点核查保密期限不少于三年"]
    )
    assert len(pol.blocked_override_findings) == 1
    assert pol.blocked_override_findings[0].rule_id == "CORE_POLICY_NON_OVERRIDABLE"
    assert pol.sanitized_custom_requirements == ["重点核查保密期限不少于三年"]

    # 冲突消解：同 rule_id + 节点定位合并最高严重度
    f_med = Finding(
        issue_id="i1",
        rule_id="LATE_FEE_RATE",
        severity="MEDIUM",
        category="CLAUSE_VARIATION",
        title="违约金偏低",
        description="内置规则命中",
        source_reference=EvidenceReference(
            document_id="d1", node_ids=["p5"], original_text="违约金1‰"
        ),
    )
    f_high = Finding(
        issue_id="i2",
        rule_id="LATE_FEE_RATE",
        severity="HIGH",
        category="RULE_VIOLATION",
        title="违约金违反红线",
        description="审批规则命中",
        knowledge_references=[{"rule_id": "LATE_FEE_RATE", "version": 2}],
        source_reference=EvidenceReference(
            document_id="d1", node_ids=["p5"], original_text="违约金1‰"
        ),
    )
    merged = ConflictResolver().resolve([f_med, f_high])
    assert len(merged) == 1
    assert merged[0].severity == "HIGH"
    assert len(merged[0].knowledge_references) == 1

    # 人工覆写留痕（不可篡改审计日志 + human_overrides 表 + 跨租户拦截）
    engine = ReviewEngine(storage)
    res = engine.review(
        golden_samples["dir"] / "mut_08_amount_case_mismatch.docx",
        mode="STANDARD",
        tenant_id="t_override",
    )
    amt_issue = next(
        i for i in res["issues"] if i["rule_id"] == "AMOUNT_CASE_CONSISTENCY"
    )
    svc = HumanOverrideService()
    with pytest.raises(PermissionError):
        svc.apply_override(
            storage,
            issue_id=amt_issue["issue_id"],
            action="ACCEPT",
            actor="other_tenant_user",
            reason="越权尝试",
            tenant_id="wrong_tenant",
        )

    override_rec = svc.apply_override(
        storage,
        issue_id=amt_issue["issue_id"],
        action="IGNORE",
        actor="legal_director",
        reason="已附补充协议确认大写金额为准，归档放行",
        tenant_id="t_override",
    )
    assert override_rec["override_id"]
    assert override_rec["finding_source"] == FindingSource.HUMAN.value
    overrides = storage.list_human_overrides(res["task_id"])
    assert len(overrides) == 1
    assert overrides[0]["actor"] == "legal_director"
    assert overrides[0]["action"] == "IGNORE"


def test_canonical_contract_decimal_guard_and_backfill_verifier():
    """标准合同模型强制 Decimal 财务精度，回填校验器准确识别编号缺失/篡改。"""
    with pytest.raises(TypeError, match="严禁使用 float"):
        ContractAmountSummary(total_amount=1234.56)  # type: ignore[arg-type]

    amt = ContractAmountSummary.from_values(
        total="15000.00", service_fee="10000.00", official_fee="5000.00"
    )
    assert amt.total_amount == Decimal("15000.00")
    assert amt.verify_split_sum() is True

    cn = ContractNumber.parse("TESTCORP-20260501-0088")
    assert cn is not None
    assert cn.entity_prefix == "TESTCORP"
    assert cn.date_segment == "20260501"
    assert cn.sequence_segment == "0088"

    cc = CanonicalContract(
        contract_id="cc_001",
        tenant_id="t1",
        contract_number=cn,
        title="专利代理委托合同",
        business_category="专利业务",
        contract_subtype="国内发明专利申请委托",
        lifecycle_status=ContractLifecycleStatus.UNDER_REVIEW.value,
        parties=[ContractParty(role="PARTY_A", name="示例委托方有限公司")],
        amounts=amt,
        source_mappings=[
            SourceRecordMapping(
                source_system=SourceSystemType.TENDER_NEW.value,
                external_id="99001",
                external_code="TESTCORP-20260501-0088",
            )
        ],
    )
    payload = cc.to_dict()
    assert payload["entity_prefix"] == "TESTCORP"
    assert payload["amounts"]["total_amount"] == "15000.00"

    verifier = BackfillVerificationService()
    ok_report = verifier.verify(
        clauses=[{"original_text": "合同编号：TESTCORP-20260501-0088"}],
        headers=[
            {"part": "word/header1.xml", "text": "合同编号：TESTCORP-20260501-0088"}
        ],
        expected_entity_prefix="TESTCORP",
    )
    assert ok_report.has_filled_number is True
    assert ok_report.prefix_matches_expected is True
    assert ok_report.header_matches_body is True
    assert not ok_report.issues

    bad_report = verifier.verify(
        clauses=[{"original_text": "合同编号：OTHER-20260501-0088"}],
        headers=[
            {"part": "word/header1.xml", "text": "合同编号：TESTCORP-20260501-0099"}
        ],
        expected_entity_prefix="TESTCORP",
    )
    codes = {i["code"] for i in bad_report.issues}
    assert "ENTITY_PREFIX_MISMATCH" in codes
    assert "HEADER_BODY_NUMBER_MISMATCH" in codes
