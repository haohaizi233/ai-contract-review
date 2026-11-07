# -*- coding: utf-8 -*-
"""测试 PersistentTaskRunner 接入显式租约栅栏与心跳机制（R02 整改验证）。"""

from __future__ import annotations

import time
from pathlib import Path
import pytest

from contract_kb.storage import Storage
from contract_kb.infrastructure.task_queue.runner import PersistentTaskRunner
from contract_kb.domain.models.execution import JobOutcome


def test_stale_worker_cannot_overwrite_newer_result(tmp_path: Path):
    """
    验证迟到的 Worker A 执行结果无法覆盖已由新 Worker B 提交的结果（租约栅栏生效）：
    1. 提交任务；
    2. Worker A 认领任务，租约 1 秒；
    3. 超时 1.1 秒后，Worker B 抢占任务并提交 SUCCEEDED {'from': 'new_worker'}；
    4. Worker A 迟到完成，尝试提交 {'from': 'stale_worker'}；
    5. 验证数据库中最终结果严格保持为 {'from': 'new_worker'}，未被篡改。
    """
    db_file = tmp_path / "runner_fence.sqlite"
    st = Storage(db_file)
    runner_a = PersistentTaskRunner(st)
    runner_a.register_handler("TEST_FENCE", lambda payload, ctx: {"from": "stale_worker"})

    # 1. 提交任务
    job, _ = runner_a.submit("TEST_FENCE", {}, tenant_id="tenant_audit")
    job_id = job["job_id"]

    # 2. Worker A 认领任务，租约 1 秒
    lease_a = st.claim_job(worker_id="worker_a", lease_seconds=1, job_types=["TEST_FENCE"])
    assert lease_a is not None
    assert lease_a.job_id == job_id

    # 3. 等待租约过期，Worker B 抢占并成功提交
    time.sleep(1.1)
    lease_b = st.claim_job(worker_id="worker_b", lease_seconds=10, job_types=["TEST_FENCE"])
    assert lease_b is not None
    assert lease_b.job_id == job_id
    assert lease_b.lease_token != lease_a.lease_token

    st.finish_job(
        lease_b.job_id,
        lease_b.lease_token,
        JobOutcome(status="SUCCEEDED", result={"from": "new_worker"}),
    )

    before_stale = st.get_async_job(job_id)
    assert before_stale["result"] == {"from": "new_worker"}

    # 4. Worker A 迟到执行（传入旧 job/lease）
    # 使用 runner_a 执行已丢失租约的任务
    runner_a._execute_claimed_job(lease_a)

    # 5. 关键断言：Worker A 尝试提交必须被栅栏拦截，结果绝不能变成 stale_worker
    after_stale = st.get_async_job(job_id)
    assert after_stale["result"] == {"from": "new_worker"}, (
        f"旧 Worker 迟到执行覆盖了新结果！实际结果: {after_stale['result']}"
    )

    st.close()


def test_heartbeat_failure_aborts_stale_worker(tmp_path: Path):
    """验证心跳续租失败时（例如租约已被其他 Worker 抢占），Worker 主动放弃并不覆盖结果。"""
    db_file = tmp_path / "runner_hb_fail.sqlite"
    st = Storage(db_file)
    runner = PersistentTaskRunner(st, lease_seconds=1, heartbeat_interval_seconds=0.2)

    aborted_in_loop = False

    def monitored_handler(payload, ctx):
        nonlocal aborted_in_loop
        for _ in range(20):
            if ctx.is_cancelled():
                aborted_in_loop = True
                return {"status": "CANCELLED"}
            time.sleep(0.1)
        return {"done": True}

    runner.register_handler("MONITORED_JOB", monitored_handler)
    job, _ = runner.submit("MONITORED_JOB", {}, tenant_id="tenant_audit")
    job_id = job["job_id"]

    lease = st.claim_job(worker_id="runner_orig", lease_seconds=1, job_types=["MONITORED_JOB"])
    assert lease is not None

    # 人为修改数据库中的 lease_token，使后续心跳失败
    with st._lock:
        st.conn.execute("UPDATE async_jobs SET lease_token = 'stolen_token_xyz' WHERE job_id = ?", (job_id,))
        st._commit()

    runner._execute_claimed_job(lease)
    assert aborted_in_loop is True, "心跳失败后 Worker 未能主动感知并放弃执行！"

    st.close()


