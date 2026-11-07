# -*- coding: utf-8 -*-
"""企业可观测性指标采集器（MetricsCollector）：
覆盖审核吞吐、成功失败率、各阶段耗时、规则命中分布、LLM 调用/超时/解析失败/降级率、RAG 命中率与 DB/HTTP 延迟。
"""

from __future__ import annotations

import re
import threading
from typing import Any


def normalize_route_path(path: str) -> str:
    """Normalize dynamic IDs (UUID, hex, req_*, task_*) to prevent metric cardinality explosion."""
    p = re.sub(r"/(?:req|task|cc|job)_[a-zA-Z0-9_\-]+", "/{id}", path)
    p = re.sub(r"/[0-9a-fA-F\-]{8,36}", "/{id}", p)
    p = re.sub(r"/\d+", "/{id}", p)
    return p


class MetricsCollector:
    """线程安全进程内指标聚合器（支持 JSON 快照与 Prometheus 文本格式导出）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._counters: dict[str, int] = {
                "review_total": 0,
                "review_succeeded": 0,
                "review_failed": 0,
                "llm_calls": 0,
                "llm_timeouts": 0,
                "llm_parse_errors": 0,
                "llm_fallbacks": 0,
                "rag_queries": 0,
                "rag_hits_queries": 0,
                "db_queries": 0,
                "async_jobs_enqueued": 0,
                "async_jobs_succeeded": 0,
                "async_jobs_dead_letter": 0,
                "lease_steals": 0,
                "budget_exhaustions": 0,
                "circuit_breaks": 0,
            }
            self._gauges: dict[str, float | int] = {
                "queue_backlog": 0,
                "queue_oldest_age_seconds": 0.0,
                "active_leases": 0,
            }
            self._rule_hits: dict[str, int] = {}
            self._http_requests: dict[str, int] = {}
            self._stage_stats: dict[str, dict[str, float]] = {}
            self._db_latency_ms_sum: float = 0.0

    def record_review(
        self,
        *,
        status: str,
        stages: list[dict[str, Any]] | None = None,
        issues: list[dict[str, Any]] | None = None,
    ) -> None:
        with self._lock:
            self._counters["review_total"] += 1
            if status == "COMPLETED":
                self._counters["review_succeeded"] += 1
            else:
                self._counters["review_failed"] += 1

            for st in stages or []:
                sname = str(st.get("stage_name") or "unknown")
                dur = float(st.get("duration_ms") or 0.0)
                entry = self._stage_stats.setdefault(
                    sname, {"count": 0.0, "sum_ms": 0.0, "max_ms": 0.0}
                )
                entry["count"] += 1.0
                entry["sum_ms"] += dur
                if dur > entry["max_ms"]:
                    entry["max_ms"] = dur

            for iss in issues or []:
                rid = str(iss.get("rule_id") or iss.get("category") or "UNCLASSIFIED")
                self._rule_hits[rid] = self._rule_hits.get(rid, 0) + 1
                if iss.get("category") == "NOT_CHECKED":
                    self._counters["llm_fallbacks"] += 1

    def record_llm_call(self, *, outcome: str = "ok") -> None:
        with self._lock:
            self._counters["llm_calls"] += 1
            if outcome == "timeout":
                self._counters["llm_timeouts"] += 1
                self._counters["llm_fallbacks"] += 1
            elif outcome == "parse_error":
                self._counters["llm_parse_errors"] += 1
                self._counters["llm_fallbacks"] += 1
            elif outcome != "ok":
                self._counters["llm_fallbacks"] += 1

    def record_rag_query(self, *, hit_count: int) -> None:
        with self._lock:
            self._counters["rag_queries"] += 1
            if hit_count > 0:
                self._counters["rag_hits_queries"] += 1

    def record_db_query(self, duration_ms: float) -> None:
        with self._lock:
            self._counters["db_queries"] += 1
            self._db_latency_ms_sum += max(0.0, float(duration_ms))

    def record_http_request(self, method: str, path: str, status_code: int) -> None:
        normalized_path = normalize_route_path(path)
        key = f"{method} {normalized_path} [{status_code}]"
        with self._lock:
            self._http_requests[key] = self._http_requests.get(key, 0) + 1

    def record_async_job_event(self, event: str) -> None:
        with self._lock:
            if event in self._counters:
                self._counters[event] += 1

    def record_lease_steal(self) -> None:
        with self._lock:
            self._counters["lease_steals"] += 1

    def record_budget_exhaustion(self) -> None:
        with self._lock:
            self._counters["budget_exhaustions"] += 1

    def record_circuit_break(self) -> None:
        with self._lock:
            self._counters["circuit_breaks"] += 1

    def set_queue_stats(self, backlog_count: int, oldest_age_seconds: float) -> None:
        with self._lock:
            self._gauges["queue_backlog"] = int(backlog_count)
            self._gauges["queue_oldest_age_seconds"] = float(oldest_age_seconds)

    def set_active_leases(self, count: int) -> None:
        with self._lock:
            self._gauges["active_leases"] = int(count)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            total_rev = self._counters["review_total"]
            total_rag = self._counters["rag_queries"]
            total_db = self._counters["db_queries"]
            stages_out = {
                k: {
                    "count": int(v["count"]),
                    "avg_ms": round(v["sum_ms"] / max(1.0, v["count"]), 3),
                    "max_ms": round(v["max_ms"], 3),
                }
                for k, v in sorted(self._stage_stats.items())
            }
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "rates": {
                    "review_success_rate": round(
                        self._counters["review_succeeded"] / total_rev, 4
                    )
                    if total_rev
                    else None,
                    "rag_hit_rate": round(
                        self._counters["rag_hits_queries"] / total_rag, 4
                    )
                    if total_rag
                    else None,
                    "db_avg_latency_ms": round(self._db_latency_ms_sum / total_db, 3)
                    if total_db
                    else 0.0,
                },
                "stage_durations_ms": stages_out,
                "rule_hit_distribution": dict(sorted(self._rule_hits.items())),
                "http_requests": dict(sorted(self._http_requests.items())),
            }

    def render_prometheus(self) -> str:
        snap = self.snapshot()
        lines: list[str] = []
        for k, v in sorted(snap["counters"].items()):
            lines.append(f"contract_kb_{k} {v}")
        for k, v in sorted(snap["gauges"].items()):
            lines.append(f"contract_kb_{k} {v}")
        for sname, st in snap["stage_durations_ms"].items():
            safe = sname.replace('"', "")
            lines.append(f'contract_kb_stage_avg_ms{{stage="{safe}"}} {st["avg_ms"]}')
        return "\n".join(lines) + "\n"


GLOBAL_METRICS = MetricsCollector()
