# -*- coding: utf-8 -*-
"""合成合同样本生成器（Phase 0 安全治理）：测试/CI 专用，不含任何真实企业信息。

- 生成结构完整的合成 DOCX 模板（段落/表格/合并单元格/空白占位/复选框/样式）
- 甲方/乙方/账户/邮箱/人名全部为合成占位（示例公司、example.com、6222…0000）
- 与真实模板同构：8 条制骨架 + 事项表格 + 费用表
- 供 pytest 与 GitHub Actions 使用；真实模板回归走 real_corpus 授权路径
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

# 全部合成信息（严禁替换为真实企业数据）
SYNTH_COMPANY_B = "示例知识产权代理有限公司"
SYNTH_COMPANY_A = "示例科技（测试）有限公司"
SYNTH_ACCOUNT = "6222000000000000000"
SYNTH_BANK = "示例银行测试支行"
SYNTH_EMAIL_SERVICE = "service@example.com"
SYNTH_EMAIL_COMPLAINT = "complaint@example.com"
SYNTH_PHONE = "13800000000"
SYNTH_REP = "张某某"

# 合成模板目录：(文件名, 标题, 副标题, L1, L2, 事项表格列, 事项行样例)
SYNTH_TEMPLATES = [
    (
        "synth_patent_application.docx",
        "合成国内专利服务合同",
        "(申请)",
        "PATENT",
        "PATENT_APPLICATION",
        ["序号", "发明名称/申请号/专利号", "申请人名称", "发明人名称", "申请类型"],
        ["1", "SYN-2026-0001", SYNTH_COMPANY_A, "王测试", "发明"],
        "乙方接受甲方委托，为甲方提供如下专利申请服务事宜：",
    ),
    (
        "synth_patent_annual_fee.docx",
        "合成国内专利服务合同",
        "(年费缴纳)",
        "PATENT",
        "PATENT_ANNUAL_FEE",
        ["序号", "专利名称", "专利号", "专利权人名称", "年费期限"],
        ["1", "测试专利", "SYN-2024-0042", SYNTH_COMPANY_A, "第3年专利年费"],
        "乙方接受甲方委托，为甲方提供如下专利年费代缴服务事宜：",
    ),
    (
        "synth_patent_transfer.docx",
        "合成国内专利服务合同",
        "(转让)",
        "PATENT",
        "PATENT_TRANSFER",
        ["序号", "专利名称", "专利号", "转让人名称", "受让人名称"],
        ["1", "测试专利", "SYN-2023-0007", SYNTH_COMPANY_A, "示例受让公司"],
        "乙方接受甲方委托，为甲方提供如下专利转让事宜：",
    ),
    (
        "synth_trademark_registration.docx",
        "合成国内商标服务合同",
        "(注册)",
        "TRADEMARK",
        "TRADEMARK_REGISTRATION",
        ["序号", "商标图样/文字", "类别", "申请人名称", "备注"],
        ["1", "测试商标", "第9类", SYNTH_COMPANY_A, "合成样例"],
        "乙方接受甲方委托，为甲方提供如下商标注册服务事宜：",
    ),
    (
        "synth_trademark_renewal.docx",
        "合成国内商标服务合同",
        "(续展)",
        "TRADEMARK",
        "TRADEMARK_RENEWAL",
        [
            "序号",
            "申请号",
            "商标名称",
            "类别",
            "申请人名称",
            "专有权期限",
            "是否进入宽展期",
        ],
        ["1", "SYN-TM-0001", "测试商标", "第9类", SYNTH_COMPANY_A, "2020-2030", "否"],
        "乙方接受甲方委托，为甲方提供如下商标续展服务事宜：",
    ),
    (
        "synth_ip_framework.docx",
        "合成知识产权服务合同",
        "(框架协议)",
        "IP_GENERAL",
        "IP_GENERAL_FRAMEWORK",
        ["序号", "服务项目", "代收代付官费（元/件）", "含税服务费（元/件）"],
        ["1", "合成服务项", "100", "200"],
        "",
    ),
]


def _add_template(
    path: Path,
    title: str,
    subtitle: str,
    l1: str,
    l2: str,
    headers: list[str],
    row: list[str],
    matter_sentence: str,
):
    doc = Document()
    # 标题样式（供格式审核器检测）
    h = doc.add_paragraph()
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = h.add_run(title)
    run.bold = True
    run.font.size = Pt(16)
    h2 = doc.add_paragraph()
    h2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    h2.add_run(subtitle).font.size = Pt(14)
    doc.add_paragraph("合同编号:SYN-2026-0001")

    tbl = doc.add_table(rows=2, cols=2)
    tbl.rows[0].cells[0].text = f"甲方: {SYNTH_COMPANY_A}\n地址: 合成测试地址1号"
    tbl.rows[0].cells[1].text = f"乙方:{SYNTH_COMPANY_B}\n地址:合成测试地址2号"
    tbl.rows[1].cells[0].text = "经办人：赵测试"
    tbl.rows[1].cells[1].text = "经办人：李测试"
    # 合并单元格（供合并检测测试）
    tbl.rows[1].cells[0].merge(tbl.rows[1].cells[1])

    def para(text, bold=False, size=None, center=False):
        p = doc.add_paragraph()
        if center:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(text)
        if bold:
            r.bold = True
        if size:
            r.font.size = Pt(size)
        return p

    doc.add_paragraph(
        "甲乙双方本着诚信友好、互惠互利的原则，根据《中华人民共和国民法典》等相关法律法规的规定，"
        f"就乙方为甲方提供{l1}服务事项，达成如下协议："
    )
    para("第一条 合同事项", bold=True, size=12)
    if l2 == "IP_GENERAL_FRAMEWORK":
        doc.add_paragraph(
            "在甲方有知识产权需要由乙方提供服务时，由甲方向乙方发出知识产权服务指示函或书面指示，"
            "双方以本合同内容为基础确定甲乙双方单个事项服务关系，服务价格按知识产权服务价格表执行。"
        )
    else:
        doc.add_paragraph(matter_sentence)

    it = doc.add_table(rows=2, cols=len(headers))
    for i, hd in enumerate(headers):
        it.rows[0].cells[i].text = hd
    for i, v in enumerate(row):
        it.rows[1].cells[i].text = v

    para("第二条 乙方权利与义务", bold=True, size=12)
    doc.add_paragraph(
        "2.1 乙方接受甲方委托，指派专业服务人员代为办理上述业务。乙方必须依法维护甲方的合法权益，"
        "为甲方提供的技术保密。为便于与甲方联系保障服务衔接，乙方安排专属联系人       "
        "（联系电话：       ；电子邮件：       ）负责与甲方沟通联系事宜，"
        f"并设立总服务邮箱（{SYNTH_EMAIL_SERVICE}）用于甲方咨询、服务反馈及紧急事务响应。"
    )
    doc.add_paragraph(
        "2.2 乙方自收到甲方提供的所有申报材料及本合同第四条所述费用后，应及时办理甲方的事务。"
    )
    doc.add_paragraph(
        "2.3 乙方为甲方提供本合同约定的相关服务过程中，应维护甲方的合法权益，"
        "甲方同意乙方可根据业务需要，选择乙方满足资质的关联公司及团队或选择其他第三方完成本合同事务。"
    )
    doc.add_paragraph(
        "2.4 乙方应及时将服务过程中所获取的官方文件送达甲方，并告知其事项的进展情况。"
    )

    para("第三条 甲方权利与义务", bold=True, size=12)
    doc.add_paragraph(
        "3.1 甲方指派固定联系人与乙方进行沟通、联系，配合乙方的工作，甲方如变更联系人或联系方式需主动"
        "以书面形式（包括经乙方确认的电子邮件）通知乙方。甲方指定人员的信息为："
    )
    ct = doc.add_table(rows=3, cols=4)
    for i, v in enumerate(["联系人", "钱测试", "电子邮件", "contact@example.com"]):
        ct.rows[0].cells[i].text = v
    for i, v in enumerate(["联系地址", "合成测试地址1号", "", ""]):
        ct.rows[1].cells[i].text = v
    for i, v in enumerate(["联系电话/手机", SYNTH_PHONE, "微信/QQ", "无"]):
        ct.rows[2].cells[i].text = v
    doc.add_paragraph(
        "3.2 甲方应及时向乙方提供办理上述事项所需要的资料，包括但不限于主体资质、委托书等资料。"
    )
    doc.add_paragraph(
        "3.3 甲方应保证所提供的文件及证明材料真实有效，若因提供虚假或无效资料而产生损失或不利后果的由甲方自行承担。"
    )
    doc.add_paragraph("3.4 按合同规定按时向乙方支付有关费用。")
    if l2 in ("PATENT_APPLICATION",):
        doc.add_paragraph(
            "3.5 甲方承诺：委托乙方办理预审的技术方案未在任何代理机构提交过预审申请。鉴于知识产权保护中心"
            '对重复预审行为将对相应专利代理机构实施"永久取消资格"处罚机制，甲方须完整披露该技术方案的预审'
            "历史记录。因甲方隐瞒重复预审事实导致乙方遭受相关机关处罚的，甲方应全额赔偿乙方损失，"
            "且已支付服务费用乙方不予退还。"
        )
    if l2 == "TRADEMARK_REGISTRATION":
        doc.add_paragraph(
            "3.5 乙方已事先告知甲方本商标注册的相关风险（包括但不限于该商标可能存在在先近似商标、"
            "缺乏显著性、具有不良影响等），在后期商标注册过程中发生的此类风险所产生的后果由甲方自行承担。"
        )

    para("第四条 费用及支付方式", bold=True, size=12)
    doc.add_paragraph(
        "4.1 甲方向乙方支付费用总额为人民币 壹仟圆整（¥1,000 元），前述费用不包括公证或其他程序所产生的费用。具体明细如下："
    )
    ft = doc.add_table(rows=3, cols=4)
    for i, v in enumerate(
        ["费用项目", "代收代付官费（元/件）", "含税服务费（元/件）", "数量"]
    ):
        ft.rows[0].cells[i].text = v
    for i, v in enumerate(["合成服务", "600", "400", "1"]):
        ft.rows[1].cells[i].text = v
    for i, v in enumerate(["合计", "600", "400", "/"]):
        ft.rows[2].cells[i].text = v
    doc.add_paragraph("4.2 甲方应自合同生效之日起三个工作日内向乙方支付上述全部费用。")
    doc.add_paragraph(
        f"4.3 乙方的账户信息：账户名：{SYNTH_COMPANY_B} 账号：{SYNTH_ACCOUNT} 开户行：{SYNTH_BANK}"
    )
    doc.add_paragraph(
        "4.4 乙方自收到甲方支付的全部费用后启动相关业务的办理（甲方支付的款项实际到达乙方指定账户之日为收到日），另有约定除外。"
    )
    doc.add_paragraph(
        "4.5 乙方自收到甲方支付的全部费用后，为甲方出具服务费增值税专用发票（税率6%）并提供官费官方收据。"
        "注:本合同所列官费均为代收代付款项，不计入应税价款。"
    )

    para("第五条 保密条款", bold=True, size=12)
    doc.add_paragraph(
        "甲乙双方对本合同内容及合同履行过程中知悉双方所有的经营信息、资料等商业秘密负有保密义务，"
        "未经对方书面同意，不得自行用于与本合同目的无关的其它事宜或向任何第三方披露、出售、许可使用，"
        "但法律要求披露或者通过合法公开途径可获得的除外。"
    )
    para("第六条 违约责任", bold=True, size=12)
    doc.add_paragraph(
        "6.1 非因乙方过错，甲方单方解除与乙方的服务关系，甲方支付给乙方的费用不予返还；"
        "非因甲方过错，乙方单方解除与甲方的服务关系，乙方应退还甲方已支付的相应案件费用。"
    )
    doc.add_paragraph(
        "6.2 甲方应按本合同约定及时向乙方支付服务费用，逾期支付的甲方应按逾期付款金额日率3‰向乙方支付违约金。"
    )
    doc.add_paragraph(
        "6.3 乙方因泄露或以其他方式侵犯甲方的商业秘密，给甲方造成损失的，乙方应按照甲方的实际经济损失进行赔偿。"
    )
    doc.add_paragraph(
        "6.4 甲方违反本合同约定的，乙方有权要求甲方赔偿全部损失（损失包括但不限于律师费、保全费、"
        "调查取证费、公证费、差旅费等相关费用）。"
    )
    para("第七条 争议解决", bold=True, size=12)
    doc.add_paragraph(
        "本合同履行过程中产生的一切争议，均应由双方友好协商解决；如协商不成，"
        "任何一方均有权向合成测试地区人民法院提起诉讼。"
    )
    para("第八条 合同生效及其他", bold=True, size=12)
    doc.add_paragraph(
        "8.1 本合同自双方签字或盖章之日起生效，至上述服务事项办理完结之日终止。"
    )
    doc.add_paragraph(
        "8.2 本合同项下之条款如有未尽或变更事宜，由双方另行协商补充，补充协议与本合同具有同等法律效力。"
    )
    doc.add_paragraph(
        "8.3 本合同履行和相关事宜的通知，应当按照本合同载明的地址发出。"
        "如果以特快专递或挂号信形式寄出，自发出之日起的第四日视为送达。"
    )
    doc.add_paragraph(
        f"8.4 在双方合作过程中，若甲方有任何问题可直接向乙方予以投诉反馈。投诉邮箱：{SYNTH_EMAIL_COMPLAINT}；"
        f"手机：{SYNTH_PHONE}"
    )
    doc.add_paragraph("8.5 本合同一式两份，甲乙双方各执一份，具有同等法律效力。")
    sig = doc.add_paragraph(f"甲  方：____________  乙  方：{SYNTH_COMPANY_B}")
    sig.add_run("")
    doc.add_paragraph("（签章）              （签章）")
    doc.add_paragraph(f"代表人：              代表人：{SYNTH_REP}")
    doc.add_paragraph("日  期：              日  期：")
    # 页眉页脚
    sec = doc.sections[0]
    sec.header.paragraphs[0].text = "合成测试合同（仅供测试）"
    sec.footer.paragraphs[0].text = "第 1 页"
    doc.save(str(path))


def generate_corpus(out_dir: Path | None = None) -> list[dict]:
    """生成合成模板语料，返回清单（含哈希，供回归比对）。"""
    out_dir = out_dir or (
        Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "synth_corpus"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for fname, title, sub, l1, l2, headers, row, matter in SYNTH_TEMPLATES:
        p = out_dir / fname
        _add_template(p, title, sub, l1, l2, headers, row, matter)
        manifest.append(
            {
                "file": fname,
                "category": l1,
                "subtype": l2,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "synthetic": True,
            }
        )
    (out_dir / "manifest.json").write_text(
        __import__("json").dumps(manifest, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return manifest


if __name__ == "__main__":
    ms = generate_corpus()
    print(f"generated {len(ms)} synthetic templates")
