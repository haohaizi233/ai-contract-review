# -*- coding: utf-8 -*-
"""第三轮整改回归（缺陷 1）：强制租户 + 资源所有权检查。

覆盖：
- 合同列表租户过滤：零匹配返回空列表，**不得回退到全量数据**；
- 文档详情 / 审核任务 / 问题详情 / 人工反馈：跨租户一律 404（不泄露资源存在性）；
- 同租户非创建者（非 admin）访问任务资源 → 403（资源所有权检查）；
- admin 可跨用户访问本租户资源；全部使用合成语料。
"""

import pytest

ADMIN_A = {"X-User": "admin_a", "X-Roles": "admin", "X-Tenant": "t1"}
REVIEWER_A = {"X-User": "rev_a", "X-Roles": "reviewer", "X-Tenant": "t1"}
REVIEWER_A2 = {"X-User": "rev_a2", "X-Roles": "reviewer", "X-Tenant": "t1"}
VIEWER_A = {"X-User": "view_a", "X-Roles": "viewer", "X-Tenant": "t1"}
REVIEWER_B = {"X-User": "rev_b", "X-Roles": "reviewer", "X-Tenant": "t2"}
ADMIN_B = {"X-User": "admin_b", "X-Roles": "admin", "X-Tenant": "t2"}

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture(scope="module")
def client(storage):
    import contract_kb.api as api

    api._storage = storage
    from fastapi.testclient import TestClient

    return TestClient(api.app)


@pytest.fixture(scope="module")
def task_t1(client, golden_samples):
    """Tenant A（rev_a）创建的审核任务。"""
    path = golden_samples["dir"] / "mut_08_amount_case_mismatch.docx"
    with open(path, "rb") as f:
        resp = client.post(
            "/api/contract-review/tasks",
            files={"file": ("m.docx", f, DOCX_MIME)},
            data={"mode": "COMBINED"},
            headers=REVIEWER_A,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == "t1" and body["created_by"] == "rev_a"
    return body["task_id"]


# ---------------- 合同列表：零匹配不得回退全量 ----------------


def test_documents_zero_match_does_not_fall_back_to_all(client):
    """Tenant B 视角：库里没有 t2 文档时必须返回空列表，不得回退到全量数据。"""
    a_docs = client.get("/api/contract-kb/documents", headers=VIEWER_A).json()
    assert a_docs, "前置：Tenant A 应有合成语料文档"
    b_docs = client.get("/api/contract-kb/documents", headers=REVIEWER_B).json()
    assert b_docs == [], f"零匹配必须返回空列表，禁止回退全量：{b_docs}"


def test_documents_strict_tenant_filter(client):
    a_docs = {
        d["document_id"]
        for d in client.get("/api/contract-kb/documents", headers=VIEWER_A).json()
    }
    b_docs = {
        d["document_id"]
        for d in client.get("/api/contract-kb/documents", headers=ADMIN_B).json()
    }
    assert a_docs and not (a_docs & b_docs), "跨租户文档集合必须不相交"


def test_document_detail_cross_tenant_hidden(client):
    doc_id = client.get("/api/contract-kb/documents", headers=VIEWER_A).json()[0][
        "document_id"
    ]
    ok = client.get(f"/api/contract-kb/documents/{doc_id}", headers=VIEWER_A)
    assert ok.status_code == 200
    hidden = client.get(f"/api/contract-kb/documents/{doc_id}", headers=REVIEWER_B)
    assert hidden.status_code == 404, "跨租户文档详情必须 404（不泄露存在性）"


# ---------------- 审核任务 / 问题 / 反馈：租户 + 所有权 ----------------


def test_task_cross_tenant_denied(client, task_t1):
    resp = client.get(f"/api/contract-review/tasks/{task_t1}", headers=REVIEWER_B)
    assert resp.status_code == 404
    resp2 = client.get(
        f"/api/contract-review/tasks/{task_t1}/issues", headers=REVIEWER_B
    )
    assert resp2.status_code == 404


def test_task_ownership_enforced(client, task_t1):
    """同租户的其他 reviewer（非创建者、非 admin）不得访问任务资源。"""
    resp = client.get(f"/api/contract-review/tasks/{task_t1}", headers=REVIEWER_A2)
    assert resp.status_code == 403, "资源所有权检查必须生效"
    ok = client.get(f"/api/contract-review/tasks/{task_t1}", headers=REVIEWER_A)
    assert ok.status_code == 200
    admin_ok = client.get(f"/api/contract-review/tasks/{task_t1}", headers=ADMIN_A)
    assert admin_ok.status_code == 200, "同租户 admin 可访问"


def test_issue_feedback_cross_tenant_denied(client, task_t1):
    issues = client.get(
        f"/api/contract-review/tasks/{task_t1}/issues", headers=REVIEWER_A
    ).json()
    assert issues
    iid = issues[0]["issue_id"]
    resp = client.post(
        f"/api/contract-review/issues/{iid}/feedback",
        json={"action": "NOTE", "note": "跨租户尝试"},
        headers=REVIEWER_B,
    )
    assert resp.status_code == 404, "跨租户反馈必须拒绝"


def test_issue_feedback_ownership_enforced(client, task_t1):
    issues = client.get(
        f"/api/contract-review/tasks/{task_t1}/issues", headers=REVIEWER_A
    ).json()
    iid = issues[0]["issue_id"]
    resp = client.post(
        f"/api/contract-review/issues/{iid}/feedback",
        json={"action": "NOTE", "note": "非拥有者尝试"},
        headers=REVIEWER_A2,
    )
    assert resp.status_code == 403, "非创建者反馈必须被资源所有权检查拒绝"
    ok = client.post(
        f"/api/contract-review/issues/{iid}/feedback",
        json={"action": "ACCEPT", "note": "拥有者反馈"},
        headers=REVIEWER_A,
    )
    assert ok.status_code == 200
    assert ok.json()["actor"] == "rev_a"


def test_task_zero_match_no_fabrication(client):
    resp = client.get(
        "/api/contract-review/tasks/nonexistent_task_id", headers=REVIEWER_A
    )
    assert resp.status_code == 404


def test_search_scoped_to_tenant(client):
    """检索同样受租户约束：Tenant B 检索不得命中 Tenant A 的语料。"""
    resp = client.post(
        "/api/contract-kb/search",
        json={"query": "违约金 逾期", "top_k": 10, "rerank": False},
        headers=REVIEWER_B,
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 0, "跨租户检索必须零命中（不回退全量）"
