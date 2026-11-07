# -*- coding: utf-8 -*-
"""P0-02 失败复现与安全测试：知识库审批乐观锁与并发冲突控制（CAS）。

验证：
1. 传入 expected_revision 时，若与数据库当前 revision 不一致，必须拒绝更新并抛出 OptimisticLockConflictError；
2. 成功审批后 revision 必须原子递增；
3. 重复传入已过期的旧 revision 必须被拦截，防止并发覆盖；
4. HTTP API 对预期 revision 冲突返回 409 Conflict，对未授权角色返回 403 Forbidden；
5. 撤销操作同样具备 CAS 乐观锁并发校验。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
import pytest

import contract_kb.api as api
from contract_kb.storage import Storage, OptimisticLockConflictError

ADMIN_HEADERS = {
    "X-User": "admin_legal",
    "X-Roles": "admin,reviewer",
    "X-Tenant": "tenant_cas",
}
AUDITOR_HEADERS = {
    "X-User": "guest_auditor",
    "X-Roles": "auditor",
    "X-Tenant": "tenant_cas",
}


def _make_sample_doc(doc_id: str = "doc_cas_test") -> dict:
    return {
        "template_id": doc_id,
        "source_file": f"{doc_id}.docx",
        "sha256": f"hash_{doc_id}_cas_1234567890",
        "template_version": "v1",
        "category": "PATENT",
        "subtype": "PATENT_APPLICATION",
        "document_type": "contract_template",
        "status": "ACTIVE",
        "clauses": [
            {
                "clause_id": f"{doc_id}_c1",
                "clause_number": "第一条",
                "clause_category": "FEE_AND_PAYMENT",
                "original_text": "本合同费用按月结算。",
            }
        ],
    }


def test_approve_with_mismatched_expected_revision_fails():
    """验证传入不匹配的 expected_revision 时，必须抛出乐观锁冲突异常。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        st = Storage(Path(tmpdir) / "cas_test.db")
        try:
            doc = _make_sample_doc("doc_cas_1")
            did, _ = st.upsert_document(doc, tenant="tenant_cas")

            doc_rec = st.get_document(did, tenant_id="tenant_cas")
            initial_revision = doc_rec.get("revision", 1)

            # 模拟并发写冲突：传入错误的 expected_revision = initial_revision + 99
            with pytest.raises(OptimisticLockConflictError) as exc_info:
                st.approve_knowledge_version(
                    tenant_id="tenant_cas",
                    document_id=did,
                    approver_id="legal_1",
                    approval_reason="并发审批测试",
                    expected_revision=initial_revision + 99,
                )
            assert (
                "冲突" in str(exc_info.value)
                or "conflict" in str(exc_info.value).lower()
            )
        finally:
            st.close()


def test_approve_and_revoke_atomic_revision_increment():
    """验证正常审批和撤销时，revision 原子递增，且旧 revision 无法再次重放。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        st = Storage(Path(tmpdir) / "cas_increment_test.db")
        try:
            doc = _make_sample_doc("doc_cas_2")
            did, _ = st.upsert_document(doc, tenant="tenant_cas")

            doc_rec = st.get_document(did, tenant_id="tenant_cas")
            rev1 = doc_rec.get("revision", 1)

            # 第一次审批：使用正确的 expected_revision = rev1
            res1 = st.approve_knowledge_version(
                tenant_id="tenant_cas",
                document_id=did,
                approver_id="legal_1",
                approval_reason="第一次批准",
                expected_revision=rev1,
            )
            assert res1["status"] == "APPROVED"
            assert res1["revision"] == rev1 + 1

            # 第二次操作尝试再次使用已过期的 rev1：必须报错拒绝！
            with pytest.raises(OptimisticLockConflictError):
                st.revoke_knowledge_version(
                    tenant_id="tenant_cas",
                    document_id=did,
                    revoker_id="legal_2",
                    revoke_reason="尝试使用旧版本覆盖",
                    expected_revision=rev1,
                )

            # 使用更新后的正确 revision: rev1 + 1 撤销
            res2 = st.revoke_knowledge_version(
                tenant_id="tenant_cas",
                document_id=did,
                revoker_id="legal_2",
                revoke_reason="使用合法最新版本撤销",
                expected_revision=rev1 + 1,
            )
            assert res2["status"] == "REVOKED"
            assert res2["revision"] == rev1 + 2
        finally:
            st.close()


def test_api_approval_cas_conflict_returns_409(tmp_path: Path):
    """验证通过 HTTP API 提交审批时，修订版本冲突返回 409，非管理员返回 403。"""
    db_file = tmp_path / "cas_api.db"
    st = Storage(db_file)
    old_st = api._storage
    api._storage = st
    client = TestClient(api.app)

    try:
        doc = _make_sample_doc("doc_cas_api")
        did, _ = st.upsert_document(doc, tenant="tenant_cas")

        # 1. 非法务/非管理员角色尝试审批：403
        resp_403 = client.post(
            f"/api/v1/contract-kb/documents/{did}/approve",
            headers=AUDITOR_HEADERS,
            json={"approval_reason": "越权审批", "expected_revision": 1},
        )
        assert resp_403.status_code == 403

        # 2. 正确提交审批：200 OK
        resp_ok = client.post(
            f"/api/v1/contract-kb/documents/{did}/approve",
            headers=ADMIN_HEADERS,
            json={"approval_reason": "法务负责人合规审批", "expected_revision": 1},
        )
        assert resp_ok.status_code == 200
        data = resp_ok.json()
        assert data["status"] == "APPROVED"
        assert data["revision"] == 2

        # 3. 模拟并发冲突：仍携带 expected_revision=1 重试 -> 必须返回 409
        resp_409 = client.post(
            f"/api/v1/contract-kb/documents/{did}/approve",
            headers=ADMIN_HEADERS,
            json={"approval_reason": "重放冲突审批", "expected_revision": 1},
        )
        assert resp_409.status_code == 409
        err_body = str(resp_409.json())
        assert "409" in err_body or "冲突" in err_body or "conflict" in err_body.lower()
    finally:
        api._storage = old_st
        st.close()
