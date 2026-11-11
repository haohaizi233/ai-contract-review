# -*- coding: utf-8 -*-
"""Phase 16-S 缺陷复现测试集合（Defect Reproduction Test Suite）。

在实施任何生产代码修复前，必须先执行本测试集，以拿到修复前真实失败证据。
涵盖缺陷：
- P0-01: 企业模式下未配置共享密钥跳过 HMAC 验签
- P0-02: Web HMAC 未验证时间戳时效性、Nonce 防重放和请求正文 SHA-256 完整性
- P0-03: Agent Session 与 SSE 缺少用户所有权校验（同租户跨用户越权与 anonymous 绕过）
- P0-04: 法务 Agent 静态规则兜底绕过数据库撤销状态
- P1-01: 用户反馈接口身份字段被伪造且客户端自批准训练样本
- P1-02: SFT 导出接口未认证且未校验角色
- P1-03: 生产环境下 Checkpoint 仍使用公开固定密钥
- P1-04: 企业模式未认证 GET / 泄露活动合同 Diff 数据
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock

import pytest

from contract_kb.application.agents.knowledge_adapter import (
    FEEDBACK_BASE_DIR,
    retrieve_governed_rules,
)
from contract_kb.application.agents.review_sessions import AgentReviewManager
from contract_kb.infrastructure.checkpoints.storage import (
    CheckpointStorage,
)
from contract_kb.domain.models.review_identity import ReviewExecutionFingerprint
from contract_kb.interfaces.http.auth import compute_auth_signature_v2
from tools.contract_diff_web import DiffVisualizerHandler


@pytest.fixture(scope="module")
def web_repro_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), DiffVisualizerHandler)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base_url = f"http://127.0.0.1:{port}"
    yield base_url
    server.shutdown()


def test_enterprise_mode_without_secret_must_reject(web_repro_server, monkeypatch):
    """P0-01: 企业模式下未配置共享密钥时必须拒绝请求，严禁因 key 为空而跳过签名校验放行。"""
    monkeypatch.setenv("DIFF_WEB_MODE", "ENTERPRISE")
    monkeypatch.delenv("CONTRACT_KB_AUTH_SHARED_KEY", raising=False)

    # 携带伪造头（key 未配置时，原代码 if key: 为假，直接放行）
    headers = {
        "X-User": "attacker",
        "X-Roles": "admin",
        "X-Tenant": "tenant_x",
        "X-Auth-Signature": "forged_signature_123456",
        "X-Auth-Timestamp": str(time.time()),
        "X-Auth-Nonce": "nonce_1234567890abcdef",
    }
    req = urllib.request.Request(
        f"{web_repro_server}/api/diff/agent_status?session_id=sess_ent_no_key",
        headers=headers,
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)

    # 期望被阻断拒绝（401 或 403 或 500 Fail-Closed），而不是返回 200 放行
    assert exc_info.value.code in (401, 403, 500, 503)


def test_expired_auth_timestamp_must_reject(web_repro_server, monkeypatch):
    """P0-02a: 过期时间戳必须被拦截拒绝 (401/403)。"""
    shared_key = "test_phase16_secret_key_repro"
    monkeypatch.setenv("DIFF_WEB_MODE", "ENTERPRISE")
    monkeypatch.setenv("CONTRACT_KB_AUTH_SHARED_KEY", shared_key)

    user = "reviewer_user"
    roles = "reviewer"
    tenant = "corp_tenant"
    nonce = "1234567890abcdef1234"
    # 2023 年历史过期时间戳
    ts = "1700000000.0"
    path = "/api/diff/agent_status"
    query = "session_id=sess_ts_test"

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
        f"{web_repro_server}{path}?{query}",
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
        urllib.request.urlopen(req)

    assert exc_info.value.code in (401, 403)


def test_replayed_nonce_must_reject(web_repro_server, monkeypatch):
    """P0-02b: 重放 Nonce 必须被拦截拒绝 (401/403)。"""
    shared_key = "test_phase16_secret_key_repro"
    monkeypatch.setenv("DIFF_WEB_MODE", "ENTERPRISE")
    monkeypatch.setenv("CONTRACT_KB_AUTH_SHARED_KEY", shared_key)

    user = "reviewer_user"
    roles = "reviewer"
    tenant = "corp_tenant"
    nonce = "unique_nonce_phase16_test_01"
    ts = str(time.time())
    path = "/api/diff/agent_status"
    query = "session_id=sess_nonce_test"

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
    headers = {
        "X-User": user,
        "X-Roles": roles,
        "X-Tenant": tenant,
        "X-Auth-Signature": sig,
        "X-Auth-Timestamp": ts,
        "X-Auth-Nonce": nonce,
    }

    # 首次请求
    req1 = urllib.request.Request(f"{web_repro_server}{path}?{query}", headers=headers)
    with urllib.request.urlopen(req1) as resp:
        assert resp.status == 200

    # 重放请求（相同 Nonce）：期望阻断
    req2 = urllib.request.Request(f"{web_repro_server}{path}?{query}", headers=headers)
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req2)

    assert exc_info.value.code in (401, 403)


def test_request_body_hash_mismatch_must_reject(web_repro_server, monkeypatch):
    """P0-02c: 篡改请求正文但伪造或未更新 X-Content-SHA256 必须被拒绝。"""
    shared_key = "test_phase16_secret_key_repro"
    monkeypatch.setenv("DIFF_WEB_MODE", "ENTERPRISE")
    monkeypatch.setenv("CONTRACT_KB_AUTH_SHARED_KEY", shared_key)

    user = "reviewer_user"
    roles = "reviewer"
    tenant = "corp_tenant"
    nonce = "nonce_body_test_12345678"
    ts = str(time.time())
    path = "/api/diff/prioritize_card"
    query = "session_id=sess_body_test"

    # 正文被篡改：签名使用的是正文 A 的 SHA256，实际发送的是正文 B
    signed_body = b'{"id": "ann-orig"}'
    actual_body = b'{"id": "ann-tampered-attack"}'
    signed_body_sha256 = hashlib.sha256(signed_body).hexdigest()

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
        body_sha256=signed_body_sha256,
    )

    headers = {
        "Content-Type": "application/json",
        "X-User": user,
        "X-Roles": roles,
        "X-Tenant": tenant,
        "X-Auth-Signature": sig,
        "X-Auth-Timestamp": ts,
        "X-Auth-Nonce": nonce,
        "X-Content-SHA256": signed_body_sha256,
    }
    req = urllib.request.Request(
        f"{web_repro_server}{path}?{query}",
        data=actual_body,
        headers=headers,
        method="POST",
    )

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)

    assert exc_info.value.code in (401, 403)


def test_cross_user_sse_subscription_must_reject():
    """P0-03: 同租户跨用户越权订阅事件流与匿名绕过必须被阻断。"""
    mgr = AgentReviewManager()

    # 用户 Alice 创建会话
    sess_alice = mgr.get_or_create_session(
        session_id="sess_alice_001",
        tenant_id="tenant_alpha",
        user_id="user_alice",
    )
    assert sess_alice.user_id == "user_alice"

    # 1. 用户 Bob 试图通过 get_or_create_session 或 subscribe_events 订阅 Alice 的会话
    with pytest.raises(PermissionError) as exc_info:
        mgr.subscribe_events(
            session_id="sess_alice_001",
            tenant_id="tenant_alpha",
            user_id="user_bob",
        )
    assert "跨用户越权阻断" in str(exc_info.value)

    # 2. 试图传入 user_id="anonymous" 绕过所有权检查
    with pytest.raises(PermissionError) as exc_info2:
        mgr.get_session(
            session_id="sess_alice_001",
            tenant_id="tenant_alpha",
            user_id="anonymous",
        )
    assert "跨用户越权阻断" in str(exc_info2.value)


def test_revoked_database_rule_must_not_reappear_from_static_fallback():
    """P0-04: 数据库已撤销 (REVOKED) 的规则不得回退到静态字典并恢复为 APPROVED 权威规则。"""
    mock_storage = MagicMock()
    # 模拟数据库中该类别所有规则均已被撤销，因此 APPROVED 规则列表为空
    mock_storage.get_rules.return_value = []

    # 检索争端解决管辖规则
    rules = retrieve_governed_rules(
        category="DISPUTE_RESOLUTION",
        delta_type="PARTY_CHANGE",
        tenant_id="tenant_alpha",
        storage=mock_storage,
        allow_unapproved=False,
    )

    # 期望：数据库明确表示没有生效规则时，严禁静默回退到代码中的静态 APPROVED 规则
    assert len(rules) == 0, (
        f"期望 Fail-Closed 返回空规则列表，实际回退并返回了静态字典: {[r.rule_id for r in rules]}"
    )


def test_client_cannot_self_approve_training_sample(web_repro_server, monkeypatch):
    """P1-01: 客户端普通反馈请求不得自报 is_approved 并将样本直接写入已审批训练集。"""
    shared_key = "test_phase16_secret_key_repro"
    monkeypatch.setenv("DIFF_WEB_MODE", "ENTERPRISE")
    monkeypatch.setenv("CONTRACT_KB_AUTH_SHARED_KEY", shared_key)

    user = "normal_reviewer"
    roles = "reviewer"
    tenant = "corp_tenant"
    nonce = "nonce_fb_test_12345678"
    ts = str(time.time())
    path = "/api/diff/feedback"
    query = "session_id=sess_feedback_test"

    # 1. 客户端试图伪造 tenant_id，必须被 403 明确阻断
    forged_body = {
        "id": "ann-1",
        "tenant_id": "forged_victim_tenant",
        "reviewer_id": "forged_chief_legal_officer",
        "is_approved": True,
        "human_analysis": "伪造的人工法律分析",
    }
    raw_forged = json.dumps(forged_body).encode("utf-8")
    sha_forged = hashlib.sha256(raw_forged).hexdigest()
    sig_forged = compute_auth_signature_v2(
        key=shared_key,
        method="POST",
        path=path,
        query=query,
        user=user,
        roles=roles,
        tenant=tenant,
        timestamp=ts,
        nonce=nonce,
        body_sha256=sha_forged,
    )
    req_forged = urllib.request.Request(
        f"{web_repro_server}{path}?{query}",
        data=raw_forged,
        headers={
            "Content-Type": "application/json",
            "X-User": user,
            "X-Roles": roles,
            "X-Tenant": tenant,
            "X-Auth-Signature": sig_forged,
            "X-Auth-Timestamp": ts,
            "X-Auth-Nonce": nonce,
            "X-Content-SHA256": sha_forged,
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_forged)
    assert exc_info.value.code == 403

    # 2. 正常反馈请求即使传入 is_approved=True，服务端也坚决不得将其标记为训练已审批
    nonce2 = "nonce_fb_test_87654321"
    ts2 = str(time.time())
    legit_body = {
        "id": "ann-1",
        "is_approved": True,
        "is_approved_for_training": True,
        "human_analysis": "合法的法务意见修改",
    }
    raw_legit = json.dumps(legit_body).encode("utf-8")
    sha_legit = hashlib.sha256(raw_legit).hexdigest()
    sig_legit = compute_auth_signature_v2(
        key=shared_key,
        method="POST",
        path=path,
        query=query,
        user=user,
        roles=roles,
        tenant=tenant,
        timestamp=ts2,
        nonce=nonce2,
        body_sha256=sha_legit,
    )
    req_legit = urllib.request.Request(
        f"{web_repro_server}{path}?{query}",
        data=raw_legit,
        headers={
            "Content-Type": "application/json",
            "X-User": user,
            "X-Roles": roles,
            "X-Tenant": tenant,
            "X-Auth-Signature": sig_legit,
            "X-Auth-Timestamp": ts2,
            "X-Auth-Nonce": nonce2,
            "X-Content-SHA256": sha_legit,
        },
        method="POST",
    )

    reviewed_file = (
        FEEDBACK_BASE_DIR / tenant / "reviewed_feedback" / "reviewed_events.jsonl"
    )
    old_count = 0
    if reviewed_file.exists():
        old_count = len(reviewed_file.read_text(encoding="utf-8").splitlines())

    with urllib.request.urlopen(req_legit) as resp:
        assert resp.status == 200

    new_count = 0
    if reviewed_file.exists():
        new_count = len(reviewed_file.read_text(encoding="utf-8").splitlines())

    assert new_count == old_count, (
        "普通反馈接口直接将客户端自称 is_approved=True 的样本持久化到了已审批训练集！"
    )


def test_training_export_requires_authorized_role(web_repro_server, monkeypatch):
    """P1-02: SFT 数据导出接口必须经过鉴权与 Admin/训练审批人角色校验。"""
    shared_key = "test_phase16_secret_key_repro"
    monkeypatch.setenv("DIFF_WEB_MODE", "ENTERPRISE")
    monkeypatch.setenv("CONTRACT_KB_AUTH_SHARED_KEY", shared_key)

    # 1. 匿名未认证调用必须拒绝 (401/403)
    req_anon = urllib.request.Request(
        f"{web_repro_server}/api/diff/export_training_data",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_anon)
    assert exc_info.value.code in (401, 403)

    # 2. 普通 Reviewer 角色调用必须被 403 权限不足拦截
    user = "reviewer_user"
    roles = "reviewer"
    tenant = "corp_tenant"
    nonce = "nonce_export_perm_001"
    ts = str(time.time())
    path = "/api/diff/export_training_data"
    query = ""
    raw_body = b"{}"
    body_sha = hashlib.sha256(raw_body).hexdigest()

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
        body_sha256=body_sha,
    )
    req_reviewer = urllib.request.Request(
        f"{web_repro_server}{path}",
        data=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-User": user,
            "X-Roles": roles,
            "X-Tenant": tenant,
            "X-Auth-Signature": sig,
            "X-Auth-Timestamp": ts,
            "X-Auth-Nonce": nonce,
            "X-Content-SHA256": body_sha,
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info2:
        urllib.request.urlopen(req_reviewer)
    assert exc_info2.value.code == 403

    # 3. 授权 Admin 角色成功放行
    admin_nonce = "nonce_export_perm_002"
    admin_sig = compute_auth_signature_v2(
        key=shared_key,
        method="POST",
        path=path,
        query=query,
        user="admin_user",
        roles="admin",
        tenant=tenant,
        timestamp=ts,
        nonce=admin_nonce,
        body_sha256=body_sha,
    )
    req_admin = urllib.request.Request(
        f"{web_repro_server}{path}",
        data=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-User": "admin_user",
            "X-Roles": "admin",
            "X-Tenant": tenant,
            "X-Auth-Signature": admin_sig,
            "X-Auth-Timestamp": ts,
            "X-Auth-Nonce": admin_nonce,
            "X-Content-SHA256": body_sha,
        },
        method="POST",
    )
    with urllib.request.urlopen(req_admin) as resp:
        assert resp.status == 200


def test_production_checkpoint_requires_secret(monkeypatch):
    """P1-03: 生产环境下缺少密钥必须阻断 Checkpoint 持久化/恢复，禁止使用公开固定默认密钥。"""
    monkeypatch.setenv("CONTRACT_KB_ENV", "production")
    monkeypatch.delenv("CONTRACT_KB_CHECKPOINT_KEY", raising=False)

    ckpt_storage = CheckpointStorage()
    fp = ReviewExecutionFingerprint(
        tenant_id="prod_tenant",
        user_id="prod_user",
        file_sha256="a" * 64,
        template_id="tpl_01",
        template_content_hash="b" * 64,
        template_version="v1",
        rule_set_version="v1",
        kb_snapshot_version="v1",
        agent_prompt_version="v1",
        model_config_version="v1",
        diff_engine_version="v1",
        annotations_fingerprint="c" * 64,
    )

    # 生产模式下未配置有效独立密钥时，严禁使用公开默认密钥执行恢复或保存
    with pytest.raises(RuntimeError) as exc_info:
        ckpt_storage.save_checkpoint(fp, [])

    assert "生产环境未配置" in str(exc_info.value) or "密钥" in str(exc_info.value)


def test_unauthenticated_root_page_does_not_leak_active_contract(
    web_repro_server, monkeypatch
):
    """P1-04: 企业模式下未认证访问 GET / 不得泄露全局活动合同数据。"""
    monkeypatch.setenv("DIFF_WEB_MODE", "ENTERPRISE")

    req = urllib.request.Request(f"{web_repro_server}/")
    with urllib.request.urlopen(req) as resp:
        html = resp.read().decode("utf-8")
        # 企业模式下未提供认证会话，初始数据占位必须为 null，严禁内嵌活动合同 Diff
        assert "window.INITIAL_DATA = null;" in html
        assert "mut_11" not in html
        assert '"clauses": [' not in html
