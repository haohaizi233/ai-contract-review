# -*- coding: utf-8 -*-
"""Phase 13R1: 表格重建结构保真与边界对抗测试集。

覆盖：
1. 15行大费用表格：不得丢行
2. 合同出现两个不同费用表：不得错误合并
3. 双方主体表与专利清单表并存
4. 母本单元格为空，送审稿有值
5. 母本有表格，送审稿整表删除
6. 送审稿单方新增表格
7. 跨列合并单元格（grid_span）
"""

from contract_kb.domain.models.source_node import ContractSourceNode
from contract_kb.modules.contract_comparison.table_aligner import (
    ExtractedTable,
    align_contract_tables,
    build_table_diff_block,
)


def _make_table(
    table_index: int,
    doc_id: str,
    headers: list[str],
    rows_data: list[list[str]],
    grid_spans: dict[tuple[int, int], int] | None = None,
) -> ExtractedTable:
    nodes: list[ContractSourceNode] = []
    # Header cells
    for c_idx, h in enumerate(headers):
        nodes.append(
            ContractSourceNode(
                document_id=doc_id,
                document_hash="hash_table_test",
                node_id=f"node_{doc_id}_{table_index}_0_{c_idx}",
                node_type="table_cell",
                table_index=table_index,
                row_index=0,
                column_index=c_idx,
                original_text=h,
            )
        )
    # Body cells
    for r_idx, r_data in enumerate(rows_data, 1):
        for c_idx, val in enumerate(r_data):
            span = grid_spans.get((r_idx, c_idx), 1) if grid_spans else 1
            nodes.append(
                ContractSourceNode(
                    document_id=doc_id,
                    document_hash="hash_table_test",
                    node_id=f"node_{doc_id}_{table_index}_{r_idx}_{c_idx}",
                    node_type="table_cell",
                    table_index=table_index,
                    row_index=r_idx,
                    column_index=c_idx,
                    grid_span=span,
                    original_text=val,
                )
            )
    return ExtractedTable(
        table_index=table_index,
        document_id=doc_id,
        document_hash="hash_table_test",
        nodes=nodes,
    )


def test_15_row_fee_table_no_loss() -> None:
    """1. 15 行费用表不得丢行。"""
    headers = ["序号", "服务项目", "官费金额（元）"]
    rows_std = [[str(i), f"常规年费代缴_{i}", str(100 + i * 10)] for i in range(1, 16)]
    rows_rev = [[str(i), f"特批加急代缴_{i}", str(120 + i * 10)] for i in range(1, 16)]

    std_t = _make_table(0, "std_doc", headers, rows_std)
    rev_t = _make_table(0, "rev_doc", headers, rows_rev)

    aligned_pairs = align_contract_tables([std_t], [rev_t])
    assert len(aligned_pairs) == 1
    pair = aligned_pairs[0]
    assert pair["status"] == "ALIGNED"
    assert pair["confidence"] > 0.8
    assert pair["rev_table"].row_count == 16  # 1 header + 15 data rows

    block, anns, _ = build_table_diff_block(pair, block_order=0, ann_counter_start=0)
    assert block["status"] == "DIFF"
    # 验证 HTML 渲染包含全部 15 行标识
    for i in range(1, 16):
        assert f"特批加急代缴_{i}" in block["reviewed_html"]
        assert f"常规年费代缴_{i}" in block["standard_html"]


def test_multiple_fee_tables_not_merged() -> None:
    """2. 一份合同出现两个不同费用表不得合并。"""
    t1_std = _make_table(
        0, "std_doc", ["序号", "国内官费项目", "金额"], [["1", "申请费", "900"]]
    )
    t2_std = _make_table(
        1, "std_doc", ["序号", "国际涉外代理项目", "金额"], [["1", "PCT初审", "5000"]]
    )

    t1_rev = _make_table(
        0, "rev_doc", ["序号", "国内官费项目", "金额"], [["1", "申请费", "950"]]
    )
    t2_rev = _make_table(
        1, "rev_doc", ["序号", "国际涉外代理项目", "金额"], [["1", "PCT初审", "5500"]]
    )

    aligned_pairs = align_contract_tables([t1_std, t2_std], [t1_rev, t2_rev])
    assert len(aligned_pairs) == 2
    assert aligned_pairs[0]["std_table"].table_index == 0
    assert aligned_pairs[0]["rev_table"].table_index == 0
    assert aligned_pairs[1]["std_table"].table_index == 1
    assert aligned_pairs[1]["rev_table"].table_index == 1


