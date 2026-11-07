# -*- coding: utf-8 -*-
"""日志与审计脱敏基础设施（Redacted Structured Logging）。

确保任何真实凭据、DSN 密码、Bearer Token、银行卡号、手机号及超长合同敏感正文
绝不以明文进入应用日志、错误堆栈或审计详情。
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import time
from typing import Any

_TRACE_CTX: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "contract_kb_trace_ctx", default={}
)


def bind_trace_context(**fields: str) -> None:
    """在当前请求/异步任务上下文绑定 request_id、trace_id、tenant_id 等追踪标识。"""
    merged = dict(_TRACE_CTX.get())
    for k, v in fields.items():
        if v:
            merged[k] = str(v)
    _TRACE_CTX.set(merged)


def get_trace_context() -> dict[str, str]:
    return dict(_TRACE_CTX.get())


def clear_trace_context() -> None:
    _TRACE_CTX.set({})


_DSN_PWD_RE = re.compile(
    r"((?:postgres(?:ql)?|mysql|mssql|redis)://[^:/@\s]+:)([^@\s]+)(@)"
)
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", re.I)
_KV_SECRET_RE = re.compile(
    r"((?:api[_-]?key|secret|password|passwd|token|shared[_-]?key)\s*[:=]\s*['\"]?)([^\s,'\"&;]{4,})",
    re.I,
)
_BANK_OR_CARD_RE = re.compile(r"\b\d{12,22}\b")
_CN_MOBILE_RE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")


def redact_sensitive_text(text: str, *, max_len: int = 500) -> str:
    """对日志、错误消息与审计字段执行确定性敏感信息脱敏与长度截断。"""
    if not text:
        return ""
    out = _DSN_PWD_RE.sub(r"\1***REDACTED***\3", text)
    out = _BEARER_RE.sub(r"\1***REDACTED***", out)
    out = _KV_SECRET_RE.sub(r"\1***REDACTED***", out)
    out = _CN_MOBILE_RE.sub(r"\1****\2", out)
    out = _BANK_OR_CARD_RE.sub("[REDACTED_ACCOUNT_NO]", out)
    if len(out) > max_len:
        return out[:max_len] + "...[TRUNCATED]"
    return out


def redact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    """递归脱敏字典结构中的敏感键值。"""
    sensitive_keys = {
        "password",
        "passwd",
        "secret",
        "api_key",
        "token",
        "authorization",
        "x-auth-sig",
        "dsn",
        "bank_account",
    }
    cleaned: dict[str, Any] = {}
    for k, v in payload.items():
        if k.lower() in sensitive_keys:
            cleaned[k] = "***REDACTED***"
        elif isinstance(v, str):
            cleaned[k] = redact_sensitive_text(v)
        elif isinstance(v, dict):
            cleaned[k] = redact_dict(v)
        elif isinstance(v, list):
            cleaned[k] = [
                redact_sensitive_text(x) if isinstance(x, str) else x for x in v[:50]
            ]
        else:
            cleaned[k] = v
    return cleaned


class StructuredJsonFormatter(logging.Formatter):
    """输出自动脱敏的单行 JSON 结构化日志，携带 request_id 与 trace_id。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": round(time.time(), 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact_sensitive_text(record.getMessage(), max_len=1000),
        }
        ctx = _TRACE_CTX.get()
        for extra_key in ("request_id", "trace_id", "tenant_id", "task_id", "job_id"):
            val = getattr(record, extra_key, None) or ctx.get(extra_key)
            if val:
                payload[extra_key] = str(val)
        if record.exc_info:
            exc_txt = self.formatException(record.exc_info)
            payload["exception"] = redact_sensitive_text(exc_txt, max_len=1500)
        return json.dumps(payload, ensure_ascii=False)


def get_redacted_logger(name: str = "contract_kb") -> logging.Logger:
    logger = logging.getLogger(name)
    if not any(
        isinstance(getattr(h, "formatter", None), StructuredJsonFormatter)
        for h in logger.handlers
    ):
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredJsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)
    return logger
