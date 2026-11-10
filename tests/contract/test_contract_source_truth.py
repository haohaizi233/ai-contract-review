# -*- coding: utf-8 -*-
"""Phase 13R1: 合同原文真实性与无硬编码演示数据验证。

测试 Case A: 官费 913 元、服务费 287 元，不得被替换为 600/150 等示例数据。
测试 Case B: 真实当事人（如“北京量子跃迁智能科技有限公司”、“上海领航航天科技合伙企业”），
              绝不被替换为“深圳市某某科技有限公司”或“深圳市世纪恒程...”等硬编码演示数据。
"""

import tempfile
from pathlib import Path
from docx import Document

from contract_kb.storage import Storage
from contract_kb.synthetic import generate_corpus
from contract_kb.ingest import scan_and_import
from contract_kb.full_diff import build_full_contract_diff
from contract_kb.domain.models.source_node import ContractSourceNode


def create_test_contract_docx(
    file_path: Path,
    party_a: str,
    party_b: str,
    official_fee: str,
    service_fee: str,
    patent_name: str = "一种基于量子纠缠的测距装置",
    patent_no: str = "ZL202610998877.6",
) -> Path:
    """动态生成包含特定当事人、专利及费用表格的合法 DOCX 合同。"""
    doc = Document()
    doc.add_heading("国内专利服务合同", level=1)
    doc.add_paragraph("(年费缴纳)")
    doc.add_paragraph("合同编号：TEST-2026-X99")
    doc.add_paragraph(f"甲方（委托方）：{party_a}")
    doc.add_paragraph(f"乙方（受托方）：{party_b}")
    doc.add_paragraph(
        "甲乙双方本着诚信友好、互惠互利的原则，根据《中华人民共和国民法典》等相关法律法规的规定，就乙方为甲方提供国内专利服务事项，达成如下协议："
    )
    doc.add_paragraph("第一条 合同事项")
    doc.add_paragraph("乙方接受甲方委托，为甲方提供如下专利年费代缴服务事宜：")

    # 事项表格
    t1 = doc.add_table(rows=2, cols=5)
    headers = ["序号", "专利名称", "专利号", "专利权人名称", "年费期限"]
    for i, h in enumerate(headers):
        t1.cell(0, i).text = h
    row1 = ["1", patent_name, patent_no, party_a, "第3年专利年费"]
    for i, val in enumerate(row1):
        t1.cell(1, i).text = val

    doc.add_paragraph("第四条 费用及支付方式")
    doc.add_paragraph("4.1 费用明细如下表格所示：")

    # 费用表格 (包含 Case A 关键数值)
    t2 = doc.add_table(rows=2, cols=4)
    fee_headers = ["序号", "服务项目", "官费（元）", "代理服务费（元）"]
    for i, h in enumerate(fee_headers):
        t2.cell(0, i).text = h
    fee_row = ["1", "第3年专利年费代缴", official_fee, service_fee]
    for i, val in enumerate(fee_row):
        t2.cell(1, i).text = val

    doc.add_paragraph("第八条 合同生效及其他")
    doc.add_paragraph("8.1 本合同自双方签字或盖章之日起生效。")
    doc.save(str(file_path))
    return file_path


def test_contract_source_truth_case_a_amounts_preserved() -> None:
    """Case A 验证：官费 913 元、服务费 287 元，不得被替换为 600/150。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        corpus_dir = tmp_path / "corpus"
        generate_corpus(corpus_dir)

        db_path = tmp_path / "test.db"
        st = Storage(db_path)
        try:
            import_res = scan_and_import(corpus_dir)
            for d in import_res.get("_raw", []):
                st.upsert_document(d.to_dict(), tenant="t1")

            test_docx = tmp_path / "case_a_amounts.docx"
            create_test_contract_docx(
                test_docx,
                party_a="极光动力创新系统（北京）有限公司",
                party_b="天元律政知识产权代理有限责任公司",
                official_fee="913",
                service_fee="287",
            )

            res = build_full_contract_diff(test_docx, storage=st)
            assert res is not None

            # 检查所有 aligned_blocks 与 annotations 中绝不出现假数据 600 / 150
            all_blocks_text = " ".join(
                (
                    b.get("standard_text", "")
                    + " "
                    + b.get("reviewed_text", "")
                    + " "
                    + b.get("reviewed_html", "")
                )
                for b in res["aligned_blocks"]
            )
            all_annotations_text = " ".join(
                (
                    a.get("quote_reviewed", "")
                    + " "
                    + a.get("ai_analysis", "")
                    + " "
                    + a.get("action_advice", "")
                )
                for a in res["annotations"]
            )
            combined_text = all_blocks_text + " " + all_annotations_text

            # 确认 913 与 287 真实存在
            assert "913" in combined_text, "送审真实官费 913 元必须被忠实保留与渲染"
            assert "287" in combined_text, "送审真实服务费 287 元必须被忠实保留与渲染"

            # 确认绝无旧版写死的演示金额 600 / 150
            assert "600元" not in combined_text
            assert "150元" not in combined_text
        finally:
            st.close()


def test_contract_source_truth_case_b_parties_preserved() -> None:
    """Case B 验证：真实当事人绝不被替换为任何写死的演示企业名称。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        corpus_dir = tmp_path / "corpus"
        generate_corpus(corpus_dir)

        db_path = tmp_path / "test.db"
        st = Storage(db_path)
        try:
            import_res = scan_and_import(corpus_dir)
            for d in import_res.get("_raw", []):
                st.upsert_document(d.to_dict(), tenant="t1")

            party_a_real = "北京量子跃迁智能科技有限公司"
            party_b_real = "上海领航航天科技合伙企业"
            test_docx = tmp_path / "case_b_parties.docx"
            create_test_contract_docx(
                test_docx,
                party_a=party_a_real,
                party_b=party_b_real,
                official_fee="1200",
                service_fee="450",
            )

            res = build_full_contract_diff(test_docx, storage=st)
            assert res is not None

            all_rendered = " ".join(
                b.get("reviewed_text", "") + " " + b.get("reviewed_html", "")
                for b in res["aligned_blocks"]
            )

            # 必须包含送审真实企业
            assert party_a_real in all_rendered, (
                f"真实甲方 [{party_a_real}] 必须被完整保留"
            )

            # 严禁出现任何写死的历史演示企业
            banned_names = [
                "深圳市某某科技有限公司",
                "深圳市世纪恒程",
                "世纪恒程创新代理",
            ]
            for banned in banned_names:
                assert banned not in all_rendered, (
                    f"渲染结果中检测到硬编码演示企业名称: {banned}"
                )
        finally:
            st.close()


def test_contract_source_node_model() -> None:
    """验证 ContractSourceNode 结构保真映射能力与哈希指纹。"""
    node = ContractSourceNode(
        document_id="doc_test_001",
        document_hash="a1b2c3d4e5f60718",
        document_version="v1.0",
        node_id="node_tbl_01_r0_c1",
        node_type="table_cell",
        table_index=0,
        row_index=0,
        column_index=1,
        original_text="代缴年费 913 元",
        extraction_method="docx_table_grid",
    )
    d = node.to_dict()
    assert d["document_id"] == "doc_test_001"
    assert d["table_index"] == 0
    assert d["column_index"] == 1
    assert d["original_text"] == "代缴年费 913 元"
    assert node.node_hash is not None and len(node.node_hash) == 16
