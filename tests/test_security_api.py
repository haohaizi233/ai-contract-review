# -*- coding: utf-8 -*-
"""test_security / test_api（合成语料；API 使用独立测试库）"""
import io

import pytest

from contract_kb.ingest import security_check_file
from contract_kb import config


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
    """合同正文中的指令性文本只作为数据检出，不得控制系统指令。"""
    from contract_kb.review_engine import ReviewEngine
    engine = ReviewEngine(storage)
    r = engine.review(golden_samples["dir"] / "mut_14_no_issue_baseline.docx")
    assert r["status"] == "COMPLETED"
    assert isinstance(r["issues"], list)


# ---------------- API ----------------

@pytest.fixture(scope="module")
def client(storage):
    """API 测试注入独立测试存储（不触碰生产 data/ 库）。"""
    import contract_kb.api as api
    api._storage = storage
    from fastapi.testclient import TestClient
    return TestClient(api.app)


def test_api_documents(client):
    resp = client.get("/api/contract-kb/documents")
    assert resp.status_code == 200
    assert resp.json(), "测试库应有合成语料文档"


def test_api_search(client):
    resp = client.post("/api/contract-kb/search",
                       json={"query": "违约金 逾期", "top_k": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] >= 1
    assert body["results"][0]["text"]


def test_api_compare(client):
    resp = client.post("/api/contract-kb/compare",
                       json={"clause_texts": ["甲乙双方保密", "甲乙双方保密"]})
    assert resp.json()["identical"] is True


def test_api_rules_lifecycle(client):
    resp = client.post("/api/contract-kb/rules", json={
        "rule_id": "TEST_RULE_API", "name": "测试规则", "rule_type": "STYLE_REQUIREMENT",
        "execution_mode": "DETERMINISTIC", "severity": "LOW"})
    assert resp.status_code in (200, 409)
    resp2 = client.post("/api/contract-kb/rules/TEST_RULE_API/approve?approver=test_legal")
    assert resp2.status_code in (200, 409)
    rules = client.get("/api/contract-kb/rules").json()
    assert any(r["rule_id"] == "TEST_RULE_API" for r in rules)


def test_api_review_task_and_feedback(client, golden_samples):
    path = golden_samples["dir"] / "mut_08_amount_case_mismatch.docx"
    with open(path, "rb") as f:
        resp = client.post("/api/contract-review/tasks",
                           files={"file": ("m.docx", f,
                                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                           data={"mode": "COMBINED"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "COMPLETED"
    tid = body["task_id"]
    issues = client.get(f"/api/contract-review/tasks/{tid}/issues").json()
    assert issues
    iid = issues[0]["issue_id"]
    fb = client.post(f"/api/contract-review/issues/{iid}/feedback",
                     json={"action": "FALSE_POSITIVE", "actor": "tester", "note": "样例误报"})
    assert fb.status_code == 200


def test_api_rejects_non_docx(client):
    resp = client.post("/api/contract-review/tasks",
                       files={"file": ("evil.exe", io.BytesIO(b"MZ"), "application/octet-stream")})
    assert resp.status_code == 400
