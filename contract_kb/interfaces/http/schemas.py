# -*- coding: utf-8 -*-
"""HTTP API v1 请求与响应契约（OpenAPI 显式 Schema）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorEnvelope(BaseModel):
    """统一企业级 API 错误响应结构（兼容历史 error + request_id 字段）。"""

    error: Any
    code: str
    status_code: int
    request_id: str = ""
    trace_id: str = ""


class PaginatedResponse(BaseModel):
    """标准分页响应包裹器。"""

    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "contract-kb"
    version: str = "1.0.0"


class ReadinessResponse(BaseModel):
    status: str
    db_ready: bool
    db_latency_ms: float


class SearchReq(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=8, ge=1, le=50)
    business_category: str | None = None
    contract_subtype: str | None = None
    clause_number: str | None = None
    keyword: str | None = Field(default=None)
    rerank: bool = True


class CompareReq(BaseModel):
    clause_texts: list[str] = Field(min_length=2, max_length=2)


class RuleIn(BaseModel):
    rule_id: str = Field(min_length=3, max_length=80)
    name: str = Field(max_length=200)
    rule_type: str
    execution_mode: str
    severity: str
    contract_types: list[str] = ["*"]
    parameters: dict[str, Any] = {}
    source_clauses: list[Any] = []


class ReviewTaskIn(BaseModel):
    mode: str = "STANDARD"
    custom_requirements: list[str] = []


class FeedbackIn(BaseModel):
    action: str
    note: str = Field(default="", max_length=2000)
    expected_state: str | None = None


class FormatStandardIn(BaseModel):
    standard_id: str = Field(default="company_format", min_length=3, max_length=80)
    params: dict[str, Any]
    note: str = Field(default="", max_length=500)


class FormatStandardApproveIn(BaseModel):
    version: int = Field(ge=1)
