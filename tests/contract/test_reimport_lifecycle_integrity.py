# -*- coding: utf-8 -*-
"""P1 缺陷回归：重复导入不得重新激活旧版本或待审批版本，必须保持生命周期与审批约束。"""

from __future__ import annotations

from pathlib import Path
import docx

from contract_kb.ingest import import_template_file
from contract_kb.storage import Storage


def _create_docx(file_path: Path, title: str, clauses: list[tuple[str, str]]) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    doc = docx.Document()
    doc.add_heading(title, level=1)
    for num, text in clauses:
        doc.add_paragraph(f"{num} {text}")
    doc.save(str(file_path))


def test_reimport_superseded_version_does_not_reactivate(tmp_path: Path):
    """复现缺陷 1: V2 审批为 ACTIVE、V1 为 SUPERSEDED 后，重复导入 V1 不得将 V1 重新激活为 ACTIVE。"""
    db_path = tmp_path / "test_reimport.db"
    st = Storage(db_path)
    tenant = "tenant_reimport_test"

    v1_path = tmp_path / "MB20261109001_V1.docx"
    v2_path = tmp_path / "MB20261109001_V2.docx"

    _create_docx(
        v1_path,
        "标准知识产权代理协议",
        [("第一条", "V1条款：服务费用总额为人民币壹万元整。")],
    )
    _create_docx(
        v2_path,
        "标准知识产权代理协议",
        [("第一条", "V2条款：服务费用总额调整为人民币贰万元整。")],
    )

    doc_v1, _ = import_template_file(v1_path)
    doc_v2, _ = import_template_file(v2_path)
    assert doc_v1 and doc_v2
    assert doc_v1.template_id == doc_v2.template_id == "MB20261109001"
    assert doc_v1.sha256 != doc_v2.sha256

    # 1. 导入 V1 并审批
    did_v1, is_new1 = st.upsert_document(doc_v1.to_dict(), tenant=tenant)
    assert is_new1 is True
    st.approve_knowledge_version(
        tenant_id=tenant,
        document_id=did_v1,
        approver_id="legal_admin",
        approval_reason="审批生效 V1",
    )
    assert st.get_document(did_v1, tenant_id=tenant)["status"] == "ACTIVE"

    # 2. 导入 V2 并审批
    did_v2, is_new2 = st.upsert_document(doc_v2.to_dict(), tenant=tenant)
    assert is_new2 is True
    st.approve_knowledge_version(
        tenant_id=tenant,
        document_id=did_v2,
        approver_id="legal_admin",
        approval_reason="审批生效 V2，替代 V1",
    )
    assert st.get_document(did_v2, tenant_id=tenant)["status"] == "ACTIVE"
    assert st.get_document(did_v1, tenant_id=tenant)["status"] == "SUPERSEDED"

    # 3. 再次导入原 V1 (同哈希重新导入)
    did_v1_re, is_new_re = st.upsert_document(doc_v1.to_dict(), tenant=tenant)
    assert is_new_re is False
    assert did_v1_re == did_v1

    # 核心断言：V1 绝对不得被自动改成 ACTIVE，必须依然保持 SUPERSEDED！
    v1_after = st.get_document(did_v1, tenant_id=tenant)
    v2_after = st.get_document(did_v2, tenant_id=tenant)
    assert v1_after["status"] == "SUPERSEDED", (
        f"重复导入旧版本导致 V1 被错误重新激活: {v1_after['status']}"
    )
    assert v2_after["status"] == "ACTIVE"

    # 检索验证：有效检索集必须只能检索到当前生效版本 V2，V1 条款不得出现
    hits = st.search("服务费用总额", top_k=5, tenant_id=tenant)
    assert len(hits) > 0
    hit_doc_ids = {h["document_id"] for h in hits}
    assert did_v1 not in hit_doc_ids, (
        f"已废止版本 V1 的条款不应被有效检索命中: {hit_doc_ids}"
    )
    assert did_v2 in hit_doc_ids


def test_reimport_pending_version_remains_pending(tmp_path: Path):
    """复现反例 2: V2 首次导入为 PENDING_REVIEW，重复导入相同内容不得被擅自改为 ACTIVE。"""
    db_path = tmp_path / "test_reimport_pending.db"
    st = Storage(db_path)
    tenant = "tenant_pending_test"

    v1_path = tmp_path / "MB20261109002_V1.docx"
    v2_path = tmp_path / "MB20261109002_V2.docx"
    _create_docx(v1_path, "合同A", [("第一条", "合同初始正文")])
    _create_docx(v2_path, "合同A", [("第一条", "合同修改正文")])

    doc_v1, _ = import_template_file(v1_path)
    doc_v2, _ = import_template_file(v2_path)
    assert doc_v1 and doc_v2

    did_v1, _ = st.upsert_document(doc_v1.to_dict(), tenant=tenant)
    st.approve_knowledge_version(
        tenant_id=tenant, document_id=did_v1, approver_id="admin"
    )

    # 导入 V2，处于待审批
    did_v2, is_new2 = st.upsert_document(doc_v2.to_dict(), tenant=tenant)
    assert is_new2 is True
    assert st.get_document(did_v2, tenant_id=tenant)["status"] == "PENDING_REVIEW"

    # 重复导入 V2
    did_v2_dup, is_new2_dup = st.upsert_document(doc_v2.to_dict(), tenant=tenant)
    assert is_new2_dup is False
    assert did_v2_dup == did_v2

    # 核心断言：重复导入不得使待审批文档绕过审批变为 ACTIVE！
    v2_status = st.get_document(did_v2, tenant_id=tenant)["status"]
    assert v2_status == "PENDING_REVIEW", (
        f"重复导入使得待审批文档被越权激活: {v2_status}"
    )


def test_reimport_preserves_archived_and_revoked_states(tmp_path: Path):
    """重复导入已归档文档及已撤销条款，必须完整保留原有治理状态。"""
    db_path = tmp_path / "test_reimport_archived.db"
    st = Storage(db_path)
    tenant = "tenant_archived_test"

    f_path = tmp_path / "MB20261109003_V1.docx"
    _create_docx(f_path, "归档合同", [("第一条", "条款内容测试")])
    doc, _ = import_template_file(f_path)
    assert doc

    did, _ = st.upsert_document(doc.to_dict(), tenant=tenant)
    st.approve_knowledge_version(tenant_id=tenant, document_id=did, approver_id="admin")

    # 人工归档文档并将某条款标记为 REVOKED
    st.conn.execute(
        "UPDATE documents SET status='ARCHIVED' WHERE document_id=?", (did,)
    )
    st.conn.execute(
        "UPDATE clauses SET approval_status='REVOKED' WHERE document_id=?", (did,)
    )
    st._commit()

    # 重复导入
    _, is_new = st.upsert_document(doc.to_dict(), tenant=tenant)
    assert is_new is False

    # 验证文档状态仍为 ARCHIVED，条款状态仍为 REVOKED
    doc_after = st.get_document(did, tenant_id=tenant)
    assert doc_after["status"] == "ARCHIVED"
    clauses = st.get_document_clauses(did, tenant_id=tenant)
    assert all(c["approval_status"] == "REVOKED" for c in clauses)

    # 携带 refresh_classification 刷新元数据也不得改变生命周期与审批状态
    _, is_new_ref = st.upsert_document(
        doc.to_dict(), tenant=tenant, refresh_classification=True
    )
    assert is_new_ref is False
    assert st.get_document(did, tenant_id=tenant)["status"] == "ARCHIVED"
    assert all(
        c["approval_status"] == "REVOKED"
        for c in st.get_document_clauses(did, tenant_id=tenant)
    )