def test_stale_worker_exception_does_not_override_newer_result(tmp_path: Path):
    """验证迟到 Worker 抛出异常时，亦被租约栅栏拦截，不覆盖已完成的 SUCCEEDED 状态。"""
    db_file = tmp_path / "runner_stale_exc.sqlite"
    st = Storage(db_file)
    runner_a = PersistentTaskRunner(st)

    def failing_handler(payload, ctx):
        raise ValueError("Late worker failed")

    runner_a.register_handler("FAIL_JOB", failing_handler)
    job, _ = runner_a.submit("FAIL_JOB", {}, tenant_id="tenant_audit")
    job_id = job["job_id"]

    lease_a = st.claim_job(worker_id="worker_a", lease_seconds=1, job_types=["FAIL_JOB"])
    time.sleep(1.1)

    # Worker B 抢占并完成
    lease_b = st.claim_job(worker_id="worker_b", lease_seconds=10, job_types=["FAIL_JOB"])
    st.finish_job(lease_b.job_id, lease_b.lease_token, JobOutcome(status="SUCCEEDED", result={"ok": True}))

    # Worker A 迟到执行抛错
    runner_a._execute_claimed_job(lease_a)

    final_job = st.get_async_job(job_id)
    assert final_job["status"] == "SUCCEEDED"
    assert final_job["result"] == {"ok": True}

    st.close()



def test_heartbeat_keeps_lease_alive_for_long_running_job(tmp_path: Path):
    """验证长任务期间心跳机制自动续租，防止被其他 Worker 抢占。"""
    db_file = tmp_path / "runner_heartbeat.sqlite"
    st = Storage(db_file)
    runner = PersistentTaskRunner(st, lease_seconds=2, heartbeat_interval_seconds=0.5)

    def long_handler(payload, ctx):
        # 模拟长耗时任务，执行 2.5 秒（超过初始 2 秒租约）
        t0 = time.time()
        while time.time() - t0 < 2.5:
            if ctx.is_cancelled():
                return {"status": "CANCELLED"}
            time.sleep(0.2)
        return {"done": True}

    runner.register_handler("LONG_JOB", long_handler)
    job, _ = runner.submit("LONG_JOB", {}, tenant_id="tenant_audit")

    # 执行任务（内部应开启心跳续租）
    res = runner.process_next()
    assert res is not None
    assert res.get("status") == "SUCCEEDED"
    assert res.get("result") == {"done": True}

    st.close()


def test_heartbeat_failure_aborts_stale_worker(tmp_path: Path):
    """验证心跳续租失败时（例如租约已被其他 Worker 抢占），Worker 主动放弃并不覆盖结果。"""
    db_file = tmp_path / "runner_hb_fail.sqlite"
    st = Storage(db_file)
    runner = PersistentTaskRunner(st, lease_seconds=1, heartbeat_interval_seconds=0.2)

    aborted_in_loop = False

    def monitored_handler(payload, ctx):
        nonlocal aborted_in_loop
        for _ in range(20):
            if ctx.is_cancelled():
                aborted_in_loop = True
                return {"status": "CANCELLED"}
            time.sleep(0.1)
        return {"done": True}

    runner.register_handler("MONITORED_JOB", monitored_handler)
    job, _ = runner.submit("MONITORED_JOB", {}, tenant_id="tenant_audit")
    job_id = job["job_id"]

    lease = st.claim_job(worker_id="runner_orig", lease_seconds=1, job_types=["MONITORED_JOB"])
    assert lease is not None

    # 人为修改数据库中的 lease_token，使后续心跳失败
    with st._lock:
        st.conn.execute("UPDATE async_jobs SET lease_token = 'stolen_token_xyz' WHERE job_id = ?", (job_id,))
        st._commit()

    runner._execute_claimed_job(lease)
    assert aborted_in_loop is True, "心跳失败后 Worker 未能主动感知并放弃执行！"

    st.close()


def test_stale_worker_exception_does_not_override_newer_result(tmp_path: Path):
    """验证迟到 Worker 抛出异常时，亦被租约栅栏拦截，不覆盖已完成的 SUCCEEDED 状态。"""
    db_file = tmp_path / "runner_stale_exc.sqlite"
    st = Storage(db_file)
    runner_a = PersistentTaskRunner(st)

    def failing_handler(payload, ctx):
        raise ValueError("Late worker failed")

    runner_a.register_handler("FAIL_JOB", failing_handler)
    job, _ = runner_a.submit("FAIL_JOB", {}, tenant_id="tenant_audit")
    job_id = job["job_id"]

    lease_a = st.claim_job(worker_id="worker_a", lease_seconds=1, job_types=["FAIL_JOB"])
    time.sleep(1.1)

    # Worker B 抢占并完成
    lease_b = st.claim_job(worker_id="worker_b", lease_seconds=10, job_types=["FAIL_JOB"])
    st.finish_job(lease_b.job_id, lease_b.lease_token, JobOutcome(status="SUCCEEDED", result={"ok": True}))

    # Worker A 迟到执行抛错
    runner_a._execute_claimed_job(lease_a)

    final_job = st.get_async_job(job_id)
    assert final_job["status"] == "SUCCEEDED"
    assert final_job["result"] == {"ok": True}

    st.close()

