# -*- coding: utf-8 -*-
"""P1 缺陷回归：回滚必须严格校验目标哈希与指定 document_id 属于同一模板版本链，禁止跨模板链串联或非法哈希生效。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import docx
import pytest

from contract_kb.ingest import import_template_file
from contract_kb.storage import Storage


def _create_docx(file_path: Path, title: str, clauses: list[tuple[str, str]]) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    doc = docx.Document()
    doc.add_heading(title, level=1)
    for num, text in clauses:
        doc.add_paragraph(f"{num} {text}")
    doc.save(str(file_path))


def test_rollback_rejects_cross_template_chain_hash(tmp_path: Path):
    """复现缺陷 2: 同租户存在 T、U 模板链，调用 rollback_to_version(T_v2, U_v1哈希) 必须拒绝并返回 False。"""
    db_path = tmp_path / "test_cross_rollback.db"
    st = Storage(db_path)
    tenant = "tenant_chain_test"

    # 创建模板 T 的 V1 与 V2
    t1_path = tmp_path / "MB20261109101_V1.docx"
    t2_path = tmp_path / "MB20261109101_V2.docx"
    _create_docx(t1_path, "模板T", [("第一条", "模板T第一版条款")])
    _create_docx(t2_path, "模板T", [("第一条", "模板T第二版条款")])
    doc_t1, _ = import_template_file(t1_path)
    doc_t2, _ = import_template_file(t2_path)
    assert doc_t1 and doc_t2

    did_t1, _ = st.upsert_document(doc_t1.to_dict(), tenant=tenant)
    st.approve_knowledge_version(
        tenant_id=tenant, document_id=did_t1, approver_id="admin"
    )
    did_t2, _ = st.upsert_document(doc_t2.to_dict(), tenant=tenant)
    st.approve_knowledge_version(
        tenant_id=tenant, document_id=did_t2, approver_id="admin"
    )

    assert st.get_document(did_t1, tenant_id=tenant)["status"] == "SUPERSEDED"
    assert st.get_document(did_t2, tenant_id=tenant)["status"] == "ACTIVE"

    # 创建模板 U 的 V1 与 V2
    u1_path = tmp_path / "MB20261109202_V1.docx"
    u2_path = tmp_path / "MB20261109202_V2.docx"
    _create_docx(u1_path, "模板U", [("第一条", "模板U第一版条款")])
    _create_docx(u2_path, "模板U", [("第一条", "模板U第二版条款")])
    doc_u1, _ = import_template_file(u1_path)
    doc_u2, _ = import_template_file(u2_path)
    assert doc_u1 and doc_u2

    did_u1, _ = st.upsert_document(doc_u1.to_dict(), tenant=tenant)
    st.approve_knowledge_version(
        tenant_id=tenant, document_id=did_u1, approver_id="admin"
    )
    did_u2, _ = st.upsert_document(doc_u2.to_dict(), tenant=tenant)
    st.approve_knowledge_version(
        tenant_id=tenant, document_id=did_u2, approver_id="admin"
    )

    assert st.get_document(did_u1, tenant_id=tenant)["status"] == "SUPERSEDED"
    assert st.get_document(did_u2, tenant_id=tenant)["status"] == "ACTIVE"

    # 尝试非法跨链回滚：给模板 T 指定模板 U_v1 的哈希
    res = st.rollback_to_version(did_t2, doc_u1.sha256, tenant_id=tenant)

    # 核心断言：跨模板链回滚必须被严格拒绝！
    assert res is False, f"跨模板链回滚未被拒绝，返回了: {res}"

    # 验证 T 与 U 两条版本链的状态均绝对未被破坏
    assert st.get_document(did_t2, tenant_id=tenant)["status"] == "ACTIVE"
    assert st.get_document(did_t1, tenant_id=tenant)["status"] == "SUPERSEDED"
    assert st.get_document(did_u2, tenant_id=tenant)["status"] == "ACTIVE"
    assert st.get_document(did_u1, tenant_id=tenant)["status"] == "SUPERSEDED"


def test_rollback_invalid_hash_or_document_id_rejected(tmp_path: Path):
    """验证错误哈希、不存在的 document_id、不存在的目标版本均被明确拒绝，无静默回退。"""
    db_path = tmp_path / "test_invalid_rollback.db"
    st = Storage(db_path)
    tenant = "tenant_invalid_test"

    t1_path = tmp_path / "MB20261109303_V1.docx"
    _create_docx(t1_path, "模板X", [("第一条", "测试正文")])
    doc_t1, _ = import_template_file(t1_path)
    assert doc_t1

    did_t1, _ = st.upsert_document(doc_t1.to_dict(), tenant=tenant)
    st.approve_knowledge_version(
        tenant_id=tenant, document_id=did_t1, approver_id="admin"
    )

    # 1. 不存在的哈希
    res1 = st.rollback_to_version(did_t1, "0" * 64, tenant_id=tenant)
    assert res1 is False
    assert st.get_document(did_t1, tenant_id=tenant)["status"] == "ACTIVE"

    # 2. 不存在的 document_id
    res2 = st.rollback_to_version(
        "non_existent_doc_999", doc_t1.sha256, tenant_id=tenant
    )
    assert res2 is False

    # 3. 跨租户相同哈希：租户 B 无法用租户 A 的 document_id 回滚
    res3 = st.rollback_to_version(did_t1, doc_t1.sha256, tenant_id="tenant_other")
    assert res3 is False


def test_rollback_cannot_bypass_approval_for_unapproved_version(tmp_path: Path):
    """待审批版本（PENDING_REVIEW）或撤销版本（REVOKED）严禁借回滚通道直接激活生效。"""
    db_path = tmp_path / "test_bypass_rollback.db"
    st = Storage(db_path)
    tenant = "tenant_bypass_test"

    v1_path = tmp_path / "MB20261109404_V1.docx"
    v2_path = tmp_path / "MB20261109404_V2.docx"
    _create_docx(v1_path, "模板Y", [("第一条", "正版V1")])
    _create_docx(v2_path, "模板Y", [("第一条", "未审批V2")])
    doc_v1, _ = import_template_file(v1_path)
    doc_v2, _ = import_template_file(v2_path)
    assert doc_v1 and doc_v2

    did_v1, _ = st.upsert_document(doc_v1.to_dict(), tenant=tenant)
    st.approve_knowledge_version(
        tenant_id=tenant, document_id=did_v1, approver_id="admin"
    )
    did_v2, _ = st.upsert_document(doc_v2.to_dict(), tenant=tenant)
    # V2 处于 PENDING_REVIEW，未经审批
    assert st.get_document(did_v2, tenant_id=tenant)["status"] == "PENDING_REVIEW"

    # 试图直接回滚到未审批的 V2 哈希
    res = st.rollback_to_version(did_v1, doc_v2.sha256, tenant_id=tenant)
    assert res is False, "未审批的 PENDING_REVIEW 版本严禁通过回滚直接被激活"
    assert st.get_document(did_v1, tenant_id=tenant)["status"] == "ACTIVE"
    assert st.get_document(did_v2, tenant_id=tenant)["status"] == "PENDING_REVIEW"


def test_rollback_atomic_on_index_failure(tmp_path: Path):
    """索引重建失败时，回滚事务必须原子回滚，数据库生命周期状态不得变更。"""
    db_path = tmp_path / "test_atomic_rollback.db"
    st = Storage(db_path)
    tenant = "tenant_atomic_test"

    v1_path = tmp_path / "MB20261109505_V1.docx"
    v2_path = tmp_path / "MB20261109505_V2.docx"
    _create_docx(v1_path, "模板Z", [("第一条", "第一版")])
    _create_docx(v2_path, "模板Z", [("第一条", "第二版")])
    doc_v1, _ = import_template_file(v1_path)
    doc_v2, _ = import_template_file(v2_path)
    assert doc_v1 and doc_v2

    did_v1, _ = st.upsert_document(doc_v1.to_dict(), tenant=tenant)
    st.approve_knowledge_version(
        tenant_id=tenant, document_id=did_v1, approver_id="admin"
    )
    did_v2, _ = st.upsert_document(doc_v2.to_dict(), tenant=tenant)
    st.approve_knowledge_version(
        tenant_id=tenant, document_id=did_v2, approver_id="admin"
    )

    assert st.get_document(did_v1, tenant_id=tenant)["status"] == "SUPERSEDED"
    assert st.get_document(did_v2, tenant_id=tenant)["status"] == "ACTIVE"

    # 模拟 rebuild_index 抛出异常
    with patch.object(st, "rebuild_index", side_effect=RuntimeError("Index Disk Full")):
        with pytest.raises(RuntimeError, match="Index Disk Full"):
            st.rollback_to_version(did_v2, doc_v1.sha256, tenant_id=tenant)

    # 验证事务已安全回滚：V2 依然是 ACTIVE，V1 依然是 SUPERSEDED
    assert st.get_document(did_v2, tenant_id=tenant)["status"] == "ACTIVE"
    assert st.get_document(did_v1, tenant_id=tenant)["status"] == "SUPERSEDED"


def test_rollback_rejects_version_with_revoked_clause(tmp_path: Path):
    """验证目标版本若存在已被撤销（REVOKED）的条款，严禁回滚激活，必须拒绝并返回 False。"""
    db_path = tmp_path / "test_revoked_clause_rollback.db"
    st = Storage(db_path)
    tenant = "tenant_revoked_test"

    v1_path = tmp_path / "MB20261109606_V1.docx"
    v2_path = tmp_path / "MB20261109606_V2.docx"
    _create_docx(v1_path, "模板W", [("第一条", "含有缺陷需撤销的条款")])
    _create_docx(v2_path, "模板W", [("第一条", "已修正合规的新条款")])
    doc_v1, _ = import_template_file(v1_path)
    doc_v2, _ = import_template_file(v2_path)
    assert doc_v1 and doc_v2

    did_v1, _ = st.upsert_document(doc_v1.to_dict(), tenant=tenant)
    st.approve_knowledge_version(
        tenant_id=tenant, document_id=did_v1, approver_id="admin"
    )
    did_v2, _ = st.upsert_document(doc_v2.to_dict(), tenant=tenant)
    st.approve_knowledge_version(
        tenant_id=tenant, document_id=did_v2, approver_id="admin"
    )

    assert st.get_document(did_v1, tenant_id=tenant)["status"] == "SUPERSEDED"
    assert st.get_document(did_v2, tenant_id=tenant)["status"] == "ACTIVE"

    # 将 V1 的条款撤销
    st.revoke_knowledge_version(
        tenant_id=tenant,
        document_id=did_v1,
        revoker_id="legal_officer",
        revoke_reason="条款存在法律合规缺陷已撤销",
    )
    v1_clauses = st.get_document_clauses(did_v1, tenant_id=tenant)
    assert any(c["approval_status"] == "REVOKED" for c in v1_clauses)

    # 尝试回滚到含有 REVOKED 条款的 V1
    res = st.rollback_to_version(did_v2, doc_v1.sha256, tenant_id=tenant)
    assert res is False, "含有撤销条款的历史版本严禁回滚恢复生效"

    # 验证状态未受影响
    assert st.get_document(did_v2, tenant_id=tenant)["status"] == "ACTIVE"
    assert st.get_document(did_v1, tenant_id=tenant)["status"] == "SUPERSEDED"


def test_same_filename_does_not_join_template_chains(tmp_path: Path):
    st = Storage(tmp_path / "same_name.db")
    try:

        def approved(template, content):
            did, _ = st.upsert_document(
                {
                    "template_id": template,
                    "sha256": content,
                    "source_file": "contract.docx",
                    "clauses": [{"clause_id": content, "original_text": content}],
                },
                tenant="a",
            )
            st.approve_knowledge_version("a", did, "legal")
            return did

        t1 = approved("T", "t1")
        t2 = approved("T", "t2")
        u1 = approved("U", "u1")
        u2 = approved("U", "u2")
        assert not st.rollback_to_version(t2, "u1", tenant_id="a")
        assert st.rollback_to_version(t2, "t1", tenant_id="a")
        assert st.get_document(t1)["status"] == "ACTIVE"
        assert st.get_document(u2)["status"] == "ACTIVE"
        assert st.get_document(u1)["status"] == "SUPERSEDED"
    finally:
        st.close()


def test_omitted_tenant_never_adopts_document_tenant(tmp_path: Path, monkeypatch):
    from contract_kb import config

    monkeypatch.setattr(config, "TENANT_ID", "local")
    st = Storage(tmp_path / "tenant.db")
    try:
        ids = []
        for content in ("v1", "v2"):
            did, _ = st.upsert_document(
                {
                    "template_id": "T",
                    "sha256": content,
                    "source_file": "contract.docx",
                    "clauses": [{"clause_id": content, "original_text": content}],
                },
                tenant="foreign",
            )
            st.approve_knowledge_version("foreign", did, "legal")
            ids.append(did)
        assert not st.rollback_to_version(ids[1], "v1")
        assert st.get_document(ids[1])["status"] == "ACTIVE"
        assert st.get_document(ids[0])["status"] == "SUPERSEDED"
    finally:
        st.close()
