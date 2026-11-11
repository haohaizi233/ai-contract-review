# -*- coding: utf-8 -*-
"""Phase 16-S: Web 企业认证与防重放机制安全测试。

覆盖：
- Case 01: 无密钥企业模式直接拒绝（Fail-Closed）
- Case 02: 旧签名重放与 Nonce 唯一性防御
- Case 03: 请求正文 Body Hash 篡改拦截
- 时间戳容差窗口（超 300s 拦截，非法值拦截）
- 角色权限矩阵 (RBAC: Viewer / Reviewer / Admin) 敏感操作阻断
"""

import hashlib
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from contract_kb.interfaces.http.auth import (
    compute_auth_signature_v2,
)
from tools.contract_diff_web import DiffVisualizerHandler


@pytest.fixture(scope="module")
def web_enterprise_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), DiffVisualizerHandler)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base_url = f"http://127.0.0.1:{port}"
    yield base_url
    server.shutdown()


def test_enterprise_mode_without_secret_fails_closed(
    web_enterprise_server, monkeypatch
):
    """Case 01: DIFF_WEB_MODE=ENTERPRISE 但未配置密钥，必须 Fail-Closed 拒绝所有请求。"""
    monkeypatch.setenv("DIFF_WEB_MODE", "ENTERPRISE")
    monkeypatch.delenv("CONTRACT_KB_AUTH_SHARED_KEY", raising=False)

    req = urllib.request.Request(
        f"{web_enterprise_server}/api/diff/agent_status?session_id=s1",
        headers={
            "X-User": "admin_user",
            "X-Roles": "admin",
            "X-Tenant": "tenant_alpha",
            "X-Auth-Signature": "fake_sig_123",
            "X-Auth-Timestamp": str(time.time()),
            "X-Auth-Nonce": "nonce_fail_closed",
        },
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 403


def test_nonce_replay_attack_rejected(web_enterprise_server, monkeypatch):
    """Case 02: 同一合法签名与 Nonce 第二次请求必须被 403 阻断（防重放）。"""
    shared_key = "test_shared_key_for_phase16_replay"
    monkeypatch.setenv("DIFF_WEB_MODE", "ENTERPRISE")
    monkeypatch.setenv("CONTRACT_KB_AUTH_SHARED_KEY", shared_key)

    user = "reviewer_alice"
    roles = "reviewer"
    tenant = "corp_alpha"
    path = "/api/diff/agent_status"
    query = "session_id=sess_replay_test"
    ts = str(time.time())
    nonce = f"nonce_unique_{time.time_ns()}"

    sig = compute_auth_signature_v2(
        key=shared_key,
        method="GET",
        path=path,
        query=query,
        user=user,
        roles=roles,
        tenant=tenant,
        timestamp=ts,
        nonce=nonce,
        body_sha256="",
    )

    req = urllib.request.Request(
        f"{web_enterprise_server}{path}?{query}",
        headers={
            "X-User": user,
            "X-Roles": roles,
            "X-Tenant": tenant,
            "X-Auth-Signature": sig,
            "X-Auth-Timestamp": ts,
            "X-Auth-Nonce": nonce,
        },
    )

    # 第一次请求成功
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200

    # 第二次重放请求必须被拒绝 (403)
    req_replay = urllib.request.Request(
        f"{web_enterprise_server}{path}?{query}",
        headers={
            "X-User": user,
            "X-Roles": roles,
            "X-Tenant": tenant,
            "X-Auth-Signature": sig,
            "X-Auth-Timestamp": ts,
            "X-Auth-Nonce": nonce,
        },
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_replay)
    assert exc_info.value.code == 403


def test_body_tampering_rejected(web_enterprise_server, monkeypatch):
    """Case 03: 攻击者篡改 POST Body 内容，Body SHA256 不匹配被 403 阻断。"""
    shared_key = "test_shared_key_for_body_tamper"
    monkeypatch.setenv("DIFF_WEB_MODE", "ENTERPRISE")
    monkeypatch.setenv("CONTRACT_KB_AUTH_SHARED_KEY", shared_key)

    user = "reviewer_alice"
    roles = "reviewer"
    tenant = "corp_alpha"
    path = "/api/diff/prioritize_card"
    query = ""
    ts = str(time.time())
    nonce = f"nonce_tamper_{time.time_ns()}"

    legit_body = b'{"id": "ann-1", "session_id": "sess_prioritize"}'
    legit_hash = hashlib.sha256(legit_body).hexdigest()

    sig = compute_auth_signature_v2(
        key=shared_key,
        method="POST",
        path=path,
        query=query,
        user=user,
        roles=roles,
        tenant=tenant,
        timestamp=ts,
        nonce=nonce,
        body_sha256=legit_hash,
    )

    # 攻击者篡改正文，但保持原有签名
    tampered_body = b'{"id": "ann-999-malicious", "session_id": "sess_prioritize"}'
    req_tampered = urllib.request.Request(
        f"{web_enterprise_server}{path}",
        data=tampered_body,
        headers={
            "Content-Type": "application/json",
            "X-User": user,
            "X-Roles": roles,
            "X-Tenant": tenant,
            "X-Auth-Signature": sig,
            "X-Auth-Timestamp": ts,
            "X-Auth-Nonce": nonce,
            "X-Content-SHA256": legit_hash,  # 自称与签名一致，但实际 body 不匹配
        },
        method="POST",
    )

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_tampered)
    assert exc_info.value.code == 403


