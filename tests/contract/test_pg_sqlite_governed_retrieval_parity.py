# -*- coding: utf-8 -*-
"""Phase 14-R G4/DEFECT-P0-05: SQLite 与 PostgreSQL 知识类型与受控检索一致性测试。"""

from __future__ import annotations


from contract_kb.modules.retrieval.governance import (
    KnowledgeGovernancePolicy,
    KnowledgeSourceType,
)


def test_reproduce_defect_layer_a_raw_clause_dropped_by_governed_retrieval():
    """复现缺陷：PostgreSQL 默认将普通条款标记为 LAYER_A_RAW_CLAUSE，导致受控检索按 TEMPLATE 过滤时全部被丢弃！"""
    # 模拟 PostgreSQL 中插入的未经规范化类型条款（LAYER_A_RAW_CLAUSE）
    raw_hit = {
        "clause_id": "c1",
        "document_id": "d1",
        "original_text": "甲方应当在三日内付款",
        "approval_status": "APPROVED",
        "doc_status": "ACTIVE",
        "tenant_id": "default",
        "knowledge_type": "LAYER_A_RAW_CLAUSE",  # 历史 PG 默认值
    }

    # 修复后验证：LAYER_A_RAW_CLAUSE 已被治理策略规范化映射为 TEMPLATE
    policy = KnowledgeGovernancePolicy()
    desc = policy.descriptor_for("LAYER_A_RAW_CLAUSE")
    assert desc is not None, "LAYER_A_RAW_CLAUSE 必须能够解析为有效知识描述符"
    assert desc.source_type == KnowledgeSourceType.TEMPLATE, (
        "验证通过：LAYER_A_RAW_CLAUSE 成功对齐 TEMPLATE，保障 PG 与 SQLite 检索契约一致"
    )

    annotated = policy.annotate_hit(raw_hit)
    assert annotated["knowledge_type"] == KnowledgeSourceType.TEMPLATE.value
    assert annotated["can_drive_mandatory_finding"] is True