def test_parties_table_and_patent_list_table_both_handled() -> None:
    """3. 一个合同同时包含双方主体表和专利清单表。"""
    t_party_std = _make_table(
        0, "std_doc", ["签约主体", "企业名称"], [["甲方", "甲公司"], ["乙方", "乙公司"]]
    )
    t_patent_std = _make_table(
        1,
        "std_doc",
        ["专利序号", "发明名称", "申请号"],
        [["1", "量子芯片", "20261001"]],
    )

    t_party_rev = _make_table(
        0,
        "rev_doc",
        ["签约主体", "企业名称"],
        [["甲方", "新甲公司"], ["乙方", "乙公司"]],
    )
    t_patent_rev = _make_table(
        1,
        "rev_doc",
        ["专利序号", "发明名称", "申请号"],
        [["1", "量子芯片", "20261001"]],
    )

    aligned_pairs = align_contract_tables(
        [t_party_std, t_patent_std], [t_party_rev, t_patent_rev]
    )
    assert len(aligned_pairs) == 2
    block0, _, _ = build_table_diff_block(aligned_pairs[0], 0, 0)
    block1, _, _ = build_table_diff_block(aligned_pairs[1], 1, 0)
    assert block0["clause_category"] == "PARTIES"
    assert block1["clause_category"] == "PATENT_LIST"


def test_cell_empty_in_standard_has_value_in_reviewed() -> None:
    """4. 母本单元格为空，送审单元格有值。"""
    std_t = _make_table(0, "std_doc", ["项目", "特殊履约要求"], [["年费代缴", ""]])
    rev_t = _make_table(
        0, "rev_doc", ["项目", "特殊履约要求"], [["年费代缴", "若逾期需三倍赔偿"]]
    )

    aligned_pairs = align_contract_tables([std_t], [rev_t])
    block, anns, _ = build_table_diff_block(aligned_pairs[0], 0, 0)

    assert block["status"] == "DIFF"
    assert "若逾期需三倍赔偿" in block["reviewed_html"]
    assert len(anns) > 0


def test_standard_table_deleted_in_reviewed() -> None:
    """5. 母本有表格，送审稿整表删除。"""
    std_t = _make_table(
        0, "std_doc", ["序号", "保密技术秘密清单"], [["1", "底层源码架构"]]
    )
    rev_tables: list[ExtractedTable] = []

    aligned_pairs = align_contract_tables([std_t], rev_tables)

    assert len(aligned_pairs) == 1
    assert aligned_pairs[0]["status"] == "MISSING_IN_REVIEWED"

    block, anns, _ = build_table_diff_block(aligned_pairs[0], 0, 0)
    assert block["status"] == "MISSING_IN_REVIEWED"
    assert "送审稿已整表删除该表格" in block["reviewed_html"]
    assert any(a["risk_level"] == "HIGH" for a in anns)


def test_new_table_added_in_reviewed() -> None:
    """6. 送审稿单方新增表格。"""
    std_tables: list[ExtractedTable] = []
    rev_t = _make_table(
        0,
        "rev_doc",
        ["附表序号", "单方免责条款"],
        [["1", "非不可抗力亦免除乙方一切责任"]],
    )

    aligned_pairs = align_contract_tables(std_tables, [rev_t])

    assert len(aligned_pairs) == 1
    assert aligned_pairs[0]["status"] == "NEW_IN_REVIEWED"

    block, anns, _ = build_table_diff_block(aligned_pairs[0], 0, 0)
    assert block["status"] == "NEW_IN_REVIEWED"
    assert "标准母本无此表格" in block["standard_html"]
    assert "单方免责条款" in block["reviewed_html"]


def test_merged_cells_grid_span_preservation() -> None:
    """7. 跨列合并单元格（grid_span）属性保留。"""
    std_t = _make_table(
        0,
        "std_doc",
        ["序号", "项目", "金额"],
        [["1", "服务费", "1000"], ["合计：人民币壹仟元整", "", ""]],
        grid_spans={(2, 0): 3},
    )
    aligned_pairs = align_contract_tables([std_t], [std_t])
    block, anns, _ = build_table_diff_block(aligned_pairs[0], 0, 0)
    assert block["status"] == "MATCH"
    assert 'colspan="3"' in block["reviewed_html"]
    assert 'colspan="3"' in block["standard_html"]
