# -*- coding: utf-8 -*-
"""回归测试：同哈希模板存量分类元数据刷新与检索同步。"""

from __future__ import annotations

from pathlib import Path

from contract_kb.storage import Storage


def test_same_hash_classification_refresh_and_search_sync(tmp_path: Path):
    """
    验证：
    1. 源文件哈希不变的情况下，分类更新可被存量刷新机制落库；
    2. 文档表与检索索引（embeddings 表）中的分类元数据原子更新；
    3. 按旧分类检索不再命中，按新分类检索能够成功召回；
    4. 分类刷新不改变已有审批状态（APPROVED 仍然是 APPROVED）；
    5. 重复刷新保持幂等；
    6. 其他租户不受影响；
    7. 若存在人工复核覆盖（human override），自动刷新不覆盖人工决策。
    """
    db_file = tmp_path / "reclassify_test.db"
    st = Storage(db_file)
    tenant = "tenant_reclass"
    other_tenant = "tenant_other"

    file_hash = "hash_patent_infringement_analysis_001"
    doc_initial = {
        "template_id": "MB20251205016",
        "source_file": "MB20251205016_侵权分析.docx",
        "sha256": file_hash,
        "category": "OLD_WRONG_CATEGORY",
        "subtype": "OLD_WRONG_SUBTYPE",
        "clauses": [
            {
                "clause_id": "c_patent_infringe_01",
                "clause_number": "1.1",
                "clause_category": "SERVICE",
                "original_text": "乙方应为甲方提供专利侵权风险深度分析报告。",
                "normalized_summary": "侵权分析报告交付要求",
                "is_company_standard": True,
            }
        ],
    }

    # 1. 初始导入并审批
    did, is_new = st.upsert_document(doc_initial, tenant=tenant)
    assert is_new is True
    st.approve_knowledge_version(
        tenant_id=tenant,
        document_id=did,
        approver_id="legal_officer",
        approval_reason="初始法务审批",
    )

    # 在另一个租户导入同类文档
    did_other, _ = st.upsert_document(
        dict(doc_initial, template_id="MB_OTHER_01", category="OTHER_TENANT_CAT"),
        tenant=other_tenant,
    )

    # 检查初始检索：按旧分类检索可命中，按新分类检索未命中
    active_old = st.find_active_template(
        business_category="OLD_WRONG_CATEGORY", tenant_id=tenant
    )
    assert active_old is not None and active_old["document_id"] == did

    active_new = st.find_active_template(
        business_category="INTELLECTUAL_PROPERTY", tenant_id=tenant
    )
    assert active_new is None

    # 2. 尝试用新分类重新导入同哈希文档
    doc_updated = dict(
        doc_initial,
        category="INTELLECTUAL_PROPERTY",
        subtype="PATENT_ANALYSIS",
    )
    # 当前未修复的代码中，upsert_document 会因 source_hash 相同直接跳过，分类无法更新
    # 修复后应通过 refresh 机制或带 refresh 的入口更新分类
    refreshed = st.update_document_classification(
        document_id=did,
        category="INTELLECTUAL_PROPERTY",
        subtype="PATENT_ANALYSIS",
        tenant_id=tenant,
    )
    assert refreshed is True, "分类刷新必须执行成功"

    # 3. 验证 documents 表与 embeddings 索引同步更新
    updated_doc = st.get_document(did, tenant_id=tenant)
    assert updated_doc["business_category"] == "INTELLECTUAL_PROPERTY"
    assert updated_doc["contract_subtype"] == "PATENT_ANALYSIS"

    # 检索同步：新分类检索立即命中，旧分类不再命中
    hit_new = st.find_active_template(
        business_category="INTELLECTUAL_PROPERTY", tenant_id=tenant
    )
    assert hit_new is not None and hit_new["document_id"] == did
    hit_old = st.find_active_template(
        business_category="OLD_WRONG_CATEGORY", tenant_id=tenant
    )
    assert hit_old is None

    # 4. 审批状态未被篡改
    clauses = st.get_document_clauses(did, tenant_id=tenant)
    assert clauses[0]["approval_status"] == "APPROVED"

    # 5. 重复刷新幂等
    refreshed_again = st.update_document_classification(
        document_id=did,
        category="INTELLECTUAL_PROPERTY",
        subtype="PATENT_ANALYSIS",
        tenant_id=tenant,
    )
    assert refreshed_again is False, "重复刷新相同分类应幂等跳过"

    # 6. 其他租户不受影响
    other_doc = st.get_document(did_other, tenant_id=other_tenant)
    assert other_doc["business_category"] == "OTHER_TENANT_CAT"

    # 7. 人工分类覆写保护 (Human Override)
    st.conn.execute(
        "UPDATE documents SET raw_meta=? WHERE document_id=?",
        ('{"manual_classification_override": true}', did),
    )
    st._commit()
    refreshed_override = st.update_document_classification(
        document_id=did,
        category="SHOULD_NOT_OVERRIDE",
        subtype="SHOULD_NOT_OVERRIDE",
        tenant_id=tenant,
        force=False,
    )
    assert refreshed_override is False, "自动刷新不能覆盖人工复核决定的分类"
    protected_doc = st.get_document(did, tenant_id=tenant)
    assert protected_doc["business_category"] == "INTELLECTUAL_PROPERTY"

    # 8. upsert_document 携带 refresh_classification 刷新
    doc_new_sub = dict(
        doc_updated,
        category="INTELLECTUAL_PROPERTY",
        subtype="PATENT_FRESH_SUBTYPE",
    )
    # 取消 override 标记以便测试
    st.conn.execute(
        "UPDATE documents SET raw_meta='{}' WHERE document_id=?",
        (did,),
    )
    st._commit()
    _, is_new_ref = st.upsert_document(
        doc_new_sub, tenant=tenant, refresh_classification=True
    )
    assert is_new_ref is False
    doc_after_upsert_ref = st.get_document(did, tenant_id=tenant)
    assert doc_after_upsert_ref["contract_subtype"] == "PATENT_FRESH_SUBTYPE"

    st.close()
