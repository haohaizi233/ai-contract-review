# -*- coding: utf-8 -*-
"""E2E 测试：独立进程 Worker 崩溃后由新进程 Worker 自动租约恢复（T06 / F03 / ADR-004）。

验证多进程场景：
1. 启动子进程 Worker 认领作业并模拟假死/崩溃退出；
2. 数据库状态在租约未过期前保持锁定；
3. 租约过期后新启动的 Worker 进程成功接管作业并执行至终态；
4. 作业状态最终进入 SUCCEEDED，断言作业绝不会永久悬挂在 RUNNING。
"""

from __future__ import annotations

import multiprocessing
import os
import sys
import time
from pathlib import Path


from contract_kb.domain.models.execution import JobOutcome
from contract_kb.storage import Storage


def _crashing_worker_process(db_path_str: str, lease_seconds: int) -> None:
    """模拟在认领任务后突然崩溃退出的 Worker 进程。"""
    st = Storage(db_path_str)
    lease = st.claim_job(worker_id="crashed_worker", lease_seconds=lease_seconds)
    if lease is not None:
        # 模拟执行中途突然崩溃（SIGKILL / os._exit 模拟硬件宕机或 OOM）
        os._exit(42)
    sys.exit(1)


def _recovering_worker_process(db_path_str: str) -> None:
    """新 Worker 进程接管并完成作业。"""
    st = Storage(db_path_str)
    lease = st.claim_job(worker_id="rescue_worker", lease_seconds=10)
    if lease is not None:
        st.finish_job(
            job_id=lease.job_id,
            lease_token=lease.lease_token,
            outcome=JobOutcome(
                status="SUCCEEDED",
                result={"recovered_by": "rescue_worker", "attempt": lease.attempt},
            ),
        )
        sys.exit(0)
    sys.exit(2)


def test_independent_worker_process_crash_and_recovery(tmp_path: Path):
    """跨独立操作系统进程的崩溃与租约接管 E2E 验证。"""
    db_file = tmp_path / "proc_crash.sqlite"
    st = Storage(db_file)
    st.enqueue_async_job(
        {
            "job_id": "job_e2e_crash",
            "job_type": "CONTRACT_REVIEW",
            "tenant_id": "t_corp",
            "created_by": "admin",
            "max_attempts": 3,
        }
    )
    st.close()

    # 1. 启动第 1 个子进程，设置租约时长为 1 秒，然后模拟突然崩溃
    ctx = multiprocessing.get_context("spawn")
    p1 = ctx.Process(target=_crashing_worker_process, args=(str(db_file), 1))
    p1.start()
    p1.join(timeout=5)
    assert p1.exitcode == 42, "子进程模拟崩溃退出"

    # 2. 检查此时状态已为 RUNNING，但 Worker 已死
    st2 = Storage(db_file)
    mid_job = st2.get_async_job("job_e2e_crash")
    assert mid_job["status"] == "RUNNING"
    assert mid_job["worker_id"] == "crashed_worker"

    # 3. 等待租约过期（> 1 秒）
    time.sleep(1.2)

    # 4. 启动第 2 个子进程进行崩溃恢复
    p2 = ctx.Process(target=_recovering_worker_process, args=(str(db_file),))
    p2.start()
    p2.join(timeout=5)
    assert p2.exitcode == 0, "新 Worker 进程成功接管并退出"

    # 5. 断言作业绝不悬挂在 RUNNING，而是正确进入 SUCCEEDED 终态
    final_job = st2.get_async_job("job_e2e_crash")
    assert final_job["status"] == "SUCCEEDED"
    assert final_job["result"] == {"recovered_by": "rescue_worker", "attempt": 2}
    assert final_job["worker_id"] == "rescue_worker"
    st2.close()
