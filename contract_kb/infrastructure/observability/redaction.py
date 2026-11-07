# -*- coding: utf-8 -*-
"""日志与审计脱敏基础设施（Redacted Structured Logging）。

确保任何真实凭据、DSN 密码、Bearer Token、银行卡号、手机号及超长合同敏感正文
绝不以明文进入应用日志、错误堆栈或审计详情。
"""

from __future__ import annotations

import ast
import contextvars
import json
import logging
import re
import time
from typing import Any, Mapping

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
    r"""((?:['"]?(?:api[_-]?key|secret|password|passwd|token|shared[_-]?key|auth(?:orization)?|credential)['"]?\s*[:=]\s*['"]?))([^\s,'\"&;]{3,})(['"]?)""",
    re.I,
)
_BANK_OR_CARD_RE = re.compile(r"\b\d{12,22}\b")
_CN_MOBILE_RE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")
_COOKIE_SECRET_RE = re.compile(
    r"""((?:session(?:id)?|token|sid|auth|jwt|ticket)\s*=\s*)([^\s;,"']{4,})""",
    re.I,
)

SENSITIVE_KEY_KEYWORDS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "apikey",
        "token",
        "auth",
        "authorization",
        "xauthsig",
        "xauthtoken",
        "dsn",
        "bankaccount",
        "cookie",
        "setcookie",
        "credential",
        "privatekey",
        "privkey",
    }
)


def _is_sensitive_key(key: Any) -> bool:
    norm = re.sub(r"[-_\s]", "", str(key)).lower()
    return any(k in norm for k in SENSITIVE_KEY_KEYWORDS)


def redact_sensitive_text(text: str, *, max_len: int = 500) -> str:
    """对日志、错误消息与审计字段执行确定性敏感信息脱敏与长度截断。"""
    if not text:
        return ""

    stripped = text.strip()
    # 尝试结构化解析：优先 JSON 文本，其次安全 Python dict/list 文本
    if (stripped.startswith("{") and stripped.endswith("}")) or (
        stripped.startswith("[") and stripped.endswith("]")
    ):
        try:
            parsed = json.loads(stripped)
            cleaned = redact_value(parsed)
            res = json.dumps(cleaned, ensure_ascii=False)
            if len(res) > max_len:
                return res[:max_len] + "...[TRUNCATED]"
            return res
        except Exception:
            try:
                node = ast.literal_eval(stripped)
                if isinstance(node, (dict, list)):
                    cleaned = redact_value(node)
                    res = json.dumps(cleaned, ensure_ascii=False)
                    if len(res) > max_len:
                        return res[:max_len] + "...[TRUNCATED]"
                    return res
            except Exception:
                pass

    out = _DSN_PWD_RE.sub(r"\1***REDACTED***\3", text)
    out = _BEARER_RE.sub(r"\1***REDACTED***", out)
    out = _KV_SECRET_RE.sub(r"\1***REDACTED***\3", out)
    out = _COOKIE_SECRET_RE.sub(r"\1***REDACTED***", out)
    out = _CN_MOBILE_RE.sub(r"\1****\2", out)
    out = _BANK_OR_CARD_RE.sub("[REDACTED_ACCOUNT_NO]", out)
    if len(out) > max_len:
        return out[:max_len] + "...[TRUNCATED]"
    return out


def redact_value(
    value: Any,
    *,
    max_depth: int = 20,
    _seen: set[int] | None = None,
) -> Any:
    """递归脱敏任意对象（字典、列表、集合、元组、字符串），防止循环引用与深层栈溢出。"""
    if max_depth <= 0:
        return "[MAX_DEPTH_EXCEEDED]"

    if _seen is None:
        _seen = set()

    val_id = id(value)
    if isinstance(value, (dict, list, tuple, set)):
        if val_id in _seen:
            return "[CIRCULAR_REFERENCE]"
        _seen = set(_seen)
        _seen.add(val_id)

    if isinstance(value, dict):
        cleaned_dict: dict[str, Any] = {}
        for k, v in value.items():
            if _is_sensitive_key(k):
                cleaned_dict[str(k)] = "***REDACTED***"
            else:
                cleaned_dict[str(k)] = redact_value(
                    v, max_depth=max_depth - 1, _seen=_seen
                )
        return cleaned_dict

    if isinstance(value, list):
        return [
            redact_value(item, max_depth=max_depth - 1, _seen=_seen)
            for item in value[:200]
        ]

    if isinstance(value, tuple):
        return tuple(
            redact_value(item, max_depth=max_depth - 1, _seen=_seen)
            for item in value[:200]
        )

    if isinstance(value, set):
        return {
            redact_value(item, max_depth=max_depth - 1, _seen=_seen)
            for item in list(value)[:200]
        }

    if isinstance(value, str):
        return redact_sensitive_text(value, max_len=1000)

    return value


def redact_dict(payload: dict[str, Any], *, max_depth: int = 20) -> dict[str, Any]:
    """递归脱敏字典结构中的敏感键值（向后兼容）。"""
    res = redact_value(payload, max_depth=max_depth)
    return res if isinstance(res, dict) else {"data": res}


AUDIT_EVENT_WHITELISTS: dict[str, set[str]] = {
    "review_task": {"mode", "bytes", "task_id", "status", "source_file"},
    "enqueue_review_job": {"created", "mode", "bytes", "job_id", "job_type"},
    "issue_feedback": {
        "action",
        "note",
        "previous_state",
        "new_state",
        "issue_id",
        "task_id",
    },
    "approve_format_standard": {"standard_id", "version", "approver"},
    "approve_rule": {"rule_id", "version", "approver"},
    "cancel_job": {"job_id", "status"},
    "document_import": {"document_id", "source_file", "source_hash", "status", "category"},
    "DEFAULT": {
        "action",
        "target",
        "status",
        "mode",
        "bytes",
        "version",
        "count",
        "task_id",
        "job_id",
        "created",
    },
}


def safe_audit_detail(event_type: str, fields: Mapping[str, object]) -> str:
    """根据审计事件类型白名单安全构建审计 detail 字符串。"""
    allowed_keys = AUDIT_EVENT_WHITELISTS.get(
        event_type, AUDIT_EVENT_WHITELISTS["DEFAULT"]
    )
    items: list[str] = []
    for k in sorted(fields.keys()):
        if k in allowed_keys:
            val_str = redact_sensitive_text(str(fields[k]), max_len=200)
            items.append(f"{k}={val_str}")
    detail_str = " ".join(items)
    return redact_sensitive_text(detail_str, max_len=500)


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
