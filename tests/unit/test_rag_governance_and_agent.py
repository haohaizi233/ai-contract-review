# -*- coding: utf-8 -*-
"""Phase D 单元测试：RAG 知识源治理、RRF + 可插拔重排序、引文核验、受控智能体 8 状态机与评测框架。"""

from __future__ import annotations

import time

import pytest
from pydantic import BaseModel

from contract_kb.application.workflow import (
    AgentWorkflowState,
    ControlledReviewAgentWorkflow,
    ControlledToolExecutor,
    InvalidWorkflowTransitionError,
    ToolCallStatus,
    ToolInvocationError,
    ToolPermission,
    ToolSpec,
    WorkflowBudget,
)
from contract_kb.evaluate import determine_evaluation_tier, eval_rag, eval_review
from contract_kb.llm_review import MockLLMProvider
from contract_kb.modules.retrieval import (
    CitationVerifier,
    GovernedRetrievalService,
    HttpCrossEncoderReranker,
    KnowledgeGovernancePolicy,
    LexicalCoverageReranker,
    reciprocal_rank_fusion,
)


def test_knowledge_governance_separates_authoritative_from_historical():
    """历史已签案卷严禁进入强制标准依据池，草稿/失效/跨租户分块必须被阻断。"""
    hist_hit = {
        "chunk_id": "c_hist",
        "clause_id": "cl_hist",
        "knowledge_type": "HISTORICAL_CASE",
        "status": "ACTIVE",
        "tenant_id": "t1",
        "text": "甲方可延期九十日付款",
    }
    assert (
        KnowledgeGovernancePolicy.is_chunk_admissible(
            hist_hit, allow_historical_cases=False, expected_tenant_id="t1"
        )
        is False
    )
    assert (
        KnowledgeGovernancePolicy.is_chunk_admissible(
            hist_hit, allow_historical_cases=True, expected_tenant_id="t1"
        )
        is True
    )
    annotated_hist = KnowledgeGovernancePolicy.annotate_hit(hist_hit)
    assert annotated_hist["can_drive_mandatory_finding"] is False
    assert "仅供参考" in annotated_hist["prompt_badge"]

    # 草稿规则与已归档模板一律拦截
    assert (
        KnowledgeGovernancePolicy.is_chunk_admissible(
            {
                "knowledge_type": "APPROVED_RULE",
                "approved_status": "DRAFT",
                "status": "ACTIVE",
            }
        )
        is False
    )
    assert (
        KnowledgeGovernancePolicy.is_chunk_admissible(
            {
                "knowledge_type": "TEMPLATE",
                "approved_status": "APPROVED",
                "status": "SUPERSEDED",
            }
        )
        is False
    )
    assert (
        KnowledgeGovernancePolicy.is_chunk_admissible(
            {
                "knowledge_type": "TEMPLATE",
                "approved_status": "APPROVED",
                "status": "ACTIVE",
                "tenant_id": "tenant_other",
            },
            expected_tenant_id="tenant_mine",
        )
        is False
    )


def test_rrf_reranker_and_external_egress_guard(monkeypatch):
    """RRF 双通道命中排名高于单通道；外部 Cross-Encoder 受 ALLOW_EXTERNAL_LLM 门禁保护。"""
    fts = [
        {"chunk_id": "k1", "text": "违约金每日3‰", "channel": "fts"},
        {"chunk_id": "k2", "text": "保密期限三年", "channel": "fts"},
    ]
    vec = [
        {"chunk_id": "k2", "text": "保密期限三年", "channel": "vector"},
        {"chunk_id": "k1", "text": "违约金每日3‰", "channel": "vector"},
        {"chunk_id": "k3", "text": "管辖法院", "channel": "vector"},
    ]
    fused = reciprocal_rank_fusion([fts, vec])
    assert {fused[0]["chunk_id"], fused[1]["chunk_id"]} == {"k1", "k2"}
    assert set(fused[0]["channels"]) == {"fts", "vector"}

    reranked = LexicalCoverageReranker().rerank("违约金 3‰", fused, top_k=2)
    assert reranked[0]["chunk_id"] == "k1"

    monkeypatch.delenv("ALLOW_EXTERNAL_LLM", raising=False)
    with pytest.raises(PermissionError, match="ALLOW_EXTERNAL_LLM"):
        HttpCrossEncoderReranker("https://rerank.external.example/v1/rerank")

    # 本机回环端点允许初始化，且连接失败自动回退至离线词法重排
    local_ce = HttpCrossEncoderReranker(
        "http://127.0.0.1:9/v1/rerank", timeout_seconds=0.05
    )
    fb = local_ce.rerank("违约金 3‰", fused, top_k=1)
    assert fb[0]["chunk_id"] == "k1"


