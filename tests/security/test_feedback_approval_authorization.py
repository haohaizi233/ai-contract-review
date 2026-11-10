# -*- coding: utf-8 -*-
"""Phase 16-S: 反馈接口、人工审批与 SFT 训练数据治理安全测试。

覆盖：
- Case 09: 客户端自报审批 (is_approved=true) 无法将样本批准为训练集（审批权属隔离）
- Case 09b: 客户端伪造 reviewer_id / tenant_id 提交反馈被服务端 403 明确拒绝
- Case 10: 跨租户 SFT 数据导出隔离与 Admin 角色授权阻断
"""

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from contract_kb.application.agents.knowledge_adapter import (
    FEEDBACK_BASE_DIR,
    record_user_feedback,
)
from contract_kb.interfaces.http.auth import compute_auth_signature_v2
from tools.contract_diff_web import DiffVisualizerHandler


@pytest.fixture(scope="module")
def web_feedback_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), DiffVisualizerHandler)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base_url = f"http://127.0.0.1:{port}"
    yield base_url
    server.shutdown()


def test_client_cannot_self_approve_training_data():
    """Case 09: 普通用户发送 is_approved=True / is_approved_for_training=True，绝不自动进入已审批训练集。"""
    unique_doc = f"doc_self_approve_{time.time_ns()}"
    sample_data = {
        "doc_id": unique_doc,
        "annotation_id": "ann-self-1",
        "human_decision": "MODIFIED",
        "human_modified_suggestion": "人工修改：严格按3‰执行",
        "tenant_id": "tenant_test_approval",
        "reviewer_id": "normal_user_bob",
        "is_approved": True,  # 攻击者尝试自提自批
        "is_approved_for_training": True,
    }

    # 通过受控 record_user_feedback 处理
    saved_path = record_user_feedback(sample_data, is_approved=False)
    assert saved_path is not None
    assert "raw_feedback" in str(saved_path)

    # 验证 reviewed_events.jsonl 中绝无此条自批记录
    rev_file = (
        FEEDBACK_BASE_DIR
        / "tenant_test_approval"
        / "reviewed_feedback"
        / "reviewed_events.jsonl"
    )
    if rev_file.exists():
        content = rev_file.read_text(encoding="utf-8")
        assert unique_doc not in content, "普通用户提交的反馈绝不得进入已审批训练集"


def test_forged_feedback_identity_rejected(web_feedback_server, monkeypatch):
    """Case 09b: 前端请求中的 tenant_id / reviewer_id 与 HMAC 认证主体不一致，必须 403 明确拒绝。"""
    shared_key = "secret_for_feedback_auth_test"
    monkeypatch.setenv("DIFF_WEB_MODE", "ENTERPRISE")
    monkeypatch.setenv("CONTRACT_KB_AUTH_SHARED_KEY", shared_key)

    user = "real_user_alice"
    roles = "reviewer"
    tenant = "corp_alpha"
    path = "/api/diff/feedback"
    query = ""
    ts = str(time.time())
    nonce = f"nonce_fb_{time.time_ns()}"

    # 请求正文自称是 victim_corp 租户，试图污染其他租户的数据
    malicious_body = json.dumps(
        {
            "doc_id": "doc-1",
            "annotation_id": "ann-1",
            "human_decision": "MODIFIED",
            "human_modified_suggestion": "伪造修改",
            "tenant_id": "victim_corp",  # 伪造租户
            "reviewer_id": "victim_reviewer",
        }
    ).encode("utf-8")
    b_hash = hashlib.sha256(malicious_body).hexdigest()

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
        f"{web_feedback_server}{path}",
        data=malicious_body,
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
    err_body = exc_info.value.read().decode("utf-8")
    assert "伪造" in err_body or "跨租户" in err_body


def test_sft_export_requires_admin_and_tenant_scoped(web_feedback_server, monkeypatch):
    """Case 10: SFT 导出需要 Admin 角色授权，且按当前已认证租户严格隔离，普通 Reviewer 被拒。"""
    shared_key = "secret_for_sft_export_test"
    monkeypatch.setenv("DIFF_WEB_MODE", "ENTERPRISE")
    monkeypatch.setenv("CONTRACT_KB_AUTH_SHARED_KEY", shared_key)

    path = "/api/diff/export_training_data"
    query = ""

    # 1. 普通 reviewer 试图导出 SFT 数据 -> 403 拦截
    ts_rev = str(time.time())
    nonce_rev = f"nonce_rev_{time.time_ns()}"
    sig_rev = compute_auth_signature_v2(
        key=shared_key,
        method="POST",
        path=path,
        query=query,
        user="normal_reviewer",
        roles="reviewer",  # 非 admin
        tenant="tenant_alpha",
        timestamp=ts_rev,
        nonce=nonce_rev,
        body_sha256=hashlib.sha256(b"{}").hexdigest(),
    )
    req_rev = urllib.request.Request(
        f"{web_feedback_server}{path}",
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "X-User": "normal_reviewer",
            "X-Roles": "reviewer",
            "X-Tenant": "tenant_alpha",
            "X-Auth-Signature": sig_rev,
            "X-Auth-Timestamp": ts_rev,
            "X-Auth-Nonce": nonce_rev,
            "X-Content-SHA256": hashlib.sha256(b"{}").hexdigest(),
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_rev)
    assert exc_info.value.code == 403

    # 2. Admin 角色请求 -> 200 成功，且限定为 tenant_alpha
    ts_admin = str(time.time())
    nonce_admin = f"nonce_admin_{time.time_ns()}"
    sig_admin = compute_auth_signature_v2(
        key=shared_key,
        method="POST",
        path=path,
        query=query,
        user="chief_admin",
        roles="admin",
        tenant="tenant_alpha",
        timestamp=ts_admin,
        nonce=nonce_admin,
        body_sha256=hashlib.sha256(b"{}").hexdigest(),
    )
    req_admin = urllib.request.Request(
        f"{web_feedback_server}{path}",
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "X-User": "chief_admin",
            "X-Roles": "admin",
            "X-Tenant": "tenant_alpha",
            "X-Auth-Signature": sig_admin,
            "X-Auth-Timestamp": ts_admin,
            "X-Auth-Nonce": nonce_admin,
            "X-Content-SHA256": hashlib.sha256(b"{}").hexdigest(),
        },
        method="POST",
    )
    with urllib.request.urlopen(req_admin) as resp:
        assert resp.status == 200
        res_data = json.loads(resp.read().decode("utf-8"))
        assert "train_count" in res_data
