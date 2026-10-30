# -*- coding: utf-8 -*-
"""API 鉴权与授权（Phase 1.4）。

原则：
- 身份由**受信任的认证层**提供（企业网关注入的 X-User / X-Roles / X-Tenant 头，
  生产应替换为 JWT/OIDC 校验），不接受客户端自报的 actor/approver 作为审批身份；
- RBAC 最小权限：viewer（只读检索）/ reviewer（审核+反馈）/ admin（规则创建+审批）；
- tenant_id 强制过滤：所有查询绑定调用者租户，跨租户访问返回 403；
- 输入长度/上传大小限制；错误不泄露内部细节；全部敏感操作写审计。
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import HTTPException, Request

from . import config

ROLE_VIEWER = config.ROLE_VIEWER
ROLE_REVIEWER = config.ROLE_REVIEWER
ROLE_ADMIN = config.ROLE_ADMIN

# 路径 → 最低所需角色
ROUTE_ROLES: dict[str, str] = {
    "GET /api/contract-kb/documents": ROLE_VIEWER,
    "GET /api/contract-kb/documents/": ROLE_VIEWER,
    "GET /api/contract-kb/taxonomy": ROLE_VIEWER,
    "POST /api/contract-kb/search": ROLE_VIEWER,
    "POST /api/contract-kb/compare": ROLE_VIEWER,
    "GET /api/contract-kb/rules": ROLE_VIEWER,
    "POST /api/contract-kb/import": ROLE_ADMIN,
    "POST /api/contract-kb/rules": ROLE_ADMIN,
    "POST /api/contract-kb/rules/": ROLE_ADMIN,
    "POST /api/contract-review/tasks": ROLE_REVIEWER,
    "GET /api/contract-review/tasks/": ROLE_REVIEWER,
    "POST /api/contract-review/issues/": ROLE_REVIEWER,
}

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
        return any(ROLE_RANK.get(r, 0) >= ROLE_RANK.get(required, 99) for r in self.roles)


class TrustedHeaderAuth:
    """受信任认证层适配器（示例实现）。

    生产部署：由企业网关/SSO 校验签名后注入 X-User / X-Roles / X-Tenant；
    若网关与本服务直连，必须配置 CONTRACT_KB_AUTH_SHARED_KEY 进行 HMAC 校验，
    防止客户端伪造头。未配置密钥时仅信任本机回环（127.0.0.1）。
    """

    def __init__(self, shared_key: str | None = None):
        self.shared_key = shared_key or os.environ.get("CONTRACT_KB_AUTH_SHARED_KEY")

    def authenticate(self, request: Request) -> Principal:
        user = request.headers.get("X-User", "").strip()
        roles_raw = request.headers.get("X-Roles", "").strip()
        tenant = request.headers.get("X-Tenant", "default").strip() or "default"
        if not user or not roles_raw:
            raise HTTPException(401, "缺少认证信息（需受信任认证层注入 X-User/X-Roles）")
        if self.shared_key:
            sig = request.headers.get("X-Auth-Signature", "")
            msg = f"{user}|{roles_raw}|{tenant}".encode()
            if not hmac.compare_digest(sig, hmac.new(self.shared_key.encode(), msg, "sha256").hexdigest()):
                raise HTTPException(401, "认证签名无效")
        else:
            client = request.client.host if request.client else None
            if client not in ("127.0.0.1", "::1", "testclient"):
                raise HTTPException(401, "未配置共享密钥时仅允许本机回环访问")
        roles = [r.strip() for r in roles_raw.split(",") if r.strip() in ROLE_RANK]
        if not roles:
            raise HTTPException(403, "无有效角色")
        return Principal(user_id=user, roles=roles, tenant_id=tenant)


import os  # noqa: E402  （供 shared_key 环境变量）


def authorize(principal: Principal, route_key: str) -> None:
    """路由级 RBAC。route_key 归一化：METHOD /api/...（前缀匹配）。"""
    required = None
    for k, v in ROUTE_ROLES.items():
        if route_key.startswith(k):
            required = v if required is None else max(required, v, key=lambda r: ROLE_RANK.get(r, 0))
    if required is None:
        required = ROLE_ADMIN  # 未知路由默认最高权限
    if not principal.has_role(required):
        raise HTTPException(403, f"权限不足：需要 {required}")


def check_tenant(principal: Principal, resource_tenant: str) -> None:
    if resource_tenant and resource_tenant != principal.tenant_id:
        raise HTTPException(403, "跨租户访问被拒绝")


def enforce_input_limits(query: str = "", custom: str = "", compare_texts: list[str] | None = None):
    if query and len(query) > MAX_QUERY_LEN:
        raise HTTPException(413, "查询超长")
    if custom and len(custom) > MAX_CUSTOM_REQ_LEN:
        raise HTTPException(413, "自定义要求超长")
    if compare_texts and sum(len(t) for t in compare_texts) > MAX_COMPARE_LEN:
        raise HTTPException(413, "比较文本超长")
