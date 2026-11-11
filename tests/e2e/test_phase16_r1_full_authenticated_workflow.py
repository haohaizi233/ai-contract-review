# -*- coding: utf-8 -*-
"""Phase 16-S-R1: 端到端受控法务审阅全链路 (Full Authenticated Workflow) E2E 验收测试集。"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from contract_kb.infrastructure.persistence.sqlite_storage import create_storage
from contract_kb.interfaces.http.auth import compute_auth_signature_v2
from contract_kb.interfaces.web.app import (
    DiffVisualizerHandler,
    get_agent_manager,
)


@pytest.fixture(scope="module")
def e2e_web_server(tmp_path_factory):
    tmp_dir = tmp_path_factory.mktemp("e2e_storage")
    db_file = tmp_dir / "e2e_app.db"
    storage_inst = create_storage(db_path=db_file)

    DiffVisualizerHandler.set_storage_provider(storage_inst)
    get_agent_manager().set_storage_provider(storage_inst)

    server = ThreadingHTTPServer(("127.0.0.1", 0), DiffVisualizerHandler)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base_url = f"http://127.0.0.1:{port}"
    yield base_url, storage_inst
    server.shutdown()


def test_full_authenticated_review_workflow(e2e_web_server, monkeypatch):
    """E2E 全链路核验：真实认证请求 -> 存储注入 -> Worker 审查 -> 证据核验 -> 状态查询。"""
    base_url, storage_inst = e2e_web_server
    shared_key = "secret_for_e2e_workflow_test"
    monkeypatch.setenv("DIFF_WEB_MODE", "ENTERPRISE")
    monkeypatch.setenv("CONTRACT_KB_AUTH_SHARED_KEY", shared_key)

    tenant_id = f"tenant_e2e_{time.time_ns()}"
    user_id = "counsel_eva"
    roles = "reviewer"
    session_id = f"sess_e2e_{time.time_ns()}"

    # 1. 预先在租户库中录入已审批出处完备的风控规则
    rule_id = f"RULE_E2E_{time.time_ns()}"
    storage_inst.save_rule(
        {
            "rule_id": rule_id,
            "version": 1,
            "name": "E2E违约责任标准红线",
            "rule_type": "standard",
            "execution_mode": "SYNC",
            "severity": "HIGH",
            "priority": 100,
            "contract_types": ["*"],
            "parameters": {"clause_category": "LIABILITY"},
            "source_doc": "E2E指引手册",
            "source_clauses": [{"document_id": "doc_e2e", "clause_id": "c1"}],
            "status": "APPROVED",
            "approved_by": "chief_counsel",
            "tenant_id": tenant_id,
        },
        tenant_id=tenant_id,
    )

    # 2. 模拟发起审查任务 (直接通过 AgentReviewManager 或 HTTP 接口)
    mgr = get_agent_manager()
    diff_data = {
        "annotations": [
            {
                "id": "ann-e2e-01",
                "clause_category": "LIABILITY",
                "delta_type": "TEXT_DELTA",
                "standard_span": "迟延违约金每日千分之三",
                "reviewed_span": "迟延履行无需支付违约金",
                "clause_title": "违约赔偿责任",
            }
        ]
    }
    session = mgr.start_document_review(
        diff_data=diff_data,
        session_id=session_id,
        tenant_id=tenant_id,
        user_id=user_id,
        storage=storage_inst,
    )
    assert session.get_storage() is storage_inst

    # 3. 通过受控 Web API 查询审查状态 (/api/diff/agent_status)
    path = "/api/diff/agent_status"
    query = f"session_id={session_id}"
    b_hash = hashlib.sha256(b"").hexdigest()

    # 等待 Worker 审查完成并在状态接口中体现
    max_wait = 10.0
    start_t = time.time()
    final_status = None
    while time.time() - start_t < max_wait:
        # 为避免重放拦截，每次轮询生成新 nonce/ts/sig
        poll_ts = str(time.time())
        poll_nonce = f"nonce_poll_{time.time_ns()}"
        poll_sig = compute_auth_signature_v2(
            key=shared_key,
            method="GET",
            path=path,
            query=query,
            user=user_id,
            roles=roles,
            tenant=tenant_id,
            timestamp=poll_ts,
            nonce=poll_nonce,
            body_sha256=b_hash,
        )
        poll_req = urllib.request.Request(
            f"{base_url}{path}?{query}",
            headers={
                "X-User": user_id,
                "X-Roles": roles,
                "X-Tenant": tenant_id,
                "X-Session-ID": session_id,
                "X-Auth-Signature": poll_sig,
                "X-Auth-Timestamp": poll_ts,
                "X-Auth-Nonce": poll_nonce,
                "X-Content-SHA256": b_hash,
            },
            method="GET",
        )
        with urllib.request.urlopen(poll_req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("completed", 0) >= 1:
                final_status = data
                break
        time.sleep(0.3)

    assert final_status is not None, "E2E 审查未能在超时时间内完成"
    assert final_status.get("session_id") == session_id
    assert final_status.get("completed") >= 1
    anns = final_status.get("annotations", [])
    assert len(anns) >= 1
    ann = anns[0]
    assert ann.get("_aiDone") is True
    assert "ai_analysis" in ann
    assert "evidence_references" in ann
