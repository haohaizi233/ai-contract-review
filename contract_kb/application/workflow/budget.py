# -*- coding: utf-8 -*-
"""LLM 与工作流调用前预算预留服务（T08 / F05 / ADR-003）。

实现显式、线程安全、高并发防穿透的调用前额度预留 (Pre-invocation Budget Reservation)：
- 成本核算一律使用 Decimal，禁止 float 精度漂移；
- 账本清晰划分 RESERVED（已预留）、SETTLED（已结算）、UNCERTAIN（超时未知挂账）与 VIOLATED（供应商违约超吐）；
- 严禁事后记账导致额度被透支到负数；
- 供应商违约超吐时记录实际消耗并抛出 ProviderLimitViolationError 阻止后续调用。
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class ReservationStatus(str, Enum):
    RESERVED = "RESERVED"
    SETTLED = "SETTLED"
    RELEASED = "RELEASED"
    UNCERTAIN = "UNCERTAIN"
    VIOLATED = "VIOLATED"


class BudgetLimitExceededError(Exception):
    """Token 或成本预算超限，在外部调用发起前被安全拒绝。"""

    pass


class ProviderLimitViolationError(Exception):
    """供应商返回 Token 超出预留最大输出约束。"""

    pass


@dataclass
class TokenUsage:
    """实际 Token 消耗。"""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class BudgetReservation:
    """不可变预算预留凭据。"""

    reservation_id: str
    input_tokens: int
    max_output_tokens: int
    reserved_tokens: int
    reserved_cost_usd: Decimal
    price_per_1k_usd: Decimal
    price_version: str
    status: ReservationStatus
    created_at: float


class BudgetLedgerService:
    """线程安全调用前预算预留与结算账本。"""

    def __init__(
        self,
        *,
        max_tokens: int = 16000,
        max_cost_usd: Decimal = Decimal("0.5000"),
        price_per_1k_tokens_usd: Decimal = Decimal("0.0020"),
        price_version: str = "v1",
    ):
        self.max_tokens = max(0, int(max_tokens))
        self.max_cost_usd = (
            max_cost_usd
            if isinstance(max_cost_usd, Decimal)
            else Decimal(str(max_cost_usd))
        )
        self.price_per_1k_tokens_usd = (
            price_per_1k_tokens_usd
            if isinstance(price_per_1k_tokens_usd, Decimal)
            else Decimal(str(price_per_1k_tokens_usd))
        )
        self.price_version = str(price_version)

        self._lock = threading.Lock()
        self._reservations: dict[str, BudgetReservation] = {}

        # 累计统计
        self._reserved_tokens: int = 0
        self._consumed_tokens: int = 0
        self._uncertain_tokens: int = 0

        self._reserved_cost: Decimal = Decimal("0.0000")
        self._consumed_cost: Decimal = Decimal("0.0000")
        self._uncertain_cost: Decimal = Decimal("0.0000")

    def update_pricing(self, new_price_per_1k_usd: Decimal, new_version: str) -> None:
        """更新定价策略，新调用按新版本核算，既有预留维持预留时单价。"""
        with self._lock:
            self.price_per_1k_tokens_usd = (
                new_price_per_1k_usd
                if isinstance(new_price_per_1k_usd, Decimal)
                else Decimal(str(new_price_per_1k_usd))
            )
            self.price_version = str(new_version)

    def reserve_budget(
        self,
        input_tokens: int,
        max_output_tokens: int,
        price_version: str | None = None,
    ) -> BudgetReservation:
        """调用前预留额度，若额度不足直接抛出 BudgetLimitExceededError。"""
        inp = max(0, int(input_tokens))
        out = max(0, int(max_output_tokens))
        needed_tokens = inp + out

        with self._lock:
            active_version = price_version or self.price_version
            unit_price = self.price_per_1k_tokens_usd
            needed_cost = (
                (Decimal(needed_tokens) / Decimal(1000)) * unit_price
            ).quantize(Decimal("0.000001"))

            current_allocated_tokens = (
                self._consumed_tokens + self._reserved_tokens + self._uncertain_tokens
            )
            current_allocated_cost = (
                self._consumed_cost + self._reserved_cost + self._uncertain_cost
            )

            if current_allocated_tokens + needed_tokens > self.max_tokens:
                raise BudgetLimitExceededError(
                    f"Token 预算不足：当前分配 {current_allocated_tokens} + 申请预留 {needed_tokens} > 上限 {self.max_tokens}"
                )

            if current_allocated_cost + needed_cost > self.max_cost_usd:
                raise BudgetLimitExceededError(
                    f"成本预算不足：当前分配 {current_allocated_cost} + 申请预留 {needed_cost} > 上限 {self.max_cost_usd}"
                )

            res_id = f"res_{uuid.uuid4().hex[:12]}"
            res = BudgetReservation(
                reservation_id=res_id,
                input_tokens=inp,
                max_output_tokens=out,
                reserved_tokens=needed_tokens,
                reserved_cost_usd=needed_cost,
                price_per_1k_usd=unit_price,
                price_version=active_version,
                status=ReservationStatus.RESERVED,
                created_at=time.time(),
            )
            self._reservations[res_id] = res
            self._reserved_tokens += needed_tokens
            self._reserved_cost += needed_cost
            return res

    def settle_budget(self, reservation_id: str, actual_usage: TokenUsage) -> None:
        """调用成功后精准核销实际消耗，结余退回可用池。"""
        with self._lock:
            res = self._reservations.get(reservation_id)
            if not res:
                raise KeyError(f"未找到预留记录：{reservation_id!r}")
            if res.status != ReservationStatus.RESERVED:
                raise RuntimeError(
                    f"预留记录 {reservation_id} 状态为 {res.status.value}，无法重复结算"
                )

            actual_total = actual_usage.total_tokens
            actual_cost = (
                (Decimal(actual_total) / Decimal(1000)) * res.price_per_1k_usd
            ).quantize(Decimal("0.000001"))

            # 校验供应商是否违约吐出超过 max_output_tokens
            if actual_usage.output_tokens > res.max_output_tokens:
                res.status = ReservationStatus.VIOLATED
                self._reserved_tokens -= res.reserved_tokens
                self._reserved_cost -= res.reserved_cost_usd
                self._consumed_tokens += actual_total
                self._consumed_cost += actual_cost
                raise ProviderLimitViolationError(
                    f"Provider limit violation: output tokens {actual_usage.output_tokens} exceeded maximum constraint {res.max_output_tokens}"
                )

            # 正常核销
            self._reserved_tokens -= res.reserved_tokens
            self._reserved_cost -= res.reserved_cost_usd
            self._consumed_tokens += actual_total
            self._consumed_cost += actual_cost
            res.status = ReservationStatus.SETTLED

    def mark_uncertain(self, reservation_id: str) -> None:
        """超时或未知故障时将预留转入 UNCERTAIN，防止并发重复消费。"""
        with self._lock:
            res = self._reservations.get(reservation_id)
            if not res:
                return
            if res.status == ReservationStatus.RESERVED:
                self._reserved_tokens -= res.reserved_tokens
                self._reserved_cost -= res.reserved_cost_usd
                self._uncertain_tokens += res.reserved_tokens
                self._uncertain_cost += res.reserved_cost_usd
                res.status = ReservationStatus.UNCERTAIN

    def release_budget(self, reservation_id: str) -> None:
        """调用被显式取消或未发出前安全释放预留额度。"""
        with self._lock:
            res = self._reservations.get(reservation_id)
            if not res or res.status != ReservationStatus.RESERVED:
                return
            self._reserved_tokens -= res.reserved_tokens
            self._reserved_cost -= res.reserved_cost_usd
            res.status = ReservationStatus.RELEASED

    def get_available_tokens(self) -> int:
        with self._lock:
            allocated = (
                self._consumed_tokens + self._reserved_tokens + self._uncertain_tokens
            )
            return max(0, self.max_tokens - allocated)

    def get_available_cost(self) -> Decimal:
        with self._lock:
            allocated = self._consumed_cost + self._reserved_cost + self._uncertain_cost
            return max(Decimal("0.0000"), self.max_cost_usd - allocated)

    def get_total_consumed_cost(self) -> Decimal:
        with self._lock:
            return self._consumed_cost
