# -*- coding: utf-8 -*-
"""test_docx_parser / test_table_parser / test_source_traceability（真实模板）"""
import re

import pytest


def test_all_26_templates_parsed(parsed_docs, import_result):
    assert import_result["file_count"] == 26
    assert len(import_result["imported"]) == 26
    assert len(parsed_docs) == 26


def test_every_doc_has_unique_hash(import_result):
    hashes = [d["sha256"] for d in import_result["imported"]]
    assert len(set(hashes)) == 26
    assert all(len(h) == 64 for h in hashes)


def test_paragraph_and_table_counts_positive(parsed_docs):
    for name, doc in parsed_docs.items():
        assert doc.paragraph_count > 30, name
        assert doc.table_count >= 1, name


def test_body_order_preserved(parsed_docs):
    """节点 order 必须严格递增（正文/表格交错顺序）。"""
    for name, doc in parsed_docs.items():
        orders = [n.order for n in doc.nodes]
        assert orders == sorted(orders), name
        assert len(set(orders)) == len(orders), name


def test_tables_have_coordinates(parsed_docs):
    for name, doc in parsed_docs.items():
        cells = [n for n in doc.nodes if n.node_type == "table_cell"]
        assert cells, name
        for c in cells:
            assert c.table_index is not None
            assert c.row_index is not None
            assert c.column_index is not None


def test_merged_cells_recorded(parsed_docs):
    """合并单元格信息（grid_span/v_merge）必须被记录而不是丢弃。"""
    any_merge = False
    for doc in parsed_docs.values():
        for n in doc.nodes:
            if n.node_type == "table_cell":
                if n.flags.get("grid_span", 1) > 1 or n.flags.get("v_merge"):
                    any_merge = True
    assert any_merge, "26 份模板中未发现任何合并单元格——疑似解析遗漏"


def test_section_and_clause_numbers(parsed_docs):
    for name, doc in parsed_docs.items():
        sections = [n for n in doc.nodes if n.section and n.node_type == "paragraph"]
        assert sections, name
        nums = [n.clause_number for n in doc.nodes if n.clause_number]
        assert nums, name
        for num in nums:
            assert re.match(r"^\d+\.\d+$", num), (name, num)


def test_no_fabricated_page_numbers(parsed_docs):
    """页码不允许伪造：所有节点 page_number 必须为 None。"""
    for name, doc in parsed_docs.items():
        assert doc.page_mapping_available is False, name
        for n in doc.nodes:
            assert n.source_locator.get("page_number") is None, name


def test_source_locator_present(parsed_docs):
    for doc in parsed_docs.values():
        for n in doc.nodes:
            assert "package_part" in n.source_locator
            assert "xml_tag" in n.source_locator


def test_blank_placeholders_and_checkboxes(parsed_docs):
    total_blank = sum(d.blank_placeholder_count for d in parsed_docs.values())
    total_cb = sum(d.checkbox_count for d in parsed_docs.values())
    assert total_blank > 50, "模板填写位识别过少"
    assert total_cb > 5, "复选框符号识别过少"


def test_unhandled_structures_recorded_not_dropped(parsed_docs):
    """无法可靠解析的结构必须记录状态。"""
    for name, doc in parsed_docs.items():
        if doc.has_images:
            assert any("图片" in w or "images" in w.lower() for w in doc.parse_warnings), name


def test_contract_number_hint_captured(parsed_docs):
    with_hint = [d for d in parsed_docs.values() if d.contract_number_hint]
    assert len(with_hint) >= 20


def test_headers_footers_extraction(parsed_docs):
    # 页眉页脚存在则必须带 part 定位
    for doc in parsed_docs.values():
        for h in doc.headers + doc.footers:
            assert "part" in h and "text" in h


def test_styles_captured(parsed_docs):
    styled = 0
    for doc in parsed_docs.values():
        for n in doc.nodes:
            if n.node_type == "paragraph" and n.style:
                styled += 1
    assert styled > 100
