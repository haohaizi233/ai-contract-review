# -*- coding: utf-8 -*-
"""合成变异合同样本生成器：基于真实模板文本制造已知错误，每样本附预期错误标签。

用于审核功能测试（test_review_pipeline / test_regression），不替代真实模板解析测试。
"""

from __future__ import annotations

import json
from pathlib import Path

from docx import Document

from . import config

# 基础合同文本（取自真实模板结构，字段留白 → 填入基准值）
BASE = {
    "title": "国内专利服务合同",
    "subtitle": "(专利年费缴纳)",
    "contract_no": "HC2026-0001",
    "party_a": "深圳市某某科技有限公司",
    "preamble": "甲乙双方本着诚信友好、互惠互利的原则，根据《中华人民共和国民法典》等相关法律法规的规定，就乙方为甲方提供国内专利服务事项，达成如下协议：",
    "matter": "乙方接受甲方委托，为甲方提供如下专利年费代缴服务事宜。",
    "fee_clause": "4.1 甲方向乙方支付费用总额为人民币 壹万圆整（¥10,000 元），前述费用不包括后续年限产生的专利年费。具体明细如下：",
    "fee_total": "合计 | 人民币 壹万圆整（¥10,000 元）",
    "payment": "4.2 甲方应自合同生效之日起三个工作日内向乙方支付上述全部费用。",
    "account": "4.3 乙方的账户信息：账户名：示例知识产权代理有限公司 账号：6222000000000000000 开户行：示例银行测试支行。",
    "startup": "4.4 乙方自收到甲方支付的全部费用后启动相关业务的办理（甲方支付的款项实际到达乙方指定账户之日为收到日），另有约定除外。",
    "invoice": "4.5 乙方自收到甲方支付的全部费用后，为甲方出具服务费增值税专用发票（税率6%）并提供官费官方收据。",
    "confidentiality": "第五条 保密条款\n甲乙双方对本合同内容及合同履行过程中知悉双方所有的经营信息、资料等商业秘密负有保密义务，未经对方书面同意，不得自行用于与本合同目的无关的其它事宜或向任何第三方披露、出售、许可使用，但法律要求披露或者通过合法公开途径可获得的除外。",
    "liability": "第六条 违约责任\n6.3 甲方应按本合同约定及时向乙方支付服务费用，逾期支付的甲方应按逾期付款金额日率3‰向乙方支付违约金。",
    "dispute": "第七条 争议解决\n本合同履行过程中产生的一切争议，均应由双方友好协商解决；如协商不成，任何一方均有权向深圳市南山区人民法院提起诉讼。",
    "effect": "第八条 合同生效及其他\n8.1 本合同自双方签字或盖章之日起生效，至上述专利事项办理完结之日终止。",
    "patent_no": "专利号：202410000001.2",
}

