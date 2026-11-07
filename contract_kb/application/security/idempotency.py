# -*- coding: utf-8 -*-
"""请求幂等性控制（Request Idempotency）。

核心契约：
1. RequestIdentity: 绑定租户、操作人、操作类型、幂等键以及请求指纹；
2. compute_request_fingerprint: 结合内容哈希、审核模式、规范化自定义要求与配置版本计算确定性哈希，
   绝不包含本地文件路径或明文敏感信息；
3. IdempotencyOutcome / IdempotencyReservation: 明确四种状态：
   - CREATED: 首次成功预约，调用方持有唯一执行资格；
   - IN_PROGRESS: 该操作人已在执行中，返回同任务/作业引用；
   - REPLAY: 该操作人此前已完成，直接回放既有结果；
   - CONFLICT: 相同操作人相同幂等键，但请求参数/文件指纹发生变更，直接 409 拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Sequence


class IdempotencyConflictError(Exception):
    """幂等键冲突异常（同一作用域下复用同一 Key 但请求参数或指纹不一致）。"""

    def __init__(
        self, message: str = "Idempotency key conflict: request parameters changed"
    ):
        super().__init__(message)


@dataclass(frozen=True)
class RequestIdentity:
    """幂等性请求标识符契约。"""

    tenant_id: str
    actor_id: str
    operation: str
    key: str
    fingerprint: str


@dataclass
class IdempotencyOutcome:
    """幂等预约结果。"""

    status: str  # "CREATED" | "IN_PROGRESS" | "REPLAY" | "CONFLICT"
    resource_id: str | None = None
    message: str | None = None


# 兼容别名
IdempotencyReservation = IdempotencyOutcome


def compute_request_fingerprint(
    *,
    content_bytes: bytes | None = None,
    content_hash: str | None = None,
    mode: str = "",
    custom_requirements: Sequence[str] | None = None,
    config_version: str = "",
    operation: str = "",
    extra_params: dict[str, Any] | None = None,
) -> str:
    """计算规范化请求指纹。

    保证：
    1. 相同内容、相同模式、相同自定义要求产出完全相同的指纹；
    2. 指纹计算纯哈希化，不包含任何本地临时路径、环境名或合同敏感原文；
    3. 列表元素与字典键自动排序规范化。
    """
    if content_hash is None:
        if content_bytes is not None:
            content_hash = hashlib.sha256(content_bytes).hexdigest()
        else:
            content_hash = ""

    norm_mode = (mode or "").strip().upper()
    norm_reqs = sorted(
        [str(r).strip() for r in (custom_requirements or []) if str(r).strip()]
    )

    norm_extra = ""
    if extra_params:
        try:
            norm_extra = json.dumps(extra_params, sort_keys=True, ensure_ascii=False)
        except Exception:
            norm_extra = str(sorted(extra_params.items()))

    canonical_repr = json.dumps(
        {
            "content_hash": content_hash,
            "mode": norm_mode,
            "custom_requirements": norm_reqs,
            "config_version": str(config_version).strip(),
            "operation": str(operation).strip().upper(),
            "extra": norm_extra,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()
