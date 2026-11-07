# -*- coding: utf-8 -*-
"""Phase 12 结构化条款比对引擎困难负样本与法律逻辑对抗测试。"""

from __future__ import annotations


from contract_kb.modules.retrieval.comparison import ClauseComparisonEngine


def test_asymmetric_party_disclosure_reversal() -> None:
    """测试保密义务中非对称披露主体倒错检出（甲方披露/乙方保密 vs 乙方披露/甲方保密）。"""
    engine = ClauseComparisonEngine()

    c_rev = {
        "clause_id": "c_rev_conf",
        "original_text": "甲方在合作过程中披露的商业秘密，乙方应采取严格保密措施并承担保密义务。",
    }
    c_ref = {
        "clause_id": "c_ref_conf",
        "original_text": "乙方在合作过程中披露的商业秘密，甲方应采取严格保密措施并承担保密义务。",
    }

    res = engine.compare_clauses(c_rev, c_ref)
    assert res.conclusion == "SUBJECT_DIFFERENCE"
    assert any("非对称" in f or "主体倒错" in f for f in res.summary_findings)
    assert res.requires_human_review is True


def test_exception_condition_mismatch() -> None:
    """测试但书例外条件的不对称检出（'除非经书面同意' vs 无例外绝对禁止）。"""
    engine = ClauseComparisonEngine()

    c_rev = {
        "clause_id": "c_rev_exc",
        "original_text": "除非经甲方事先书面同意，否则乙方不得将本协议项下的权利义务转让给第三方。",
    }
    c_ref = {
        "clause_id": "c_ref_exc",
        "original_text": "乙方在任何情况下均不得将本协议项下的权利义务转让给第三方。",
    }

    res = engine.compare_clauses(c_rev, c_ref)
    assert res.conclusion == "EXCEPTION_DIFFERENCE"
    assert any("例外条件不对称" in f for f in res.summary_findings)


def test_money_amounts_high_precision_difference() -> None:
    """测试金额绝对值的 Decimal 高精度比对检出。"""
    engine = ClauseComparisonEngine()

    c_rev = {
        "clause_id": "c_rev_money",
        "original_text": "如乙方逾期交付，应向甲方支付违约金人民币 10,000 元。",
    }
    c_ref = {
        "clause_id": "c_ref_money",
        "original_text": "如乙方逾期交付，应向甲方支付违约金人民币 100,000 元。",
    }

    res = engine.compare_clauses(c_rev, c_ref)
    assert res.conclusion == "NUMERIC_DIFFERENCE"
    assert len(res.numeric_differences) == 1
    diff = res.numeric_differences[0]
    assert diff.constraint_type == "MONEY"
    assert diff.difference_magnitude == 90000.0
    assert diff.is_mismatch is True
