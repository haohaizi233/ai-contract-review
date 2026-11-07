# -*- coding: utf-8 -*-
"""HTTP 请求追踪、指标采集中间件与统一安全异常处理器。"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ...infrastructure.observability import (
    GLOBAL_METRICS,
    bind_trace_context,
    clear_trace_context,
    get_redacted_logger,
    redact_sensitive_text,
)

logger = get_redacted_logger("contract_kb.http")


def _extract_ids(request: Request) -> tuple[str, str]:
    req_id = (
        getattr(request.state, "request_id", None)
        or request.headers.get("X-Request-ID")
        or request.headers.get("X-Request-Id")
        or f"req_{uuid.uuid4().hex[:12]}"
    )
    trace_id = (
        getattr(request.state, "trace_id", None)
        or request.headers.get("X-Trace-ID")
        or f"trc_{uuid.uuid4().hex[:16]}"
    )
    return str(req_id), str(trace_id)


def _error_headers(req_id: str, trace_id: str) -> dict[str, str]:
    return {"X-Request-ID": req_id, "X-Trace-ID": trace_id}


def install_observability_and_error_handlers(app: FastAPI) -> None:
    """挂载全链路 Request/Trace ID 中间件、指标采集与脱敏错误信封。"""

    @app.middleware("http")
    async def _trace_and_metrics_middleware(request: Request, call_next):
        req_id, trace_id = _extract_ids(request)
        request.state.request_id = req_id
        request.state.trace_id = trace_id
        bind_trace_context(trace_id=trace_id, request_id=req_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = req_id
            response.headers["X-Trace-ID"] = trace_id
            path = request.url.path
            if path.startswith("/api/") and not path.startswith("/api/v1/"):
                response.headers["Deprecation"] = "true"
                v1_path = "/api/v1/" + path[len("/api/") :]
                response.headers["Link"] = f'<{v1_path}>; rel="successor-version"'
            GLOBAL_METRICS.record_http_request(
                request.method, path, response.status_code
            )
            return response
        finally:
            clear_trace_context()

    @app.exception_handler(HTTPException)
    async def _http_exc_handler(request: Request, exc: HTTPException):
        req_id, trace_id = _extract_ids(request)
        return JSONResponse(
            status_code=exc.status_code,
            headers=_error_headers(req_id, trace_id),
            content={
                "error": exc.detail,
                "code": f"HTTP_{exc.status_code}",
                "status_code": exc.status_code,
                "request_id": req_id,
                "trace_id": trace_id,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exc_handler(request: Request, exc: RequestValidationError):
        req_id, trace_id = _extract_ids(request)
        return JSONResponse(
            status_code=422,
            headers=_error_headers(req_id, trace_id),
            content={
                "error": "request validation failed",
                "detail": exc.errors(),
                "code": "VALIDATION_ERROR",
                "status_code": 422,
                "request_id": req_id,
                "trace_id": trace_id,
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_exc_handler(request: Request, exc: Exception):
        req_id, trace_id = _extract_ids(request)
        safe_msg = redact_sensitive_text(f"{type(exc).__name__}: {exc}", max_len=400)
        logger.error(f"Unhandled HTTP exception on {request.url.path}: {safe_msg}")
        return JSONResponse(
            status_code=500,
            headers=_error_headers(req_id, trace_id),
            content={
                "error": "internal server error",
                "code": "INTERNAL_ERROR",
                "status_code": 500,
                "request_id": req_id,
                "trace_id": trace_id,
            },
        )
