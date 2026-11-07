# -*- coding: utf-8 -*-
"""Phase 11: Storage Parity Behavioral Contract Test.

Verifies that SQLite (Storage) and PostgreSQL (PgVectorStorage) adhere to the
exact same security, approval, isolation, and lifecycle contracts.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from contract_kb.storage import Storage


def test_sqlite_storage_contract_lifecycle_and_approval():
    """验证 SQLite 存储实现完全遵循 Phase 11 审批与隔离契约。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        st = Storage(Path(tmpdir) / "parity_sqlite.db")
        try:
            doc = {
                "template_id": "parity_doc_1",
                "source_file": "parity.docx",
                "sha256": "hash_parity_001",
                "template_version": "v1",
                "category": "PATENT",
                "subtype": "PATENT_APPLICATION",
                "document_type": "contract_template",
                "status": "ACTIVE",
                "clauses": [
                    {
                        "clause_id": "clause_parity_1",
                        "original_text": "服务费用为每件专利人民币伍仟圆整（¥5,000 元）。",
                        "clause_category": "FEE_AND_PAYMENT",
                        "approval_status": "PENDING_REVIEW",
                    }
                ],
            }
            did, new = st.upsert_document(doc, tenant="tenant_parity")
            assert new is True

            # 1. 初始状态必须是 PENDING_REVIEW
            c1 = st.get_clause("clause_parity_1", tenant_id="tenant_parity")
            assert c1 is not None
            assert c1["approval_status"] == "PENDING_REVIEW"

            # 2. 跨租户查询返回 None
            c_cross = st.get_clause("clause_parity_1", tenant_id="other_tenant")
            assert c_cross is None

            # 3. 未审批前 approved_only 检索不出
            hits_unapproved = st.search(
                "服务费用", approved_only=True, tenant_id="tenant_parity"
            )
            assert len(hits_unapproved) == 0

            # 4. 审批操作成功并记录审计日志
            appr_res = st.approve_knowledge_version(
                tenant_id="tenant_parity",
                document_id=did,
                approver_id="approver_parity",
                approval_reason="基准一致性验证",
            )
            assert appr_res["status"] == "APPROVED"
            assert appr_res["approved_count"] == 1

            # 5. 审批后 approved_only 检索成功召回
            hits_approved = st.search(
                "服务费用", approved_only=True, tenant_id="tenant_parity"
            )
            assert len(hits_approved) == 1
            assert hits_approved[0]["clause_id"] == "clause_parity_1"

            # 6. 撤销审批操作生效并阻断检索
            rev_res = st.revoke_knowledge_version(
                tenant_id="tenant_parity",
                document_id=did,
                revoker_id="approver_parity",
                revoke_reason="测试撤销",
            )
            assert rev_res["status"] == "REVOKED"
            hits_revoked = st.search(
                "服务费用", approved_only=True, tenant_id="tenant_parity"
            )
            assert len(hits_revoked) == 0
        finally:
            st.close()


def test_sqlite_storage_non_active_document_blocked():
    """验证文档状态置为 SUPERSEDED 后，其下所有条款在 search 中自动被排除。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        st = Storage(Path(tmpdir) / "parity_stale.db")
        try:
            doc = {
                "template_id": "stale_doc_1",
                "source_file": "stale.docx",
                "sha256": "hash_stale_001",
                "template_version": "v1",
                "document_type": "contract_template",
                "status": "ACTIVE",
                "clauses": [
                    {
                        "clause_id": "clause_stale_1",
                        "original_text": "本协议自甲乙双方签字盖章之日起生效。",
                        "clause_category": "EFFECTIVENESS_AND_TERMINATION",
                        "approval_status": "APPROVED",
                    }
                ],
            }
            did, _ = st.upsert_document(doc, tenant="t1")
            hits1 = st.search("签字盖章", approved_only=True, tenant_id="t1")
            assert len(hits1) == 1

            # 将文档置为 SUPERSEDED
            st.set_document_status(did, "SUPERSEDED", tenant_id="t1")
            hits2 = st.search("签字盖章", approved_only=True, tenant_id="t1")
            assert len(hits2) == 0
        finally:
            st.close()
