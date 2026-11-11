# -*- coding: utf-8 -*-
"""Phase 16-S-R1: 知识与规则引用真实核验 (Citation Verification) 独立安全测试集。"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

from contract_kb.application.agents.legal_review_agent import LegalReviewAgent
from contract_kb.modules.retrieval.citation import CitationVerifier


def test_verify_rule_evidence_tenant_isolation():
    """跨租户规则核验必须报 TENANT_MISMATCH 并拒绝标为 VERIFIED。"""
    verifier = CitationVerifier()
    mock_storage = MagicMock()
    # 数据库中该规则属于 victim_corp
    mock_storage.get_rules.return_value = [
        {
            "rule_id": "RULE_CROSS_01",
            "tenant_id": "victim_corp",
            "status": "APPROVED",
            "name": "跨租户规则",
            "source_clauses": [{"document_id": "doc1", "clause_id": "c1"}],
        }
    ]

    rule_item = {
        "rule_id": "RULE_CROSS_01",
        "tenant_id": "attacker_corp",
        "status": "APPROVED",
        "source_clauses": [{"document_id": "doc1", "clause_id": "c1"}],
    }

    res = verifier.verify_rule_evidence(
        storage=mock_storage,
        rule_item=rule_item,
        expected_tenant_id="attacker_corp",
    )
    assert res.verified is False
    assert res.status == "TENANT_MISMATCH"
    assert "越权" in " ".join(res.failure_reasons) or "不匹配" in " ".join(
        res.failure_reasons
    )


def test_verify_rule_evidence_revoked_or_draft_status():
    """未通过审批 (DRAFT/PENDING) 或已撤销 (REVOKED) 的规则绝不可标为 VERIFIED。"""
    verifier = CitationVerifier()
    mock_storage = MagicMock()
    mock_storage.get_rules.return_value = [
        {
            "rule_id": "RULE_REVOKED_01",
            "tenant_id": "corp_alpha",
            "status": "REVOKED",
            "name": "已废止规则",
            "source_clauses": [{"document_id": "doc1", "clause_id": "c1"}],
        }
    ]

    rule_item = {
        "rule_id": "RULE_REVOKED_01",
        "tenant_id": "corp_alpha",
        "status": "REVOKED",
        "source_clauses": [{"document_id": "doc1", "clause_id": "c1"}],
    }

    res = verifier.verify_rule_evidence(
        storage=mock_storage,
        rule_item=rule_item,
        expected_tenant_id="corp_alpha",
    )
    assert res.verified is False
    assert res.status == "APPROVAL_REVOKED"


def test_verify_rule_evidence_expired_date():
    """超过生效截止日期的规则必须识别为 SOURCE_INACTIVE 并拒绝标为 VERIFIED。"""
    verifier = CitationVerifier()
    mock_storage = MagicMock()
    past_date = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10)
    ).isoformat()
    mock_storage.get_rules.return_value = [
        {
            "rule_id": "RULE_EXPIRED_01",
            "tenant_id": "corp_alpha",
            "status": "APPROVED",
            "valid_until": past_date,
            "source_clauses": [{"document_id": "doc1", "clause_id": "c1"}],
        }
    ]

    rule_item = {
        "rule_id": "RULE_EXPIRED_01",
        "tenant_id": "corp_alpha",
        "status": "APPROVED",
        "source_clauses": [{"document_id": "doc1", "clause_id": "c1"}],
    }

    res = verifier.verify_rule_evidence(
        storage=mock_storage,
        rule_item=rule_item,
        expected_tenant_id="corp_alpha",
    )
    assert res.verified is False
    assert res.status == "SOURCE_INACTIVE"


def test_verify_rule_evidence_insufficient_provenance():
    """虽经 APPROVED 但缺少真实出处条款 (source_clauses 为空) 的规则不得标为 VERIFIED。"""
    verifier = CitationVerifier()
    mock_storage = MagicMock()
    mock_storage.get_rules.return_value = [
        {
            "rule_id": "RULE_NO_SOURCE_01",
            "tenant_id": "corp_alpha",
            "status": "APPROVED",
            "source_clauses": [],  # 无出处引文
        }
    ]

    rule_item = {
        "rule_id": "RULE_NO_SOURCE_01",
        "tenant_id": "corp_alpha",
        "status": "APPROVED",
        "source_clauses": [],
    }

    res = verifier.verify_rule_evidence(
        storage=mock_storage,
        rule_item=rule_item,
        expected_tenant_id="corp_alpha",
    )
    assert res.verified is False
    assert res.status == "INSUFFICIENT_PROVENANCE"


def test_verify_rule_evidence_genuine_approved_with_provenance():
    """合法审批、在有效期内、租户匹配且具备完整出处的规则成功核验为 VERIFIED。"""
    verifier = CitationVerifier()
    mock_storage = MagicMock()
    mock_storage.get_rules.return_value = [
        {
            "rule_id": "RULE_GENUINE_01",
            "tenant_id": "corp_alpha",
            "status": "APPROVED",
            "revision": "v1.2",
            "source_doc": "DOC_STD_CONTRACT_2026",
            "source_clauses": ["CLAUSE_LIABILITY_SEC4"],
        }
    ]
    mock_storage.get_clause.return_value = {
        "clause_id": "CLAUSE_LIABILITY_SEC4",
        "document_id": "DOC_STD_CONTRACT_2026",
        "doc_status": "ACTIVE",
    }

    rule_item = {
        "rule_id": "RULE_GENUINE_01",
        "tenant_id": "corp_alpha",
        "status": "APPROVED",
        "revision": "v1.2",
        "source_doc": "DOC_STD_CONTRACT_2026",
        "source_clauses": ["CLAUSE_LIABILITY_SEC4"],
    }

    res = verifier.verify_rule_evidence(
        storage=mock_storage,
        rule_item=rule_item,
        expected_tenant_id="corp_alpha",
    )
    assert res.verified is True
    assert res.status == "VERIFIED"
    assert len(res.failure_reasons) == 0


def test_legal_review_agent_fallback_marks_insufficient_provenance():
    """LegalReviewAgent 高精度降级时，对无出处规则标记 INSUFFICIENT_PROVENANCE 而非 VERIFIED。"""
    agent = LegalReviewAgent()
    mock_storage = MagicMock()
    mock_storage.get_rules.return_value = [
        {
            "rule_id": "RULE_UNVERIFIED_SOURCE",
            "tenant_id": "corp_alpha",
            "clause_category": "LIABILITY",
            "status": "APPROVED",
            "name": "测试规则",
            "parameters": {},
            "source_clauses": [],
        }
    ]

    res = agent.build_high_precision_fallback(
        annotation={
            "id": "ann-fallback-cite",
            "clause_category": "LIABILITY",
            "delta_type": "TEXT_DELTA",
            "standard_span": "std",
            "reviewed_span": "rev",
        },
        error_msg="测试高精度本地研判降级",
        storage=mock_storage,
        tenant_id="corp_alpha",
    )
    refs = res.get("evidence_references", [])
    if refs:
        assert refs[0].get("verification_status") == "INSUFFICIENT_PROVENANCE"
        assert refs[0].get("verification_status") != "VERIFIED"
