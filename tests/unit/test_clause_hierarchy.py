# -*- coding: utf-8 -*-
"""Unit tests for Hierarchical Knowledge Tree & Parent-Child Retrieval (Phase 12-C)."""

from __future__ import annotations


from contract_kb.modules.retrieval.hierarchy import (
    ChunkGranularity,
    HierarchicalKnowledgeTree,
)


def test_build_hierarchical_knowledge_tree():
    """测试多粒度知识树构建（DOCUMENT -> ARTICLE -> SUBCLAUSE -> TABLE）。"""
    clauses = [
        {
            "clause_id": "c_5_1",
            "clause_number": "5.1",
            "article_title": "第五条 费用结算",
            "clause_category": "FEE_AND_PAYMENT",
            "original_text": "甲方应在合同生效后5个工作日内支付首期费用50000元。",
            "conditions": ["合同生效后"],
            "source_node_ids": ["n501"],
        },
        {
            "clause_id": "c_5_2",
            "clause_number": "5.2",
            "article_title": "第五条 费用结算",
            "clause_category": "FEE_AND_PAYMENT",
            "original_text": "尾款支付流程依照第十二条执行。",
            "source_node_ids": ["n502"],
        },
        {
            "clause_id": "c_12_1",
            "clause_number": "12.1",
            "article_title": "第十二条 验收与尾款",
            "clause_category": "FEE_AND_PAYMENT",
            "original_text": "成果验收合格后10个工作日内结清全部尾款。",
            "source_node_ids": ["n1201"],
        },
    ]
    tables = [
        {"headers": ["费用项目", "金额", "结算方式"], "row_count": 3}
    ]

    tree = HierarchicalKnowledgeTree.build_from_clauses(
        document_id="doc_test_123",
        clauses=clauses,
        table_schemas=tables,
    )

    # 1. 验证 Document 根节点
    root = tree.get_node(tree.root_id)
    assert root is not None
    assert root.granularity == ChunkGranularity.DOCUMENT.value

    # 2. 验证 Article 节点
    art5 = tree.find_by_clause_reference("第五条")
    assert art5 is not None
    assert art5.granularity == ChunkGranularity.ARTICLE.value
    assert len(art5.child_ids) == 2
    assert "c_5_1" in art5.child_ids and "c_5_2" in art5.child_ids

    # 3. 验证 Subclause 与链表连接 (previous/next)
    sub51 = tree.get_node("c_5_1")
    assert sub51 is not None
    assert sub51.parent_id == art5.node_id
    assert sub51.next_id == "c_5_2"

    sub52 = tree.get_node("c_5_2")
    assert sub52 is not None
    assert sub52.previous_id == "c_5_1"

    # 4. 验证 Table 节点挂载在根节点下
    table_nodes = [n for n in tree.nodes.values() if n.granularity == ChunkGranularity.TABLE.value]
    assert len(table_nodes) == 1
    assert "费用项目" in table_nodes[0].text


def test_parent_child_context_expansion():
    """测试 Parent-Child 关联扩展：命中小条款后向上溯源父章节，向外扩展跨条引用。"""
    clauses = [
        {
            "clause_id": "c_8_1",
            "clause_number": "8.1",
            "article_title": "第八条 违约赔偿责任",
            "clause_category": "LIABILITY",
            "original_text": "在乙方没有重大过失的情况下，违约金最高赔偿上限依照第十二条执行。",
            "conditions": ["乙方没有重大过失"],
            "source_node_ids": ["n801"],
        },
        {
            "clause_id": "c_12_1",
            "clause_number": "12.1",
            "article_title": "第十二条 责任免除与限制",
            "clause_category": "LIABILITY",
            "original_text": "双方违约赔偿总额以本合同标的总金额的20%为限。",
            "source_node_ids": ["n1201"],
        },
    ]

    tree = HierarchicalKnowledgeTree.build_from_clauses("doc_parent_child", clauses)

    expanded = tree.expand_parent_child_context("c_8_1")
    assert expanded["target_node"] is not None
    assert expanded["parent_article"] is not None
    assert "第八条" in expanded["parent_article"].title

    # 验证关联出了引用的第十二条
    ref_nodes = expanded["referenced_nodes"]
    assert len(ref_nodes) >= 1
    assert "第十二条" in ref_nodes[0].title or "12.1" in ref_nodes[0].clause_number

    # 验证上下文文本包含所属章节与援引条目
    ctx_text = expanded["expanded_context_text"]
    assert "【所属章节】第八条" in ctx_text
    assert "【条款正文】" in ctx_text
    assert "【援引外部条款】" in ctx_text