def test_governed_retrieval_and_citation_verifier(storage):
    """GovernedRetrievalService 输出真实可回查引文与权威标注。"""
    svc = GovernedRetrievalService(storage)
    bundle = svc.retrieve_for_review("违约金 逾期付款", top_k=4)
    assert bundle.authoritative_hits
    assert bundle.historical_reference_hits == []
    assert bundle.citation_summary is not None
    assert bundle.citation_summary.citation_correctness == 1.0
    assert all(h["can_drive_mandatory_finding"] for h in bundle.authoritative_hits)

    # 伪造不存在的 clause_id 必须降低 citation_correctness
    bad_summary = CitationVerifier().verify_retrieval_hits(
        storage, [{"clause_id": "non_existent_clause_999", "text": "编造文本"}]
    )
    assert bad_summary.citation_correctness == 0.0
    assert "non_existent_clause_999" in bad_summary.unverified_ids


def test_controlled_agent_state_machine_full_run_and_transitions(
    storage, golden_samples
):
    """受控智能体必须严格遵循 8 状态拓扑，非法跳转立即抛错。"""
    with pytest.raises(InvalidWorkflowTransitionError):
        ControlledReviewAgentWorkflow.verify_transition(
            AgentWorkflowState.DOCUMENT_ANALYSIS,
            AgentWorkflowState.REPORT_GENERATION,
        )

    canned = MockLLMProvider(
        {
            "result": {
                "findings": [
                    {
                        "finding_id": "ag1",
                        "category": "SEMANTIC_RISK",
                        "severity": "MEDIUM",
                        "title": "条款衔接提示",
                        "description": "建议核对付款节点",
                        "original_text": "工作日",
                        "suggestion": "明确起算日",
                        "confidence": 0.82,
                        "requires_human_review": False,
                    }
                ],
                "custom_requirement_results": [],
            }
        }
    )
    wf = ControlledReviewAgentWorkflow(storage=storage, llm_provider=canned)
    res = wf.run(
        golden_samples["dir"] / "mut_08_amount_case_mismatch.docx",
        mode="COMBINED",
        tenant_id="tenant_agent",
    )
    assert res.status == "COMPLETED"
    assert res.terminal_state == AgentWorkflowState.COMPLETED.value
    assert res.state_history == [
        "INIT",
        "DocumentAnalysis",
        "ContractClassification",
        "EvidenceRetrieval",
        "RuleChecking",
        "SemanticRiskAnalysis",
        "FindingConsolidation",
        "ReportGeneration",
        "HumanReviewCoordination",
        "COMPLETED",
    ]
    assert len(res.tool_audit_trail) == 8
    assert all(r["status"] == "SUCCEEDED" for r in res.tool_audit_trail)
    assert any(i["rule_id"] == "AMOUNT_CASE_CONSISTENCY" for i in res.issues)


