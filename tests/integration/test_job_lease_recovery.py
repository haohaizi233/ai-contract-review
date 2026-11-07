# -*- coding: utf-8 -*-
"""集成测试：持久化作业租约、心跳续约与崩溃自动恢复（T06 / F03 / ADR-004）。

严格验证：
1. 崩溃恢复：Worker 在任务 RUNNING 期间假死或崩溃退出，租约过期后新 Worker 能够自动接管并成功执行；
2. 租约 CAS 保护：过期失效的旧 Worker 无法覆盖新 Worker 的执行成果（finish 校验 lease_token）；
3. 心跳续约：长时间执行任务通过心跳保持租约，不被其他 Worker 抢占；
4. 重试耗尽保护：租约超时反复失联达到 max_attempts 后自动进入 DEAD_LETTER。
"""

from __future__ import annotations

from pathlib import Path
import time


from contract_kb.domain.models.execution import JobLease, JobOutcome
from contract_kb.infrastructure.task_queue.runner import PersistentTaskRunner
from contract_kb.storage import Storage


def test_crash_recovery_after_lease_expires(tmp_path: Path):
    """验证崩溃后租约过期，新 Worker 自动认领并恢复执行。"""
    st = Storage(tmp_path / "lease_recovery.sqlite")
    runner = PersistentTaskRunner(st, base_backoff_seconds=0.0)

    # 1. 提交异步作业
    job, created = runner.submit(
        "CONTRACT_REVIEW",
        {"mode": "STANDARD", "test": "crash_recovery"},
        tenant_id="t1",
        created_by="alice",
    )
    job_id = job["job_id"]

    # 2. Worker 1 认领作业，设置租约时长为 1 秒（模拟短期租约）
    lease1 = st.claim_job(worker_id="worker_alpha", lease_seconds=1, job_types=["CONTRACT_REVIEW"])
    assert lease1 is not None
    assert isinstance(lease1, JobLease)
    assert lease1.job_id == job_id
    assert lease1.worker_id == "worker_alpha"

    # 立即再次认领：租约未到期，其他 Worker 绝不能认领到
    lease_busy = st.claim_job(worker_id="worker_beta", lease_seconds=1, job_types=["CONTRACT_REVIEW"])
    assert lease_busy is None

    # 3. 模拟 Worker 1 崩溃（未调用 finish_job），等待租约过期（> 1秒）
    time.sleep(1.2)

    # 4. Worker 2 启动认领：租约已过期，Worker 2 成功接管
    lease2 = st.claim_job(worker_id="worker_beta", lease_seconds=10, job_types=["CONTRACT_REVIEW"])
    assert lease2 is not None
    assert lease2.job_id == job_id
    assert lease2.worker_id == "worker_beta"
    assert lease2.lease_token != lease1.lease_token
    assert lease2.attempt == 2

    # 5. Worker 1 假死复活后尝试提交结果：由于 lease_token 已变更，CAS 提交必须失败
    ok_stale = st.finish_job(
        job_id=job_id,
        lease_token=lease1.lease_token,
        outcome=JobOutcome(status="SUCCEEDED", result={"stale": "corrupt_data"}),
    )
    assert ok_stale is False, "已过期的旧 Worker 绝不能成功写入终态"

    # 6. Worker 2 正常提交结果：成功
    ok_new = st.finish_job(
        job_id=job_id,
        lease_token=lease2.lease_token,
        outcome=JobOutcome(status="SUCCEEDED", result={"valid": "fresh_data"}),
    )
    assert ok_new is True

    final_job = st.get_async_job(job_id)
    assert final_job["status"] == "SUCCEEDED"
    assert final_job["result"] == {"valid": "fresh_data"}
    st.close()


def test_heartbeat_renewal_prevents_premature_reclaim(tmp_path: Path):
    """验证定期心跳延长租约，防止长任务被误抢占。"""
    st = Storage(tmp_path / "heartbeat_test.sqlite")
    st.enqueue_async_job(
        {
            "job_id": "job_long_running",
            "job_type": "CONTRACT_REVIEW",
            "tenant_id": "t1",
            "created_by": "alice",
        }
    )

    lease = st.claim_job(worker_id="worker_1", lease_seconds=1, job_types=["CONTRACT_REVIEW"])
    assert lease is not None

    # 经过 0.6 秒后执行心跳续约 2 秒
    time.sleep(0.6)
    renewed = st.heartbeat_job(lease.job_id, lease.lease_token, extension_seconds=2)
    assert renewed is True

    # 再等待 0.8 秒（距离最初认领已 1.4 秒，原租约 1 秒已过，但因续约租约仍在）
    time.sleep(0.8)
    competing_lease = st.claim_job(worker_id="worker_2", lease_seconds=1, job_types=["CONTRACT_REVIEW"])
    assert competing_lease is None, "心跳续约中的作业不得被抢占"

    # 成功完成
    st.finish_job(
        lease.job_id,
        lease.lease_token,
        JobOutcome(status="SUCCEEDED", result={"status": "ok"}),
    )
    st.close()


def test_lease_expiry_exhaustion_enters_dead_letter(tmp_path: Path):
    """验证多次失联达到最大重试次数后进入 DEAD_LETTER 死信状态。"""
    st = Storage(tmp_path / "dlq_test.sqlite")
    st.enqueue_async_job(
        {
            "job_id": "job_crash_loop",
            "job_type": "CONTRACT_REVIEW",
            "tenant_id": "t1",
            "created_by": "alice",
            "max_attempts": 2,
        }
    )

    # 第 1 次认领与失联
    l1 = st.claim_job(worker_id="w1", lease_seconds=1, job_types=["CONTRACT_REVIEW"])
    assert l1 is not None and l1.attempt == 1
    time.sleep(1.1)

    # 第 2 次认领与失联
    l2 = st.claim_job(worker_id="w2", lease_seconds=1, job_types=["CONTRACT_REVIEW"])
    assert l2 is not None and l2.attempt == 2
    time.sleep(1.1)

    # 第 3 次尝试认领：已耗尽 max_attempts(2)，必须进入 DEAD_LETTER
    l3 = st.claim_job(worker_id="w3", lease_seconds=1, job_types=["CONTRACT_REVIEW"])
    assert l3 is None

    final_job = st.get_async_job("job_crash_loop")
    assert final_job["status"] == "DEAD_LETTER"
    assert "lease_expired" in str(final_job.get("dead_letter_reason"))
    st.close()
