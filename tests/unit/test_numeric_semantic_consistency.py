# -*- coding: utf-8 -*-
"""Unit tests for Numeric & Semantic Consistency Checking (Phase 12-E)."""

from __future__ import annotations

import pytest

from contract_kb.modules.retrieval.comparison import ClauseComparisonEngine


def test_numeric_consistency_rate_and_decimal():
    """测试比率的高精度 Decimal 比对（包含千分比与百分比换算）。"""
    engine = ClauseComparisonEngine()

    # 1. 0.05% vs 0.5% (相差 10 倍，必须检出硬差异)
    c_rev = {
        "clause_id": "c_rev_rate",
        "original_text": "甲方逾期付款的，每日应按逾期金额的0.05%向乙方支付违约金。",
    }
    c_ref = {
        "clause_id": "c_ref_rate",
        "original_text": "甲方逾期付款的，每日应按逾期金额的0.5%向乙方支付违约金。",
    }
    res = engine.compare_clauses(c_rev, c_ref)
    assert res.conclusion == "NUMERIC_DIFFERENCE"
    assert len(res.numeric_differences) == 1
    nd = res.numeric_differences[0]
    assert nd.constraint_type == "RATE"
    assert nd.is_mismatch is True
    assert round(nd.difference_magnitude, 4) == 0.0045

    # 2. 3‰ vs 0.3%（两者数学等价，3‰ = 0.003, 0.3% = 0.003，不应误报）
    c_permille = {
        "clause_id": "c_permille",
        "original_text": "每日按逾期金额的3‰支付违约金。",
    }
    c_percent = {
        "clause_id": "c_percent",
        "original_text": "每日按逾期金额的0.3%支付违约金。",
    }
    res_eq = engine.compare_clauses(c_permille, c_percent)
    rate_diffs = [d for d in res_eq.numeric_differences if d.constraint_type == "RATE"]
    assert len(rate_diffs) == 0


def test_workdays_vs_calendar_days_consistency():
    """测试工期与天数字符单位比对（工作日 vs 自然日）。"""
    engine = ClauseComparisonEngine()

    # 1. 10 个工作日 vs 5 个工作日
    c_rev = {
        "clause_id": "c1",
        "original_text": "乙方应于收到材料后10个工作日内交付初稿。",
    }
    c_ref = {
        "clause_id": "c2",
        "original_text": "乙方应于收到材料后5个工作日内交付初稿。",
    }
    res = engine.compare_clauses(c_rev, c_ref)
    assert res.conclusion == "NUMERIC_DIFFERENCE"
    work_diff = [d for d in res.numeric_differences if d.constraint_type == "WORKDAYS"][0]
    assert work_diff.difference_magnitude == 5.0

    # 2. 10 个工作日 vs 10 日（自然日）
    c_cal = {
        "clause_id": "c3",
        "original_text": "乙方应于收到材料后10日内交付初稿。",
    }
    res_mismatch = engine.compare_clauses(c_rev, c_cal)
    assert any(d.constraint_type == "DAY_UNIT_MISMATCH" for d in res_mismatch.numeric_differences)


def test_polarity_inversion_detection():
    """测试法律极性反转（不得 vs 可以 / 应当）。"""
    engine = ClauseComparisonEngine()

    c_prohib = {
        "clause_id": "c_prohib",
        "original_text": "甲方不得向第三方披露技术秘密。",
    }
    c_allow = {
        "clause_id": "c_allow",
        "original_text": "甲方可以向第三方披露技术秘密。",
    }

    res = engine.compare_clauses(c_prohib, c_allow)
    assert res.polarity_inverted is True
    assert res.conclusion == "POLARITY_INVERSION"
    assert res.requires_human_review is True
    assert any("法律极性反转" in f for f in res.summary_findings)


def test_subject_direction_inversion():
    """测试义务主体方向反转（甲方应当 vs 乙方应当）。"""
    engine = ClauseComparisonEngine()

    c_a = {
        "clause_id": "c_a",
        "original_text": "甲方应当在5个工作日内完成交付成果。",
    }
    c_b = {
        "clause_id": "c_b",
        "original_text": "乙方应当在5个工作日内完成交付成果。",
    }

    res = engine.compare_clauses(c_a, c_b)
    assert res.conclusion in ("SUBJECT_DIFFERENCE", "NUMERIC_DIFFERENCE")
    assert len(res.subjects_changed) == 1
    assert res.subjects_changed[0]["reviewed_subject"] == "甲方"
    assert res.subjects_changed[0]["reference_subject"] == "乙方"


def test_condition_and_exception_differences():
    """测试生效前提与免责例外差异。"""
    engine = ClauseComparisonEngine()

    c_rev = {
        "clause_id": "c_rev",
        "original_text": "乙方对所有延期承担违约责任。",
        "conditions": ["乙方逾期"],
        "exceptions": [],
    }
    c_ref = {
        "clause_id": "c_ref",
        "original_text": "乙方承担违约责任，不可抗力及甲方原因除外。",
        "conditions": ["乙方逾期"],
        "exceptions": ["不可抗力及甲方原因除外"],
    }

    res = engine.compare_clauses(c_rev, c_ref)
    assert res.conclusion == "EXCEPTION_DIFFERENCE"
    assert len(res.exception_differences) == 1
    assert "不可抗力及甲方原因除外" in str(res.exception_differences[0]["reference_exceptions"])
