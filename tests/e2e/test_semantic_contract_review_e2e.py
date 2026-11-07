# -*- coding: utf-8 -*-
"""端到端语义 RAG 合同智能审核测试（E2E Semantic Contract Review）。

验证核心升级全链路：
1. 条款驱动动态 Query Planning (Contract-Aware Query Planner)；
2. 结构感知多粒度知识树 (Hierarchical Knowledge Tree)；
3. 多路混合检索与 EvidenceBundle 强类型绑定；
4. 结构化条款差异比对引擎 (ClauseComparisonEngine: 极性倒错、数值偏差、期限口径差异)；
5. 原文证据与知识溯源严格核验 (VerifyEvidenceStage)；
6. 多租户数据边界隔离。
"""

from __future__ import annotations

from pathlib import Path
from docx import Document

from contract_kb.application.pipeline.runner import ReviewPipeline
from contract_kb.storage import Storage


def _create_sample_docx(path: Path, paragraphs: list[str]) -> None:
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    doc.save(str(path))


def test_semantic_contract_review_e2e_full_pipeline(tmp_path: Path):
    db_path = tmp_path / "semantic_rag_e2e.sqlite"
    st = Storage(db_path)

    # 1. 在租户 tenant_alpha 录入企业权威标准模板
    standard_doc_id = "std_tpl_ip_001"
    st.upsert_document(
        {
            "template_id": standard_doc_id,
            "source_file": "标准知识产权许可协议_权威版.docx",
            "sha256": "hash_std_001",
            "category": "PATENT_LICENSE",
            "subtype": "EXCLUSIVE_LICENSE",
            "status": "ACTIVE",
            "approval_status": "APPROVED",
            "doc_status": "ACTIVE",
            "effective_version": "v2.0",
            "document_type": "contract_template",
            "clauses": [
                {
                    "clause_id": "std_art_5",
                    "clause_number": "第五条",
                    "clause_category": "LIABILITY",
                    "article_title": "第五条 违约责任",
                    "original_text": "第五条 违约责任：任何一方违约，应向守约方支付违约金。",
                    "conditions": ["乙方没有重大过失的情况下"],
                    "exceptions": ["不可抗力除外"],
                    "approval_status": "APPROVED",
                    "is_company_standard": True,
                    "source_node_ids": ["n_std_5"],
                },
                {
                    "clause_id": "std_clause_5_1",
                    "clause_number": "5.1",
                    "clause_category": "LIABILITY",
                    "article_title": "第五条 违约责任",
                    "original_text": "5.1 乙方延迟交付许可技术成果的，每日应按合同总金额千分之三（3‰）向甲方支付违约金。",
                    "conditions": [],
                    "exceptions": [],
                    "approval_status": "APPROVED",
                    "is_company_standard": True,
                    "source_node_ids": ["n_std_5_1"],
                },
                {
                    "clause_id": "std_clause_6_1",
                    "clause_number": "6.1",
                    "clause_category": "CONFIDENTIALITY",
                    "article_title": "第六条 保密义务",
                    "original_text": "6.1 未经甲方书面明确许可，乙方严禁且不得向任何第三方披露保密信息。",
                    "conditions": [],
                    "exceptions": [],
                    "approval_status": "APPROVED",
                    "is_company_standard": True,
                    "source_node_ids": ["n_std_6_1"],
                },
                {
                    "clause_id": "std_clause_7_1",
                    "clause_number": "7.1",
                    "clause_category": "TERMINATION",
                    "article_title": "第七条 合同解除",
                    "original_text": "7.1 甲方逾期付款超过15个工作日的，乙方有权单方书面通知解除合同。",
                    "conditions": ["甲方逾期付款超过15个工作日"],
                    "exceptions": [],
                    "approval_status": "APPROVED",
                    "is_company_standard": True,
                    "source_node_ids": ["n_std_7_1"],
                },
            ],
        },
        tenant="tenant_alpha",
    )
    st.approve_knowledge_version(
        tenant_id="tenant_alpha",
        document_id=standard_doc_id,
        approver_id="legal_admin",
        approval_reason="E2E标准模板基准审批",
    )

    # 在隔离租户 tenant_beta 录入不应泄露的条款
    st.upsert_document(
        {
            "template_id": "beta_secret_doc",
            "source_file": "Beta机密合同.docx",
            "sha256": "hash_beta",
            "category": "PATENT_LICENSE",
            "status": "ACTIVE",
            "approval_status": "APPROVED",
            "clauses": [
                {
                    "clause_id": "beta_clause_1",
                    "clause_category": "LIABILITY",
                    "original_text": "Beta租户独占条款：每日违约金按百分之一计算。",
                    "approval_status": "APPROVED",
                }
            ],
        },
        tenant="tenant_beta",
    )

    # 2. 生成待审合同 docx，故意引入法律语义差异：
    # - 5.1: 费率差异（千分之三 3‰ 变更为 万分之五 0.05%）
    # - 6.1: 法律极性反转（不得披露 变更为 可以披露）
    # - 7.1: 期限口径错位（15个工作日 变更为 30日自然日）
    review_docx = tmp_path / "待审专利许可合同.docx"
    _create_sample_docx(
        review_docx,
        [
            "专利实施许可合同",
            "第一条 双方主体：甲方（许可方）与乙方（被许可方）。",
            "第五条 违约责任",
            "5.1 乙方延迟交付许可技术成果的，每日应按合同总金额万分之五（0.05%）向甲方支付违约金。",
            "第六条 保密条款",
            "6.1 未经甲方书面明确许可，乙方可以向任何第三方披露保密信息。",
            "第七条 协议解除",
            "7.1 甲方逾期付款超过30日的，乙方有权单方书面通知解除合同。",
        ],
    )

    # 3. 运行完整 14 阶段 ReviewPipeline 流水线
    pipeline = ReviewPipeline(st)
    result = pipeline.run(
        str(review_docx),
        tenant_id="tenant_alpha",
        mode="STANDARD",
    )

    # 4. 校验流水线基本状态
    assert result.status == "COMPLETED"
    assert len(result.stages) == 14
    stage_names = [s["stage_name"] for s in result.stages]
    assert "RetrieveEvidence" in stage_names
    assert "LLMSemanticReview" in stage_names
    assert "VerifyEvidence" in stage_names

    # 5. 校验 Phase 12 结构感知知识树
    assert result.knowledge_tree is not None
    assert hasattr(result.knowledge_tree, "nodes")
    assert len(result.knowledge_tree.nodes) > 0

    # 6. 校验 EvidenceBundle 检索与租户隔离
    assert result.evidence_bundles is not None
    assert len(result.evidence_bundles) > 0
    # 验证没有任何来自 tenant_beta 的数据泄露
    for cid, bundle in result.evidence_bundles.items():
        auth_clauses = bundle.get("authoritative_clauses") or []
        for ac in auth_clauses:
            assert ac.get("tenant_id", "tenant_alpha") == "tenant_alpha"
            assert ac.get("document_id") != "beta_secret_doc"

    # 7. 校验结构化条款比对引擎成果 (Clause Comparisons)
    comparisons = result.clause_comparisons
    assert len(comparisons) > 0
    conclusions = {comp.get("conclusion") for comp in comparisons}

    # 验证极性反转检出
    assert "POLARITY_INVERSION" in conclusions or any(
        comp.get("polarity_inverted") for comp in comparisons
    )

    # 验证数值/期限差异检出
    has_numeric = "NUMERIC_DIFFERENCE" in conclusions or any(
        len(comp.get("numeric_differences", [])) > 0 for comp in comparisons
    )
    assert has_numeric

    # 8. 校验审核发现与证据真实性核验
    issues = result.issues
    assert len(issues) > 0

    comparison_issues = [
        iss for iss in issues if str(iss.get("rule_id", "")).startswith("COMPARISON:")
    ]
    assert len(comparison_issues) > 0

    for ci in comparison_issues:
        src_ref = ci.get("source_reference")
        assert src_ref is not None
        assert len(src_ref.get("node_ids", [])) > 0
        krefs = ci.get("knowledge_references")
        assert krefs and len(krefs) > 0
        assert krefs[0].get("document_id") == standard_doc_id
        assert krefs[0].get("approved_status") == "APPROVED"
        assert ci.get("requires_human_review") is True
