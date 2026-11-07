# -*- coding: utf-8 -*-
"""Integration tests for Cross-Encoder & Structural Comparison Pipeline (Phase 12-E)."""

from __future__ import annotations

import pytest

from contract_kb.modules.retrieval import (
    ClauseComparisonEngine,
    HttpCrossEncoderReranker,
    LexicalCoverageReranker,
)


def test_cross_encoder_and_structural_verification_pipeline():
    """测试重排层与结构化差异校验层的协同：
    即使重排层因字面相似度给出高分，结构化校验层仍能截断并发现法律极性倒错与数值差异。
    """
    reranker = LexicalCoverageReranker()
    comparison_engine = ClauseComparisonEngine()

    query = "甲方不得向第三方披露技术秘密"
    candidate_standard = {
        "chunk_id": "std_clause_sec",
        "clause_id": "std_clause_sec",
        "text": "甲方可以向第三方披露技术秘密及相关源代码资料。",
        "original_text": "甲方可以向第三方披露技术秘密及相关源代码资料。",
    }

    # 1. 词法/重排打分
    reranked = reranker.rerank(query, [candidate_standard], top_k=1)
    assert len(reranked) == 1
    hit = reranked[0]
    # 字面重合度很高（除不得/可以外，其余字面完全一致）
    assert hit.get("rerank_score", 0) > 0.60

    # 2. 结构化对比引擎介入
    comp = comparison_engine.compare_clauses(
        {"clause_id": "c_user", "original_text": query},
        hit,
        similarity_score=hit.get("rerank_score"),
    )

    # 核心硬约束：结构化比对层必须识别出法律极性反转，绝不因字面相似度高而判定为合规等价
    assert comp.polarity_inverted is True
    assert comp.conclusion == "POLARITY_INVERSION"
    assert comp.requires_human_review is True
    assert comp.similarity_score == hit.get("rerank_score")
