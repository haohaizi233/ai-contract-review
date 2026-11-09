# -*- coding: utf-8 -*-
"""回归测试：同一模板的新版本导入、条款隔离、状态转换与版本回滚。"""

from __future__ import annotations

from pathlib import Path
import docx

from contract_kb.ingest import import_template_file
from contract_kb.modules.retrieval.citation import CitationVerifier
from contract_kb.storage import Storage


def _create_template_docx(
    file_path: Path, title: str, clause1_text: str, clause2_text: str | None = None
) -> None:
    doc = docx.Document()
    doc.add_heading(title, level=1)
    doc.add_paragraph("第一条 付款条款")
    doc.add_paragraph(clause1_text)
    if clause2_text:
        doc.add_paragraph("第二条 交付条款")
        doc.add_paragraph(clause2_text)
    doc.save(file_path)


def test_template_version_v1_v2_import_and_rollback(tmp_path: Path):
    """
    验证：
    1. V1 成功导入并审批。
    2. 同一模板的 V2 能够成功导入，不触发 documents.document_id 主键冲突。
    3. V2 导入后状态为待审批 (PENDING_REVIEW)，未审批前不覆盖 V1 的有效检索。
    4. V2 重复导入保持幂等。
    5. V2 审批后生效 (ACTIVE)，旧版本退出当前检索 (SUPERSEDED)。
    6. 回滚至 V1 后，V1 重新 ACTIVE，V2 SUPERSEDED。
    7. 两个版本各自的条款 ID 互不覆盖，V1 历史引文仍能精确定位。
    """
    db_file = tmp_path / "version_test.db"
    st = Storage(db_file)
    tenant = "tenant_ver_test"

    v1_path = tmp_path / "MB20251205026_V1.docx"
    v2_path = tmp_path / "MB20251205026_V2.docx"

    _create_template_docx(
        v1_path,
        "标准服务协议 V1",
        "甲方应于合同签订后3个工作日内支付首付款一万元整。",
        "乙方应于收到首付款后5个工作日内完成交付。",
    )
    _create_template_docx(
        v2_path,
        "标准服务协议 V2",
        "甲方应于合同签订后10个工作日内支付首付款二万元整。",  # 修改了第1条
        "乙方应于收到首付款后5个工作日内完成交付。",  # 第2条完全未修改
    )

    doc_v1, err1 = import_template_file(v1_path)
    assert err1 is None and doc_v1 is not None
    v1_dict = doc_v1.to_dict()

    doc_v2, err2 = import_template_file(v2_path)
    assert err2 is None and doc_v2 is not None
    v2_dict = doc_v2.to_dict()

    assert doc_v1.template_id == doc_v2.template_id == "MB20251205026"
    assert doc_v1.sha256 != doc_v2.sha256

    # 1. 导入 V1
    did_v1, is_new1 = st.upsert_document(v1_dict, tenant=tenant)
    assert is_new1 is True

    # 审批 V1
    st.approve_knowledge_version(
        tenant_id=tenant,
        document_id=did_v1,
        approver_id="legal_admin",
        approval_reason="审批发布 V1 模板",
    )
    v1_doc = st.get_document(did_v1, tenant_id=tenant)
    assert v1_doc["status"] == "ACTIVE"
    v1_clauses = st.get_document_clauses(did_v1, tenant_id=tenant)
    assert len(v1_clauses) >= 2
    assert all(c["approval_status"] == "APPROVED" for c in v1_clauses)
    v1_c2_id = v1_clauses[-1]["clause_id"]

    # 2. 导入 V2 (必须成功导入，不能主键冲突报错)
    did_v2, is_new2 = st.upsert_document(v2_dict, tenant=tenant)
    assert is_new2 is True
    assert did_v2 != did_v1, "V2 文档标识必须与 V1 区分"

    # 3. 验证 V2 刚导入时普通导入必须为待审批，且不剥夺 V1 的条款
    v2_clauses = st.get_document_clauses(did_v2, tenant_id=tenant)
    assert len(v2_clauses) >= 2
    assert all(c["approval_status"] == "PENDING_REVIEW" for c in v2_clauses)

    # 检查 V1 的条款未被挪走
    v1_clauses_after = st.get_document_clauses(did_v1, tenant_id=tenant)
    assert len(v1_clauses_after) == len(v1_clauses)
    assert v1_clauses_after[-1]["clause_id"] == v1_c2_id
    assert v1_clauses_after[1]["approval_status"] == "APPROVED"

    # 历史引文核验：针对 V1 条款的引用仍能精确定位 V1
    verifier = CitationVerifier()
    evidence_v1 = {
        "tenant_id": tenant,
        "document_id": did_v1,
        "clause_id": v1_c2_id,
        "source_hash": doc_v1.sha256,
        "source_version": v1_doc.get("source_version", "v1"),
    }
    ver_res = verifier.verify_evidence_item(st, evidence_v1, expected_tenant_id=tenant)
    assert ver_res.verified is True, f"V1 历史引文定位失败: {ver_res.failure_reasons}"

    # 4. 重复导入 V2 保持幂等
    did_v2_dup, is_new2_dup = st.upsert_document(v2_dict, tenant=tenant)
    assert did_v2_dup == did_v2
    assert is_new2_dup is False

    # 5. 审批 V2，V2 成为 ACTIVE，V1 退出当前检索 (SUPERSEDED)
    st.approve_knowledge_version(
        tenant_id=tenant,
        document_id=did_v2,
        approver_id="legal_admin",
        approval_reason="审批发布 V2 模板",
    )
    v2_doc = st.get_document(did_v2, tenant_id=tenant)
    assert v2_doc["status"] == "ACTIVE"
    v1_doc_after = st.get_document(did_v1, tenant_id=tenant)
    assert v1_doc_after["status"] == "SUPERSEDED"

    # 6. 回滚至 V1
    ok_rollback = st.rollback_to_version(did_v1, doc_v1.sha256, tenant_id=tenant)
    assert ok_rollback is True
    v1_doc_rb = st.get_document(did_v1, tenant_id=tenant)
    v2_doc_rb = st.get_document(did_v2, tenant_id=tenant)
    assert v1_doc_rb["status"] == "ACTIVE"
    assert v2_doc_rb["status"] == "SUPERSEDED"

    st.close()


def test_cross_tenant_same_template_and_content_isolated(tmp_path: Path):
    """验证：两个不同租户使用相同模板编号与相同内容，互不干扰且无唯一约束冲突。"""
    db_file = tmp_path / "tenant_iso.db"
    st = Storage(db_file)

    p = tmp_path / "MB20251205099_V1.docx"
    _create_template_docx(p, "通用合同", "双方友好协商。")
    doc, _ = import_template_file(p)
    assert doc is not None

    did1, is_new1 = st.upsert_document(doc.to_dict(), tenant="tenant_corp_1")
    assert is_new1 is True

    did2, is_new2 = st.upsert_document(doc.to_dict(), tenant="tenant_corp_2")
    assert is_new2 is True

    # 两个租户各自分隔
    assert st.get_document(did1, tenant_id="tenant_corp_1") is not None
    assert st.get_document(did2, tenant_id="tenant_corp_2") is not None
    assert st.get_document(did1, tenant_id="tenant_corp_2") is None

    st.close()