MUTANTS = [
    (
        "mut_01_missing_contract_no",
        "删除合同编号",
        ["FIELD_REQUIRED", "CONTRACT_NUMBER_BACKFILL"],
        lambda t: {**t, "contract_no": ""},
    ),
    (
        "mut_02_party_name_change",
        "修改主体名称与附件不一致",
        ["PARTY_ATTACHMENT_CONSISTENCY", "PARTY_MISMATCH"],
        lambda t: {
            **t,
            "party_a": "深圳市某某科技有限公司",
            "matter_extra": "（甲方：广州另一家公司）",
        },
    ),
    (
        "mut_03_service_fee_change",
        "修改服务费与合计不符",
        ["AMOUNT_MISMATCH"],
        lambda t: {
            **t,
            "fee_clause": t["fee_clause"]
            .replace("壹万", "贰万")
            .replace("10,000", "20,000"),
        },
    ),
    (
        "mut_04_official_fee_change",
        "修改官费",
        ["AMOUNT_MISMATCH"],
        lambda t: {
            **t,
            "fee_total": t["fee_total"]
            .replace("壹万", "玖仟")
            .replace("10,000", "9,000"),
        },
    ),
    (
        "mut_05_payment_deadline",
        "修改付款期限",
        ["CLAUSE_VARIATION", "PAYMENT_DEADLINE"],
        lambda t: {**t, "payment": t["payment"].replace("三个工作日", "三十个工作日")},
    ),
    (
        "mut_06_missing_confidentiality",
        "删除保密条款",
        ["CLAUSE_MISSING", "CLAUSE_REQUIRED_CONFIDENTIALITY"],
        lambda t: {**t, "confidentiality": ""},
    ),
    (
        "mut_07_dispute_change",
        "修改争议解决管辖",
        ["CLAUSE_VARIATION", "DISPUTE_JURISDICTION_STANDARD"],
        lambda t: {
            **t,
            "dispute": t["dispute"].replace(
                "深圳市南山区人民法院", "乙方所在地有管辖权的人民法院"
            ),
        },
    ),
    (
        "mut_08_amount_case_mismatch",
        "大小写金额不一致",
        ["AMOUNT_MISMATCH", "AMOUNT_CASE_CONSISTENCY"],
        lambda t: {**t, "fee_clause": t["fee_clause"].replace("壹万", "贰万")},
    ),  # 大写贰万 vs 小写10,000
    (
        "mut_09_patent_no_change",
        "修改专利号",
        ["FIELD_FORMAT", "IDENTIFIER_FORMAT_CHECK"],
        lambda t: {**t, "patent_no": "专利号：XXXX-INVALID"},
    ),
    (
        "mut_10_contradictory_clause",
        "添加互相矛盾条款",
        ["SEMANTIC_RISK", "MANUAL_REVIEW"],
        lambda t: {
            **t,
            "payment_extra": "4.6 甲方无需支付任何费用，本合同为无偿服务。",
        },
    ),
    (
        "mut_11_late_fee_change",
        "修改违约金比率",
        ["CLAUSE_VARIATION", "LATE_FEE_RATE"],
        lambda t: {**t, "liability": t["liability"].replace("3‰", "1‰")},
    ),
    (
        "mut_12_missing_liability",
        "删除违约责任",
        ["CLAUSE_MISSING", "CLAUSE_REQUIRED_LIABILITY"],
        lambda t: {**t, "liability": ""},
    ),
    (
        "mut_13_invoice_variant",
        "发票条款未明示税率",
        ["INVOICE_TERM_VARIANT", "INVOICE_TYPE_CONSISTENCY"],
        lambda t: {
            **t,
            "invoice": "4.5 乙方自收到甲方支付的全部费用后，为甲方出具相应发票。",
        },
    ),
    ("mut_14_no_issue_baseline", "无错误基准合同", ["__NONE__"], lambda t: t),
]


def _build_docx(t: dict, out_path: Path):
    doc = Document()
    doc.add_paragraph(t["title"])
    doc.add_paragraph(t["subtitle"])
    doc.add_paragraph(f"合同编号:{t['contract_no']}")
    tbl = doc.add_table(rows=2, cols=2)
    tbl.rows[0].cells[0].text = "甲方:"
    tbl.rows[0].cells[1].text = t["party_a"]
    tbl.rows[1].cells[0].text = "乙方:"
    tbl.rows[1].cells[1].text = "示例知识产权代理有限公司"
    doc.add_paragraph(t["preamble"])
    doc.add_paragraph("第一条 合同事项")
    doc.add_paragraph(t["matter"] + t.get("matter_extra", ""))
    doc.add_paragraph(f"专利名称：年费代缴 {t['patent_no']}")
    # 费用表
    ft = doc.add_table(rows=3, cols=4)
    for i, h in enumerate(
        ["费用项目", "代收代付官费（元/件）", "含税服务费（元/件）", "数量"]
    ):
        ft.rows[0].cells[i].text = h
    for i, v in enumerate(["年费代缴", "600", "150", "1"]):
        ft.rows[1].cells[i].text = v
    for i, v in enumerate(["小计", "600", "150", "/"]):
        ft.rows[2].cells[i].text = v
    doc.add_paragraph("第四条 费用及支付方式")
    doc.add_paragraph(t["fee_clause"])
    doc.add_paragraph(t["fee_total"])
    doc.add_paragraph(t["payment"])
    doc.add_paragraph(t.get("account", ""))
    doc.add_paragraph(t.get("startup", ""))
    doc.add_paragraph(t["invoice"])
    doc.add_paragraph(t.get("payment_extra", ""))
    if t.get("confidentiality"):
        doc.add_paragraph(t["confidentiality"])
    if t.get("liability"):
        doc.add_paragraph(t["liability"])
    doc.add_paragraph(t["dispute"])
    doc.add_paragraph(t["effect"])
    doc.save(str(out_path))


def generate_all(out_dir: Path | None = None) -> list[dict]:
    config.ensure_dirs()
    out_dir = out_dir or config.GOLDEN_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, desc, expected, mutator in MUTANTS:
        t = mutator(dict(BASE))
        path = out_dir / f"{name}.docx"
        _build_docx(t, path)
        manifest.append(
            {
                "sample_id": name,
                "file": path.name,
                "description": desc,
                "expected_issue_categories": expected,
                "expect_no_issue": expected == ["__NONE__"],
            }
        )
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return manifest


if __name__ == "__main__":
    ms = generate_all()
    print(f"generated {len(ms)} golden samples → {config.GOLDEN_DIR}")
