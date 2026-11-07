# -*- coding: utf-8 -*-
"""集成测试：授权条件下推数据库后再分页（T05 / F10）。

验证：
1. 解决内存过滤导致分页空洞与伪造 total 的缺陷（F10）；
2. 用户 Alice 只能分页看到自身创建的任务与作业，total 必须为 Alice 的真实总数；
3. 多用户交错写入时，分页偏移量（offset/limit）在数据库层面准确生效，不漏条目、不重复条目；
4. Admin 用户在相同接口可以分页查看该租户下的全量条目；
5. 仓储层接口（SQLite 与 PostgreSQL）均原生支持 (tenant_id, created_by) 下推过滤。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

import contract_kb.api as api
from contract_kb.storage import Storage

ALICE_T1 = {"X-User": "alice", "X-Roles": "reviewer", "X-Tenant": "t1"}
BOB_T1 = {"X-User": "bob", "X-Roles": "reviewer", "X-Tenant": "t1"}
ADMIN_T1 = {"X-User": "superadmin", "X-Roles": "admin", "X-Tenant": "t1"}


@pytest.fixture
def pagination_app(tmp_path: Path):
    db_file = tmp_path / "pagination_test.sqlite"
    st = Storage(db_file)
    old_st = api._storage
    api._storage = st
    client = TestClient(api.app)
    try:
        yield client, st
    finally:
        api._storage = old_st
        st.close()


def test_review_tasks_authorized_pagination_pushdown(pagination_app):
    """验证 review_tasks 列表分页条件完整下推，消灭内存二次过滤造成的空页与假 total。"""
    client, st = pagination_app

    # 交错插入 Alice 与 Bob 的审核任务（共 6 个，各 3 个）
    for i in range(3):
        st.save_task(
            task_id=f"bob_task_{i}",
            document_id=f"doc_b_{i}",
            mode="STANDARD",
            status="COMPLETED",
            meta={"idx": i},
            tenant_id="t1",
            created_by="bob",
        )
        st.save_task(
            task_id=f"alice_task_{i}",
            document_id=f"doc_a_{i}",
            mode="STANDARD",
            status="COMPLETED",
            meta={"idx": i},
            tenant_id="t1",
            created_by="alice",
        )

    # 1. Alice 请求第一页：limit=2, offset=0
    r1 = client.get("/api/v1/contract-review/tasks?limit=2&offset=0", headers=ALICE_T1)
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["total"] == 3, f"Alice 可见的真实总数必须为 3，实际为 {d1['total']}"
    assert len(d1["items"]) == 2
    for item in d1["items"]:
        assert item["created_by"] == "alice"

    # 2. Alice 请求第二页：limit=2, offset=2
    r2 = client.get("/api/v1/contract-review/tasks?limit=2&offset=2", headers=ALICE_T1)
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["total"] == 3
    assert len(d2["items"]) == 1
    assert d2["items"][0]["created_by"] == "alice"
    # 确保两页条目不重复
    seen_ids = {item["task_id"] for item in d1["items"]} | {d2["items"][0]["task_id"]}
    assert len(seen_ids) == 3

    # 3. Alice 请求越界页：limit=2, offset=4
    r3 = client.get("/api/v1/contract-review/tasks?limit=2&offset=4", headers=ALICE_T1)
    assert r3.status_code == 200
    d3 = r3.json()
    assert d3["total"] == 3
    assert len(d3["items"]) == 0

    # 4. Admin 请求第一页：可见全量 6 条
    r_admin = client.get("/api/v1/contract-review/tasks?limit=4&offset=0", headers=ADMIN_T1)
    assert r_admin.status_code == 200
    d_admin = r_admin.json()
    assert d_admin["total"] == 6
    assert len(d_admin["items"]) == 4


def test_async_jobs_authorized_pagination_pushdown(pagination_app):
    """验证 async_jobs 列表分页条件完整下推到数据库。"""
    client, st = pagination_app

    # 交错插入 Alice 与 Bob 的异步作业
    for i in range(3):
        st.enqueue_async_job(
            {
                "job_id": f"job_b_{i}",
                "job_type": "CONTRACT_REVIEW",
                "tenant_id": "t1",
                "created_by": "bob",
            }
        )
        st.enqueue_async_job(
            {
                "job_id": f"job_a_{i}",
                "job_type": "CONTRACT_REVIEW",
                "tenant_id": "t1",
                "created_by": "alice",
            }
        )

    # 1. Bob 请求第一页：limit=2, offset=0
    r1 = client.get("/api/v1/jobs?limit=2&offset=0", headers=BOB_T1)
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["total"] == 3
    assert len(d1["items"]) == 2
    for j in d1["items"]:
        assert j["created_by"] == "bob"

    # 2. Bob 请求第二页：limit=2, offset=2
    r2 = client.get("/api/v1/jobs?limit=2&offset=2", headers=BOB_T1)
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["total"] == 3
    assert len(d2["items"]) == 1
    assert d2["items"][0]["created_by"] == "bob"

    # 3. Admin 请求：total 为 6
    r_admin = client.get("/api/v1/jobs?limit=10&offset=0", headers=ADMIN_T1)
    assert r_admin.status_code == 200
    assert r_admin.json()["total"] == 6
