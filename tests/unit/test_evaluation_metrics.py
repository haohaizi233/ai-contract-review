# -*- coding: utf-8 -*-
"""Unit tests for information retrieval evaluation metrics and dataset manifests."""

from __future__ import annotations

import math

from contract_kb.domain.models.evaluation import (
    EvaluationDatasetManifest,
    hit_at_k,
    recall_at_k,
    ndcg_at_k,
    mrr,
    legacy_success_rate_at_k,
)
from contract_kb.retrieval_golden import BENCHMARK_15_HARD_NEGATIVE
from contract_kb.evaluate import EVALUATE_30_BROAD_BUSINESS


def test_recall_at_k_calculation_versus_hit_at_k():
    """Verify Recall@K calculates retrieved relevant / total relevant, distinct from Hit@K."""
    relevant_ids = {"doc1_c1", "doc1_c2", "doc2_c3"}
    retrieved = ["doc1_c1", "doc9_c9", "doc8_c8"]

    # In top-2: retrieved has "doc1_c1", which is 1 out of 3 relevant items
    hit_2 = hit_at_k(retrieved, relevant_ids, k=2)
    recall_2 = recall_at_k(retrieved, relevant_ids, k=2)

    assert hit_2 == 1.0, (
        "Hit@2 should be 1.0 since at least one relevant item was retrieved"
    )
    assert recall_2 is not None
    assert math.isclose(recall_2, 1.0 / 3.0, rel_tol=1e-3), (
        f"Recall@2 must be 1/3 (retrieved 1 out of 3 relevant), got {recall_2}"
    )
    assert hit_2 != recall_2, (
        "Hit@K and Recall@K must be visibly distinct when not all relevant items are retrieved"
    )


def test_recall_at_k_empty_relevant_returns_none():
    """Empty ground truth must yield None (N/A), not false 1.0 or 0.0."""
    retrieved = ["doc1_c1", "doc2_c2"]
    assert recall_at_k(retrieved, set(), k=5) is None
    assert hit_at_k(retrieved, set(), k=5) is None


def test_ndcg_at_k_uses_independent_qrels_idcg():
    """IDCG must be computed from full independent ground-truth qrels, not just retrieved items."""
    # Ground truth: 2 highly relevant (grade 2), 1 moderately relevant (grade 1)
    qrels = {
        "c_gold_1": 2,
        "c_gold_2": 2,
        "c_gold_3": 1,
    }

    # Search engine missed the two grade 2 items, only found the grade 1 item at rank 1
    retrieved = ["c_gold_3", "c_junk_1", "c_junk_2"]

    # True IDCG for top-3 from qrels: [2, 2, 1]
    # IDCG = (2^2-1)/log2(2) + (2^2-1)/log2(3) + (2^1-1)/log2(4) = 3 + 1.8928 + 0.5 = 5.3928
    # DCG = (2^1-1)/log2(2) + 0 + 0 = 1.0
    # Expected NDCG@3 = 1.0 / 5.3928 =~ 0.1854
    ndcg_3 = ndcg_at_k(retrieved, qrels, k=3)
    assert ndcg_3 is not None
    assert ndcg_3 < 0.25, (
        f"NDCG@3 should be ~0.1854 due to missing grade 2 items, got {ndcg_3}"
    )

    # Verify IDCG does NOT degrade when fewer relevant items are retrieved
    retrieved_empty = ["c_junk_1", "c_junk_2"]
    ndcg_empty = ndcg_at_k(retrieved_empty, qrels, k=3)
    assert ndcg_empty == 0.0


def test_mrr_calculation():
    """MRR should be reciprocal rank of first relevant item."""
    relevant_ids = {"c_gold"}
    assert mrr(["c_junk", "c_gold"], relevant_ids) == 0.5
    assert mrr(["c_gold", "c_junk"], relevant_ids) == 1.0
    assert mrr(["c_junk", "c_other"], relevant_ids) == 0.0
    assert mrr(["c_gold"], set()) is None


def test_legacy_success_rate_labeled_explicitly():
    """The old calculation is explicitly preserved as legacy_success_rate_at_k."""
    relevant_ids = {"c1", "c2", "c3"}
    retrieved = ["c1", "junk"]
    # Legacy calculation treated any hit as 1.0
    assert legacy_success_rate_at_k(retrieved, relevant_ids, k=2) == 1.0


def test_dataset_manifest_and_split_versioning():
    """EvaluationDatasetManifest validates schema, hash digest, and tier."""
    manifest = EvaluationDatasetManifest(
        dataset_id="eval_synthetic_v1",
        dataset_version="1.0.0",
        provenance_tier="SYNTHETIC",
        split="test",
        qrels_digest="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        authorization_reference="INTERNAL_SYNTHETIC",
    )
    assert manifest.dataset_id == "eval_synthetic_v1"
    assert manifest.provenance_tier == "SYNTHETIC"


def test_synthetic_qrels_fixture_loads_and_evaluates():
    """Verify the fixture JSON adheres to schema and metrics compute over it."""
    import json
    from pathlib import Path

    p = Path("tests/fixtures/evaluation_synthetic/qrels.json")
    data = json.loads(p.read_text(encoding="utf-8"))
    manifest = EvaluationDatasetManifest(**data["manifest"])
    assert manifest.provenance_tier == "SYNTHETIC"

    q0 = data["queries"][0]
    qrels = q0["qrels"]
    retrieved = ["doc1_clause1", "doc_junk"]
    r_at_2 = recall_at_k(retrieved, list(qrels.keys()), k=2)
    assert r_at_2 is not None
    assert math.isclose(r_at_2, 1.0 / 3.0, rel_tol=1e-3)


def test_query_sets_are_separated_and_versioned():
    """15 benchmark hard-negative queries and 30 broad business evaluate queries are distinct sets."""
    assert len(BENCHMARK_15_HARD_NEGATIVE) == 15
    assert len(EVALUATE_30_BROAD_BUSINESS) == 30
    assert id(BENCHMARK_15_HARD_NEGATIVE) != id(EVALUATE_30_BROAD_BUSINESS)
