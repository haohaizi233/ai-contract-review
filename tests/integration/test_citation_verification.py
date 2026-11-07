# -*- coding: utf-8 -*-
"""集成测试：原文引用与 AST 节点锚定真伪校验（T12 / F11）。

验证契约：
1. 模型或规则生成合法引言时，能精准锚定到真实 AST 节点与条款号；
2. 引用幻觉（生成不存在的条款号、改写文本超出容差）时，
   Stage 11 VerifyEvidenceStage 自动将其标注为 CITATION_UNVERIFIED 并降级为 INSUFFICIENT_EVIDENCE；
3. 结构化 EvidenceReference 包含精确原文定位片段。
"""

from __future__ import annotations

from pathlib import Path

from contract_kb.application.pipeline.stages import VerifyEvidenceStage
from contract_kb.domain.models.review import (
    CheckStatus,
    EvidenceReference,
    Finding,
    PipelineContext,
)


def test_hallucinated_citation_marked_unverified_and_downgraded():
    """验证模型虚构条款引用时被证据核验拦截并降级为 CITATION_UNVERIFIED。"""
    ctx = PipelineContext(
        docx_path=Path("dummy.docx"),
        valid_node_ids={"node_1", "node_2"},
        clauses=[
            {
                "clause_id": "c1",
                "clause_number": "第一条",
                "original_text": "合同金额为人民币壹拾万元整。",
                "source_node_ids": ["node_1"],
            }
        ],
    )

    # 1. 合法引用：原文引述真实存在于合同第一条
    legit_finding = Finding(
        issue_id="issue_valid",
        rule_id="RULE_01",
        severity="MEDIUM",
        category="AMOUNT_CHECK",
        description="金额核实",
        source_reference=EvidenceReference(
            document_id="doc1",
            original_text="合同金额为人民币壹拾万元整。",
        ),
    )

    # 2. 幻觉引用：原文引述纯属虚构（如“合同金额为一亿美元且应免税交付”）
    hallucinated_finding = Finding(
        issue_id="issue_hallucinated",
        rule_id="RULE_02",
        severity="HIGH",
        category="TAX_RISK",
        description="税收风险",
        source_reference=EvidenceReference(
            document_id="doc1",
            original_text="合同金额为一亿美元且应免税交付。",
        ),
    )

    ctx.findings = [legit_finding, hallucinated_finding]

    verify_stage = VerifyEvidenceStage()
    detail = verify_stage.execute(ctx)

    assert detail["downgraded_count"] == 1
    assert detail["bound_from_quote"] == 1

    # 验证合法引用被正确绑定到 node_1 与 第一条
    assert legit_finding.source_reference is not None
    assert legit_finding.source_reference.node_ids == ["node_1"]
    assert legit_finding.source_reference.clause_number == "第一条"

    # 验证幻觉引用被降级为证据不足并标注 CITATION_UNVERIFIED
    assert hallucinated_finding.check_status == CheckStatus.INSUFFICIENT_EVIDENCE.value
    assert hallucinated_finding.requires_human_review is True
    assert hallucinated_finding.citation_status == "CITATION_UNVERIFIED"
