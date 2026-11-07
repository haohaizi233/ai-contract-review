# -*- coding: utf-8 -*-
"""测试 Auth V2 真实 Body 摘要校验、共享持久化 Nonce 与先验签后消费（R04 整改验证）。"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

import contract_kb.api as api
from contract_kb.auth import TrustedHeaderAuth, compute_auth_signature_v2
from contract_kb.storage import Storage

SHARED_KEY = "synthetic-auth-v2-remediation-key"


def _make_headers(
    method: str,
    path: str,
    nonce: str,
    body: bytes = b"",
    key: str = SHARED_KEY,
    user: str = "alice",
    roles: str = "admin",
    tenant: str = "tenant_sec",
    tamper_header_sha: str | None = None,
) -> dict[str, str]:
    ts = str(time.time())
    real_sha = hashlib.sha256(body).hexdigest()
    header_sha = tamper_header_sha if tamper_header_sha is not None else real_sha
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


def test_tampered_actual_body_is_rejected_401(tmp_path: Path):
    """
    验证客户端保持 X-Content-SHA256 及签名不变，但篡改实际 POST 正文时：
    服务端必须有界读取真实 body 并核验证验摘要，发现不符立即 401 阻断。
    """
    db_file = tmp_path / "tamper_test.db"
    st = Storage(db_file)

    original_rule = {
        "rule_id": "rule_legit_001",
        "name": "Legit Rule",
        "rule_type": "SYNTHETIC",
        "execution_mode": "DETERMINISTIC",
        "severity": "LOW",
    }
    original_bytes = json.dumps(original_rule).encode("utf-8")

    tampered_rule = dict(original_rule, rule_id="rule_tampered_999", severity="HIGH")
    tampered_bytes = json.dumps(tampered_rule).encode("utf-8")

    # 对合法原始 body 签名
    h = _make_headers(
        "POST",
        "/api/v1/contract-kb/rules",
        "nonce_tamper_test_00000001",
        original_bytes,
    )
    h["Content-Type"] = "application/json"

    with patch.object(api, "get_storage", return_value=st), patch.object(
        api, "_auth", TrustedHeaderAuth(shared_key=SHARED_KEY, storage=st, auth_version="2")
    ):
        client = TestClient(api.app)
        # 发送被篡改的正文
        resp = client.post(
            "/api/v1/contract-kb/rules",
            headers=h,
            content=tampered_bytes,
        )
        assert resp.status_code == 401, (
            f"篡改真实 body 后未被 401 阻断！实际返回: {resp.status_code}, {resp.text}"
        )

    st.close()


def test_shared_nonce_prevents_replay_across_api_instances(tmp_path: Path):
    """
    验证多个独立 API 实例（或重启后的实例）共享存储，无法重复使用同一个已消费的 Nonce。
    """
    db_file = tmp_path / "nonce_replay.db"
    st = Storage(db_file)

    h = _make_headers(
        "GET",
        "/api/v1/contract-kb/rules",
        "nonce_cross_instance_00000001",
    )

    statuses = []
    # 模拟两个独立进程/实例
    for _ in range(2):
        with patch.object(api, "get_storage", return_value=st), patch.object(
            api, "_auth", TrustedHeaderAuth(shared_key=SHARED_KEY, storage=st, auth_version="2")
        ):
            client = TestClient(api.app)
            r = client.get("/api/v1/contract-kb/rules", headers=h)
            statuses.append(r.status_code)

    assert statuses[0] == 200, f"首次合法请求失败: {statuses[0]}"
    assert statuses[1] == 401, f"跨实例重放同一 Nonce 未被阻断！实际状态码: {statuses}"

    st.close()


def test_invalid_signature_does_not_consume_nonce(tmp_path: Path):
    """
    验证发送错误签名的请求不会抢占/消耗该 Nonce；随后携带正确签名的请求仍能成功通过。
    """
    db_file = tmp_path / "nonce_poison.db"
    st = Storage(db_file)

    test_nonce = "nonce_poison_test_00000001"
    valid_headers = _make_headers(
        "GET",
        "/api/v1/contract-kb/rules",
        test_nonce,
    )

    # 构造带有错误签名的请求
    bad_headers = dict(valid_headers, **{"X-Auth-Signature": "invalid_bogus_signature_hex"})

    with patch.object(api, "get_storage", return_value=st), patch.object(
        api, "_auth", TrustedHeaderAuth(shared_key=SHARED_KEY, storage=st, auth_version="2")
    ):
        client = TestClient(api.app)

        # 1. 发送伪造/错误签名的请求
        bad_resp = client.get("/api/v1/contract-kb/rules", headers=bad_headers)
        assert bad_resp.status_code == 401

        # 2. 发送合法签名的同 Nonce 请求，必须能够成功通过（因为 Nonce 未被无效签名提前烧掉）
        good_resp = client.get("/api/v1/contract-kb/rules", headers=valid_headers)
        assert good_resp.status_code == 200, (
            f"错误签名提前消耗了合法 Nonce（Nonce 投毒）！实际返回: {good_resp.status_code}"
        )

    st.close()
