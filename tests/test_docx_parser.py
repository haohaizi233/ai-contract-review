# -*- coding: utf-8 -*-
"""test_docx_parser / test_table_parser / test_source_traceability（合成语料）"""

import re

import pytest


def test_corpus_parsed(parsed_docs, import_result):
    assert len(parsed_docs) >= 6
    assert len(import_result["imported"]) == len(parsed_docs)
    assert not import_result["failures"]


def test_every_doc_has_unique_hash(import_result):
    hashes = [d["sha256"] for d in import_result["imported"]]
    assert len(set(hashes)) == len(hashes)
    assert all(len(h) == 64 for h in hashes)


def test_paragraph_and_table_counts_positive(parsed_docs):
    for name, doc in parsed_docs.items():
        assert doc.paragraph_count > 30, name
        assert doc.table_count >= 3, name


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
    assert any_merge, "合成语料含合并单元格，解析必须记录"


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
    assert total_blank > 10, "模板填写位识别过少"


def test_unhandled_structures_recorded_not_dropped(parsed_docs):
    """无法可靠解析的结构必须记录状态。"""
    for name, doc in parsed_docs.items():
        if doc.has_images:
            assert any(
                "图片" in w or "images" in w.lower() for w in doc.parse_warnings
            ), name


def test_contract_number_hint_captured(parsed_docs):
    with_hint = [d for d in parsed_docs.values() if d.contract_number_hint]
    assert len(with_hint) == len(parsed_docs)


def test_headers_footers_extraction(parsed_docs):
    found = 0
    for doc in parsed_docs.values():
        for h in doc.headers + doc.footers:
            assert "part" in h and "text" in h
            found += 1
    assert found > 0, "合成语料含页眉页脚，必须抽取"


def test_styles_captured(parsed_docs):
    styled = 0
    bold_runs = 0
    for doc in parsed_docs.values():
        doc_styled = 0
        for n in doc.nodes:
            if n.node_type == "paragraph" and n.style:
                styled += 1
                doc_styled += 1
                for r in n.style.get("runs", []):
                    if r.get("bold"):
                        bold_runs += 1
        assert doc_styled > 0, "每份文档必须记录到段落样式"
    assert styled > 2 * len(parsed_docs)
    assert bold_runs > 0, "标题加粗样式必须被记录"


def test_order_table_interleaving(parsed_docs):
    """表格与正文交错：表格节点之后必须还能出现段落节点。"""
    for name, doc in parsed_docs.items():
        seen_tbl = False
        after = False
        for n in doc.nodes:
            if n.node_type == "table":
                seen_tbl = True
            elif seen_tbl and n.node_type == "paragraph" and n.text.strip():
                after = True
        assert seen_tbl and after, name


# ---------------- 真实语料授权回归（默认跳过） ----------------


@pytest.mark.real_corpus
def test_real_corpus_26_templates(real_corpus_dir):
    from contract_kb.docx_parser import parse_docx

    files = [
        f
        for f in real_corpus_dir.iterdir()
        if f.suffix.lower() == ".docx" and not f.name.startswith("~")
    ]
    assert len(files) == 26
    for f in files:
        doc = parse_docx(f)
        assert doc.paragraph_count > 30, f.name
        assert doc.table_count >= 1, f.name


@pytest.mark.real_corpus
def test_real_corpus_unique_hashes(real_corpus_dir):
    from contract_kb.docx_parser import sha256_file

    files = [
        f
        for f in real_corpus_dir.iterdir()
        if f.suffix.lower() == ".docx" and not f.name.startswith("~")
    ]
    hashes = [sha256_file(f) for f in files]
    assert len(set(hashes)) == 26
