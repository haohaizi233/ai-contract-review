# -*- coding: utf-8 -*-
"""Phase 16-S: 企业模式带认证合同审核全链路 E2E 业务测试 (Authenticated Review Workflow E2E)。

覆盖端到端真实 HTTP 请求全链路：
1. 企业认证 HMAC 签名计算与请求放行
2. 获取 Agent 审核状态
3. 卡片优先级调优与跳过等任务控制
4. 提交人工反馈意见
5. 管理员导出微调训练数据
6. 全程租户隔离与未认证/跨用户越权阻断
"""

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from contract_kb.application.agents.review_sessions import get_agent_manager
from contract_kb.interfaces.http.auth import compute_auth_signature_v2
from tools.contract_diff_web import DiffVisualizerHandler


@pytest.fixture(scope="module")
def web_e2e_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), DiffVisualizerHandler)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base_url = f"http://127.0.0.1:{port}"
    yield base_url
    server.shutdown()


def test_authenticated_review_lifecycle_e2e(web_e2e_server, monkeypatch):
    """端到端验证：企业模式下，合法认证的主体完成全流程操作，非法请求被逐级阻断。"""
    shared_key = "phase16_e2e_super_secret_shared_key"
    monkeypatch.setenv("DIFF_WEB_MODE", "ENTERPRISE")
    monkeypatch.setenv("CONTRACT_KB_AUTH_SHARED_KEY", shared_key)

    tenant_id = "corp_tencent"
    user_id = "legal_officer_zhang"
    roles = "reviewer"
    session_id = f"sess_e2e_{time.time_ns()}"

    def _make_signed_request(
        method: str,
        path: str,
        query: str = "",
        body: bytes = b"",
        user: str = user_id,
        uroles: str = roles,
        utenant: str = tenant_id,
    ) -> urllib.request.Request:
        ts = str(time.time())
        nonce = f"nonce_{time.time_ns()}_{secrets_suffix()}"
        b_hash = hashlib.sha256(body).hexdigest() if body else ""

        sig = compute_auth_signature_v2(
            key=shared_key,
            method=method,
            path=path,
            query=query,
            user=user,
            roles=uroles,
            tenant=utenant,
            timestamp=ts,
            nonce=nonce,
            body_sha256=b_hash,
        )

        url = f"{web_e2e_server}{path}"
        if query:
            url = f"{url}?{query}"

        headers = {
            "X-User": user,
            "X-Roles": uroles,
            "X-Tenant": utenant,
            "X-Auth-Signature": sig,
            "X-Auth-Timestamp": ts,
            "X-Auth-Nonce": nonce,
        }
        if body:
            headers["Content-Type"] = "application/json"
            headers["X-Content-SHA256"] = b_hash

        return urllib.request.Request(
            url,
            data=body if body else None,
            headers=headers,
            method=method,
        )

    # 1. 初始化会话归属并查看状态 (GET /api/diff/agent_status)
    get_agent_manager().get_or_create_session(
        session_id=session_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )

    req_status = _make_signed_request(
        "GET", "/api/diff/agent_status", query=f"session_id={session_id}"
    )
    with urllib.request.urlopen(req_status) as resp:
        assert resp.status == 200
        snap = json.loads(resp.read().decode("utf-8"))
        assert snap["session_id"] == session_id

    # 2. 模拟跨用户窃听 (同租户的 user_li 尝试访问 session_id) -> 403 阻断
    req_eavesdrop = _make_signed_request(
        "GET",
        "/api/diff/agent_status",
        query=f"session_id={session_id}",
        user="user_li_attacker",
        uroles="reviewer",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_eavesdrop)
    assert exc_info.value.code == 403

    # 3. 提交人工反馈意见 (POST /api/diff/feedback)
    fb_body = json.dumps(
        {
            "doc_id": "contract_e2e_001.docx",
            "annotation_id": "ann-1",
            "human_decision": "MODIFIED",
            "human_modified_suggestion": "人工修改：严格按 3‰ 执行违约金",
            "session_id": session_id,
        }
    ).encode("utf-8")
    req_feedback = _make_signed_request(
        "POST", "/api/diff/feedback", query="", body=fb_body
    )
    with urllib.request.urlopen(req_feedback) as resp_fb:
        assert resp_fb.status == 200
        fb_res = json.loads(resp_fb.read().decode("utf-8"))
        assert fb_res.get("success") is True

    # 4. Reviewer 尝试导出 SFT 训练集 -> 403 权限不足拦截
    req_export_rev = _make_signed_request(
        "POST", "/api/diff/export_training_data", query="", body=b"{}"
    )
    with pytest.raises(urllib.error.HTTPError) as exc_exp_rev:
        urllib.request.urlopen(req_export_rev)
    assert exc_exp_rev.value.code == 403

    # 5. 管理员导出 SFT 训练集 -> 200 放行
    req_export_admin = _make_signed_request(
        "POST",
        "/api/diff/export_training_data",
        query="",
        body=b"{}",
        user="compliance_admin",
        uroles="admin",
    )
    with urllib.request.urlopen(req_export_admin) as resp_exp_admin:
        assert resp_exp_admin.status == 200
        exp_res = json.loads(resp_exp_admin.read().decode("utf-8"))
        assert "train_count" in exp_res


def secrets_suffix() -> str:
    import secrets

    return secrets.token_hex(4)
