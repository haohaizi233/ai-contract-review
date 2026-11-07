# -*- coding: utf-8 -*-
"""Integration tests for Parent-Child Retrieval with Governed Retrieval Service (Phase 12-D)."""

from __future__ import annotations

import pytest

from contract_kb.modules.retrieval import (
    GovernedRetrievalService,
    HierarchicalKnowledgeTree,
)


def test_parent_child_retrieval_integration(storage):
    """验证知识树 Parent-Child 扩展与受控检索服务的集成。"""
    svc = GovernedRetrievalService(storage)

    reviewed_clauses = [
        {
            "clause_id": "c_sub_8_2",
            "clause_number": "8.2",
            "article_title": "第八条 违约责任与免责通则",
            "clause_category": "LIABILITY",
            "original_text": "乙方逾期交付成果超过10个工作日的，每日按未付金额0.05%支付违约金。",
            "conditions": ["乙方逾期交付成果超过10个工作日"],
            "source_node_ids": ["node_8_2"],
        }
    ]

    # 构建带父级章条的知识树
    tree = HierarchicalKnowledgeTree.build_from_clauses(
        document_id="doc_reviewed_01",
        clauses=[
            {
                "clause_id": "c_art_8",
                "clause_number": None,
                "article_title": "第八条 违约责任与免责通则",
                "clause_category": "LIABILITY",
                "original_text": "第八条 违约责任与免责通则：在乙方没有重大过失的情况下，免责及责任如下：",
                "conditions": ["乙方没有重大过失的情况下"],
                "exceptions": ["不可抗力及甲方原因除外"],
                "source_node_ids": ["node_art_8"],
            },
            {
                "clause_id": "c_sub_8_2",
                "clause_number": "8.2",
                "article_title": "第八条 违约责任与免责通则",
                "clause_category": "LIABILITY",
                "original_text": "乙方逾期交付成果超过10个工作日的，每日按未付金额0.05%支付违约金。",
                "conditions": ["乙方逾期交付成果超过10个工作日"],
                "source_node_ids": ["node_8_2"],
            }
        ],
    )

    bundle = svc.retrieve_for_clause(
        reviewed_clauses[0],
        top_k=2,
        tenant_id="t1",
        knowledge_tree=tree,
    )

    assert bundle.target_clause_id == "c_sub_8_2"
    assert bundle.parent_clause is not None
    assert "第八条" in bundle.parent_clause["title"]
    assert "乙方没有重大过失的情况下" in bundle.related_conditions
    assert "不可抗力及甲方原因除外" in bundle.related_exceptions

    # 提示词中包含章条通则与免责
    ctx_text = bundle.prompt_context_text()
    assert "【所属章条背景】" in ctx_text
    assert "【通用前置条件】" in ctx_text
