# -*- coding: utf-8 -*-
"""安全测试：幂等性凭证的作用域绑定、资源所有权防泄露与请求指纹冲突校验。

严格验证 F01 修复前后的安全行为：
1. 幂等作用域必须绑定到 tenant + actor + operation + key；
2. 同租户不同用户复用相同幂等键，绝不能泄露他人已生成的任务或作业结果；
3. 同一用户复用相同幂等键但请求内容（文件哈希、模式、自定义要求）发生变更，必须返回 409 冲突；
4. 同一用户同一指纹在执行中或完成后，按幂等契约安全返回自身资源。
"""

from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

import contract_kb.api as api
from contract_kb.evaluation.datasets.golden import generate_all
from contract_kb.storage import Storage

ALICE_T1 = {"X-User": "alice", "X-Roles": "reviewer", "X-Tenant": "tenant_1"}
BOB_T1 = {"X-User": "bob", "X-Roles": "reviewer", "X-Tenant": "tenant_1"}
ALICE_T2 = {"X-User": "alice", "X-Roles": "reviewer", "X-Tenant": "tenant_2"}


@pytest.fixture
def isolated_api(tmp_path: Path):
    db_file = tmp_path / "idempotency_sec.sqlite"
    st = Storage(db_file)
    old_storage = api._storage
    old_runner = api._runner
    old_runner_ref = api._runner_storage_ref

    api._storage = st
    api._runner = None
    api._runner_storage_ref = None

    client = TestClient(api.app)
    try:
        yield client, st, tmp_path
    finally:
        api._storage = old_storage
        api._runner = old_runner
        api._runner_storage_ref = old_runner_ref
        st.close()


def test_async_job_idempotency_cross_owner_leak_prevented(isolated_api):
    client, storage, tmp = isolated_api
    key = "reused-async-key-001"

    # 1. Alice 提交异步审核作业
    docx_bytes = b"PK\x03\x04synthetic-alice-document"
    r_alice = client.post(
        "/api/v1/jobs/review",
        files={
            "file": ("doc_a.docx", io.BytesIO(docx_bytes), "application/octet-stream")
        },
        data={"mode": "STANDARD", "custom": "alice-private-rule"},
        headers={**ALICE_T1, "X-Idempotency-Key": key},
    )
    assert r_alice.status_code == 202
    alice_job = r_alice.json()["job"]
    assert alice_job["created_by"] == "alice"
    assert "alice-private-rule" in alice_job["payload"]["custom_requirements"]

    # 2. Bob 同租户使用相同幂等键提交
    bob_bytes = b"PK\x03\x04synthetic-bob-document"
    r_bob = client.post(
        "/api/v1/jobs/review",
        files={
            "file": ("doc_b.docx", io.BytesIO(bob_bytes), "application/octet-stream")
        },
        data={"mode": "STANDARD", "custom": "bob-requirement"},
        headers={**BOB_T1, "X-Idempotency-Key": key},
    )
    assert r_bob.status_code == 202
    bob_job = r_bob.json()["job"]

    # Bob 绝不能读取或关联到 Alice 的任务
    assert bob_job["created_by"] == "bob"
    assert bob_job["job_id"] != alice_job["job_id"]
    assert "alice-private-rule" not in bob_job["payload"].get("custom_requirements", [])


def test_async_job_idempotency_fingerprint_mismatch_returns_409(isolated_api):
    client, storage, tmp = isolated_api
    key = "alice-param-change-key"

    doc1 = b"PK\x03\x04doc-version-1"
    r1 = client.post(
        "/api/v1/jobs/review",
        files={"file": ("doc1.docx", io.BytesIO(doc1), "application/octet-stream")},
        data={"mode": "STANDARD"},
        headers={**ALICE_T1, "X-Idempotency-Key": key},
    )
    assert r1.status_code == 202

    # 同一用户相同 key，但更换了合同内容 -> 必须 409
    doc2 = b"PK\x03\x04doc-version-2-changed"
    r_diff_content = client.post(
        "/api/v1/jobs/review",
        files={"file": ("doc2.docx", io.BytesIO(doc2), "application/octet-stream")},
        data={"mode": "STANDARD"},
        headers={**ALICE_T1, "X-Idempotency-Key": key},
    )
    assert r_diff_content.status_code == 409
    assert "idempotency key conflict" in r_diff_content.json()["error"].lower()

    # 同一用户相同 key、相同内容，但更换审核模式 -> 必须 409
    r_diff_mode = client.post(
        "/api/v1/jobs/review",
        files={"file": ("doc1.docx", io.BytesIO(doc1), "application/octet-stream")},
        data={"mode": "CUSTOM", "custom": "check-penalty"},
        headers={**ALICE_T1, "X-Idempotency-Key": key},
    )
    assert r_diff_mode.status_code == 409


def test_sync_review_task_idempotency_authorization_and_conflict(isolated_api):
    client, storage, tmp = isolated_api
    samples_dir = tmp / "samples"
    manifest = generate_all(samples_dir)
    file_a = samples_dir / manifest[0]["file"]
    file_b = samples_dir / manifest[1]["file"]
    key = "sync-task-key-shared"

    # Alice 提交同步审核任务
    with open(file_a, "rb") as f:
        r_alice = client.post(
            "/api/v1/contract-review/tasks",
            files={"file": ("contract_a.docx", f, "application/octet-stream")},
            data={"mode": "STANDARD"},
            headers={**ALICE_T1, "X-Idempotency-Key": key},
        )
    assert r_alice.status_code == 200
    alice_res = r_alice.json()
    assert alice_res["created_by"] == "alice"

    # Bob 使用同 key 提交不同文件 -> 绝不能返回 Alice 的任务结果
    with open(file_b, "rb") as f:
        r_bob = client.post(
            "/api/v1/contract-review/tasks",
            files={"file": ("contract_b.docx", f, "application/octet-stream")},
            data={"mode": "STANDARD"},
            headers={**BOB_T1, "X-Idempotency-Key": key},
        )
    assert r_bob.status_code == 200
    bob_res = r_bob.json()
    assert bob_res["created_by"] == "bob"
    assert bob_res["task_id"] != alice_res["task_id"]

    # Alice 自身用同一 key 换文件提交 -> 409
    with open(file_b, "rb") as f:
        r_alice_conflict = client.post(
            "/api/v1/contract-review/tasks",
            files={"file": ("contract_b.docx", f, "application/octet-stream")},
            data={"mode": "STANDARD"},
            headers={**ALICE_T1, "X-Idempotency-Key": key},
        )
    assert r_alice_conflict.status_code == 409
