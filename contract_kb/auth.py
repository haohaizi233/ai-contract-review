# -*- coding: utf-8 -*-
"""API 鉴权与授权（企业级加固版）。

原则：
- 身份由**受信任的认证层**提供（企业网关注入的 X-User / X-Roles / X-Tenant 头，
  生产支持共享密钥 HMAC + 时间戳防重放校验），不接受客户端自报 actor/approver；
- RBAC 最小权限：viewer（只读检索）/ reviewer（审核+反馈）/ admin（规则创建+审批+导入）；
- 路径段级精确匹配（防止前缀碰撞越权），未知路由默认 fail-closed（需 admin）；
- tenant_id 强制过滤：所有查询绑定调用者租户；
- 输入长度/上传大小限制；错误不泄露内部细节。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
import urllib.parse
from typing import Any
from dataclasses import dataclass

from fastapi import HTTPException, Request

from . import config

ROLE_VIEWER = config.ROLE_VIEWER
ROLE_REVIEWER = config.ROLE_REVIEWER
ROLE_ADMIN = config.ROLE_ADMIN

# 路径 → 最低所需角色（支持 /api/* 与 /api/v1/* 双路由）
_BASE_ROUTE_ROLES: dict[str, str] = {
    "GET /healthz": ROLE_VIEWER,
    "GET /readyz": ROLE_VIEWER,
    "GET /api/healthz": ROLE_VIEWER,
    "GET /api/readyz": ROLE_VIEWER,
    "GET /api/metrics": ROLE_ADMIN,
    "GET /api/contract-kb/documents": ROLE_VIEWER,
    "GET /api/contract-kb/documents/": ROLE_VIEWER,
    "GET /api/contract-kb/taxonomy": ROLE_VIEWER,
    "POST /api/contract-kb/search": ROLE_VIEWER,
    "POST /api/contract-kb/compare": ROLE_VIEWER,
    "GET /api/contract-kb/rules": ROLE_VIEWER,
    "POST /api/contract-kb/import": ROLE_ADMIN,
    "POST /api/contract-kb/rules": ROLE_ADMIN,
    "POST /api/contract-kb/rules/": ROLE_ADMIN,
    "GET /api/contract-kb/format-standards": ROLE_VIEWER,
    "POST /api/contract-kb/format-standards": ROLE_ADMIN,
    "POST /api/contract-kb/format-standards/": ROLE_ADMIN,
    "POST /api/contract-review/tasks": ROLE_REVIEWER,
    "GET /api/contract-review/tasks": ROLE_REVIEWER,
    "GET /api/contract-review/tasks/": ROLE_REVIEWER,
    "POST /api/contract-review/tasks/": ROLE_REVIEWER,
    "POST /api/contract-review/issues/": ROLE_REVIEWER,
    "GET /api/canonical-contracts": ROLE_VIEWER,
    "GET /api/canonical-contracts/": ROLE_VIEWER,
    "GET /api/jobs": ROLE_REVIEWER,
    "GET /api/jobs/": ROLE_REVIEWER,
    "POST /api/jobs": ROLE_REVIEWER,
    "POST /api/jobs/": ROLE_REVIEWER,
    "POST /api/jobs/worker/": ROLE_ADMIN,
}

ROUTE_ROLES: dict[str, str] = dict(_BASE_ROUTE_ROLES)
for _rk, _rv in list(_BASE_ROUTE_ROLES.items()):
    if " /api/" in _rk:
        ROUTE_ROLES[_rk.replace(" /api/", " /api/v1/", 1)] = _rv

ROLE_RANK = {ROLE_VIEWER: 1, ROLE_REVIEWER: 2, ROLE_ADMIN: 3}

MAX_QUERY_LEN = 2000
MAX_CUSTOM_REQ_LEN = 4000
MAX_COMPARE_LEN = 20000


@dataclass
class Principal:
    """经认证层解析出的调用者身份（不可由客户端伪造 actor/approver 字段）。"""

    user_id: str
    roles: list[str]
    tenant_id: str = "default"

    def has_role(self, required: str) -> bool:
        return any(
            ROLE_RANK.get(r, 0) >= ROLE_RANK.get(required, 99) for r in self.roles
        )


EMPTY_BODY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def compute_auth_signature_v2(
    *,
    key: str,
    method: str,
    path: str,
    query: str = "",
    user: str = "",
    roles: str = "",
    tenant: str = "default",
    timestamp: str = "",
    nonce: str = "",
    body_sha256: str = "",
) -> str:
    """计算 V2 规范化认证 HMAC-SHA256 签名。

    规范化载荷：
    METHOD\nPATH\nSORTED_QUERY\nUSER\nROLES\nTENANT\nTIMESTAMP\nNONCE\nBODY_SHA256
    """
    if query:
        pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
        pairs.sort(key=lambda x: (x[0], x[1]))
        canonical_query = urllib.parse.urlencode(pairs)
    else:
        canonical_query = ""

    norm_body_sha256 = (body_sha256 or EMPTY_BODY_SHA256).strip().lower()
    canonical_msg = "\n".join(
        [
            method.upper().strip(),
            path.strip(),
            canonical_query,
            user.strip(),
            roles.strip(),
            tenant.strip(),
            str(timestamp).strip(),
            nonce.strip(),
            norm_body_sha256,
        ]
    )
    return hmac.new(
        key.encode("utf-8"), canonical_msg.encode("utf-8"), hashlib.sha256
    ).hexdigest()


class TrustedHeaderAuth:
    """受信任认证层适配器（支持签名 V2 完整载荷绑定与防重放 Nonce 校验）。"""

    def __init__(
        self,
        shared_key: str | None = None,
        storage: Any = None,
        auth_version: str | None = None,
    ):
        self._explicit_key = shared_key
        self.storage = storage
        self._auth_version = auth_version
        self._seen_nonces: dict[str, float] = {}

    @property
    def shared_key(self) -> str | None:
        if self._explicit_key is not None:
            return self._explicit_key
        val = os.environ.get("CONTRACT_KB_AUTH_SHARED_KEY", "").strip()
        return val or None

    def authenticate(self, request: Request) -> Principal:
        user = request.headers.get("X-User", "").strip()
        roles_raw = request.headers.get("X-Roles", "").strip()
        tenant = request.headers.get("X-Tenant", "default").strip() or "default"
        if not user or not roles_raw:
            raise HTTPException(
                401, "缺少认证信息（需受信任认证层注入 X-User/X-Roles）"
            )

        key = self.shared_key
        if key:
            sig = request.headers.get("X-Auth-Signature", "").strip()
            ts_raw = request.headers.get("X-Auth-Timestamp", "").strip()
            version_hdr = request.headers.get("X-Auth-Version", "").strip()
            nonce = request.headers.get("X-Auth-Nonce", "").strip()

            target_version = (
                version_hdr
                or self._auth_version
                or os.environ.get("CONTRACT_KB_AUTH_VERSION", "COMPAT")
            )
            is_v2 = target_version == "2" or (target_version != "1" and bool(nonce))

            if is_v2:
                if not ts_raw:
                    raise HTTPException(401, "缺少认证时间戳 X-Auth-Timestamp")
                try:
                    ts_val = float(ts_raw)
                except ValueError as exc:
                    raise HTTPException(401, "认证时间戳格式非法") from exc

                max_skew = int(
                    os.environ.get(
                        "CONTRACT_KB_AUTH_MAX_SKEW_SECONDS",
                        str(config.AUTH_MAX_SKEW_SECONDS),
                    )
                )
                if abs(time.time() - ts_val) > max_skew:
                    raise HTTPException(401, "认证时间戳超出防重放窗口")

                if not nonce or len(nonce) < 16:
                    raise HTTPException(401, "缺少有效防重放 Nonce（需至少 16 字符）")

                # (1) 读取真实请求正文并校验正文摘要 SHA-256
                raw_body = getattr(request, "_body", None)
                if raw_body is None:
                    raw_body = request.scope.get("_body", None)

                if raw_body is None:
                    # 鉴权无法获得可靠正文摘要时必须拒绝请求，严禁把“无法取得正文”当成合法空正文
                    cl = request.headers.get("content-length", "").strip()
                    te = request.headers.get("transfer-encoding", "").strip()
                    ct = request.headers.get("content-type", "").strip()
                    if cl == "0":
                        raw_body = b""
                    elif (
                        not cl
                        and not te
                        and not ct
                        and request.method
                        in ("GET", "HEAD", "OPTIONS", "DELETE", "POST", "PUT")
                    ):
                        raw_body = b""
                    else:
                        raise HTTPException(
                            401, "无法获取可靠请求正文摘要以完成签名校验"
                        )
                real_body_sha = hashlib.sha256(raw_body).hexdigest()
                header_body_sha = request.headers.get("X-Content-SHA256", "").strip()
                if not header_body_sha:
                    header_body_sha = EMPTY_BODY_SHA256

                if header_body_sha.lower() != real_body_sha.lower():
                    raise HTTPException(
                        401,
                        f"正文摘要校验失败（X-Content-SHA256 与真实请求正文不匹配）：header={header_body_sha}, actual={real_body_sha}",
                    )

                # (2) 校验签名（先验证签名，未通过前严禁消费 Nonce，避免 Nonce 投毒拒绝服务）
                raw_query = request.scope.get("query_string", b"").decode("utf-8")
                expected = compute_auth_signature_v2(
                    key=key,
                    method=request.method,
                    path=request.url.path,
                    query=raw_query,
                    user=user,
                    roles=roles_raw,
                    tenant=tenant,
                    timestamp=ts_raw,
                    nonce=nonce,
                    body_sha256=header_body_sha,
                )
                if not sig or not hmac.compare_digest(sig, expected):
                    raise HTTPException(401, "认证签名无效")

                # (3) 签名合法后，原子消费 Nonce（防重放与跨实例共享存储）
                now = time.time()
                self._seen_nonces = {
                    k: exp for k, exp in self._seen_nonces.items() if exp > now
                }
                if nonce in self._seen_nonces:
                    raise HTTPException(401, "认证请求重放（Nonce 已被使用）")

                st = self.storage
                if st is None:
                    try:
                        import contract_kb.api as api_mod

                        st = getattr(api_mod, "get_storage", lambda: None)()
                    except Exception:
                        st = None

                if st is not None:
                    record_fn = getattr(st, "record_and_check_nonce", None)
                    if record_fn is None:
                        raise HTTPException(503, "持久化 Nonce 防重放服务未就绪")
                    ok = record_fn(nonce, tenant, user, float(ts_val) + max_skew)
                    if not ok:
                        raise HTTPException(401, "认证请求重放（Nonce 已被使用）")
                else:
                    if os.environ.get("CONTRACT_KB_ENV") == "production":
                        raise HTTPException(503, "持久化 Nonce 存储不可用")

                self._seen_nonces[nonce] = float(ts_val) + max_skew
            else:
                # V1 模式校验
                if (
                    self._auth_version == "2"
                    or os.environ.get("CONTRACT_KB_AUTH_VERSION") == "2"
                ):
                    raise HTTPException(
                        401,
                        "系统已启用签名 V2 强制模式，拒绝 V1 签名（缺少 Nonce/版本）",
                    )

                require_ts = (
                    os.environ.get("CONTRACT_KB_REQUIRE_AUTH_TIMESTAMP", "0") == "1"
                    or config.REQUIRE_AUTH_TIMESTAMP
                )
                if require_ts and not ts_raw:
                    raise HTTPException(401, "缺少认证时间戳 X-Auth-Timestamp")
                if ts_raw:
                    try:
                        ts_val = float(ts_raw)
                    except ValueError as exc:
                        raise HTTPException(401, "认证时间戳格式非法") from exc
                    max_skew = int(
                        os.environ.get(
                            "CONTRACT_KB_AUTH_MAX_SKEW_SECONDS",
                            str(config.AUTH_MAX_SKEW_SECONDS),
                        )
                    )
                    if abs(time.time() - ts_val) > max_skew:
                        raise HTTPException(401, "认证时间戳超出防重放窗口")
                    msg = f"{user}|{roles_raw}|{tenant}|{ts_raw}".encode()
                else:
                    msg = f"{user}|{roles_raw}|{tenant}".encode()

                expected = hmac.new(key.encode(), msg, "sha256").hexdigest()
                if not sig or not hmac.compare_digest(sig, expected):
                    raise HTTPException(401, "认证签名无效")
        else:
            client = request.client.host if request.client else None
            if client not in ("127.0.0.1", "::1", "testclient"):
                raise HTTPException(401, "未配置共享密钥时仅允许本机回环访问")

        roles = [r.strip() for r in roles_raw.split(",") if r.strip() in ROLE_RANK]
        if not roles:
            raise HTTPException(403, "无有效角色")
        return Principal(user_id=user, roles=roles, tenant_id=tenant)


def _route_matches(route_key: str, pattern: str) -> bool:
    if pattern.endswith("/"):
        return route_key.startswith(pattern)
    return route_key == pattern or route_key.startswith(pattern + "/")


def authorize(principal: Principal, route_key: str) -> None:
    """路由级 RBAC（路径段级边界匹配，未知路由默认最高权限 fail-closed）。"""
    required = None
    for k, v in ROUTE_ROLES.items():
        if _route_matches(route_key, k):
            required = (
                v
                if required is None
                else max(required, v, key=lambda r: ROLE_RANK.get(r, 0))
            )
    if required is None:
        required = ROLE_ADMIN
    if not principal.has_role(required):
        raise HTTPException(403, f"权限不足：需要 {required}")


def check_tenant(principal: Principal, resource_tenant: str) -> None:
    if resource_tenant and resource_tenant != principal.tenant_id:
        raise HTTPException(403, "跨租户访问被拒绝")


def enforce_input_limits(
    query: str = "", custom: str = "", compare_texts: list[str] | None = None
) -> None:
    if query and len(query) > MAX_QUERY_LEN:
        raise HTTPException(413, "查询超长")
    if custom and len(custom) > MAX_CUSTOM_REQ_LEN:
        raise HTTPException(413, "自定义要求超长")
    if compare_texts and sum(len(t) for t in compare_texts) > MAX_COMPARE_LEN:
        raise HTTPException(413, "比较文本超长")
