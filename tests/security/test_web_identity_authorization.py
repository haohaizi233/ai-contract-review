# -*- coding: utf-8 -*-
"""Phase 14-R G1/DEFECT-P0-01: Web 工作台可信身份、租户隔离与会话目录安全验证测试。"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from contract_kb.interfaces.http.auth import compute_auth_signature_v2
from tools.contract_diff_web import (
    DiffVisualizerHandler,
    get_session_dir,
)


def test_session_dir_collision_and_tenant_isolation_verified():
    """验证修复：通过安全 Slug 与 SHA256 尾缀消除目录碰撞，并实现租户级物理隔离。"""
    dir_a = get_session_dir("sess/1", tenant_id="tenant_a")
    dir_b = get_session_dir("sess@1", tenant_id="tenant_a")
    dir_c = get_session_dir("sess/1", tenant_id="tenant_b")

    # 1. 相同租户不同特殊字符的会话不再碰撞
    assert dir_a != dir_b, f"期望会话隔离，实际发生了碰撞: dir_a={dir_a}, dir_b={dir_b}"

    # 2. 不同租户同名会话物理目录严格隔离
    assert dir_a != dir_c, f"期望跨租户目录隔离，实际合流: dir_a={dir_a}, dir_c={dir_c}"
    assert "tenant_a" in str(dir_a)
    assert "tenant_b" in str(dir_c)


@pytest.fixture(scope="module")
def web_test_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), DiffVisualizerHandler)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base_url = f"http://127.0.0.1:{port}"
    yield base_url
    server.shutdown()


def test_spoofed_tenant_in_query_or_header_rejected(web_test_server):
    """验证安全防护：单机本地模式下，客户端未认证试图越权声明非 default 外部租户被严格拦截拒绝 (403)。"""
    # 模拟攻击者调用 agent_status，未经认证自报 ?tenant_id=victim_corp&user_id=attacker
    req = urllib.request.Request(
        f"{web_test_server}/api/diff/agent_status?session_id=sess_test&tenant_id=victim_corp&user_id=attacker"
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)

    assert exc_info.value.code == 403, (
        f"期望返回 403 拦截越权租户，实际返回 {exc_info.value.code}"
    )
    body = exc_info.value.read().decode("utf-8")
    assert "禁止未经认证访问或伪造非 default 租户身份" in body


def test_legitimate_local_dev_request_allowed(web_test_server):
    """验证单机开发模式正常请求默认租户正常放行 (200)。"""
    req = urllib.request.Request(
        f"{web_test_server}/api/diff/agent_status?session_id=sess_valid"
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert "status" in data


def test_csrf_origin_rejected_on_post(web_test_server):
    """验证写操作跨站请求伪造 (CSRF) 防护：恶意外部 Origin 提交被 403 拦截。"""
    req = urllib.request.Request(
        f"{web_test_server}/api/diff/retry_card",
        data=b'{"id": "ann_1"}',
        headers={
            "Content-Type": "application/json",
            "Origin": "http://malicious-phishing-site.com",
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)

    assert exc_info.value.code == 403
    body = exc_info.value.read().decode("utf-8")
    assert "跨站请求伪造被拦截" in body


def test_enterprise_mode_signature_verification(web_test_server, monkeypatch):
    """验证企业多用户模式：需通过 HMAC-SHA256 签名，未签名或签名伪造直接 403 拦截。"""
    shared_key = "test_enterprise_secret_key_123456"
    monkeypatch.setenv("DIFF_WEB_MODE", "ENTERPRISE")
    monkeypatch.setenv("CONTRACT_KB_AUTH_SHARED_KEY", shared_key)

    # 1. 缺少签名的请求被拒绝
    req_no_sig = urllib.request.Request(
        f"{web_test_server}/api/diff/agent_status?session_id=sess_ent"
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_no_sig)
    assert exc_info.value.code == 403

    # 2. 携带有效 HMAC 签名的请求成功放行
    user = "enterprise_user_1"
    roles = "reviewer"
    tenant = "corp_tenant_alpha"
    nonce = "1234567890abcdef"
    ts = "1700000000.0"
    path = "/api/diff/agent_status"
    query = "session_id=sess_ent"

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

    req_valid = urllib.request.Request(
        f"{web_test_server}{path}?{query}",
        headers={
            "X-User": user,
            "X-Roles": roles,
            "X-Tenant": tenant,
            "X-Auth-Signature": sig,
            "X-Auth-Timestamp": ts,
            "X-Auth-Nonce": nonce,
        },
    )
    with urllib.request.urlopen(req_valid) as resp:
        assert resp.status == 200


def test_root_html_preserves_trusted_origin_cors(web_test_server):
    request = urllib.request.Request(
        web_test_server + "/", headers={"Origin": "http://localhost:3000"}
    )
    with urllib.request.urlopen(request) as response:
        assert (
            response.headers.get("Access-Control-Allow-Origin")
            == "http://localhost:3000"
        )