def test_timestamp_skew_tolerance_rejected(web_enterprise_server, monkeypatch):
    """验证时间戳超出 300s 窗口或未来超限时被 403 阻断。"""
    shared_key = "test_shared_key_for_timestamp_skew"
    monkeypatch.setenv("DIFF_WEB_MODE", "ENTERPRISE")
    monkeypatch.setenv("CONTRACT_KB_AUTH_SHARED_KEY", shared_key)

    user = "reviewer_alice"
    roles = "reviewer"
    tenant = "corp_alpha"
    path = "/api/diff/agent_status"
    query = "session_id=s_skew"

    # 过期时间戳（1000秒前）
    ts_past = str(time.time() - 1000)
    nonce_past = f"nonce_past_{time.time_ns()}"
    sig_past = compute_auth_signature_v2(
        key=shared_key,
        method="GET",
        path=path,
        query=query,
        user=user,
        roles=roles,
        tenant=tenant,
        timestamp=ts_past,
        nonce=nonce_past,
        body_sha256="",
    )
    req_past = urllib.request.Request(
        f"{web_enterprise_server}{path}?{query}",
        headers={
            "X-User": user,
            "X-Roles": roles,
            "X-Tenant": tenant,
            "X-Auth-Signature": sig_past,
            "X-Auth-Timestamp": ts_past,
            "X-Auth-Nonce": nonce_past,
        },
    )
    with pytest.raises(urllib.error.HTTPError) as exc_past:
        urllib.request.urlopen(req_past)
    assert exc_past.value.code == 403

    # 未来时间戳（1000秒后）
    ts_future = str(time.time() + 1000)
    nonce_future = f"nonce_fut_{time.time_ns()}"
    sig_future = compute_auth_signature_v2(
        key=shared_key,
        method="GET",
        path=path,
        query=query,
        user=user,
        roles=roles,
        tenant=tenant,
        timestamp=ts_future,
        nonce=nonce_future,
        body_sha256="",
    )
    req_future = urllib.request.Request(
        f"{web_enterprise_server}{path}?{query}",
        headers={
            "X-User": user,
            "X-Roles": roles,
            "X-Tenant": tenant,
            "X-Auth-Signature": sig_future,
            "X-Auth-Timestamp": ts_future,
            "X-Auth-Nonce": nonce_future,
        },
    )
    with pytest.raises(urllib.error.HTTPError) as exc_fut:
        urllib.request.urlopen(req_future)
    assert exc_fut.value.code == 403


def test_rbac_viewer_blocked_from_mutating_actions(web_enterprise_server, monkeypatch):
    """验证 RBAC: Viewer 角色无法执行敏感写操作（如卡片跳过、重审、重载模板）。"""
    shared_key = "test_shared_key_rbac"
    monkeypatch.setenv("DIFF_WEB_MODE", "ENTERPRISE")
    monkeypatch.setenv("CONTRACT_KB_AUTH_SHARED_KEY", shared_key)

    user = "viewer_bob"
    roles = "viewer"  # 仅只读权限
    tenant = "corp_alpha"
    path = "/api/diff/skip_card"
    query = ""
    ts = str(time.time())
    nonce = f"nonce_viewer_{time.time_ns()}"
    body = b'{"id": "ann-1", "session_id": "sess_viewer"}'
    b_hash = hashlib.sha256(body).hexdigest()

    sig = compute_auth_signature_v2(
        key=shared_key,
        method="POST",
        path=path,
        query=query,
        user=user,
        roles=roles,
        tenant=tenant,
        timestamp=ts,
        nonce=nonce,
        body_sha256=b_hash,
    )

    req = urllib.request.Request(
        f"{web_enterprise_server}{path}",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-User": user,
            "X-Roles": roles,
            "X-Tenant": tenant,
            "X-Auth-Signature": sig,
            "X-Auth-Timestamp": ts,
            "X-Auth-Nonce": nonce,
            "X-Content-SHA256": b_hash,
        },
        method="POST",
    )

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 403
