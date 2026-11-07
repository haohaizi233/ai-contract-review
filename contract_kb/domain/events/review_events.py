# -*- coding: utf-8 -*-
"""领域事件定义（纯内存值对象，不绑定消息中间件）。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DomainEvent:
    occurred_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ReviewStarted(DomainEvent):
    task_id: str = ""
    tenant_id: str = "default"
    mode: str = "STANDARD"


@dataclass(frozen=True)
class ReviewStageCompleted(DomainEvent):
    task_id: str = ""
    stage_name: str = ""
    duration_ms: float = 0.0


@dataclass(frozen=True)
class ReviewCompleted(DomainEvent):
    task_id: str = ""
    tenant_id: str = "default"
    issue_count: int = 0
    review_gate: str = "PASS"


@dataclass(frozen=True)
class HumanOverrideApplied(DomainEvent):
    override_id: str = ""
    task_id: str = ""
    issue_id: str = ""
    actor: str = ""
    action: str = ""
    reason: str = ""
