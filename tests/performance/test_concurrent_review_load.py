# -*- coding: utf-8 -*-
"""Performance and concurrent load test harness: verifying multi-threaded review execution and latency distributions."""

from __future__ import annotations

import concurrent.futures
import time
from pathlib import Path
import numpy as np

from contract_kb.application.security.idempotency import RequestIdentity
from contract_kb.storage import Storage


def test_concurrent_review_load_distribution(tmp_path: Path):
    """Run concurrent task persistence and idempotency checks across thread pools, measuring P50/P95/P99."""
    db_path = tmp_path / "load_test.db"
    st = Storage(db_path)

    concurrency_levels = [1, 4, 8]
    tasks_per_level = 20

    for workers in concurrency_levels:
        latencies: list[float] = []

        def worker_task(idx: int) -> tuple[int, float]:
            t0 = time.perf_counter()
            task_id = f"task_w{workers}_{idx}"
            identity = RequestIdentity(
                tenant_id="tenant_load",
                actor_id="load_tester",
                operation="CREATE_REVIEW",
                key=f"idem_{workers}_{idx}",
                fingerprint=f"req_hash_{workers}_{idx}",
            )
            with st.unit_of_work():
                st.reserve_idempotency_key(identity, resource_id=task_id)
                st.save_task(
                    task_id=task_id,
                    document_id=f"doc_{workers}_{idx}",
                    mode="DETERMINISTIC",
                    status="COMPLETED",
                    meta={"contract_name": f"Load Contract {idx}"},
                    tenant_id="tenant_load",
                    created_by="load_tester",
                )
            dur = time.perf_counter() - t0
            return idx, dur

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(worker_task, i) for i in range(tasks_per_level)]
            for fut in concurrent.futures.as_completed(futures):
                idx, dur = fut.result()
                latencies.append(dur)

        assert len(latencies) == tasks_per_level
        arr = np.array(latencies)
        p50 = float(np.percentile(arr, 50))
        p95 = float(np.percentile(arr, 95))
        p99 = float(np.percentile(arr, 99))

        # Latency assertions (should execute within reasonable thresholds on local SQLite)
        assert p50 < 0.5, f"Workers {workers}: P50 latency too high: {p50:.3f}s"
        assert p95 < 1.5, f"Workers {workers}: P95 latency too high: {p95:.3f}s"
        assert p99 < 3.0, f"Workers {workers}: P99 latency too high: {p99:.3f}s"

    # Verify total persisted rows match
    _page, total = st.list_tasks(tenant_id="tenant_load")
    assert total == sum([tasks_per_level for _ in concurrency_levels])
    st.close()
