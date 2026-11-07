# -*- coding: utf-8 -*-
"""FastAPI HTTP 接口契约、中间件与统一错误响应信封。"""

from .middleware import install_observability_and_error_handlers
from .schemas import (
    CompareReq,
    ErrorEnvelope,
    FeedbackIn,
    FormatStandardApproveIn,
    FormatStandardIn,
    HealthResponse,
    PaginatedResponse,
    ReadinessResponse,
    ReviewTaskIn,
    RuleIn,
    SearchReq,
)

__all__ = [
    "CompareReq",
    "ErrorEnvelope",
    "FeedbackIn",
    "FormatStandardApproveIn",
    "FormatStandardIn",
    "HealthResponse",
    "PaginatedResponse",
    "ReadinessResponse",
    "ReviewTaskIn",
    "RuleIn",
    "SearchReq",
    "install_observability_and_error_handlers",
]
