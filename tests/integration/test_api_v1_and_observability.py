# -*- coding: utf-8 -*-
"""Phase E 集成测试：API v1 契约治理、双路由兼容、分页、阶段查询、人工覆盖审计、异步任务与可观测性。"""

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

import contract_kb.api as api

ADMIN = {"X-User": "admin_e", "X-Roles": "admin", "X-Tenant": "t1"}
REVIEWER_A = {"X-User": "rev_a", "X-Roles": "reviewer", "X-Tenant": "t1"}
REVIEWER_B = {"X-User": "rev_b", "X-Roles": "reviewer", "X-Tenant": "t1"}
VIEWER = {"X-User": "view_e", "X-Roles": "viewer", "X-Tenant": "t1"}
OTHER_TENANT_ADMIN = {"X-User": "admin_t2", "X-Roles": "admin", "X-Tenant": "t2"}


@pytest.fixture(scope="module")
def v1_client(storage):
    api._storage = storage
    api._runner = None
    api._runner_storage_ref = None
    return TestClient(api.app)


def test_healthz_readyz_and_trace_headers(v1_client: TestClient):
    h = v1_client.get(
        "/healthz",
        headers={"X-Request-ID": "req_custom_99", "X-Trace-ID": "trc_custom_99"},
    )
    assert h.status_code == 200
    assert h.json()["status"] == "ok"
    assert h.headers["X-Request-ID"] == "req_custom_99"
    assert h.headers["X-Trace-ID"] == "trc_custom_99"

    r = v1_client.get("/api/v1/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["db_ready"] is True
    assert body["db_latency_ms"] >= 0.0


def test_v1_pagination_vs_legacy_deprecation_headers(v1_client: TestClient):
    legacy = v1_client.get("/api/contract-kb/documents", headers=VIEWER)
    assert legacy.status_code == 200
    assert isinstance(legacy.json(), list)
    assert legacy.headers.get("Deprecation") == "true"
    assert "/api/v1/contract-kb/documents" in legacy.headers.get("Link", "")

    v1 = v1_client.get("/api/v1/contract-kb/documents?limit=3&offset=0", headers=VIEWER)
    assert v1.status_code == 200
    assert "Deprecation" not in v1.headers
    v1_body = v1.json()
    assert set(v1_body.keys()) == {"items", "total", "limit", "offset"}
    assert len(v1_body["items"]) <= 3
    assert v1_body["total"] == len(legacy.json())


def test_unified_error_envelope_contains_code_and_trace(v1_client: TestClient):
    unauth = v1_client.get(
        "/api/v1/contract-kb/documents", headers={"X-Request-ID": "req_err_401"}
    )
    assert unauth.status_code == 401
    err = unauth.json()
    assert err["code"] == "HTTP_401"
    assert err["status_code"] == 401
    assert err["request_id"] == "req_err_401"
    assert err["trace_id"].startswith("trc_")


def test_v1_sync_review_idempotency_stages_overrides_and_cancel(
    v1_client: TestClient, golden_samples
):
    sample = golden_samples["dir"] / "mut_08_amount_case_mismatch.docx"
    headers = {**REVIEWER_A, "X-Idempotency-Key": "sync-idem-v1-001"}
    with open(sample, "rb") as f:
        r1 = v1_client.post(
            "/api/v1/contract-review/tasks",
            files={"file": ("s.docx", f, "application/octet-stream")},
            data={"mode": "STANDARD"},
            headers=headers,
        )
    assert r1.status_code == 200
    tid = r1.json()["task_id"]

    # 同一租户同一幂等键重复提交应命中同一 task_id
    with open(sample, "rb") as f:
        r2 = v1_client.post(
            "/api/v1/contract-review/tasks",
            files={"file": ("s.docx", f, "application/octet-stream")},
            data={"mode": "STANDARD"},
            headers=headers,
        )
    assert r2.status_code == 200
    assert r2.json()["task_id"] == tid

    # 阶段列表查询（14 阶段流水线持久化）
    stages_resp = v1_client.get(
        f"/api/v1/contract-review/tasks/{tid}/stages", headers=REVIEWER_A
    )
    assert stages_resp.status_code == 200
    stages = stages_resp.json()["stages"]
    assert len(stages) >= 13
    assert stages[0]["stage_name"] == "Receive"

    # 同租户另一位 reviewer 访问应返回 403；跨租户 admin 应返回 404
    assert (
        v1_client.get(
            f"/api/v1/contract-review/tasks/{tid}/stages", headers=REVIEWER_B
        ).status_code
        == 403
    )
    assert (
        v1_client.get(
            f"/api/v1/contract-review/tasks/{tid}/stages", headers=OTHER_TENANT_ADMIN
        ).status_code
        == 404
    )

    # 分页查询任务列表与标准合同列表
    tasks_page = v1_client.get(
        "/api/v1/contract-review/tasks?limit=10&offset=0", headers=REVIEWER_A
    ).json()
    assert any(t["task_id"] == tid for t in tasks_page["items"])

    cc_page = v1_client.get(
        "/api/v1/canonical-contracts?limit=10&offset=0", headers=VIEWER
    )
    assert cc_page.status_code == 200
    assert cc_page.json()["total"] >= 1

    # 人工复核覆盖并校验不可篡改审计表 human_overrides
    issues = v1_client.get(
        f"/api/v1/contract-review/tasks/{tid}/issues", headers=REVIEWER_A
    ).json()
    assert issues
    iid = issues[0]["issue_id"]
    fb = v1_client.post(
        f"/api/v1/contract-review/issues/{iid}/feedback",
        json={"action": "ACCEPT", "note": "法务复核确认金额大小写不一致"},
        headers=REVIEWER_A,
    )
    assert fb.status_code == 200
    fb_body = fb.json()
    assert fb_body["actor"] == "rev_a"
    assert fb_body["finding_source"] == "HUMAN"
    assert fb_body["override_id"].startswith("ovr_")

    overrides = v1_client.get(
        f"/api/v1/contract-review/tasks/{tid}/overrides", headers=REVIEWER_A
    ).json()["overrides"]
    assert len(overrides) >= 1
    assert overrides[-1]["issue_id"] == iid
    assert overrides[-1]["new_state"] == "ACCEPT"

    # 任务取消接口
    cancel_resp = v1_client.post(
        f"/api/v1/contract-review/tasks/{tid}/cancel", headers=REVIEWER_A
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["cancel_requested"] is True


def test_v1_async_jobs_http_lifecycle_and_metrics_rbac(
    v1_client: TestClient, golden_samples
):
    sample = golden_samples["dir"] / "mut_14_no_issue_baseline.docx"
    job_headers = {**REVIEWER_A, "X-Idempotency-Key": "async-job-v1-001"}

    with open(sample, "rb") as f:
        enq1 = v1_client.post(
            "/api/v1/jobs/review",
            files={"file": ("async.docx", f, "application/octet-stream")},
            data={"mode": "STANDARD"},
            headers=job_headers,
        )
    assert enq1.status_code == 202
    assert enq1.json()["created"] is True
    job_id = enq1.json()["job"]["job_id"]
    assert enq1.json()["job"]["status"] == "PENDING"

    # 幂等重放不重复创建作业
    with open(sample, "rb") as f:
        enq2 = v1_client.post(
            "/api/v1/jobs/review",
            files={"file": ("async.docx", f, "application/octet-stream")},
            data={"mode": "STANDARD"},
            headers=job_headers,
        )
    assert enq2.status_code == 202
    assert enq2.json()["created"] is False
    assert enq2.json()["job"]["job_id"] == job_id

    # 提交第二个作业并立即取消
    with open(sample, "rb") as f:
        enq_cancel = v1_client.post(
            "/api/v1/jobs/review",
            files={"file": ("cancel_me.docx", f, "application/octet-stream")},
            headers=REVIEWER_A,
        )
    cancel_jid = enq_cancel.json()["job"]["job_id"]
    c_res = v1_client.post(f"/api/v1/jobs/{cancel_jid}/cancel", headers=REVIEWER_A)
    assert c_res.status_code == 200
    assert (
        v1_client.get(f"/api/v1/jobs/{cancel_jid}", headers=REVIEWER_A).json()["status"]
        == "CANCELLED"
    )

    # 非管理员调用 worker/drain 必须返回 403；管理员调用驱动执行完成
    assert (
        v1_client.post("/api/v1/jobs/worker/drain", headers=REVIEWER_A).status_code
        == 403
    )
    drain_resp = v1_client.post("/api/v1/jobs/worker/drain", headers=ADMIN)
    assert drain_resp.status_code == 200
    assert job_id in drain_resp.json()["job_ids"]

    finished = v1_client.get(f"/api/v1/jobs/{job_id}", headers=REVIEWER_A).json()
    assert finished["status"] == "SUCCEEDED"
    assert finished["progress"] == 100
    assert finished["result"]["status"] == "COMPLETED"

    # 指标端点 RBAC：viewer/reviewer 403，admin 200（支持 JSON 与 Prometheus 格式）
    assert v1_client.get("/api/v1/metrics", headers=VIEWER).status_code == 403
    assert v1_client.get("/api/v1/metrics", headers=REVIEWER_A).status_code == 403
    m_json = v1_client.get("/api/v1/metrics", headers=ADMIN)
    assert m_json.status_code == 200
    assert m_json.json()["counters"]["review_total"] >= 1
    m_prom = v1_client.get("/api/v1/metrics?format=prometheus", headers=ADMIN)
    assert m_prom.status_code == 200
    assert "contract_kb_review_total" in m_prom.text
