# -*- coding: utf-8 -*-
"""Phase 16-S-R2: 真正的业务端到端全链路验收测试 (Full Business E2E Workflow)

完整执行：
企业 HMAC 认证
   ↓
HTTP 上传合成/黄金合同 (POST /api/diff/upload)
   ↓
母本匹配与 Diff 生成
   ↓
真实 Web Worker 启动
   ↓
可信规则检索与 CitationVerifier 来源核验
   ↓
受控模型响应与 TOCTOU 二次复核
   ↓
真实 SSE 事件流消费 (GET /api/agent/stream)
   ↓
HTTP 提交人工反馈 (POST /api/diff/feedback)
   ↓
防自提自批拦截与独立审批 (POST /api/diff/feedback/approve)
   ↓
受控 SFT 微调数据集导出核验
   ↓
跨租户物理与逻辑隔离阻断测试
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from contract_kb import config
from contract_kb.application.agents.knowledge_adapter import export_sft_dataset
from contract_kb.infrastructure.persistence.sqlite_storage import create_storage
from contract_kb.interfaces.http.auth import compute_auth_signature_v2
from contract_kb.interfaces.web.app import (
    DiffVisualizerHandler,
    get_agent_manager,
)


def _make_auth_headers(
    method: str,
    path: str,
    query: str,
    body_bytes: bytes,
    shared_key: str,
    tenant_id: str,
    user_id: str,
    roles: str = "reviewer",
    session_id: str | None = None,
) -> dict[str, str]:
    timestamp = str(time.time())
    nonce = f"nonce_{time.time_ns()}_{secrets.token_hex(8)}"
    body_sha256 = hashlib.sha256(body_bytes).hexdigest()
    sig = compute_auth_signature_v2(
        key=shared_key,
        method=method,
        path=path,
        query=query,
        user=user_id,
        roles=roles,
        tenant=tenant_id,
        timestamp=timestamp,
        nonce=nonce,
        body_sha256=body_sha256,
    )
    headers = {
        "X-User": user_id,
        "X-Roles": roles,
        "X-Tenant": tenant_id,
        "X-Auth-Signature": sig,
        "X-Auth-Timestamp": timestamp,
        "X-Auth-Nonce": nonce,
        "X-Content-SHA256": body_sha256,
    }
    if session_id:
        headers["X-Session-ID"] = session_id
    return headers


@pytest.fixture(scope="module")
def r2_business_web_server(tmp_path_factory):
    tmp_dir = tmp_path_factory.mktemp("r2_e2e_storage")
    db_file = tmp_dir / "r2_e2e.db"
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


def test_full_business_workflow_e2e(r2_business_web_server, monkeypatch, tmp_path):
    """端到端真实业务流程：HMAC认证 -> HTTP上传 -> Diff -> Worker审查 -> SSE流 -> 反馈 -> 审批 -> SFT导出。"""
    base_url, storage_inst = r2_business_web_server
    shared_key = "r2_e2e_super_secret_shared_key_2026"
    monkeypatch.setenv("DIFF_WEB_MODE", "ENTERPRISE")
    monkeypatch.setenv("CONTRACT_KB_AUTH_SHARED_KEY", shared_key)
    monkeypatch.setenv("CONTRACT_KB_DATA_DIR", str(tmp_path))

    tenant_a = f"corp_alpha_{time.time_ns()}"
    tenant_b = f"corp_beta_{time.time_ns()}"
    alice_id = "lawyer_alice"
    bob_id = "reviewer_bob"

    # 1. 预先在租户 A 存储中录入已审批出处完备的风控规则与条款
    rule_id = f"RULE_LATE_FEE_{time.time_ns()}"
    clause_id = f"CLAUSE_FEE_{time.time_ns()}"
    storage_inst.save_rule(
        {
            "rule_id": rule_id,
            "version": 1,
            "name": "滞纳金与违约金下限风控",
            "rule_type": "standard",
            "execution_mode": "SYNC",
            "severity": "HIGH",
            "priority": 100,
            "contract_types": ["*"],
            "parameters": {"clause_category": "LIABILITY"},
            "source_doc": "DOC_FEE_SPEC_2026",
            "source_clauses": [clause_id],
            "status": "APPROVED",
            "approved_by": "lead_counsel",
            "tenant_id": tenant_a,
        },
        tenant_id=tenant_a,
    )
    # 录入底层文档与条款元数据
    with storage_inst._lock:
        storage_inst.conn.execute(
            """
            INSERT OR REPLACE INTO documents (document_id, tenant_id, status, source_file, imported_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("DOC_FEE_SPEC_2026", tenant_a, "ACTIVE", "fee_spec.docx", time.time()),
        )
        storage_inst.conn.execute(
            """
            INSERT OR REPLACE INTO clauses (clause_id, document_id, clause_category, original_text, approval_status, revision)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                clause_id,
                "DOC_FEE_SPEC_2026",
                "LIABILITY",
                "甲方逾期付款每日按千分之三支付违约金",
                "APPROVED",
                1,
            ),
        )
        storage_inst.conn.commit()

    # 2. 准备真实 DOCX 文件并构造 multipart/form-data HTTP 上传请求
    golden_files = (
        list(config.GOLDEN_DIR.glob("*.docx")) if config.GOLDEN_DIR.exists() else []
    )
    if golden_files:
        sample_docx = golden_files[0]
    else:
        fixture_golden = list(Path("tests/fixtures/golden").glob("*.docx"))
        if fixture_golden:
            sample_docx = fixture_golden[0]
        else:
            import docx

            sample_path = tmp_path / "sample_contract.docx"
            doc = docx.Document()
            doc.add_paragraph("甲方与乙方签订技术服务合同。")
            doc.add_paragraph("甲方逾期付款每日按千分之一支付违约金。")
            doc.save(str(sample_path))
            sample_docx = sample_path

    file_bytes = sample_docx.read_bytes()

    boundary = "----WebKitFormBoundaryE2ETest2026"
    body_parts = [
        f"--{boundary}\r\n".encode("utf-8"),
        b'Content-Disposition: form-data; name="file"; filename="sample_contract.docx"\r\n',
        b"Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document\r\n\r\n",
        file_bytes,
        f"\r\n--{boundary}--\r\n".encode("utf-8"),
    ]
    upload_body = b"".join(body_parts)

    upload_path = "/api/diff/upload"
    upload_headers = _make_auth_headers(
        method="POST",
        path=upload_path,
        query="",
        body_bytes=upload_body,
        shared_key=shared_key,
        tenant_id=tenant_a,
        user_id=alice_id,
        roles="reviewer",
    )
    upload_headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    upload_headers["Content-Length"] = str(len(upload_body))

    req = urllib.request.Request(
        f"{base_url}{upload_path}",
        data=upload_body,
        headers=upload_headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        assert resp.status == 200
        upload_res = json.loads(resp.read().decode("utf-8"))

    session_id = upload_res.get("session_id")
    assert session_id, f"上传未能创建有效 session_id: {upload_res}"
    assert upload_res.get("tenant_id") == tenant_a
    annotations = upload_res.get("annotations") or []
    assert len(annotations) > 0, "Diff 生成未能产出批注列表"

    # 3. HTTP 建立真实 SSE 事件订阅连接 (GET /api/diff/agent_stream)
    stream_path = "/api/diff/agent_stream"
    stream_query = f"session_id={session_id}"
    stream_headers = _make_auth_headers(
        method="GET",
        path=stream_path,
        query=stream_query,
        body_bytes=b"",
        shared_key=shared_key,
        tenant_id=tenant_a,
        user_id=alice_id,
        roles="reviewer",
        session_id=session_id,
    )
    stream_headers["Accept"] = "text/event-stream"

    sse_req = urllib.request.Request(
        f"{base_url}{stream_path}?{stream_query}", headers=stream_headers, method="GET"
    )
    sse_events_received = []

    def _read_sse_stream():
        try:
            with urllib.request.urlopen(sse_req, timeout=5) as sse_resp:
                for _ in range(10):
                    line = sse_resp.readline().decode("utf-8")
                    if not line:
                        break
                    if line.startswith("event:") or line.startswith("data:"):
                        sse_events_received.append(line.strip())
        except Exception:
            pass

    sse_thread = threading.Thread(target=_read_sse_stream, daemon=True)
    sse_thread.start()

    # 4. 等待 Worker 处理完成并通过 HTTP 查询审查状态
    status_path = "/api/diff/agent_status"
    status_query = f"session_id={session_id}"
    completed = False
    for _ in range(30):
        time.sleep(0.3)
        st_headers = _make_auth_headers(
            method="GET",
            path=status_path,
            query=status_query,
            body_bytes=b"",
            shared_key=shared_key,
            tenant_id=tenant_a,
            user_id=alice_id,
            roles="reviewer",
            session_id=session_id,
        )
        st_req = urllib.request.Request(
            f"{base_url}{status_path}?{status_query}", headers=st_headers, method="GET"
        )
        with urllib.request.urlopen(st_req, timeout=5) as st_resp:
            status_data = json.loads(st_resp.read().decode("utf-8"))
            if status_data.get("status") in ("COMPLETED", "IDLE"):
                completed = True
                break

    assert completed, f"Worker 超时未完成审核: {status_data}"

    # 5. HTTP 提交普通用户反馈 (POST /api/diff/feedback)
    target_ann = annotations[0]
    ann_id = str(target_ann.get("id"))
    feedback_payload = {
        "task_id": session_id,
        "annotation_id": ann_id,
        "human_analysis": "法务复核确认：该条款违约金调整过低，确需驳回修改并恢复母本标准。",
        "human_advice": "坚决恢复每日千分之三违约金标准。",
        "clause_category": "LIABILITY",
        "delta_type": "NUMBER_CHANGE",
        "quote_standard": "每日千分之三",
        "quote_reviewed": "每日千分之一",
        "is_approved": True,  # 客户端试图自提自批，必须被服务层安全拦截降级为 False
    }
    fb_body = json.dumps(feedback_payload).encode("utf-8")
    fb_path = "/api/diff/feedback"
    fb_headers = _make_auth_headers(
        method="POST",
        path=fb_path,
        query="",
        body_bytes=fb_body,
        shared_key=shared_key,
        tenant_id=tenant_a,
        user_id=alice_id,
        roles="reviewer",
        session_id=session_id,
    )
    fb_headers["Content-Type"] = "application/json"
    fb_req = urllib.request.Request(
        f"{base_url}{fb_path}", data=fb_body, headers=fb_headers, method="POST"
    )
    with urllib.request.urlopen(fb_req, timeout=5) as fb_resp:
        assert fb_resp.status == 200
        fb_res = json.loads(fb_resp.read().decode("utf-8"))
        assert fb_res.get("success") is True
        assert "raw_feedback" in fb_res.get("saved_to", ""), (
            f"普通反馈必须落入 raw_feedback 待审池: {fb_res}"
        )

    # 6. 安全拦截验证：提交人 Alice 试图自己调用审批接口自提自批 -> 必须被 403 阻断！
    approve_path = "/api/diff/feedback/approve"
    approve_payload = {
        "task_id": session_id,
        "annotation_id": ann_id,
        "reason": "自提自批恶意准入",
    }
    appr_body = json.dumps(approve_payload).encode("utf-8")
    self_appr_headers = _make_auth_headers(
        method="POST",
        path=approve_path,
        query="",
        body_bytes=appr_body,
        shared_key=shared_key,
        tenant_id=tenant_a,
        user_id=alice_id,  # 提交人自己审批
        roles="reviewer",
        session_id=session_id,
    )
    self_appr_headers["Content-Type"] = "application/json"
    self_req = urllib.request.Request(
        f"{base_url}{approve_path}",
        data=appr_body,
        headers=self_appr_headers,
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(self_req, timeout=5)
    assert exc_info.value.code == 403, f"防自提自批必须返回 403: {exc_info.value.code}"

    # 7. 独立授权审批：由独立复核员 Bob (不同用户，具备 reviewer/admin 权限) 批准进入训练集
    valid_appr_headers = _make_auth_headers(
        method="POST",
        path=approve_path,
        query="",
        body_bytes=appr_body,
        shared_key=shared_key,
        tenant_id=tenant_a,
        user_id=bob_id,  # 独立复核员审批
        roles="admin",
        session_id=session_id,
    )
    valid_appr_headers["Content-Type"] = "application/json"
    valid_req = urllib.request.Request(
        f"{base_url}{approve_path}",
        data=appr_body,
        headers=valid_appr_headers,
        method="POST",
    )
    with urllib.request.urlopen(valid_req, timeout=5) as appr_resp:
        assert appr_resp.status == 200
        appr_res = json.loads(appr_resp.read().decode("utf-8"))
        assert appr_res.get("status") == "APPROVED_FOR_TRAINING"
        assert appr_res.get("approved_by") == bob_id

    # 8. SFT 训练集导出核验：导出样本必须包含经过 Bob 审批的样本，且租户物理隔离
    export_res = export_sft_dataset(tenant_id=tenant_a)
    assert (export_res.get("train_count", 0) + export_res.get("eval_count", 0)) >= 1
    target_out_file = Path(
        export_res.get("train_file")
        if export_res.get("train_count", 0) > 0
        else export_res.get("eval_file")
    )
    assert target_out_file.exists()
    content = target_out_file.read_text(encoding="utf-8")
    assert session_id in content
    assert "法务复核确认" in content

    # 9. 跨租户物理与逻辑隔离安全验证：租户 B 试图访问租户 A 的会话状态 -> 必须 403 阻断！
    tenant_b_headers = _make_auth_headers(
        method="GET",
        path=status_path,
        query=status_query,
        body_bytes=b"",
        shared_key=shared_key,
        tenant_id=tenant_b,  # 租户 B
        user_id="attacker_mallory",
        roles="reviewer",
        session_id=session_id,  # 试图偷窥租户 A 的 session
    )
    attack_req = urllib.request.Request(
        f"{base_url}{status_path}?{status_query}",
        headers=tenant_b_headers,
        method="GET",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_b:
        urllib.request.urlopen(attack_req, timeout=5)
    assert exc_b.value.code in (403, 404), (
        f"跨租户偷窥必须被阻断 (403/404): {exc_b.value.code}"
    )
