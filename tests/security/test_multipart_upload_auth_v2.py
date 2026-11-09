# -*- coding: utf-8 -*-
"""回归测试：multipart 上传接口的 Auth V2 签名必须绑定实际请求正文。"""

from __future__ import annotations

import hashlib
import io
import time
from pathlib import Path
from unittest.mock import patch
import docx
from fastapi.testclient import TestClient

import contract_kb.api as api
from contract_kb.auth import TrustedHeaderAuth, compute_auth_signature_v2
from contract_kb.storage import Storage

SHARED_KEY = "test-multipart-auth-v2-secret-key"


def _create_synthetic_docx_bytes(
    text: str = "合同正文：甲方应于五个工作日内付款。",
) -> bytes:
    doc = docx.Document()
    doc.add_heading("合成技术服务合同", level=1)
    doc.add_paragraph("第一条 支付条款")
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _build_multipart_payload(
    file_bytes: bytes,
    filename: str = "synthetic.docx",
    mode: str = "STANDARD",
    custom: str = "",
    boundary: str = "----WebKitFormBoundary7MA4YWxkTrZu0gW",
) -> tuple[bytes, str]:
    """构建确定性的 multipart/form-data 请求正文及 Content-Type。"""
    crlf = b"\r\n"
    parts = []

    # file part
    parts.append(f"--{boundary}".encode())
    parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"'.encode()
    )
    parts.append(
        b"Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    parts.append(b"")
    parts.append(file_bytes)

    # mode part
    if mode:
        parts.append(f"--{boundary}".encode())
        parts.append(b'Content-Disposition: form-data; name="mode"')
        parts.append(b"")
        parts.append(mode.encode("utf-8"))

    # custom part
    if custom:
        parts.append(f"--{boundary}".encode())
        parts.append(b'Content-Disposition: form-data; name="custom"')
        parts.append(b"")
        parts.append(custom.encode("utf-8"))

    parts.append(f"--{boundary}--".encode())
    parts.append(b"")
    body = crlf.join(parts)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def _make_v2_headers(
    method: str,
    path: str,
    nonce: str,
    body: bytes,
    key: str = SHARED_KEY,
    user: str = "reviewer_alice",
    roles: str = "reviewer",
    tenant: str = "test_tenant",
    override_sha: str | None = None,
) -> dict[str, str]:
    ts = str(time.time())
    real_sha = hashlib.sha256(body).hexdigest()
    header_sha = override_sha if override_sha is not None else real_sha
    sig = compute_auth_signature_v2(
        key=key,
        method=method,
        path=path,
        user=user,
        roles=roles,
        tenant=tenant,
        timestamp=ts,
        nonce=nonce,
        body_sha256=header_sha,
    )
    return {
        "X-User": user,
        "X-Roles": roles,
        "X-Tenant": tenant,
        "X-Auth-Version": "2",
        "X-Auth-Timestamp": ts,
        "X-Auth-Nonce": nonce,
        "X-Content-SHA256": header_sha,
        "X-Auth-Signature": sig,
    }


def test_multipart_upload_with_actual_body_signature_succeeds(tmp_path: Path):
    """验证：使用实际 multipart 正文计算 SHA256 与 V2 签名时，上传接口应成功 200。"""
    st = Storage(tmp_path / "multipart_test.db")
    docx_bytes = _create_synthetic_docx_bytes("甲方向乙方支付十万元整。")
    body, ctype = _build_multipart_payload(docx_bytes, mode="STANDARD")

    path = "/api/v1/contract-review/tasks"
    nonce = "nonce_multipart_valid_00000001"
    headers = _make_v2_headers("POST", path, nonce, body)
    headers["Content-Type"] = ctype

    with (
        patch.object(api, "get_storage", return_value=st),
        patch.object(
            api,
            "_auth",
            TrustedHeaderAuth(shared_key=SHARED_KEY, storage=st, auth_version="2"),
        ),
    ):
        client = TestClient(api.app)
        resp = client.post(path, headers=headers, content=body)
        assert resp.status_code == 200, (
            f"合法签名正文上传失败: {resp.status_code}, {resp.text}"
        )
        data = resp.json()
        assert "task_id" in data
    st.close()


def test_multipart_upload_with_empty_body_signature_rejected(tmp_path: Path):
    """
    验证：非空 multipart 上传请求如果使用空正文摘要 (EMPTY_BODY_SHA256) 计算签名，
    服务端必须 401 阻断，禁止空正文签名绕过。
    """
    st = Storage(tmp_path / "multipart_test2.db")
    docx_bytes = _create_synthetic_docx_bytes("重要保密合同条款。")
    body, ctype = _build_multipart_payload(docx_bytes, mode="STANDARD")

    path = "/api/v1/contract-review/tasks"
    nonce = "nonce_multipart_empty_sha_00001"
    empty_sha = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    # 用空正文 SHA 计算签名
    headers = _make_v2_headers("POST", path, nonce, b"", override_sha=empty_sha)
    headers["Content-Type"] = ctype

    with (
        patch.object(api, "get_storage", return_value=st),
        patch.object(
            api,
            "_auth",
            TrustedHeaderAuth(shared_key=SHARED_KEY, storage=st, auth_version="2"),
        ),
    ):
        client = TestClient(api.app)
        resp = client.post(path, headers=headers, content=body)
        assert resp.status_code == 401, (
            f"非空 multipart 使用空正文签名未被阻断！返回={resp.status_code}, {resp.text}"
        )
    st.close()


def test_multipart_upload_tampered_payload_rejected(tmp_path: Path):
    """
    验证：篡改文件正文、mode 或 custom 但保留原摘要及签名，必须返回 401。
    """
    st = Storage(tmp_path / "multipart_test3.db")
    docx_bytes = _create_synthetic_docx_bytes("合规条款文本 A。")
    original_body, ctype = _build_multipart_payload(
        docx_bytes, mode="STANDARD", custom="req1"
    )

    path = "/api/v1/contract-review/tasks"
    nonce = "nonce_multipart_tamper_0000001"
    # 对原始 body 签名
    headers = _make_v2_headers("POST", path, nonce, original_body)
    headers["Content-Type"] = ctype

    # 篡改 body (改变 mode)
    tampered_body, _ = _build_multipart_payload(
        docx_bytes, mode="STRICT", custom="req1"
    )

    with (
        patch.object(api, "get_storage", return_value=st),
        patch.object(
            api,
            "_auth",
            TrustedHeaderAuth(shared_key=SHARED_KEY, storage=st, auth_version="2"),
        ),
    ):
        client = TestClient(api.app)
        resp = client.post(path, headers=headers, content=tampered_body)
        assert resp.status_code == 401, f"篡改后未返回 401！返回={resp.status_code}"
    st.close()


def test_failed_signature_does_not_consume_nonce(tmp_path: Path):
    """验证：签名校验失败不消费 nonce，修正合法签名后仍可正常使用该 nonce。"""
    st = Storage(tmp_path / "multipart_test4.db")
    docx_bytes = _create_synthetic_docx_bytes("条款文本。")
    body, ctype = _build_multipart_payload(docx_bytes)

    path = "/api/v1/contract-review/tasks"
    nonce = "nonce_reuse_after_fail_000001"

    # 先用错误的签名
    headers_bad = _make_v2_headers("POST", path, nonce, body, key="wrong_secret_key")
    headers_bad["Content-Type"] = ctype

    with (
        patch.object(api, "get_storage", return_value=st),
        patch.object(
            api,
            "_auth",
            TrustedHeaderAuth(shared_key=SHARED_KEY, storage=st, auth_version="2"),
        ),
    ):
        client = TestClient(api.app)
        r_bad = client.post(path, headers=headers_bad, content=body)
        assert r_bad.status_code == 401

        # 随后用正确的签名重试同一 nonce
        headers_good = _make_v2_headers("POST", path, nonce, body, key=SHARED_KEY)
        headers_good["Content-Type"] = ctype
        r_good = client.post(path, headers=headers_good, content=body)
        assert r_good.status_code == 200, (
            f"失败后未消费的 Nonce 应可被合法请求消费: {r_good.status_code}"
        )

        # 再次重放该 nonce 必须被阻断
        r_replay = client.post(path, headers=headers_good, content=body)
        assert r_replay.status_code == 401, "已消费的 Nonce 重放未被阻断"
    st.close()


def test_normal_json_and_get_requests_remain_available(tmp_path: Path):
    """验证：正常的 JSON、GET 与合法空正文请求在修复后保持可用。"""
    st = Storage(tmp_path / "normal_req.db")

    # 1. GET 请求 (合法空正文)
    path_get = "/api/v1/contract-kb/rules"
    nonce_get = "nonce_normal_get_000000001"
    headers_get = _make_v2_headers("GET", path_get, nonce_get, b"")

    with (
        patch.object(api, "get_storage", return_value=st),
        patch.object(
            api,
            "_auth",
            TrustedHeaderAuth(shared_key=SHARED_KEY, storage=st, auth_version="2"),
        ),
    ):
        client = TestClient(api.app)
        r_get = client.get(path_get, headers=headers_get)
        assert r_get.status_code == 200, f"GET 请求失败: {r_get.status_code}"

        # 2. POST JSON 请求
        path_post = "/api/v1/contract-kb/rules"
        nonce_post = "nonce_normal_json_00000001"
        rule_data = b'{"rule_id":"r_json_01","name":"JSON Rule","rule_type":"SYNTHETIC","execution_mode":"DETERMINISTIC","severity":"LOW"}'
        headers_post = _make_v2_headers(
            "POST", path_post, nonce_post, rule_data, roles="admin"
        )
        headers_post["Content-Type"] = "application/json"

        r_post = client.post(path_post, headers=headers_post, content=rule_data)
        assert r_post.status_code == 200, (
            f"POST JSON 失败: {r_post.status_code}, {r_post.text}"
        )

    st.close()


def test_oversized_request_bounded_during_reading(tmp_path: Path):
    """验证：超大请求在读取过程中被限制，返回 413。"""
    st = Storage(tmp_path / "oversize.db")
    path = "/api/v1/contract-review/tasks"
    nonce = "nonce_oversized_000000001"

    # 生成超过 MAX_REQUEST_BODY_BYTES 的数据流
    oversized_bytes = b"X" * (api.config.MAX_REQUEST_BODY_BYTES + 1024)
    headers = _make_v2_headers("POST", path, nonce, oversized_bytes)
    headers["Content-Type"] = "application/octet-stream"

    with (
        patch.object(api, "get_storage", return_value=st),
        patch.object(
            api,
            "_auth",
            TrustedHeaderAuth(shared_key=SHARED_KEY, storage=st, auth_version="2"),
        ),
    ):
        client = TestClient(api.app)
        r = client.post(path, headers=headers, content=oversized_bytes)
        assert r.status_code == 413, f"超大请求未返回 413: {r.status_code}"

    st.close()
