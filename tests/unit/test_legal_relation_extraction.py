# -*- coding: utf-8 -*-
"""Unit tests for Legal Relation Extraction (Phase 12-C)."""

from __future__ import annotations


from contract_kb.modules.retrieval.relations import (
    LegalRelationExtractor,
    LegalRelationType,
)


def test_extract_obligation_and_exception():
    """测试义务与例外抽取：
    原文：乙方应在收到全部资料后5个工作日内交付初稿，但因甲方补充资料造成的延误除外。
    """
    extractor = LegalRelationExtractor()
    clause = {
        "clause_id": "c_rel_01",
        "original_text": "乙方应在收到全部资料后5个工作日内交付初稿，但因甲方补充资料造成的延误除外。",
        "conditions": ["收到全部资料后"],
        "exceptions": ["因甲方补充资料造成的延误除外"],
    }
    relations = extractor.extract_relations(clause)

    types = [r.relation_type for r in relations]
    assert LegalRelationType.OBLIGATION.value in types
    assert LegalRelationType.DEADLINE.value in types

    oblig = [
        r for r in relations if r.relation_type == LegalRelationType.OBLIGATION.value
    ][0]
    assert oblig.subject == "乙方"
    assert "交付初稿" in oblig.action_or_content
    assert oblig.trigger_condition == "收到全部资料后"
    assert oblig.exception_term == "因甲方补充资料造成的延误除外"

    dl = [r for r in relations if r.relation_type == LegalRelationType.DEADLINE.value][
        0
    ]
    assert "5个工作日" in dl.deadline


def test_extract_prohibition_negation():
    """测试禁止性负向义务：甲方不得向第三方披露技术秘密。"""
    extractor = LegalRelationExtractor()
    clause = {
        "clause_id": "c_rel_02",
        "original_text": "甲方不得向第三方披露技术秘密及相关源程序代码。",
    }
    relations = extractor.extract_relations(clause)
    prohibs = [
        r for r in relations if r.relation_type == LegalRelationType.PROHIBITION.value
    ]
    assert len(prohibs) == 1
    assert prohibs[0].subject == "甲方"
    assert "不得" in prohibs[0].action_or_content


def test_extract_cross_reference_and_termination():
    """测试单方解除权与跨条款引用：
    第八条：合同解除依照第十二条执行。
    第十二条：经甲方书面通知30日后乙方有权单方解除本合同。
    """
    extractor = LegalRelationExtractor()
    c8 = {
        "clause_id": "c_8",
        "original_text": "合同解除相关流程依照第十二条执行。",
    }
    rel8 = extractor.extract_relations(c8)
    assert any(
        r.relation_type == LegalRelationType.CROSS_REFERENCE.value
        and r.target_reference == "第十二条"
        for r in rel8
    )

    c12 = {
        "clause_id": "c_12",
        "original_text": "经甲方书面通知30日后乙方有权单方解除本合同。",
    }
    rel12 = extractor.extract_relations(c12)
    terms = [r for r in rel12 if r.relation_type == LegalRelationType.TERMINATION.value]
    assert len(terms) == 1
    assert terms[0].subject == "乙方"
    assert "单方解除" in terms[0].action_or_content
