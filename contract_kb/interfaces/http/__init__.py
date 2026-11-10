# -*- coding: utf-8 -*-
"""FastAPI HTTP 接口契约、鉴权、中间件与统一错误响应信封。"""

from .auth import (
    EMPTY_BODY_SHA256,
    ROLE_ADMIN,
    ROLE_RANK,
    ROLE_REVIEWER,
    ROLE_VIEWER,
    ROUTE_ROLES,
    Principal,
    TrustedHeaderAuth,
    authorize,
    check_tenant,
    compute_auth_signature_v2,
    enforce_input_limits,
)
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
    "EMPTY_BODY_SHA256",
    "ErrorEnvelope",
    "FeedbackIn",
    "FormatStandardApproveIn",
    "FormatStandardIn",
    "HealthResponse",
    "PaginatedResponse",
    "Principal",
    "ROLE_ADMIN",
    "ROLE_RANK",
    "ROLE_REVIEWER",
    "ROLE_VIEWER",
    "ROUTE_ROLES",
    "ReadinessResponse",
    "ReviewTaskIn",
    "RuleIn",
    "SearchReq",
    "TrustedHeaderAuth",
    "authorize",
    "check_tenant",
    "compute_auth_signature_v2",
    "enforce_input_limits",
    "install_observability_and_error_handlers",
]
