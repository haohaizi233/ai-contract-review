# -*- coding: utf-8 -*-
"""P0-01 失败复现与安全防护测试：导入绝不能直接授予审批权限。

验证矩阵：
1. 普通导入传入 APPROVED 状态必须被强制置为 PENDING_REVIEW；
2. 重复导入已撤销（REVOKED）的条款，不得将其篡改为 PENDING_REVIEW 或 APPROVED；
3. 重复导入已批准（APPROVED）的条款，不得将其重置为 PENDING_REVIEW；
4. 跨租户导入相同文本彼此隔离；
5. 重建索引不得改变审批状态；
6. 授权批量导入方法可受控导入历史审批，但普通 upsert_document 必须严格 Fail-Closed。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from contract_kb.storage import Storage


def _make_sample_doc(
    doc_id: str = "doc_bypass_test", status: str = "ACTIVE", version: str = "v1"
) -> dict:
    return {
        "template_id": doc_id,
        "source_file": f"{doc_id}.docx",
        "sha256": f"hash_{doc_id}_{version}_1234567890abcdef",
        "template_version": version,
        "category": "PATENT",
        "subtype": "PATENT_APPLICATION",
        "document_type": "contract_template",
        "status": status,
        "clauses": [
            {
                "clause_id": f"{doc_id}_c1",
                "clause_number": "第一条",
                "clause_category": "FEE_AND_PAYMENT",
                "original_text": "乙方应于合同签订后3个工作日内支付首期款项人民币壹拾万元整。",
                "approval_status": "APPROVED",  # 试图通过导入参数越权授予 APPROVED
                "is_company_standard": True,
            }
        ],
    }


def test_import_cannot_grant_approval():
    """验证普通导入传入 APPROVED 绝不生效，强制置为 PENDING_REVIEW。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        st = Storage(Path(tmpdir) / "bypass_test.db")
        try:
            doc = _make_sample_doc("doc_bypass_1")
            did, is_new = st.upsert_document(doc, tenant="tenant_corp_a")
            assert is_new is True

            c1 = st.get_clause("doc_bypass_1_c1", tenant_id="tenant_corp_a")
            assert c1 is not None
            # 核心断言：普通导入必须为 PENDING_REVIEW，绝对不得为 APPROVED！
            assert c1["approval_status"] == "PENDING_REVIEW"
        finally:
            st.close()


