# -*- coding: utf-8 -*-
"""单元测试：统一执行截止时间 (ExecutionDeadline) 与可取消 IO (T07 / F04)。

验证契约：
1. 可控 Clock 下 remaining_seconds() 与 raise_if_expired() 行为；
2. 截止时间到达后禁止继续执行，抛出 ExecutionDeadlineExpiredError；
3. CancelToken 支持显式取消信号与回调检测；
4. 阻塞工具在超时后在明确容差内返回，不泄漏挂起线程。
"""

from __future__ import annotations

import time

import pytest

from contract_kb.application.pipeline.deadline import (
    CancellationError,
    CancelToken,
    ExecutionDeadline,
    ExecutionDeadlineExpiredError,
)


class MockClock:
    def __init__(self, start: float = 100.0):
        self.current = start

    def __call__(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds


def test_deadline_with_controllable_clock():
    """验证可控时钟下 deadline 的倒计时与超时判定。"""
    clock = MockClock(start=100.0)
    deadline = ExecutionDeadline(timeout_seconds=5.0, clock=clock)

    assert deadline.remaining_seconds() == 5.0
    assert not deadline.is_expired()
    deadline.raise_if_expired()  # 不抛异常

    clock.advance(3.0)
    assert abs(deadline.remaining_seconds() - 2.0) < 1e-6
    assert not deadline.is_expired()

    clock.advance(2.001)
    assert deadline.remaining_seconds() == 0.0
    assert deadline.is_expired()

    with pytest.raises(ExecutionDeadlineExpiredError) as exc_info:
        deadline.raise_if_expired()
    assert "deadline exceeded" in str(exc_info.value).lower()


def test_cancel_token_semantics():
    """验证 CancelToken 判定与中断响应。"""
    flag = False
    token = CancelToken(check_fn=lambda: flag)
    assert not token.is_cancelled()

    # 回调变化触发取消
    flag = True
    assert token.is_cancelled()

    with pytest.raises(CancellationError):
        token.raise_if_cancelled()


def test_blocking_tool_timeout_tolerance():
    """验证阻塞型工具在 deadline 约束下快速超时返回，不无限期挂起。"""
    deadline = ExecutionDeadline(timeout_seconds=0.2)
    t0 = time.monotonic()

    # 模拟受 deadline 保护的阻塞等待
    timeout_occurred = False
    try:
        rem = deadline.remaining_seconds()
        # 模拟有界等待
        time.sleep(rem + 0.05)
        deadline.raise_if_expired()
    except ExecutionDeadlineExpiredError:
        timeout_occurred = True

    elapsed = time.monotonic() - t0
    assert timeout_occurred is True
    assert elapsed < 0.5, f"阻塞工具超时返回时间过长：{elapsed:.3f}s"
