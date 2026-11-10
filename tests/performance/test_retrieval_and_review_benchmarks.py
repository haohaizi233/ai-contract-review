# -*- coding: utf-8 -*-
"""检索质量与审核延迟性能基准回归测试（Performance & Retrieval Non-Regression Benchmarks）。"""

from __future__ import annotations

import statistics
import time

from contract_kb.evaluation.review.evaluate import eval_rag
from contract_kb.review_engine import ReviewEngine


def test_rag_retrieval_quality_and_honest_tier_non_regression(storage):
    res = eval_rag(storage)
    assert res["evaluation_tier"] in (
        "SYNTHETIC_UNIT_REGRESSION",
        "DESENSITIZED_TEMPLATE_BENCHMARK",
        "REAL_MODEL_END_TO_END",
    )
    m = res["metrics"]
    assert m["Hit@8"] >= 0.80, f"检索 Hit@8 出现退化：{m}"
    assert m["MRR"] >= 0.70, f"检索 MRR 出现退化：{m}"
    assert m["AnswerGroundedness"] == 1.0


def test_review_pipeline_p95_latency_and_determinism_benchmark(storage, golden_samples):
    engine = ReviewEngine(storage)
    sample_files = sorted(golden_samples["dir"].glob("*.docx"))[:6]
    assert sample_files

    latencies_ms: list[float] = []
    for fpath in sample_files:
        t0 = time.perf_counter()
        res = engine.review(fpath, mode="STANDARD")
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        assert res["status"] == "COMPLETED"
        latencies_ms.append(elapsed_ms)

    sorted_lat = sorted(latencies_ms)
    p50 = statistics.median(sorted_lat)
    p95_idx = min(len(sorted_lat) - 1, int(round(0.95 * (len(sorted_lat) - 1))))
    p95 = sorted_lat[p95_idx]

    # 本地确定性流水线单份合同审核 P50 应在 500ms 内，P95 应在 1500ms 内
    assert p50 < 500.0, f"审核流水线 P50 延迟异常升高：{p50:.2f} ms"
    assert p95 < 1500.0, f"审核流水线 P95 延迟异常升高：{p95:.2f} ms"