def test_controlled_agent_permission_and_budget_degradation(storage, golden_samples):
    """缺少 INVOKE_LLM 权限或预算耗尽时，审计留痕且优雅降级 NOT_CHECKED，确定性结果完整保留。"""
    with pytest.raises(TypeError, match="严禁 float"):
        WorkflowBudget(max_cost_usd=0.5)  # type: ignore[arg-type]

    canned = MockLLMProvider(
        {"result": {"findings": [], "custom_requirement_results": []}}
    )
    # 剥夺 INVOKE_LLM 权限
    perms = {
        ToolPermission.READ_DOCUMENT,
        ToolPermission.READ_KNOWLEDGE,
        ToolPermission.EXECUTE_RULES,
        ToolPermission.WRITE_REPORT,
    }
    wf = ControlledReviewAgentWorkflow(
        storage=storage, llm_provider=canned, granted_permissions=perms
    )
    res = wf.run(
        golden_samples["dir"] / "mut_08_amount_case_mismatch.docx",
        mode="COMBINED",
    )
    assert res.status == "COMPLETED"
    assert len(canned.calls) == 0, "未获授权严禁触发外部 LLM 调用"
    sem_audit = next(
        r
        for r in res.tool_audit_trail
        if r["tool_name"] == "run_semantic_risk_analysis"
    )
    assert sem_audit["status"] == ToolCallStatus.PERMISSION_DENIED.value
    assert any(
        i["rule_id"] == "LLM_SEMANTIC_REVIEW" and i["check_status"] == "NOT_CHECKED"
        for i in res.issues
    )
    assert any(i["rule_id"] == "AMOUNT_CASE_CONSISTENCY" for i in res.issues)


def test_tool_executor_schema_validation_and_timeout():
    """受控工具执行器拦截非法输入 Schema 与超时调用。"""

    class _In(BaseModel):
        count: int

    class _Out(BaseModel):
        ok: bool

    def _slow(_: _In) -> _Out:
        time.sleep(0.08)
        return _Out(ok=True)

    ex = ControlledToolExecutor(budget=WorkflowBudget(max_total_tool_calls=3))
    ex.register(
        ToolSpec(
            name="slow_tool",
            description="超时测试工具",
            input_schema=_In,
            output_schema=_Out,
            required_permission=ToolPermission.EXECUTE_RULES,
            handler=_slow,  # type: ignore[arg-type]
            timeout_seconds=0.01,
        )
    )
    with pytest.raises(ToolInvocationError) as e_val:
        ex.invoke("slow_tool", "RuleChecking", {"count": "not_an_int"})
    assert e_val.value.status == ToolCallStatus.VALIDATION_ERROR

    with pytest.raises(ToolInvocationError) as e_to:
        ex.invoke("slow_tool", "RuleChecking", {"count": 1})
    assert e_to.value.status == ToolCallStatus.TIMEOUT


def test_upgraded_evaluation_harness_honesty_and_metrics(storage, golden_samples):
    """评测框架输出完整检索/审核/延迟/成本指标，并诚实标注 SYNTHETIC_UNIT_REGRESSION。"""
    assert (
        determine_evaluation_tier("local_hash_256", llm_configured=False, doc_count=6)
        == "SYNTHETIC_UNIT_REGRESSION"
    )
    assert (
        determine_evaluation_tier("bge-m3", llm_configured=True, doc_count=78)
        == "REAL_MODEL_END_TO_END"
    )

    rag_rep = eval_rag(storage)
    assert rag_rep["evaluation_tier"] == "SYNTHETIC_UNIT_REGRESSION"
    assert "AnswerGroundedness" in rag_rep["metrics"]
    assert "p95_ms" in rag_rep["latency_ms"]

    rev_rep = eval_review(storage, golden_dir=golden_samples["dir"])
    assert rev_rep["evaluation_tier"] == "SYNTHETIC_UNIT_REGRESSION"
    assert 0.0 <= rev_rep["false_positive_rate"] <= 1.0
    assert 0.0 <= rev_rep["false_negative_rate"] <= 1.0
    assert rev_rep["answer_groundedness"] == 1.0
    assert rev_rep["latency_ms"]["p95_ms"] > 0
    assert "cost_per_contract_usd" in rev_rep["token_cost"]
