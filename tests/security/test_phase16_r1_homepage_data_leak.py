# -*- coding: utf-8 -*-
"""Phase 16-S-R1: 企业首页未认证元数据泄露防护与数据最小化安全测试集。"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from contract_kb.interfaces.http.auth import compute_auth_signature_v2
from contract_kb.interfaces.web.app import (
    DiffVisualizerHandler,
    generate_rendered_html,
)


@pytest.fixture(scope="module")
def web_homepage_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), DiffVisualizerHandler)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base_url = f"http://127.0.0.1:{port}"
    yield base_url
    server.shutdown()


def test_enterprise_mode_html_nullifies_all_internal_metadata():
    """企业模式下生成的 HTML 必须严格将所有占位数据置空，绝不包含真实案例或母本。"""
    html = generate_rendered_html(is_enterprise=True)

    assert "let allTemplates = [] || [];" in html
    assert "window.MATERIAL_DATA = {} || {};" in html
    assert "let refTemplatesCache = [] || [];" in html
    assert "window.INITIAL_DATA = null;" in html

    # 严禁在页面中直接泄漏包含具体编号、客户主体、合同名称的案例列表
    assert '"initial_cases":' not in html


def test_unauthenticated_http_get_enterprise_homepage(web_homepage_server, monkeypatch):
    """未通过身份认证的用户直接发起 HTTP GET / 访问企业版前端，不得获取任何敏感内部数据。"""
    shared_key = "secret_for_homepage_leak_test"
    monkeypatch.setenv("DIFF_WEB_MODE", "ENTERPRISE")
    monkeypatch.setenv("CONTRACT_KB_AUTH_SHARED_KEY", shared_key)

    req = urllib.request.Request(f"{web_homepage_server}/", method="GET")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        html = resp.read().decode("utf-8")

    assert "window.MATERIAL_DATA = {} || {};" in html
    assert "window.INITIAL_DATA = null;" in html
    assert "let allTemplates = [] || [];" in html
    assert '"initial_cases":' not in html


def test_local_dev_mode_preserves_offline_sample_experience(monkeypatch):
    """单机开发模式下为保持离线开箱即用，允许正常注入本地演示样例。"""
    monkeypatch.delenv("DIFF_WEB_MODE", raising=False)
    monkeypatch.delenv("CONTRACT_KB_AUTH_SHARED_KEY", raising=False)

    html = generate_rendered_html(is_enterprise=False)
    # 只要存在素材，在单机开发模式下可以注入离线数据供本地快速演示
    assert html is not None
    assert len(html) > 1000


def test_authenticated_api_endpoints_still_provide_scoped_data(
    web_homepage_server, monkeypatch
):
    """已认证的用户通过受控 API (带 HMAC 验签) 能够正常获取对应权限的模板清单。"""
    shared_key = "secret_for_homepage_api_test"
    monkeypatch.setenv("DIFF_WEB_MODE", "ENTERPRISE")
    monkeypatch.setenv("CONTRACT_KB_AUTH_SHARED_KEY", shared_key)

    path = "/api/templates/list"
    query = ""
    user = "legal_viewer_01"
    roles = "viewer"
    tenant = "corp_audit_tenant"
    ts = str(time.time())
    nonce = f"nonce_tpl_{time.time_ns()}"

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
        body_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )

    req = urllib.request.Request(
        f"{web_homepage_server}{path}",
        headers={
            "X-User": user,
            "X-Roles": roles,
            "X-Tenant": tenant,
            "X-Auth-Signature": sig,
            "X-Auth-Timestamp": ts,
            "X-Auth-Nonce": nonce,
            "X-Content-SHA256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        },
        method="GET",
    )

    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert "templates" in data