def test_reimport_cannot_restore_revoked_approval():
    """验证条款被法务撤销后，重新导入相同文档或新版本不得恢复已被撤销的审批状态。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        st = Storage(Path(tmpdir) / "revoke_reimport_test.db")
        try:
            doc = _make_sample_doc("doc_revoke_1")
            did, _ = st.upsert_document(doc, tenant="tenant_corp_a")

            # 1. 正式审批
            st.approve_knowledge_version(
                tenant_id="tenant_corp_a",
                document_id=did,
                approver_id="legal_officer_1",
                approval_reason="首次合规准入",
            )
            c_approved = st.get_clause("doc_revoke_1_c1", tenant_id="tenant_corp_a")
            assert c_approved["approval_status"] == "APPROVED"

            # 2. 法务执行撤销
            st.revoke_knowledge_version(
                tenant_id="tenant_corp_a",
                document_id=did,
                revoker_id="legal_officer_2",
                revoke_reason="条款存在法律合规缺陷，紧急撤销",
            )
            c_revoked = st.get_clause("doc_revoke_1_c1", tenant_id="tenant_corp_a")
            assert c_revoked["approval_status"] == "REVOKED"

            # 3. 攻击/误操作场景：重新导入相同条款数据并声称 APPROVED
            reimport_doc = _make_sample_doc("doc_revoke_1")
            reimport_doc["clauses"][0]["approval_status"] = "APPROVED"
            st.upsert_document(reimport_doc, tenant="tenant_corp_a")

            c_after = st.get_clause("doc_revoke_1_c1", tenant_id="tenant_corp_a")
            # 核心断言：撤销状态必须被持久锁定，重导入不得将其洗白为 APPROVED 或 PENDING_REVIEW！
            assert c_after["approval_status"] == "REVOKED"
        finally:
            st.close()


def test_cross_tenant_import_isolation():
    """验证不同租户导入相同条款时彼此隔离，各自具备独立的生命周期与审批状态。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        st = Storage(Path(tmpdir) / "tenant_isolation_test.db")
        try:
            doc_a = _make_sample_doc("doc_shared_1")
            did_a, _ = st.upsert_document(doc_a, tenant="tenant_a")

            doc_b = _make_sample_doc("doc_shared_1")
            did_b, _ = st.upsert_document(doc_b, tenant="tenant_b")

            # 租户 A 经过法务审批
            st.approve_knowledge_version(
                tenant_id="tenant_a",
                document_id=did_a,
                approver_id="legal_a",
                approval_reason="租户 A 准入",
            )

            # 租户 A 为 APPROVED
            c_a = st.get_clause("tenant_a:doc_shared_1_c1", tenant_id="tenant_a")
            if not c_a:
                c_a = st.get_clause("doc_shared_1_c1", tenant_id="tenant_a")
            assert c_a is not None
            assert c_a["approval_status"] == "APPROVED"

            # 租户 B 依然是 PENDING_REVIEW，不得越权变为 APPROVED
            c_b = st.get_clause("tenant_b:doc_shared_1_c1", tenant_id="tenant_b")
            if not c_b:
                c_b = st.get_clause("doc_shared_1_c1", tenant_id="tenant_b")
            assert c_b is not None
            assert c_b["approval_status"] == "PENDING_REVIEW"
        finally:
            st.close()


def test_rebuild_index_preserves_approval_status():
    """验证索引重建操作绝不会篡改条款的审批状态。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        st = Storage(Path(tmpdir) / "rebuild_test.db")
        try:
            doc = _make_sample_doc("doc_rebuild_1")
            did, _ = st.upsert_document(doc, tenant="tenant_rebuild")

            st.approve_knowledge_version(
                tenant_id="tenant_rebuild",
                document_id=did,
                approver_id="legal_rebuild",
                approval_reason="审批测试",
            )
            c_before = st.get_clause("doc_rebuild_1_c1", tenant_id="tenant_rebuild")
            assert c_before["approval_status"] == "APPROVED"

            # 重建索引
            st.rebuild_index()

            c_after = st.get_clause("doc_rebuild_1_c1", tenant_id="tenant_rebuild")
            assert c_after["approval_status"] == "APPROVED"
        finally:
            st.close()


def test_authorized_bulk_import_creates_audit_record():
    """验证受控的授权历史数据迁移命令能够正常置为 APPROVED 并留存完整审计记录。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        st = Storage(Path(tmpdir) / "auth_import_test.db")
        try:
            doc = _make_sample_doc("doc_hist_1")
            did, is_new = st.authorized_bulk_import_approved(
                doc,
                tenant="tenant_hist",
                approver_id="chief_legal_counsel",
                approval_reason="2026年度标准模板历史合规库迁移",
            )
            assert is_new is True

            c = st.get_clause("doc_hist_1_c1", tenant_id="tenant_hist")
            assert c is not None
            assert c["approval_status"] == "APPROVED"

            # 验证审计表记录
            cur = st.conn.execute(
                "SELECT * FROM knowledge_approval_records WHERE tenant_id=? AND knowledge_id=?",
                ("tenant_hist", did),
            )
            record = cur.fetchone()
            assert record is not None
            assert record["approver_id"] == "chief_legal_counsel"
            assert record["approval_action"] == "AUTHORIZE_HISTORICAL_IMPORT"
            assert "2026年度" in record["approval_reason"]
        finally:
            st.close()
