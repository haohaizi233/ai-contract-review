# -*- coding: utf-8 -*-
"""单元测试：LLM 与工具调用前预算预留、并发硬约束与供应商越界防护（T08 / F05 / ADR-003）。

验证契约：
1. 调用前预留（Pre-invocation Reservation）：调用前必须根据 input_tokens + max_output_tokens
   扣减可用额度，额度不足在调用前直接拒绝，杜绝事后记账导致超额穿透；
2. 供应商违约拦截（Provider Limit Violation）：声明 max_output=1 但返回 9999 Token 时，
   账本真实记录供应商违约消耗，将状态标记为 VIOLATED 并抛出 ProviderLimitViolationError 阻止后续调用；
3. 超时未知消耗隔离（Uncertain Budget Isolation）：请求超时后由于未获知实际 Token，
   账本将额度锁定为 UNCERTAIN，杜绝并发请求双重消费，等待供应商对账；
4. 并发硬约束：多线程并发预留时，预算严格不能透支到负数；
5. 成本统一使用 Decimal，禁止 float 精度漂移。
"""

from __future__ import annotations

import concurrent.futures
from decimal import Decimal

import pytest

from contract_kb.application.workflow.budget import (
    BudgetLedgerService,
    BudgetLimitExceededError,
    BudgetReservation,
    ProviderLimitViolationError,
    ReservationStatus,
    TokenUsage,
)


def test_pre_invocation_budget_reservation_blocks_before_call():
    """验证额度不足时在外部调用前即时拦截，不产生调用。"""
    service = BudgetLedgerService(
        max_tokens=1000,
        max_cost_usd=Decimal("0.0100"),
        price_per_1k_tokens_usd=Decimal("0.0020"),
    )

    # 1. 正常预留 600 Token (input 200 + max_out 400)
    res1 = service.reserve_budget(input_tokens=200, max_output_tokens=400)
    assert isinstance(res1, BudgetReservation)
    assert res1.reserved_tokens == 600
    assert res1.status == ReservationStatus.RESERVED

    # 2. 尝试预留 500 Token：600 + 500 = 1100 > 1000，必须在调用前抛出 BudgetLimitExceededError
    with pytest.raises(BudgetLimitExceededError) as exc_info:
        service.reserve_budget(input_tokens=100, max_output_tokens=400)
    assert "token" in str(exc_info.value).lower() or "budget" in str(exc_info.value).lower()

    # 3. 结算第 1 次调用实际消耗 300 Token
    service.settle_budget(res1.reservation_id, TokenUsage(input_tokens=200, output_tokens=100))
    assert res1.status == ReservationStatus.SETTLED

    # 4. 释放多余预留后，剩余 1000 - 300 = 700 可用，此时 500 Token 可以成功预留
    res2 = service.reserve_budget(input_tokens=100, max_output_tokens=400)
    assert res2.reserved_tokens == 500


def test_provider_limit_violation_detection():
    """验证供应商返回超出 max_output_tokens 时的违约检测与阻断。"""
    service = BudgetLedgerService(
        max_tokens=5000,
        max_cost_usd=Decimal("0.0500"),
        price_per_1k_tokens_usd=Decimal("0.0020"),
    )

    # 预留上限为 1 个输出 token
    res = service.reserve_budget(input_tokens=10, max_output_tokens=1)

    # 模拟供应商违规吐出 9999 tokens
    with pytest.raises(ProviderLimitViolationError) as exc_info:
        service.settle_budget(
            res.reservation_id,
            TokenUsage(input_tokens=10, output_tokens=9999),
        )
    assert "violation" in str(exc_info.value).lower()
    assert res.status == ReservationStatus.VIOLATED


def test_timeout_usage_unknown_locks_into_uncertain():
    """验证超时后使用量未知时，额度安全隔离为 UNCERTAIN，防止并发重复消费。"""
    service = BudgetLedgerService(
        max_tokens=1000,
        max_cost_usd=Decimal("0.0100"),
        price_per_1k_tokens_usd=Decimal("0.0020"),
    )

    res = service.reserve_budget(input_tokens=400, max_output_tokens=400)  # 预留 800
    assert service.get_available_tokens() == 200

    # 发生超时，实际消耗未知
    service.mark_uncertain(res.reservation_id)
    assert res.status == ReservationStatus.UNCERTAIN

    # 此时可用额度仍为 200，绝不能把 800 释放回池子让后续并发错误复用
    assert service.get_available_tokens() == 200
    with pytest.raises(BudgetLimitExceededError):
        service.reserve_budget(input_tokens=100, max_output_tokens=200)  # 300 > 200


def test_concurrent_budget_reservations_cannot_overspend():
    """验证多线程高并发预留下，可用额度严格受控，绝不产生超支或负数。"""
    service = BudgetLedgerService(
        max_tokens=1000,
        max_cost_usd=Decimal("0.0500"),
        price_per_1k_tokens_usd=Decimal("0.0020"),
    )

    success_count = 0
    blocked_count = 0

    def _try_reserve():
        nonlocal success_count, blocked_count
        try:
            # 每次尝试预留 200 Token，总额 1000，最多成功 5 次
            service.reserve_budget(input_tokens=50, max_output_tokens=150)
            return True
        except BudgetLimitExceededError:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_try_reserve) for _ in range(20)]
        results = [f.result() for f in futures]

    successes = sum(1 for r in results if r)
    failures = sum(1 for r in results if not r)

    assert successes == 5, f"预期恰好成功 5 次，实际成功 {successes} 次"
    assert failures == 15
    assert service.get_available_tokens() == 0
