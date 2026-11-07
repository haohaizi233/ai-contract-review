# -*- coding: utf-8 -*-
"""执行领域模型：作业租约与执行结果（T06 / F03 / ADR-004）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class JobLease:
    """代表已被 Worker 独占认领的作业租约。"""

    job_id: str
    worker_id: str
    lease_token: str
    lease_until: float
    attempt: int
    job_type: str = ""
    tenant_id: str = "default"
    created_by: str = "system"
    payload: dict[str, Any] = field(default_factory=dict)
    temp_file_path: str | None = None


@dataclass
class JobOutcome:
    """作业执行终态或重试结果。"""

    status: str  # "SUCCEEDED", "FAILED", "CANCELLED", "DEAD_LETTER", "RETRYING"
    result: dict[str, Any] | None = None
    error_message: str | None = None
    dead_letter_reason: str | None = None
    next_run_at: float | None = None
    progress: int = 100
