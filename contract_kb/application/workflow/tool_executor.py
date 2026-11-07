# -*- coding: utf-8 -*-
"""受控智能体工具契约、权限控制、调用超时与预算账本（Controlled Agent Tooling）。

严格禁止自由放任（Autonomous Unbounded Loop）工具调用：
- 每个工具必须声明强类型 Pydantic 输入/输出 Schema；
- 每次调用校验显式授权权限集（`ToolPermission`）；
- 每次调用受单工具最大次数、单次执行超时及全局调用总预算/Token/成本上限约束；
- 每次调用生成不可变审计记录（`ToolExecutionAuditRecord`）。
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from .budget import (
    BudgetLedgerService,
    BudgetLimitExceededError,
    BudgetReservation,
    ProviderLimitViolationError,
    TokenUsage,
)


class ToolPermission(str, Enum):
    READ_DOCUMENT = "READ_DOCUMENT"
    READ_KNOWLEDGE = "READ_KNOWLEDGE"
    EXECUTE_RULES = "EXECUTE_RULES"
    INVOKE_LLM = "INVOKE_LLM"
    WRITE_REPORT = "WRITE_REPORT"


class ToolCallStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    TIMEOUT = "TIMEOUT"
    FAILED = "FAILED"


@dataclass
class WorkflowBudget:
    """单次合同智能体工作流硬性资源与成本上限。"""

    max_total_tool_calls: int = 16
    max_llm_tokens: int = 16000
    max_cost_usd: Decimal = Decimal("0.5000")
    max_wall_time_seconds: float = 90.0
    price_per_1k_tokens_usd: Decimal = Decimal("0.0020")

    def __post_init__(self) -> None:
        if isinstance(self.max_cost_usd, float) or isinstance(
            self.price_per_1k_tokens_usd, float
        ):
            raise TypeError("WorkflowBudget 金额上限必须使用 Decimal，严禁 float")


@dataclass
class BudgetLedger:
    """工作流运行期预算消耗账本。"""

    total_tool_calls: int = 0
    per_tool_calls: dict[str, int] = field(default_factory=dict)
    llm_tokens_used: int = 0
    estimated_cost_usd: Decimal = Decimal("0.0000")
    started_monotonic: float = field(default_factory=time.monotonic)

    def record_llm_tokens(self, tokens: int, price_per_1k_usd: Decimal) -> None:
        if tokens <= 0:
            return
        self.llm_tokens_used += int(tokens)
        added = (Decimal(int(tokens)) / Decimal(1000)) * price_per_1k_usd
        self.estimated_cost_usd = (self.estimated_cost_usd + added).quantize(
            Decimal("0.000001")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_tool_calls": self.total_tool_calls,
            "per_tool_calls": dict(self.per_tool_calls),
            "llm_tokens_used": self.llm_tokens_used,
            "estimated_cost_usd": str(self.estimated_cost_usd),
        }


@dataclass
class ToolExecutionAuditRecord:
    """受控工具调用审计留痕。"""

    call_id: str
    tool_name: str
    workflow_state: str
    required_permission: str
    status: str
    started_at: float
    duration_ms: float
    input_digest: str
    tokens_used: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolSpec:
    """受控工具定义契约。"""

    name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    required_permission: ToolPermission
    handler: Callable[[BaseModel], dict[str, Any] | BaseModel]
    timeout_seconds: float = 30.0
    max_calls_per_run: int = 2


class ToolInvocationError(RuntimeError):
    def __init__(self, status: ToolCallStatus, message: str):
        super().__init__(message)
        self.status = status


class ControlledToolExecutor:
    """执行带 Schema 校验、权限校验、预算门禁与超时保护的受控工具。"""

    def __init__(
        self,
        budget: WorkflowBudget,
        granted_permissions: set[ToolPermission] | None = None,
        executor_pool: concurrent.futures.ThreadPoolExecutor | None = None,
    ):
        self.budget = budget
        self.granted_permissions: set[ToolPermission] = (
            set(granted_permissions)
            if granted_permissions is not None
            else set(ToolPermission)
        )
        self.ledger = BudgetLedger()
        self.budget_service = BudgetLedgerService(
            max_tokens=self.budget.max_llm_tokens,
            max_cost_usd=self.budget.max_cost_usd,
            price_per_1k_tokens_usd=self.budget.price_per_1k_tokens_usd,
        )
        self.audit_records: list[ToolExecutionAuditRecord] = []
        self._specs: dict[str, ToolSpec] = {}
        self._pool = executor_pool or concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="tool-exec"
        )
        self._owns_pool = executor_pool is None

    def close(self) -> None:
        if self._owns_pool and self._pool is not None:
            self._pool.shutdown(wait=False)

    def register(self, spec: ToolSpec) -> None:
        self._specs[spec.name] = spec

    def invoke(
        self,
        tool_name: str,
        workflow_state: str,
        raw_input: dict[str, Any],
    ) -> BaseModel:
        if tool_name not in self._specs:
            raise ToolInvocationError(
                ToolCallStatus.VALIDATION_ERROR, f"未注册的受控工具：{tool_name!r}"
            )
        spec = self._specs[tool_name]
        call_id = f"tcall_{uuid.uuid4().hex[:10]}"
        t0_wall = time.time()
        t0_mono = time.monotonic()
        digest = hashlib.sha256(
            json.dumps(raw_input, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]

        # 1. 权限门禁
        if spec.required_permission not in self.granted_permissions:
            msg = (
                f"工具 {tool_name} 需要权限 {spec.required_permission.value}，"
                "当前工作流未获授权"
            )
            self._append_audit(
                call_id,
                spec,
                workflow_state,
                ToolCallStatus.PERMISSION_DENIED,
                t0_wall,
                t0_mono,
                digest,
                msg,
            )
            raise ToolInvocationError(ToolCallStatus.PERMISSION_DENIED, msg)

        # 2. 全局墙钟耗时、总调用次数、单工具次数及 LLM Token/成本预算门禁
        elapsed_wall = time.monotonic() - self.ledger.started_monotonic
        if elapsed_wall > self.budget.max_wall_time_seconds:
            msg = f"工作流墙钟时间超限：{elapsed_wall:.2f}s > {self.budget.max_wall_time_seconds}s"
            self._append_audit(
                call_id,
                spec,
                workflow_state,
                ToolCallStatus.BUDGET_EXCEEDED,
                t0_wall,
                t0_mono,
                digest,
                msg,
            )
            raise ToolInvocationError(ToolCallStatus.BUDGET_EXCEEDED, msg)

        if self.ledger.total_tool_calls >= self.budget.max_total_tool_calls:
            msg = f"工具总调用次数已达上限：{self.budget.max_total_tool_calls}"
            self._append_audit(
                call_id,
                spec,
                workflow_state,
                ToolCallStatus.BUDGET_EXCEEDED,
                t0_wall,
                t0_mono,
                digest,
                msg,
            )
            raise ToolInvocationError(ToolCallStatus.BUDGET_EXCEEDED, msg)

        used_for_tool = self.ledger.per_tool_calls.get(tool_name, 0)
        if used_for_tool >= spec.max_calls_per_run:
            msg = f"工具 {tool_name} 单次运行调用次数已达上限：{spec.max_calls_per_run}"
            self._append_audit(
                call_id,
                spec,
                workflow_state,
                ToolCallStatus.BUDGET_EXCEEDED,
                t0_wall,
                t0_mono,
                digest,
                msg,
            )
            raise ToolInvocationError(ToolCallStatus.BUDGET_EXCEEDED, msg)

        if spec.required_permission == ToolPermission.INVOKE_LLM:
            if self.ledger.llm_tokens_used >= self.budget.max_llm_tokens:
                msg = f"LLM Token 预算已耗尽：{self.ledger.llm_tokens_used} >= {self.budget.max_llm_tokens}"
                self._append_audit(
                    call_id,
                    spec,
                    workflow_state,
                    ToolCallStatus.BUDGET_EXCEEDED,
                    t0_wall,
                    t0_mono,
                    digest,
                    msg,
                )
                raise ToolInvocationError(ToolCallStatus.BUDGET_EXCEEDED, msg)
            if self.ledger.estimated_cost_usd >= self.budget.max_cost_usd:
                msg = f"LLM 成本预算已耗尽：{self.ledger.estimated_cost_usd} >= {self.budget.max_cost_usd}"
                self._append_audit(
                    call_id,
                    spec,
                    workflow_state,
                    ToolCallStatus.BUDGET_EXCEEDED,
                    t0_wall,
                    t0_mono,
                    digest,
                    msg,
                )
                raise ToolInvocationError(ToolCallStatus.BUDGET_EXCEEDED, msg)

        # 3. 输入 Schema 严格校验
        try:
            validated_in = spec.input_schema.model_validate(raw_input)
        except ValidationError as exc:
            msg = f"工具 {tool_name} 输入参数校验失败：{exc}"
            self._append_audit(
                call_id,
                spec,
                workflow_state,
                ToolCallStatus.VALIDATION_ERROR,
                t0_wall,
                t0_mono,
                digest,
                msg,
            )
            raise ToolInvocationError(ToolCallStatus.VALIDATION_ERROR, msg) from exc

        # 4. 计入调用预算并带超时保护执行
        self.ledger.total_tool_calls += 1
        self.ledger.per_tool_calls[tool_name] = used_for_tool + 1

        reservation: BudgetReservation | None = None
        if spec.required_permission == ToolPermission.INVOKE_LLM:
            raw_text = json.dumps(raw_input, ensure_ascii=False, default=str)
            estimated_input = max(1, len(raw_text) // 2)
            max_out = getattr(spec, "max_output_tokens", 4000)
            try:
                reservation = self.budget_service.reserve_budget(
                    input_tokens=estimated_input, max_output_tokens=max_out
                )
            except BudgetLimitExceededError as exc:
                msg = f"LLM 调用前预算预留失败：{exc}"
                self._append_audit(
                    call_id,
                    spec,
                    workflow_state,
                    ToolCallStatus.BUDGET_EXCEEDED,
                    t0_wall,
                    t0_mono,
                    digest,
                    msg,
                )
                raise ToolInvocationError(ToolCallStatus.BUDGET_EXCEEDED, msg) from exc

        try:
            fut = self._pool.submit(spec.handler, validated_in)
            raw_out = fut.result(timeout=spec.timeout_seconds)
        except concurrent.futures.TimeoutError as exc:
            if reservation is not None:
                self.budget_service.mark_uncertain(reservation.reservation_id)
            msg = f"工具 {tool_name} 执行超时（>{spec.timeout_seconds}s）"
            self._append_audit(
                call_id,
                spec,
                workflow_state,
                ToolCallStatus.TIMEOUT,
                t0_wall,
                t0_mono,
                digest,
                msg,
            )
            raise ToolInvocationError(ToolCallStatus.TIMEOUT, msg) from exc
        except Exception as exc:
            if reservation is not None:
                self.budget_service.release_budget(reservation.reservation_id)
            msg = f"{type(exc).__name__}: {exc}"
            self._append_audit(
                call_id,
                spec,
                workflow_state,
                ToolCallStatus.FAILED,
                t0_wall,
                t0_mono,
                digest,
                msg,
            )
            raise ToolInvocationError(ToolCallStatus.FAILED, msg) from exc

        # 5. 输出 Schema 严格校验与 Token 记账
        try:
            validated_out = (
                raw_out
                if isinstance(raw_out, spec.output_schema)
                else spec.output_schema.model_validate(raw_out)
            )
        except ValidationError as exc:
            if reservation is not None:
                self.budget_service.release_budget(reservation.reservation_id)
            msg = f"工具 {tool_name} 输出契约校验失败：{exc}"
            self._append_audit(
                call_id,
                spec,
                workflow_state,
                ToolCallStatus.VALIDATION_ERROR,
                t0_wall,
                t0_mono,
                digest,
                msg,
            )
            raise ToolInvocationError(ToolCallStatus.VALIDATION_ERROR, msg) from exc

        tokens_used = int(getattr(validated_out, "tokens_used", 0) or 0)
        if reservation is not None:
            inp = reservation.input_tokens
            out = max(0, tokens_used - inp) if tokens_used >= inp else tokens_used
            try:
                self.budget_service.settle_budget(
                    reservation.reservation_id,
                    TokenUsage(input_tokens=inp, output_tokens=out),
                )
            except ProviderLimitViolationError as exc:
                msg = f"LLM 供应商输出违规：{exc}"
                self._append_audit(
                    call_id,
                    spec,
                    workflow_state,
                    ToolCallStatus.FAILED,
                    t0_wall,
                    t0_mono,
                    digest,
                    msg,
                )
                raise ToolInvocationError(ToolCallStatus.FAILED, msg) from exc

        if tokens_used > 0:
            self.ledger.record_llm_tokens(
                tokens_used, self.budget.price_per_1k_tokens_usd
            )

        self._append_audit(
            call_id,
            spec,
            workflow_state,
            ToolCallStatus.SUCCEEDED,
            t0_wall,
            t0_mono,
            digest,
            None,
            tokens_used=tokens_used,
        )
        return validated_out

    def _append_audit(
        self,
        call_id: str,
        spec: ToolSpec,
        workflow_state: str,
        status: ToolCallStatus,
        started_wall: float,
        started_mono: float,
        digest: str,
        error: str | None,
        *,
        tokens_used: int = 0,
    ) -> None:
        self.audit_records.append(
            ToolExecutionAuditRecord(
                call_id=call_id,
                tool_name=spec.name,
                workflow_state=workflow_state,
                required_permission=spec.required_permission.value,
                status=status.value,
                started_at=started_wall,
                duration_ms=round((time.monotonic() - started_mono) * 1000.0, 3),
                input_digest=digest,
                tokens_used=tokens_used,
                error=error,
            )
        )
