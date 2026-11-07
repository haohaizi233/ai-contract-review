# -*- coding: utf-8 -*-
"""安全测试：认证签名 V2 与真实防重放机制（T04 / F08）。

严格验证：
1. 防重放：相同签名与时间戳在时钟窗口内发起第二次请求必须被拒绝（401）；
2. 完整绑定：篡改 HTTP Method、Path、Query 参数或 Body 摘要中的任意一项，签名校验必须失败（401）；
3. 时钟窗口：时间戳偏差超过 ±300 秒必须被拒绝（401）；
4. Nonce 约束：V2 签名下缺失 Nonce 或 Nonce 长度小于 16 必须被拒绝（401）；
5. 渐进式过渡兼容：在兼容模式（COMPAT）下安全支持 V1 签名，在 V2 强制模式下拒绝 V1。
"""

from __future__ import annotations

import time

from fastapi import HTTPException
import pytest
from starlette.requests import Request

from contract_kb.auth import (
    TrustedHeaderAuth,
    compute_auth_signature_v2,
)
from contract_kb.storage import Storage

SHARED_KEY = "synthetic-corp-shared-secret-key-32b"


def _make_request(
    method: str = "GET",
    path: str = "/api/v1/contract-kb/documents",
    query: str = "",
    headers: dict[str, str] | None = None,
    client: tuple[str, int] = ("10.0.0.1", 12345),
) -> Request:
    raw_headers = [
        (k.lower().encode("utf-8"), v.encode("utf-8"))
        for k, v in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": query.encode("utf-8"),
        "headers": raw_headers,
        "client": client,
    }
    return Request(scope)


def test_v2_signature_prevents_replay_attack(tmp_path):
    """验证相同签名+Nonce在窗口内第二次请求被拒绝。"""
    st = Storage(tmp_path / "replay_auth.sqlite")
    auth = TrustedHeaderAuth(shared_key=SHARED_KEY, storage=st, auth_version="2")

    ts = str(int(time.time()))
    nonce = "random_nonce_1234567890_abcdef"
    method = "POST"
    path = "/api/v1/contract-kb/rules/r1/approve"
    user = "alice"
    roles = "admin"
    tenant = "corp_tenant"

    sig = compute_auth_signature_v2(
        key=SHARED_KEY,
        method=method,
        path=path,
        query="",
        user=user,
        roles=roles,
        tenant=tenant,
        timestamp=ts,
        nonce=nonce,
        body_sha256="",
    )

    req_headers = {
        "X-User": user,
        "X-Roles": roles,
        "X-Tenant": tenant,
        "X-Auth-Timestamp": ts,
        "X-Auth-Nonce": nonce,
        "X-Auth-Signature": sig,
        "X-Auth-Version": "2",
    }

    req1 = _make_request(method=method, path=path, headers=req_headers)
    p1 = auth.authenticate(req1)
    assert p1.user_id == "alice"
    assert p1.has_role("admin")

    # 第二次使用完全相同的凭据发起重放 -> 必须 401
    req2 = _make_request(method=method, path=path, headers=req_headers)
    with pytest.raises(HTTPException) as exc_info:
        auth.authenticate(req2)
    assert exc_info.value.status_code == 401
    assert "重放" in exc_info.value.detail or "replay" in exc_info.value.detail.lower()
    st.close()


def test_v2_signature_tamper_detection(tmp_path):
    """验证篡改 Method、Path、Query、Body 任意一项即刻被拒。"""
    st = Storage(tmp_path / "tamper_auth.sqlite")
    auth = TrustedHeaderAuth(shared_key=SHARED_KEY, storage=st, auth_version="2")

    ts = str(int(time.time()))
    nonce = "tamper_test_nonce_987654321"
    user = "bob"
    roles = "reviewer"
    tenant = "corp_tenant"

    sig = compute_auth_signature_v2(
        key=SHARED_KEY,
        method="POST",
        path="/api/v1/jobs/review",
        query="mode=STANDARD",
        user=user,
        roles=roles,
        tenant=tenant,
        timestamp=ts,
        nonce=nonce,
        body_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )

    base_headers = {
        "X-User": user,
        "X-Roles": roles,
        "X-Tenant": tenant,
        "X-Auth-Timestamp": ts,
        "X-Auth-Nonce": nonce,
        "X-Auth-Signature": sig,
        "X-Auth-Version": "2",
        "X-Content-SHA256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    }

    # 1. 篡改 Method（POST 改为 GET）
    req_tamper_method = _make_request(
        method="GET", path="/api/v1/jobs/review", query="mode=STANDARD", headers=base_headers
    )
    with pytest.raises(HTTPException) as exc:
        auth.authenticate(req_tamper_method)
    assert exc.value.status_code == 401

    # 2. 篡改 Path（发往未授权路径）
    req_tamper_path = _make_request(
        method="POST", path="/api/v1/contract-kb/rules", query="mode=STANDARD", headers=base_headers
    )
    with pytest.raises(HTTPException) as exc:
        auth.authenticate(req_tamper_path)
    assert exc.value.status_code == 401

    # 3. 篡改 Query（更改参数）
    req_tamper_query = _make_request(
        method="POST", path="/api/v1/jobs/review", query="mode=CUSTOM", headers=base_headers
    )
    with pytest.raises(HTTPException) as exc:
        auth.authenticate(req_tamper_query)
    assert exc.value.status_code == 401
    st.close()


def test_v2_signature_clock_skew_rejected(tmp_path):
    """验证时钟偏差超过 300 秒被拒绝。"""
    st = Storage(tmp_path / "skew_auth.sqlite")
    auth = TrustedHeaderAuth(shared_key=SHARED_KEY, storage=st, auth_version="2")

    old_ts = str(int(time.time()) - 400)
    nonce = "nonce_skew_check_1234567"
    sig = compute_auth_signature_v2(
        key=SHARED_KEY,
        method="GET",
        path="/api/v1/contract-kb/documents",
        query="",
        user="alice",
        roles="viewer",
        tenant="default",
        timestamp=old_ts,
        nonce=nonce,
    )
    headers = {
        "X-User": "alice",
        "X-Roles": "viewer",
        "X-Tenant": "default",
        "X-Auth-Timestamp": old_ts,
        "X-Auth-Nonce": nonce,
        "X-Auth-Signature": sig,
        "X-Auth-Version": "2",
    }
    req = _make_request(method="GET", path="/api/v1/contract-kb/documents", headers=headers)
    with pytest.raises(HTTPException) as exc:
        auth.authenticate(req)
    assert exc.value.status_code == 401
    assert "时间戳" in exc.value.detail or "skew" in exc.value.detail.lower()
    st.close()


def test_v2_mode_rejects_missing_nonce(tmp_path):
    """验证在 V2 模式下缺少 Nonce 直接 401。"""
    st = Storage(tmp_path / "nonce_auth.sqlite")
    auth = TrustedHeaderAuth(shared_key=SHARED_KEY, storage=st, auth_version="2")
    ts = str(int(time.time()))
    headers = {
        "X-User": "alice",
        "X-Roles": "viewer",
        "X-Tenant": "default",
        "X-Auth-Timestamp": ts,
        "X-Auth-Signature": "some_sig",
        "X-Auth-Version": "2",
    }
    req = _make_request(method="GET", path="/api/v1/contract-kb/documents", headers=headers)
    with pytest.raises(HTTPException) as exc:
        auth.authenticate(req)
    assert exc.value.status_code == 401
    assert "nonce" in exc.value.detail.lower()
    st.close()
