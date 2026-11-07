# -*- coding: utf-8 -*-
"""执行截止时间 (ExecutionDeadline) 与取消令牌 (CancelToken)（T07 / F04）。

提供进程内高精度单调时钟计时、外部可控 Clock 注入、以及持久化 expires_at 支持。
确保每个阶段及最终提交点均强制进行超时与取消安全检查。
"""

from __future__ import annotations

import time
from typing import Callable


class ExecutionDeadlineExpiredError(TimeoutError):
    """流水线执行超时。"""

    pass


class CancellationError(RuntimeError):
    """流水线执行被显式取消。"""

    pass


class ExecutionDeadline:
    """统一执行截止时间管理对象。"""

    def __init__(
        self,
        timeout_seconds: float,
        *,
        started_monotonic: float | None = None,
        expires_at: float | None = None,
        clock: Callable[[], float] | None = None,
    ):
        self.timeout_seconds = max(0.0001, float(timeout_seconds))
        self._clock: Callable[[], float] = clock or time.monotonic
        self.started_monotonic = (
            started_monotonic if started_monotonic is not None else self._clock()
        )
        self.expires_at = (
            expires_at
            if expires_at is not None
            else (time.time() + self.timeout_seconds)
        )

    def remaining_seconds(self) -> float:
        now_mono = self._clock()
        remaining = self.timeout_seconds - (now_mono - self.started_monotonic)
        return max(0.0, remaining)

    def is_expired(self) -> bool:
        return self.remaining_seconds() <= 0.0

    def raise_if_expired(self) -> None:
        if self.is_expired():
            raise ExecutionDeadlineExpiredError(
                f"Execution deadline exceeded: timeout of {self.timeout_seconds:.3f}s reached"
            )


class CancelToken:
    """协作式取消令牌。"""

    def __init__(self, check_fn: Callable[[], bool] | None = None):
        self._check_fn = check_fn
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        if self._cancelled:
            return True
        if self._check_fn is not None:
            return bool(self._check_fn())
        return False

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise CancellationError("Execution was cancelled")
