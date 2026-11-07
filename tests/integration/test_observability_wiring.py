# -*- coding: utf-8 -*-
"""Integration tests for observability metrics collection, route normalization, and operational counters."""

from __future__ import annotations

from contract_kb.infrastructure.observability.metrics import (
    MetricsCollector,
    normalize_route_path,
)


def test_normalize_route_path_prevents_cardinality_explosion():
    """Verify route templates collapse dynamic task and request IDs."""
    path1 = "/api/reviews/req_12345678abcdef"
    path2 = "/api/reviews/req_9876543210fedc"
    path3 = "/api/v1/contracts/cc_8a7b6c5d4e3f2a1b"

    assert normalize_route_path(path1) == "/api/reviews/{id}"
    assert normalize_route_path(path2) == "/api/reviews/{id}"
    assert normalize_route_path(path3) == "/api/v1/contracts/{id}"


def test_metrics_collector_record_http_request_normalizes_routes():
    """Verify record_http_request aggregates dynamic URLs into a single template counter."""
    collector = MetricsCollector()
    collector.record_http_request("GET", "/api/reviews/req_abc111", 200)
    collector.record_http_request("GET", "/api/reviews/req_xyz222", 200)

    snap = collector.snapshot()
    assert "GET /api/reviews/{id} [200]" in snap["http_requests"]
    assert snap["http_requests"]["GET /api/reviews/{id} [200]"] == 2
    assert "GET /api/reviews/req_abc111 [200]" not in snap["http_requests"]


def test_metrics_collector_counts_llm_and_rag_events():
    """Verify LLM timeout, parse error, and RAG query events increment metrics accurately."""
    collector = MetricsCollector()
    collector.record_llm_call(outcome="ok")
    collector.record_llm_call(outcome="timeout")
    collector.record_llm_call(outcome="parse_error")

    collector.record_rag_query(hit_count=3)
    collector.record_rag_query(hit_count=0)

    snap = collector.snapshot()
    assert snap["counters"]["llm_calls"] == 3
    assert snap["counters"]["llm_timeouts"] == 1
    assert snap["counters"]["llm_parse_errors"] == 1
    assert snap["counters"]["llm_fallbacks"] == 2

    assert snap["counters"]["rag_queries"] == 2
    assert snap["counters"]["rag_hits_queries"] == 1
    assert snap["rates"]["rag_hit_rate"] == 0.5


def test_zero_sample_rate_returns_none_not_false_one():
    """Verify zero review samples report None rather than false 1.0 (100%)."""
    collector = MetricsCollector()
    snap = collector.snapshot()
    assert snap["rates"]["review_success_rate"] is None


def test_operational_metrics_and_queue_stats():
    """Verify lease steals, budget exhaustions, circuit breaks, and queue stats are tracked."""
    collector = MetricsCollector()
    collector.record_lease_steal()
    collector.record_budget_exhaustion()
    collector.record_circuit_break()
    collector.set_queue_stats(backlog_count=15, oldest_age_seconds=120.5)
    collector.set_active_leases(count=4)

    snap = collector.snapshot()
    assert snap["counters"]["lease_steals"] == 1
    assert snap["counters"]["budget_exhaustions"] == 1
    assert snap["counters"]["circuit_breaks"] == 1
    assert snap["gauges"]["queue_backlog"] == 15
    assert snap["gauges"]["queue_oldest_age_seconds"] == 120.5
    assert snap["gauges"]["active_leases"] == 4
