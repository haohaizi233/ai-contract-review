# -*- coding: utf-8 -*-
"""Integration tests for Dynamic Retrieval Pipeline & EvidenceBundle (Phase 12-D)."""

from __future__ import annotations

import pytest

from contract_kb.modules.retrieval import (
    ContractAwareQueryPlanner,
    EvidenceBundle,
    GovernedRetrievalService,
    HierarchicalKnowledgeTree,
)


def test_dynamic_retrieval_for_clause(storage):
    """测试条款驱动的动态多路检索并绑定 EvidenceBundle。"""
    svc = GovernedRetrievalService(storage)
    planner = ContractAwareQueryPlanner()

    clause = {
        "clause_id": "c_review_fee_01",
        "clause_number": "4.1",
        "article_title": "第四条 费用与结算",
        "clause_category": "FEE_AND_PAYMENT",
        "original_text": "甲方逾期向乙方支付款项的，每日应按逾期未付金额的0.05%向乙方支付违约金。",
        "conditions": ["甲方逾期向乙方支付款项"],
        "source_node_ids": ["node_fee_1"],
    }

    bundle = svc.retrieve_for_clause(
        clause,
        top_k=3,
        tenant_id="t1",
        planner=planner,
    )

    assert isinstance(bundle, EvidenceBundle)
    assert bundle.target_clause_id == "c_review_fee_01"
    assert len(bundle.intents) >= 1
    # 验证检索命中了经审批的权威标准条款
    assert len(bundle.authoritative_clauses) > 0
    for h in bundle.authoritative_clauses:
        assert h.get("can_drive_mandatory_finding") is True
        assert h.get("approved_status") == "APPROVED"
        assert h.get("tenant_id") == "t1"

    # 验证生成的提示词片段结构完整
    prompt_text = bundle.prompt_context_text()
    assert "[企业现行标准模板条款-权威基准]" in prompt_text or "违约" in prompt_text
    assert bundle.citation_summary is not None
    assert bundle.citation_summary.citation_correctness == 1.0


def test_retrieve_contract_evidence_bundles_batch(storage):
    """测试整份合同批量生成条款级证据包映射字典。"""
    svc = GovernedRetrievalService(storage)

    clauses = [
        {
            "clause_id": "c_1",
            "article_title": "第一条 合同标的",
            "clause_category": "CONTRACT_SUBJECT",
            "original_text": "甲乙双方就专利申请代理达成一致。",
        },
        {
            "clause_id": "c_2",
            "article_title": "第六条 违约金",
            "clause_category": "LIABILITY",
            "clause_subcategory": "LIQUIDATED_DAMAGES",
            "original_text": "逾期付款应每日支付3‰的违约金。",
            "conditions": ["逾期付款"],
        },
        {
            "clause_id": "c_3",
            "article_title": "第七条 争议管辖",
            "clause_category": "DISPUTE_RESOLUTION",
            "original_text": "由深圳国际仲裁院管辖。",
        },
    ]

    bundles_map = svc.retrieve_contract_evidence_bundles(
        clauses,
        top_k_per_clause=2,
        tenant_id="t1",
    )

    assert len(bundles_map) == 3
    assert "c_2" in bundles_map
    b2 = bundles_map["c_2"]
    assert b2.target_clause_id == "c_2"
    assert len(b2.authoritative_clauses) > 0
