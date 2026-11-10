# -*- coding: utf-8 -*-
"""LLM 抽象端口契约、结果模型与错误类型（Application Port）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from pydantic import BaseModel, Field


class LLMFailure(str, Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    TIMEOUT = "TIMEOUT"
    PARSE_ERROR = "PARSE_ERROR"
    CONTEXT_TOO_LONG = "CONTEXT_TOO_LONG"
    REFUSED = "REFUSED"
    REFERENCE_MISMATCH = "REFERENCE_MISMATCH"
    UNKNOWN = "UNKNOWN"


class LLMFinding(BaseModel):
    """LLM 审核发现（严格 Schema）。"""

    finding_id: str = Field(min_length=1)
    category: str
    severity: str = Field(pattern="^(HIGH|MEDIUM|LOW|INFO)$")
    title: str
    description: str
    original_text: str = ""  # 引用的合同原文片段（必须能在文档中找到）
    suggestion: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1)
    requires_human_review: bool = True


class LLMReviewResult(BaseModel):
    status: str = "CHECKED"  # CHECKED | NOT_CHECKED | NEEDS_MANUAL_REVIEW | FAILED
    failure: str | None = None
    findings: list[LLMFinding] = Field(default_factory=list)
    custom_requirement_results: list[dict] = Field(default_factory=list)
    model: str = ""
    raw_usage: dict = Field(default_factory=dict)


class LLMNotConfigured(RuntimeError):
    pass


class LLMConfigError(LLMNotConfigured):
    """LLM 授权配置不完整/非法（fail-closed：直接抛错，不降级为 None）。"""


class LLMProvider(ABC):
    name = "abstract"

    @abstractmethod
    def complete(
        self,
        system: str,
        user: str,
        timeout: float = 60.0,
        max_tokens: int | None = None,
    ) -> dict:
        """返回 {"text": str, "model": str, "usage": dict}；失败抛 Timeout/Parse 类异常。"""


__all__ = [
    "LLMConfigError",
    "LLMFailure",
    "LLMFinding",
    "LLMNotConfigured",
    "LLMProvider",
    "LLMReviewResult",
]
