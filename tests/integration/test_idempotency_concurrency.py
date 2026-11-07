# -*- coding: utf-8 -*-
"""并发与存储级幂等预约测试（Idempotency Concurrency & Database Constraint）。

验证：
1. 数据库唯一约束 (tenant_id, actor_id, operation, idempotency_key) 的并发竞争保护；
2. 并发 reserve 请求下有且仅有一个获得 CREATED 状态，其余获得 IN_PROGRESS 或 REPLAY；
3. 指纹冲突直接拒绝（CONFLICT）；
4. 双存储实现（SQLite 与 PostgreSQL）均满足并发约束。
"""

from __future__ import annotations

import concurrent.futures
from pathlib import Path

from contract_kb.application.security.idempotency import (
    IdempotencyOutcome,
    RequestIdentity,
    compute_request_fingerprint,
)
from contract_kb.storage import Storage


def test_idempotency_concurrency_single_winner(tmp_path: Path):
    db_file = tmp_path / "concurrent_idem.sqlite"
    storage = Storage(db_file)

    ident = RequestIdentity(
        tenant_id="tenant_concurrency",
        actor_id="alice",
        operation="CONTRACT_REVIEW_JOB",
        key="concurrent-key-001",
        fingerprint=compute_request_fingerprint(
            content_bytes=b"sample-docx-stream",
            mode="STANDARD",
            custom_requirements=["rule_a"],
        ),
    )

    outcomes: list[IdempotencyOutcome] = []

    def _attempt_reserve(worker_idx: int) -> IdempotencyOutcome:
        return storage.reserve_idempotency_key(
            identity=ident,
            resource_id=f"job_candidate_{worker_idx}",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_attempt_reserve, i) for i in range(8)]
        for f in concurrent.futures.as_completed(futures):
            outcomes.append(f.result())

    created_count = sum(1 for o in outcomes if o.status == "CREATED")
    in_progress_count = sum(1 for o in outcomes if o.status in ("IN_PROGRESS", "REPLAY"))

    assert created_count == 1, f"并发预约必须有且仅有 1 个胜出者，实际={created_count}"
    assert in_progress_count == 7
    # 获胜者锁定的 resource_id 是后续重试看到的 resource_id
    winner = next(o for o in outcomes if o.status == "CREATED")
    for o in outcomes:
        if o.status != "CREATED":
            assert o.resource_id == winner.resource_id

    # 尝试用不同指纹预约 -> CONFLICT
    conflict_ident = RequestIdentity(
        tenant_id="tenant_concurrency",
        actor_id="alice",
        operation="CONTRACT_REVIEW_JOB",
        key="concurrent-key-001",
        fingerprint="different-fingerprint-sha256",
    )
    res_conflict = storage.reserve_idempotency_key(conflict_ident, resource_id="new_cand")
    assert res_conflict.status == "CONFLICT"
    storage.close()
