# -*- coding: utf-8 -*-
"""Information Retrieval evaluation domain models and mathematical metric calculations."""

from __future__ import annotations

import math
from typing import Literal
from pydantic import BaseModel, Field


class EvaluationDatasetManifest(BaseModel):
    """Manifest describing an evaluation dataset, its version, tier, and provenance."""

    dataset_id: str
    dataset_version: str
    provenance_tier: Literal["SYNTHETIC", "REAL_DESENSITIZED", "REAL_MODEL_E2E"]
    split: Literal["train", "dev", "test"]
    qrels_digest: str
    authorization_reference: str | None = None
    created_at: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


def hit_at_k(
    retrieved_ids: list[str],
    relevant_ids: set[str] | list[str],
    k: int,
) -> float | None:
    """Calculate Hit@K (binary success: whether any relevant item is in top-K).

    Returns None if ground truth relevant_ids is empty.
    """
    rel_set = set(relevant_ids)
    if not rel_set:
        return None
    top_k = set(retrieved_ids[:k])
    return 1.0 if bool(top_k.intersection(rel_set)) else 0.0


def recall_at_k(
    retrieved_ids: list[str],
    relevant_ids: set[str] | list[str],
    k: int,
) -> float | None:
    """Calculate true Recall@K = |retrieved_top_k ∩ relevant| / |relevant|.

    Returns None if ground truth relevant_ids is empty.
    """
    rel_set = set(relevant_ids)
    if not rel_set:
        return None
    top_k = set(retrieved_ids[:k])
    hits = top_k.intersection(rel_set)
    return float(len(hits)) / float(len(rel_set))


def legacy_success_rate_at_k(
    retrieved_ids: list[str],
    relevant_ids: set[str] | list[str],
    k: int,
) -> float | None:
    """Legacy metric previously mislabeled as Recall@K in prototype reports.

    Identical to Hit@K (returns 1.0 if any relevant item is in top-K).
    """
    return hit_at_k(retrieved_ids, relevant_ids, k)


def mrr(
    retrieved_ids: list[str],
    relevant_ids: set[str] | list[str],
) -> float | None:
    """Calculate Mean Reciprocal Rank (MRR) for a single query.

    Returns 1 / rank of first relevant item (1-based), or 0.0 if none retrieved.
    Returns None if ground truth relevant_ids is empty.
    """
    rel_set = set(relevant_ids)
    if not rel_set:
        return None
    for idx, doc_id in enumerate(retrieved_ids):
        if doc_id in rel_set:
            return 1.0 / float(idx + 1)
    return 0.0


def ndcg_at_k(
    retrieved_ids: list[str],
    relevant_grades: dict[str, int],
    k: int,
) -> float | None:
    """Calculate true NDCG@K using independent ground-truth qrels for IDCG.

    relevant_grades: map from item_id -> relevance grade (e.g. 2 for perfect, 1 for partial).
    Items not in relevant_grades default to grade 0.
    Returns None if no relevant items exist in ground truth.
    """
    if not relevant_grades:
        return None

    all_ideal_grades = sorted(
        [g for g in relevant_grades.values() if g > 0],
        reverse=True,
    )
    ideal_k = all_ideal_grades[:k]
    if not ideal_k or sum(ideal_k) == 0:
        return None

    idcg = sum(
        (2.0**g - 1.0) / math.log2(i + 2.0)
        for i, g in enumerate(ideal_k)
    )
    if idcg <= 0.0:
        return 0.0

    retrieved_k = retrieved_ids[:k]
    actual_grades = [relevant_grades.get(doc_id, 0) for doc_id in retrieved_k]
    dcg = sum(
        (2.0**g - 1.0) / math.log2(i + 2.0)
        for i, g in enumerate(actual_grades)
    )

    return float(dcg / idcg)
