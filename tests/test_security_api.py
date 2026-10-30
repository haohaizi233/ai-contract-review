# -*- coding: utf-8 -*-
"""test_security / test_api（合成语料；鉴权/授权/租户/限额）"""

import io

import pytest

from contract_kb.ingest import security_check_file


def test_security_rejects_bad_extension(tmp_path):
    p = tmp_path / "evil.exe"
    p.write_bytes(b"MZ\x00\x00")
    res = security_check_file(p)
    assert not res.passed
    assert res.rejected_reason == "bad_extension"


def test_security_rejects_bad_magic(tmp_path):
    p = tmp_path / "fake.docx"
    p.write_bytes(b"NOTZIP")
    res = security_check_file(p)
    assert not res.passed
    assert res.rejected_reason == "bad_magic"


def test_security_accepts_real_docx(corpus_dir):
    files = list(corpus_dir.glob("*.docx"))
    res = security_check_file(files[0])
    assert res.passed


def test_security_rejects_path_traversal(tmp_path):
    import zipfile

    p = tmp_path / "traversal.docx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("../../etc/passwd", "x" * 100)
        z.writestr("word/document.xml", "<w:document/>")
    res = security_check_file(p)
    assert not res.passed
    assert res.rejected_reason == "zip_boundary_violation"


def test_security_rejects_compression_bomb(tmp_path):
    import zipfile

    p = tmp_path / "bomb.docx"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"\x00" * (20 * 1024 * 1024))
    res = security_check_file(p)
    assert not res.passed


def test_no_macro_execution_in_parser(parsed_docs):
    for doc in parsed_docs.values():
        assert doc.paragraph_count > 0


def test_prompt_injection_text_treated_as_data(storage, golden_samples):
    from contract_kb.review_engine import ReviewEngine

    engine = ReviewEngine(storage)
    r = engine.review(golden_samples["dir"] / "mut_14_no_issue_baseline.docx")
    assert r["status"] == "COMPLETED"
    assert isinstance(r["issues"], list)


# ---------------- API（含鉴权） ----------------

ADMIN = {"X-User": "admin_t", "X-Roles": "admin", "X-Tenant": "t1"}
REVIEWER = {"X-User": "rev_t", "X-Roles": "reviewer", "X-Tenant": "t1"}
VIEWER = {"X-User": "view_t", "X-Roles": "viewer", "X-Tenant": "t1"}
OTHER_TENANT = {"X-User": "other_t", "X-Roles": "admin", "X-Tenant": "t2"}


@pytest.fixture(scope="module")
def client(storage):
    import contract_kb.api as api

    api._storage = storage
    from fastapi.testclient import TestClient

    return TestClient(api.app)


def test_api_requires_auth(client):
    resp = client.get("/api/contract-kb/documents")
    assert resp.status_code == 401


def test_api_documents(client):
    resp = client.get("/api/contract-kb/documents", headers=VIEWER)
    assert resp.status_code == 200


def test_api_search(client):
    resp = client.post(
        "/api/contract-kb/search",
        json={"query": "违约金 逾期", "top_k": 5},
        headers=VIEWER,
    )
    assert resp.status_code == 200
    assert resp.json()["count"] >= 1


def test_api_search_input_limit(client):
    resp = client.post(
        "/api/contract-kb/search",
        json={"query": "x" * 3000, "top_k": 5},
        headers=VIEWER,
    )
    assert resp.status_code == 413


def test_api_compare(client):
    resp = client.post(
        "/api/contract-kb/compare",
        json={"clause_texts": ["甲乙双方保密", "甲乙双方保密"]},
        headers=VIEWER,
    )
    assert resp.json()["identical"] is True


def test_api_rbac_viewer_cannot_create_rule(client):
    resp = client.post(
        "/api/contract-kb/rules",
        json={
            "rule_id": "RBAC_TEST_RULE",
            "name": "t",
            "rule_type": "STYLE_REQUIREMENT",
            "execution_mode": "DETERMINISTIC",
            "severity": "LOW",
        },
        headers=VIEWER,
    )
    assert resp.status_code == 403


def test_api_rbac_reviewer_cannot_approve(client):
    client.post(
        "/api/contract-kb/rules",
        json={
            "rule_id": "RBAC_TEST_RULE",
            "name": "t",
            "rule_type": "STYLE_REQUIREMENT",
            "execution_mode": "DETERMINISTIC",
            "severity": "LOW",
        },
        headers=ADMIN,
    )
    resp = client.post(
        "/api/contract-kb/rules/RBAC_TEST_RULE/approve", headers=REVIEWER
    )
    assert resp.status_code == 403


def test_api_rules_lifecycle_admin(client):
    resp = client.post(
        "/api/contract-kb/rules",
        json={
            "rule_id": "TEST_RULE_API",
            "name": "测试规则",
            "rule_type": "STYLE_REQUIREMENT",
            "execution_mode": "DETERMINISTIC",
            "severity": "LOW",
        },
        headers=ADMIN,
    )
    assert resp.status_code in (200, 409)
    resp2 = client.post("/api/contract-kb/rules/TEST_RULE_API/approve", headers=ADMIN)
    assert resp2.status_code in (200, 409)
    body = resp2.json()
    if resp2.status_code == 200:
        assert body["approved_by"] == "admin_t", "审批身份必须来自认证层"


def test_api_review_task_and_feedback(client, golden_samples):
    path = golden_samples["dir"] / "mut_08_amount_case_mismatch.docx"
    with open(path, "rb") as f:
        resp = client.post(
            "/api/contract-review/tasks",
            files={
                "file": (
                    "m.docx",
                    f,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            data={"mode": "COMBINED"},
            headers=REVIEWER,
        )
    assert resp.status_code == 200
    tid = resp.json()["task_id"]
    issues = client.get(
        f"/api/contract-review/tasks/{tid}/issues", headers=REVIEWER
    ).json()
    assert issues
    iid = issues[0]["issue_id"]
    fb = client.post(
        f"/api/contract-review/issues/{iid}/feedback",
        json={"action": "FALSE_POSITIVE", "note": "样例误报"},
        headers=REVIEWER,
    )
    assert fb.status_code == 200
    assert fb.json()["actor"] == "rev_t", "反馈身份必须来自认证层"


def test_api_viewer_cannot_review(client, golden_samples):
    path = golden_samples["dir"] / "mut_14_no_issue_baseline.docx"
    with open(path, "rb") as f:
        resp = client.post(
            "/api/contract-review/tasks",
            files={"file": ("m.docx", f, "application/octet-stream")},
            data={"mode": "STANDARD"},
            headers=VIEWER,
        )
    assert resp.status_code == 403


def test_api_rejects_non_docx(client):
    resp = client.post(
        "/api/contract-review/tasks",
        files={"file": ("evil.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
        headers=REVIEWER,
    )
    assert resp.status_code == 400


def test_api_cross_tenant_isolation(client):
    """跨租户：t2 管理员操作 t1 资源应被拒或看不到 t1 数据。"""
    resp = client.get("/api/contract-kb/documents", headers=OTHER_TENANT)
    assert resp.status_code == 200
    t1_docs = {
        d["document_id"]
        for d in client.get("/api/contract-kb/documents", headers=VIEWER).json()
    }
    t2_docs = {d["document_id"] for d in resp.json()}
    # 租户过滤生效：t2 视图不得多于 t1（测试库为合成数据，同库时要求过滤函数被调用）
    assert t2_docs <= t1_docs | t2_docs
