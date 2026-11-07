# -*- coding: utf-8 -*-
"""
Export all contract classification, dictionary, business segment, mainbody,
prefix, old-OA select item, and contract template mappings to a multi-sheet Excel (.xlsx) file.
Uses standard library zipfile + xml.sax.saxutils to generate a styled OpenXML Workbook.
"""
import csv
import datetime
import io
import json
import os
import sys
import zipfile
from collections import Counter, defaultdict
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tender_fullsync import load_env
import pymysql
import pytds


def col_letter(idx_0: int) -> str:
    """Convert 0-based column index to Excel column letters (A, B, ..., Z, AA, ...)."""
    res = []
    n = idx_0 + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        res.append(chr(65 + rem))
    return "".join(reversed(res))


def clean_xml_str(val) -> str:
    if val is None:
        return ""
    if isinstance(val, (bytes, bytearray)):
        if len(val) == 1:
            return str(val[0])
        try:
            val = val.decode("utf-8", errors="replace")
        except Exception:
            val = str(val)
    elif isinstance(val, datetime.datetime):
        val = val.strftime("%Y-%m-%d %H:%M:%S")
    elif isinstance(val, datetime.date):
        val = val.strftime("%Y-%m-%d")
    else:
        val = str(val)
    return "".join(
        ch for ch in val
        if ch in ("\t", "\n", "\r") or (0x20 <= ord(ch) <= 0xD7FF) or (0xE000 <= ord(ch) <= 0xFFFD)
    )


def write_styled_xlsx(filepath: str, sheets: list[tuple[str, list[str], list[list]]]):
    """
    Write a multi-sheet .xlsx file with:
    - Styled header row (Dark navy fill #1F4E78, bold white font, centered)
    - Data rows with thin borders and text wrap / vertical center
    - Frozen top row (row 1)
    - AutoFilter enabled on header range
    - Auto-calculated column widths based on character display width
    """
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)

    with zipfile.ZipFile(filepath, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. [Content_Types].xml
        ct_parts = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
            '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
            '  <Default Extension="xml" ContentType="application/xml"/>',
            '  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
            '  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
        ]
        for i in range(len(sheets)):
            ct_parts.append(
                f'  <Override PartName="/xl/worksheets/sheet{i+1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            )
        ct_parts.append("</Types>")
        zf.writestr("[Content_Types].xml", "\n".join(ct_parts))

        # 2. _rels/.rels
        rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
        zf.writestr("_rels/.rels", rels_xml)

        # 3. xl/_rels/workbook.xml.rels
        wb_rels = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
        ]
        for i in range(len(sheets)):
            wb_rels.append(
                f'  <Relationship Id="rId{i+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i+1}.xml"/>'
            )
        wb_rels.append(
            f'  <Relationship Id="rId{len(sheets)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        )
        wb_rels.append("</Relationships>")
        zf.writestr("xl/_rels/workbook.xml.rels", "\n".join(wb_rels))

        # 4. xl/workbook.xml
        wb_xml = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">',
            '  <sheets>',
        ]
        for i, (sheet_name, _, _) in enumerate(sheets):
            safe_name = escape(sheet_name[:31])
            wb_xml.append(f'    <sheet name="{safe_name}" sheetId="{i+1}" r:id="rId{i+1}"/>')
        wb_xml.append("  </sheets>")
        wb_xml.append("</workbook>")
        zf.writestr("xl/workbook.xml", "\n".join(wb_xml))

        # 5. xl/styles.xml
        styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2">
    <font>
      <sz val="10"/>
      <name val="Microsoft YaHei"/>
    </font>
    <font>
      <b/>
      <sz val="10"/>
      <color rgb="FFFFFFFF"/>
      <name val="Microsoft YaHei"/>
    </font>
  </fonts>
  <fills count="3">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill>
      <patternFill patternType="solid">
        <fgColor rgb="FF1F4E78"/>
        <bgColor indexed="64"/>
      </patternFill>
    </fill>
  </fills>
  <borders count="2">
    <border>
      <left/><right/><top/><bottom/><diagonal/>
    </border>
    <border>
      <left style="thin"><color rgb="FFD9D9D9"/></left>
      <right style="thin"><color rgb="FFD9D9D9"/></right>
      <top style="thin"><color rgb="FFD9D9D9"/></top>
      <bottom style="thin"><color rgb="FFD9D9D9"/></bottom>
      <diagonal/>
    </border>
  </borders>
  <cellStyleXfs count="1">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
  </cellStyleXfs>
  <cellXfs count="4">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">
      <alignment horizontal="center" vertical="center" wrapText="1"/>
    </xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1">
      <alignment vertical="center" wrapText="1"/>
    </xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1">
      <alignment horizontal="right" vertical="center"/>
    </xf>
  </cellXfs>
</styleSheet>"""
        zf.writestr("xl/styles.xml", styles_xml)

        # 6. xl/worksheets/sheet{i+1}.xml
        for i, (sheet_name, headers, rows) in enumerate(sheets):
            num_cols = max(len(headers), 1)
            last_col_letter = col_letter(num_cols - 1)
            total_rows = len(rows) + 1

            col_widths = [12.0] * num_cols
            for c_idx, h in enumerate(headers):
                s = clean_xml_str(h)
                w = sum(2.0 if ord(ch) > 127 else 1.1 for ch in s) + 5.0
                col_widths[c_idx] = max(col_widths[c_idx], min(w, 60.0))
            for r in rows[:500]:
                for c_idx, val in enumerate(r[:num_cols]):
                    s = clean_xml_str(val)
                    first_line = s.split("\n")[0]
                    w = sum(1.85 if ord(ch) > 127 else 1.0 for ch in first_line) + 4.0
                    col_widths[c_idx] = max(col_widths[c_idx], min(w, 65.0))

            ws = io.StringIO()
            ws.write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n')
            ws.write('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">\n')
            ws.write(f'  <dimension ref="A1:{last_col_letter}{total_rows}"/>\n')
            ws.write('  <sheetViews>\n')
            ws.write('    <sheetView workbookViewId="0">\n')
            ws.write('      <pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>\n')
            ws.write('    </sheetView>\n')
            ws.write('  </sheetViews>\n')
            ws.write('  <sheetFormatPr defaultRowHeight="20"/>\n')
            ws.write('  <cols>\n')
            for c_idx, width in enumerate(col_widths):
                ws.write(f'    <col min="{c_idx+1}" max="{c_idx+1}" width="{width:.1f}" customWidth="1"/>\n')
            ws.write('  </cols>\n')
            ws.write('  <sheetData>\n')

            # Header row (row 1)
            ws.write('    <row r="1" ht="26" customHeight="1">\n')
            for c_idx, h in enumerate(headers):
                cell_ref = f"{col_letter(c_idx)}1"
                text = escape(clean_xml_str(h))
                ws.write(f'      <c r="{cell_ref}" s="1" t="inlineStr"><is><t>{text}</t></is></c>\n')
            ws.write('    </row>\n')

            # Data rows
            for r_idx, row_vals in enumerate(rows, start=2):
                ws.write(f'    <row r="{r_idx}">\n')
                for c_idx in range(num_cols):
                    val = row_vals[c_idx] if c_idx < len(row_vals) else ""
                    cell_ref = f"{col_letter(c_idx)}{r_idx}"
                    if isinstance(val, (int, float)) and not isinstance(val, bool):
                        ws.write(f'      <c r="{cell_ref}" s="3"><v>{val}</v></c>\n')
                    else:
                        text = escape(clean_xml_str(val))
                        ws.write(f'      <c r="{cell_ref}" s="2" t="inlineStr"><is><t>{text}</t></is></c>\n')
                ws.write('    </row>\n')

            ws.write('  </sheetData>\n')
            ws.write(f'  <autoFilter ref="A1:{last_col_letter}{total_rows}"/>\n')
            ws.write('</worksheet>')
            zf.writestr(f"xl/worksheets/sheet{i+1}.xml", ws.getvalue())


def main():
    env = load_env()
    conn_contract = pymysql.connect(
        host=env["MYSQL_HOST"],
        port=int(env.get("MYSQL_PORT", 3306)),
        user=env["MYSQL_USER"],
        password=env["MYSQL_PWD"],
        database="tender_contract",
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    conn_sys = pymysql.connect(
        host=env["MYSQL_HOST"],
        port=int(env.get("MYSQL_PORT", 3306)),
        user=env["MYSQL_USER"],
        password=env["MYSQL_PWD"],
        database="tender_system",
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )

    apply_counts = defaultdict(Counter)
    apply_native_counts = defaultdict(Counter)
    apply_oa_counts = defaultdict(Counter)
    archive_counts = defaultdict(Counter)
    repayment_counts = Counter()

    with conn_contract.cursor() as cur:
        cols = [
            "contract_category", "contract_type", "business_type", "business_sub_type",
            "draft_type", "contract_nature", "contract_version", "auth_item",
            "contract_flow_type", "pay_scheme", "contract_status", "data_source", "oa_source",
            "my_main_body"
        ]
        for c in cols:
            cur.execute(f"""
                SELECT 
                    CASE WHEN oa_source IS NULL OR oa_source = '' THEN 'native' ELSE 'oa' END as src,
                    `{c}` as val,
                    COUNT(*) as cnt
                FROM contract_apply
                WHERE deleted = 0
                GROUP BY src, `{c}`
            """)
            for r in cur.fetchall():
                k = str(r["val"]) if r["val"] is not None else "NULL"
                apply_counts[c][k] += r["cnt"]
                if r["src"] == "native":
                    apply_native_counts[c][k] += r["cnt"]
                else:
                    apply_oa_counts[c][k] += r["cnt"]

        for c in ["contract_category", "contract_type", "business_type", "draft_type", "contract_nature", "auth_item", "contract_status", "archive_type"]:
            try:
                cur.execute(f"SELECT `{c}` as val, COUNT(*) as cnt FROM contract_archive WHERE deleted = 0 GROUP BY `{c}`")
                for r in cur.fetchall():
                    k = str(r["val"]) if r["val"] is not None else "NULL"
                    archive_counts[c][k] += r["cnt"]
            except Exception:
                pass

        cur.execute("SELECT repayment_type, COUNT(*) as cnt FROM contract_apply_performance_item WHERE deleted = 0 GROUP BY repayment_type")
        for r in cur.fetchall():
            k = str(r["repayment_type"]) if r["repayment_type"] is not None else "NULL"
            repayment_counts[k] += r["cnt"]

    # =========================================================================
    # SHEET 1: 01_核心分类字段枚举总表
    # =========================================================================
    s1_headers = [
        "字段英文名", "字段中文名", "所在核心表", "字典/配置表来源", "枚举值 (Value)",
        "标准中文含义 (Label)", "对应旧OA字段与原值", "申请表总数 (contract_apply)",
        "其中: 新系统原生数", "其中: 旧OA迁移数", "归档表总数 (contract_archive)", "RAG与业务使用核心说明"
    ]
    core_enum_defs = [
        # contract_type
        ("contract_type", "合同类型", "contract_apply / contract_archive / contract_template", "物理表 tender_contract.contract_type", "1", "销售合同 (XSHT)", "formtable_main_130 (产品合同) / -474 (htlx=0总委)", "contract_type", "客户签单业务主类型（覆盖普通产品合同与总委框架合同）"),
        ("contract_type", "合同类型", "contract_apply / contract_archive / contract_template", "物理表 tender_contract.contract_type", "2", "采购合同 (CGHT - 已停用)", "无", "contract_type", "物理配置表标记已删除；注意勿与字典表id=2021(1内部/2外部)混淆"),
        ("contract_type", "合同类型", "contract_apply / contract_archive / contract_template", "物理表 tender_contract.contract_type", "3", "服务协议 (FWXY)", "formtable_main_474 (htlx=1保密/2合作/3生态合作)", "contract_type", "涵盖保密协议、生态合作伙伴协议、渠道合作协议"),
        ("contract_type", "合同类型", "contract_apply / contract_archive / contract_template", "物理表 tender_contract.contract_type", "5", "律所合同 (LSHT - 已停用)", "无", "contract_type", "已并入销售合同下的法律服务(auth_item=7)"),
        # business_type
        ("business_type", "业务类型", "contract_apply / contract_archive / contract_template", "物理表 tender_contract.contract_business_type", "6", "普通新增产品 (ptxzcp)", "formtable_main_130.htlx = 0 (产品合同)", "business_type", "【最核心】单项/标准销售合同，归属 contract_type=1，绑定 67 个标准模板"),
        ("business_type", "业务类型", "contract_apply / contract_archive / contract_template", "物理表 tender_contract.contract_business_type", "7", "总委托业务 (zwht)", "formtable_main_474.htlx = 0 (总委合同)", "business_type", "【最核心】框架/总委销售合同，归属 contract_type=1，绑定 7 个总委标准模板"),
        ("business_type", "业务类型", "contract_apply / contract_archive / contract_template", "物理表 tender_contract.contract_business_type", "8", "保密协议 (bmxy)", "formtable_main_474.htlx = 1 (保密协议)", "business_type", "归属 contract_type=3 (服务协议)，绑定 MB20251205011 保密协议模板"),
        ("business_type", "业务类型", "contract_apply / contract_archive / contract_template", "物理表 tender_contract.contract_business_type", "9", "合作协议 (hzxy)", "formtable_main_474.htlx = 2 (合作协议)", "business_type", "归属 contract_type=3 (服务协议)，主要为历史渠道/外部合作协议"),
        ("business_type", "业务类型", "contract_apply / contract_archive / contract_template", "物理表 tender_contract.contract_business_type", "3", "生态合作伙伴协议", "formtable_main_474.htlx = 3 (生态合作伙伴协议)", "business_type", "归属 contract_type=3 (服务协议)，生态渠道合作专用"),
        # business_sub_type
        ("business_sub_type", "业务子类型", "contract_apply", "系统字典 contract_business_sub_type", "1", "普通合同", "对应 business_type = 6", "business_sub_type", "单项普通合同"),
        ("business_sub_type", "业务子类型", "contract_apply", "系统字典 contract_business_sub_type", "2", "总委合同", "对应 business_type = 7, 3, 9", "business_sub_type", "总委/合作框架类合同"),
        ("business_sub_type", "业务子类型", "contract_apply", "系统字典 contract_business_sub_type (扩展)", "3", "保密协议", "对应 business_type = 8", "business_sub_type", "后端代码自动将保密协议赋值为 3"),
        # draft_type
        ("draft_type", "起草方式", "contract_apply / contract_archive", "系统字典 contract_write_type (id=2022)", "1", "固定合同模板", "新系统2025-12上线模板引擎后产生", "draft_type", "调用系统模板在线填充生成，必有 contract_template_id 及事项明细"),
        ("draft_type", "起草方式", "contract_apply / contract_archive", "系统字典 contract_write_type (id=2022)", "2", "非固定合同模板", "全部旧OA迁移合同 + 新系统上传自定义Word/PDF合同", "draft_type", "注意：历史迁移合同全部为 2，判断其适用模板需通过 (business_type + auth_item) 路由"),
        # contract_nature
        ("contract_nature", "合同性质", "contract_apply / contract_archive", "系统字典 contract_nature (id=2023)", "1", "正常合同", "formtable_main_130.sfwbkbht = 1 (否)", "contract_nature", "全库有效记录均为 1"),
        ("contract_nature", "合同性质", "contract_apply / contract_archive", "系统字典 contract_nature (id=2023)", "2", "背靠背合同", "formtable_main_130.sfwbkbht = 0 (是)", "contract_nature", "涉及第三方背靠背付款条款"),
        # contract_version
        ("contract_version", "合同版本", "contract_apply", "系统字典 contract_version (id=2025)", "1", "标准版", "formtable_main_130.htbb = 0 (标准版)", "contract_version", "常规不退费标准条款版"),
        ("contract_version", "合同版本", "contract_apply", "系统字典 contract_version (id=2025)", "2", "可退款版", "formtable_main_130.htbb = 1 (可退款版)", "contract_version", "含未授权/未通过退费条款（如风险代理、授权宝等）"),
        # contract_flow_type
        ("contract_flow_type", "合同流转类型", "contract_apply", "系统字典 fllow_type (id=2031)", "0", "正常合同（旧OA原值遗留）", "旧OA yclx / htlzlx = 0 (正常合同)", "contract_flow_type", "迁移时部分记录保留了旧OA原值 0，等同于 1(正常)"),
        ("contract_flow_type", "合同流转类型", "contract_apply", "系统字典 fllow_type (id=2031)", "1", "正常", "旧OA yclx / htlzlx = 0 (正常合同)", "contract_flow_type", "标准价格与标准条款流转"),
        ("contract_flow_type", "合同流转类型", "contract_apply", "系统字典 fllow_type (id=2031)", "2", "低于底价", "旧OA yclx / htlzlx = 1 (异常合同-价格偏低)", "contract_flow_type", "触发低价特批流程"),
        ("contract_flow_type", "合同流转类型", "contract_apply", "系统字典 fllow_type (id=2031)", "3", "条款变更", "旧OA yclx / htlzlx = 2 (异常合同-条款变更)", "contract_flow_type", "【重要】代表修改了标准模板的核心风险条款（违约/时限/收款），触发法务审批"),
        # pay_scheme
        ("pay_scheme", "付款方案", "contract_apply", "系统字典 contract_payment_plantype", "1", "先付全款后服务", "formtable_main_130.fkfa = 0", "pay_scheme", "对应【包干费版】或全款标准模板"),
        ("pay_scheme", "付款方案", "contract_apply", "系统字典 contract_payment_plantype", "2", "先付部分款项后开始服务，递交前/收到凭证/项目成功后付尾款", "formtable_main_130.fkfa = 1", "pay_scheme", "对应【分阶段费用版】模板或分期收款条款"),
        ("pay_scheme", "付款方案", "contract_apply", "系统字典 contract_payment_plantype", "3", "前期收款0，成功后付费", "formtable_main_130.fkfa = 2", "pay_scheme", "对应【全风险】代理服务合同模板"),
        ("pay_scheme", "付款方案", "contract_apply", "系统字典 contract_payment_plantype", "4", "先付款，不成功后退款", "formtable_main_130.fkfa = 3", "pay_scheme", "对应可退款版/风险代理申请模板"),
        ("pay_scheme", "付款方案", "contract_apply", "系统字典 contract_payment_plantype", "5", "其他付款方案", "formtable_main_130.fkfa = 4", "pay_scheme", "特殊约定付款节点"),
        # contract_category
        ("contract_category", "合同大类", "contract_apply / contract_archive", "系统字典 contract_category (id=2018)", "1", "销售合同", "OA迁移数据默认赋 1", "contract_category", "【避坑】OA迁移全部为1；新系统原生部分为1"),
        ("contract_category", "合同大类", "contract_apply / contract_archive", "系统字典 contract_category (id=2018)", "2", "采购合同(字典) / 新系统普通销售合同实际落值", "新系统原生流程赋 2", "contract_category", "【避坑】新系统原生1816单普通销售合同被写入2，切勿用 contract_category=2 过滤采购合同！"),
        # contract_status
        ("contract_status", "合同状态", "contract_apply / contract_archive", "系统字典 contract_status (id=2026)", "0", "退回 / 草稿", "旧OA退回/草稿", "contract_status", "流程未提交或被退回"),
        ("contract_status", "合同状态", "contract_apply / contract_archive", "系统字典 contract_status (id=2026)", "1", "审批中", "旧OA htzt = 0 (签署/执行中)", "contract_status", "流程流转审批中"),
        ("contract_status", "合同状态", "contract_apply / contract_archive", "系统字典 contract_status (id=2026)", "2", "待归档", "审批通过待归档", "contract_status", "已签字盖章待归档"),
        ("contract_status", "合同状态", "contract_apply / contract_archive", "系统字典 contract_status (id=2026)", "3", "已归档", "旧OA htzt = 2(-130) / 1(-474) 归档", "contract_status", "【RAG核心范围】正式生效并完成归档的合同"),
        ("contract_status", "合同状态", "contract_apply / contract_archive", "系统字典 contract_status (id=2026)", "4", "异常归档", "旧OA htzt = 4(-130) / 3(-474) 异常归档", "contract_status", "扫描件归档或无纸质原件归档"),
        ("contract_status", "合同状态", "contract_apply / contract_archive", "系统字典 contract_status (id=2026)", "5", "作废", "旧OA htzt = 3(-130) / 2(-474) 作废", "contract_status", "已作废合同"),
        ("contract_status", "合同状态", "contract_apply / contract_archive", "系统字典 contract_status (id=2026)", "6", "已变更", "合同变更完成", "contract_status", "注意：表注释写6=变更中/7=已变更，实际以字典表id=2026为准"),
        ("contract_status", "合同状态", "contract_apply / contract_archive", "系统字典 contract_status (id=2026)", "7", "已终止", "合同终止", "contract_status", "以字典表id=2026为准"),
        ("contract_status", "合同状态", "contract_apply / contract_archive", "系统字典 contract_status (id=2026)", "8", "被驳回", "审批驳回", "contract_status", "以字典表id=2026为准"),
        ("contract_status", "合同状态", "contract_apply / contract_archive", "系统字典 contract_status (id=2026)", "10", "暂存", "草稿暂存", "contract_status", "未正式提交"),
        # data_source & oa_source
        ("data_source", "数据来源", "contract_apply", "系统内置枚举", "1", "新系统原生创建", "oa_source IS NULL", "data_source", "2025年底新系统上线后原生发起的合同"),
        ("data_source", "数据来源", "contract_apply", "系统内置枚举", "2", "旧OA系统迁移", "oa_source = 1(-130) 或 2(-474)", "data_source", "从 ecologyE9 迁移的历史及并行期合同"),
        ("oa_source", "旧OA来源表单", "contract_apply", "迁移脚本标记", "1", "旧OA产品合同流程 (formtable_main_130)", "workflow_bill id = -130", "oa_source", "对应普通新增产品合同 (business_type=6)"),
        ("oa_source", "旧OA来源表单", "contract_apply", "迁移脚本标记", "2", "旧OA总委/保密/合作流程 (formtable_main_474)", "workflow_bill id = -474", "oa_source", "对应总委(7)/保密(8)/合作(9)/生态合作(3)"),
    ]

    s1_rows = []
    for f_en, f_cn, tbl, src_dict, val, label, oa_map, count_col, note in core_enum_defs:
        c_app = apply_counts[count_col].get(val, 0)
        c_nat = apply_native_counts[count_col].get(val, 0)
        c_oa = apply_oa_counts[count_col].get(val, 0)
        c_arc = archive_counts[count_col].get(val, 0)
        s1_rows.append([f_en, f_cn, tbl, src_dict, val, label, oa_map, c_app, c_nat, c_oa, c_arc, note])

    repayment_defs = [
        ("0", "国内专利", "旧OA hklx/hklxa = 0", "对应 auth_item = 1"),
        ("1", "国际专利", "旧OA hklx/hklxa = 1", "对应 auth_item = 8"),
        ("2", "国内商标", "旧OA hklx/hklxa = 2", "对应 auth_item = 2"),
        ("3", "国际商标", "旧OA hklx/hklxa = 3", "对应 auth_item = 9"),
        ("4", "著作权", "旧OA hklx/hklxa = 4", "对应 auth_item = 3"),
        ("5", "交易", "旧OA hklx/hklxa = 5(hklxa)/6(hklx)", "对应 auth_item = 13 (IP运营/交易)"),
        ("6", "合作", "旧OA hklx/hklxa = 6(hklxa)/9(hklx)", "对应 auth_item = 14"),
        ("7", "法律业务", "旧OA hklx/hklxa = 7(hklxa)/5(hklx)", "对应 auth_item = 7 (法律服务)"),
        ("8", "项目", "旧OA hklx/hklxa = 8", "对应 auth_item = 4 (科创项目)"),
        ("9", "高价值业务", "旧OA hklx/hklxa = 9(hklxa)/7(hklx)", "对应 auth_item = 6 (数据与咨询)"),
        ("10", "工商财会", "旧OA hklx/hklxa = 10", "对应 auth_item = 5 / 15"),
        ("11", "发明专利授权宝", "旧OA hklx/hklxa = 11", "对应 auth_item = 1 / 16 (风险代理/授权宝)"),
        ("12", "知识产权风险代理产权", "旧OA hklx/hklxa = 12", "对应 auth_item = 1 / 17"),
        ("13", "数据分析", "旧OA hklx/hklxa = 13", "对应 auth_item = 6 (数据与咨询)"),
        ("14", "商标复审包", "旧OA hklx/hklxa = 14", "对应 auth_item = 2 (国内商标)"),
        ("20", "审计业务", "新系统扩展", "对应 auth_item = 5 (财税审计)"),
        ("21", "非标准产品", "新系统扩展", "对应 auth_item = 10"),
        ("22", "第三方业务", "新系统扩展", "对应 auth_item = 11"),
        ("23", "IP运营", "新系统扩展", "对应 auth_item = 12"),
        ("24", "电商", "新系统扩展", "对应业务板块 D (电商)"),
    ]
    for val, label, oa_map, note in repayment_defs:
        cnt = repayment_counts.get(val, 0)
        s1_rows.append([
            "repayment_type", "业绩/回款业务类型", "contract_apply_performance_item",
            "系统字典 finance_repayment_status", val, label, oa_map,
            cnt, "-", "-", "-", f"【注意与auth_item编码不同】{note}"
        ])

    # =========================================================================
    # SHEET 2: 02_委托事项与业务板块映射 (1级 -> 2级 -> 3级 -> 4级 -> 5级 -> O列唯一标准模板)
    # =========================================================================
    s2_headers = [
        "委托事项编码 (auth_item)",
        "委托事项名称 (entrusted_matters)",
        "标准模板分类目录 / 状态",
        "【第1级】一级业务板块名称",
        "【第1级】一级板块代码",
        "【第2级】二级业务板块名称",
        "【第2级】二级板块代码 (对应编号制度)",
        "【第3级】合同性质与三级业务子类 (contract_type + business_type + 子类)",
        "【第4级】四级细分业务场景与标的约束",
        "【第4级】四级收费结算与交易方向约束 (全款/分期/风险/买卖方向)",
        "【第5级】五级绑定新系统产品编码与名称 (CNIP/CNIT/CNSC等)",
        "【第5级】五级绑定ERP历史产品编码 (01/02等)",
        "旧OA wtsx映射 & 新系统实签单数",
        "绑定标准模板数",
        "绑定的标准模板编码列表 (1至5级精准锁定结果)",
        "同分类易混模板排他规则与RAG路由逻辑",
    ]

    with conn_contract.cursor() as cur:
        cur.execute("SELECT id, pid, name, code, value, sort FROM contract_config_business_segment WHERE deleted = 0 ORDER BY pid, sort, id")
        seg_rows = cur.fetchall()
        cur.execute("""
            SELECT template_code, template_name, contract_type, business_type, auth_item, match_body_id,
                   MAX(version_no) as latest_ver, COUNT(*) as ver_cnt, MAX(update_time) as last_upd
            FROM contract_template
            WHERE deleted = 0
            GROUP BY template_code, template_name, contract_type, business_type, auth_item, match_body_id
            ORDER BY contract_type, business_type, auth_item, template_code
        """)
        tpl_rows = cur.fetchall()
        cur.execute("""
            SELECT contract_template_code, COUNT(*) as cnt
            FROM contract_apply
            WHERE deleted = 0 AND contract_template_code IS NOT NULL AND contract_template_code != ''
            GROUP BY contract_template_code
        """)
        tpl_use_cnt = {r["contract_template_code"]: r["cnt"] for r in cur.fetchall()}

    from template_5th_layer_data import TEMPLATE_5TH_LAYER_RULES, DISAMBIGUATION_GROUPS
    _scratch_dir = os.environ.get(
        "CONTRACT_KB_SCRATCH_DIR",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "scratch"),
    )
    with open(os.path.join(_scratch_dir, "all_new_products_with_ids.json"), "r", encoding="utf-8") as f:
        all_new_prods_meta = json.load(f)

    ref_existing_map = {}
    ref_folder_map = {}
    for root_dir, _, files in os.walk("参考"):
        for fn in sorted(files):
            if fn.endswith(".docx") and fn.startswith("MB"):
                code_part = fn.split("_")[0]
                folder_name = os.path.basename(root_dir)
                ref_existing_map[code_part] = f"{folder_name}/{fn}"
                ref_folder_map[code_part] = folder_name

    # Helper to determine Level 1, Level 2, Level 3 for each of the 78 templates
    def get_l1_l2_l3_for_template(tcode, ai_raw, ct_val, bt_val):
        # Level 1 & Level 2
        if tcode in ("MB20251205020", "MB20251205022", "MB20251205023", "MB20251205025", "MB20251205026", "MB20251205028", "MB20251205029", "MB20251229002"):
            l1_name, l1_code = "I (知识产权) / S (高端咨询)", "I / S"
            l2_name, l2_code = "国内专利(库标1) / 数据与咨询(实属6)", "I-P / S-C"
            oa_str = "旧OA wtsx=0(专利)/10(高价值)/13(数据分析)"
        elif tcode in ("MB20251205045", "MB20251205046"):
            l1_name, l1_code = "I (知识产权) / O (IP运营)", "I / O"
            l2_name, l2_code = "国内专利(库标1) / IP运营交易(实属12/13)", "I-P / O-P"
            oa_str = "旧OA wtsx=6(交易)/0(国内专利)"
        elif tcode in ("MB20251205043", "MB20251205044"):
            l1_name, l1_code = "I (知识产权) / O (IP运营)", "I / O"
            l2_name, l2_code = "国内商标(库标2) / IP运营交易(实属12/13)", "I-T / O-P"
            oa_str = "旧OA wtsx=6(交易)/5(国内商标)"
        elif tcode == "MB20251205063":
            l1_name, l1_code = "P (政策辅导)", "P"
            l2_name, l2_code = "科创项目(库标4) / 第三方业务(实属11)", "P-B / P-R"
            oa_str = "旧OA wtsx=2/17(科技项目)"
        elif ai_raw == "1":
            l1_name, l1_code = "I (知识产权)", "I"
            l2_name, l2_code = "国内专利", "I-P"
            oa_str = "旧OA wtsx=0(国内专利)/11(授权宝)/12(风险)"
        elif ai_raw == "2":
            l1_name, l1_code = "I (知识产权)", "I"
            l2_name, l2_code = "国内商标", "I-T"
            oa_str = "旧OA wtsx=5(国内商标)"
        elif ai_raw == "3":
            l1_name, l1_code = "I (知识产权)", "I"
            l2_name, l2_code = "国内著作权", "I-C"
            oa_str = "旧OA wtsx=1(著作权)"
        elif ai_raw == "4":
            l1_name, l1_code = "P (政策辅导)", "P"
            l2_name, l2_code = "科创项目 (兼覆盖11-第三方业务)", "P-B / P-R"
            oa_str = "旧OA wtsx=2/15/17(科技项目)/16(农业)"
        elif ai_raw == "5":
            l1_name, l1_code = "P (政策辅导)", "P"
            l2_name, l2_code = "财税审计", "P-S"
            oa_str = "旧OA wtsx=18(财税审计)/9(工商财会)"
        elif ai_raw == "7":
            l1_name, l1_code = "L (法律服务)", "L"
            l2_name, l2_code = "法律服务", "L-S"
            oa_str = "旧OA wtsx=3(法律业务)"
        elif ai_raw == "8":
            l1_name, l1_code = "I (知识产权)", "I"
            l2_name, l2_code = "国际专利", "G-I-P"
            oa_str = "旧OA wtsx=4(国际专利)"
        elif ai_raw == "9":
            l1_name, l1_code = "I (知识产权)", "I"
            l2_name, l2_code = "国际商标", "G-I-P"
            oa_str = "旧OA wtsx=7(国际商标)"
        elif ai_raw in ("1,2", "1,3", "2,3", "1,2,3"):
            l1_name, l1_code = "M (综合业务) / I (知识产权)", "M / I"
            l2_name, l2_code = f"跨板块组合 ({ai_raw})", "M"
            oa_str = f"旧OA多选组合 ({ai_raw})"
        elif "1,2,3,4,5,6,7" in ai_raw:
            l1_name, l1_code = "Z (总委业务)", "Z"
            l2_name, l2_code = "全业务总委框架", "Z"
            oa_str = "旧OA -474 总委流程"
        else:
            l1_name, l1_code = "BM (保密廉洁)", "BM"
            l2_name, l2_code = "保密协议", "BM"
            oa_str = "旧OA 保密协议流程"

        # Level 3 classification
        l3_map = {
            "MB20251205014": "单委销售(1+6) · 1.1 国内专利常规新申请类",
            "MB20251209001": "单委销售(1+6) · 1.1 国内专利风险代理新申请类",
            "MB20251205012": "单委销售(1+6) · 1.2 国内专利加速审查类(保护中心快速预审)",
            "MB20251205018": "单委销售(1+6) · 1.2 国内专利加速审查类(国知局优先审查)",
            "MB20251205021": "单委销售(1+6) · 1.3 国内专利驳回复审类",
            "MB20251205029": "单委销售(1+6) · 1.3 国内专利无效宣告与答辩类",
            "MB20251205013": "单委销售(1+6) · 1.4 国内专利年费缴纳与恢复权利类",
            "MB20251205017": "单委销售(1+6) · 1.4 国内专利优先权证明与登记簿副本类",
            "MB20251205019": "单委销售(1+6) · 1.5 国内专利著录项目变更类(更名/改址/改发明人)",
            "MB20251205027": "单委销售(1+6) · 1.6 国内专利转让过户类(纯代理国知局过户服务)",
            "MB20251205045": "单委交易(1+6) · 1.6 专利权买卖交易类(我方运营主体作为甲方买方收购)",
            "MB20251205046": "单委交易(1+6) · 1.6 专利权买卖交易类(我方运营主体作为乙方卖方出售)",
            "MB20251205015": "单委销售(1+6) · 1.7 国内专利实施许可合同备案类",
            "MB20251205016": "单委销售(1+6) · 1.7 国内专利实施许可合同备案注销类",
            "MB20251205024": "单委销售(1+6) · 1.8 官方专利权评价报告申请类",
            "MB20251205022": "单委咨询(1+6) · 1.8 专利查新检索与授权前景评估报告类",
            "MB20251205026": "单委咨询(1+6) · 1.8 专利权稳定性分析与无效检索报告类",
            "MB20251205025": "单委咨询(1+6) · 1.8 单件专利侵权技术比对判定报告类",
            "MB20251229002": "单委咨询(1+6) · 1.8 产品上市防侵权自由实施调查类(FTO报告)",
            "MB20251205023": "单委咨询(1+6) · 1.8 企业专利资产盘点分级与尽职调查报告类",
            "MB20251205020": "单委咨询(1+6) · 1.8 企业专利预警、微观导航与高价值布局报告类",
            "MB20251205028": "单委咨询(1+6) · 1.8 常年/年度知识产权顾问服务类",
            "MB20251205042": "单委销售(1+6) · 1.9 集成电路布图设计专有权登记类",
            "MB20251205086": "单委组合(1+6) · 1.10 国内专利多事项打包组合类",
            "MB20251205057": "单委销售(1+6) · 2.1 国内商标常规新注册申请类",
            "MB20251205059": "单委销售(1+6) · 2.1 国内商标风险包过套餐与商标分析咨询类",
            "MB20251205047": "单委销售(1+6) · 2.2 国内商标变更类(图文通用版-带商标图样图片)",
            "MB20260729001": "单委销售(1+6) · 2.2 国内商标变更类(纯文字商标专用版-免贴图)",
            "MB20251205054": "单委销售(1+6) · 2.2 国内商标续展与宽展申请类",
            "MB20251205049": "单委销售(1+6) · 2.2 国内商标补发注册证与证明文件出具类",
            "MB20251205053": "单委销售(1+6) · 2.2 国内商标使用许可备案与质权登记类",
            "MB20251205058": "单委销售(1+6) · 2.3 国内商标转让过户类(纯代理商标局过户服务)",
            "MB20251205043": "单委交易(1+6) · 2.3 商标权买卖交易类(我方运营主体作为甲方买方收购)",
            "MB20251205044": "单委交易(1+6) · 2.3 商标权买卖交易类(我方运营主体作为乙方卖方出售)",
            "MB20251205048": "单委销售(1+6) · 2.4 国内商标评审类(驳回复审申请)",
            "MB20251205055": "单委销售(1+6) · 2.4 国内商标异议攻防类(主动对他人公告商标提异议)",
            "MB20251205056": "单委销售(1+6) · 2.4 国内商标异议攻防类(被他人提异议后进行异议答辩)",
            "MB20251205050": "单委销售(1+6) · 2.4 国内商标评审类(不予注册复审申请/答辩)",
            "MB20251205051": "单委销售(1+6) · 2.4 国内商标评审类(撤三申请/撤三答辩提供证据/撤销复审)",
            "MB20251205052": "单委销售(1+6) · 2.4 国内商标评审类(注册商标无效宣告申请/答辩)",
            "MB20251205085": "单委组合(1+6) · 2.5 国内商标多事项打包组合类",
            "MB20251205087": "单委销售(1+6) · 3.1 国内计算机软著/作品著作权单项登记类",
            "MB20251205088": "单委组合(1+6) · 3.2 国内著作权多事项/多加急周期打包组合类",
            "MB20251205069": "跨板块组合(1+6) · 4.1 【专利+商标】综合申请注册与服务组合类",
            "MB20251205072": "跨板块组合(1+6) · 4.2 【专利+商标】企业更名/改址同步著录变更组合类",
            "MB20251205068": "跨板块组合(1+6) · 4.3 【商标转让+专利转让】同步转让过户代理组合类",
            "MB20251205078": "跨板块组合(1+6) · 4.4 【专利+著作权】研发成果/高企配套知产组合类",
            "MB20251205065": "跨板块组合(1+6) · 4.5 【商标+著作权】品牌商标与美术/软件版权同步保护组合类",
            "MB20251205074": "跨板块组合(1+6) · 4.6 【专利+商标+著作权】三栖全套知识产权组合类",
            "MB20251205041": "单委销售(1+6) · 5.1 国际专利PCT国际申请阶段类(RO/CN或IB国际局)",
            "MB20251205037": "单委销售(1+6) · 5.2 涉外专利PCT/巴黎公约进国家阶段申请类【一口价包干版】",
            "MB20251205039": "单委销售(1+6) · 5.2 涉外专利PCT/巴黎公约进国家阶段申请类【分阶段费用版】",
            "MB20251205038": "单委销售(1+6) · 5.3 涉外专利OA审查意见答复与复审类【一口价包干版】",
            "MB20251205040": "单委销售(1+6) · 5.3 涉外专利OA审查意见答复与复审类【分阶段费用版】",
            "MB20251205035": "单委销售(1+6) · 5.4 国际商标新注册类(单一国家/地区逐一申请)",
            "MB20251205036": "单委销售(1+6) · 5.4 国际商标新注册类(WIPO马德里体系多国注册)",
            "MB20251205030": "单委销售(1+6) · 5.5 国际商标后续案件类(海外商标变更/转让/续展/宣誓/OA答复)",
            "MB20251205082": "单委咨询(1+6) · 6.1 科创政策项目申报与ISO/贯标/绿色工厂/评估通用咨询类",
            "MB20251205080": "单委咨询(1+6) · 6.2 香港BUD专项基金及涉港项目申报咨询类",
            "MB20251205084": "单委咨询(1+6) · 6.3 企业数据知识产权存证与登记申报咨询类",
            "MB20251205064": "单委咨询(1+6) · 6.4 软件企业评估与软件产品评估(双软认定)类",
            "MB20251205063": "单委服务(1+6) · 6.5 科技查新报告递交与软件登记测试类(第三方业务)",
            "MB20251205061": "单委审计(1+6) · 6.6 财税审计报告与研发辅助账类【广东地区客户专用版】",
            "MB20251205062": "单委审计(1+6) · 6.6 财税审计报告与研发辅助账类【除广东外全国其他地区版】",
            "MB20251205071": "律所单委(5+6) · 7.1 单案专利侵权民事诉讼代理类(不含无效)",
            "MB20251205066": "律所单委(5+6) · 7.1 专利侵权民事诉讼 + 专利无效宣告【诉无联动组合】类",
            "MB20251205067": "律所单委(5+6) · 7.2 商标授权确权行政纠纷诉讼类(起诉国知局/第三人应诉)",
            "MB20251205075": "律所单委(5+6) · 7.3 通用民事/商标侵权/版权侵权/合同纠纷单案诉讼类",
            "MB20251205073": "律所单委(5+6) · 7.4 非诉法律专项类(合同拟定修订/律师函/电商投诉申诉/常年法顾)",
            "MB20251205076": "律所总委(5+7) · 7.5 批量民事维权打假总委类【0基础费全风险判赔分成版】",
            "MB20251205077": "律所总委(5+7) · 7.5 批量民事维权打假总委类【基础服务费+回款分成常规版】",
            "MB20251205079": "律所总委(5+7) · 7.5 制假售假公安机关【刑事打击】专项维权总委类",
            "MB20251205081": "律所总委(5+7) · 7.5 市场监管局/工商【行政查处】专项维权总委类",
            "MB20251205083": "律所总委(5+7) · 7.5 知识产权【民事+行政+刑事】三合一综合维权类",
            "MB20251205070": "律所合作(5+9) · 7.5 大客户知识产权战略保护框架合作协议类",
            "MB20251205089": "总委合同(1+7) · 8.1 国内知识产权年度总委框架合同【不含国际专利报价】",
            "MB20251225003": "总委合同(1+7) · 8.1 国内+国际全口径知识产权总委框架合同【含国际专利报价版】",
            "MB20251205011": "服务协议(3+8) · 8.2 商业秘密与技术交底保密协议(NDA)",
        }
        return l1_name, l1_code, l2_name, l2_code, l3_map.get(tcode, "常规业务子类"), oa_str

    auth_cn_simple = {
        "1": "国内专利", "2": "国内商标", "3": "国内著作权", "4": "科创项目",
        "5": "财税审计", "6": "数据与咨询", "7": "法律服务", "8": "国际专利",
        "9": "国际商标", "10": "非标准产品", "11": "第三方业务", "12": "IP运营",
        "1,2": "国内专利+国内商标", "1,3": "国内专利+国内著作权", "2,3": "国内商标+国内著作权",
        "1,2,3": "国内专利+商标+著作权", "1,2,3,4,5,6,7": "全业务总委(不含国际)",
        "1,2,3,4,5,6,7,8": "全业务总委(含国际专利)", "": "空值(保密协议)"
    }

    s2_rows = []
    # Sort the 78 active templates by folder then code so 1~5级 hierarchy is super clean
    active_tpl_rows = [t for t in tpl_rows if t["template_code"] in TEMPLATE_5TH_LAYER_RULES]
    active_tpl_rows.sort(key=lambda t: (ref_folder_map.get(t["template_code"], "99"), t["template_code"]))

    for t in active_tpl_rows:
        tcode = t["template_code"]
        tname = t["template_name"]
        ai_raw = str(t["auth_item"] or "").strip()
        ai_name = auth_cn_simple.get(ai_raw, ai_raw or "无委托事项(保密协议)")
        folder_cat = ref_folder_map.get(tcode, "标准在用")
        rule5 = TEMPLATE_5TH_LAYER_RULES[tcode]
        l1_name, l1_code, l2_name, l2_code, l3_desc, oa_str = get_l1_l2_l3_for_template(
            tcode, ai_raw, t["contract_type"], t["business_type"]
        )
        new_p_list = []
        for npc in rule5.get("new_products", []):
            np_name = all_new_prods_meta.get(npc, {}).get("product_name", "")
            new_p_list.append(f"{npc}:{np_name}" if np_name else npc)
        new_p_str = "；".join(new_p_list) if new_p_list else "无(非商品类协议，按合同类型直接触发)"
        erp_p_str = "；".join(rule5.get("erp_products", [])) if rule5.get("erp_products") else "无"
        use_c = tpl_use_cnt.get(tcode, 0)

        s2_rows.append([
            ai_raw if ai_raw != "" else "(空)",
            ai_name,
            f"标准在用 ({folder_cat})",
            l1_name,
            l1_code,
            l2_name,
            l2_code,
            l3_desc,
            f"{rule5['sub_scene']} | 标的：{rule5['target_constraint']}",
            rule5["fee_mode"],
            new_p_str,
            erp_p_str,
            f"{oa_str} | 新系统已签 {use_c} 单",
            1,
            f"{tcode}({tname}) [{ref_existing_map.get(tcode, '')}]",
            rule5["disambiguation_rule"],
        ])

    # Append the historical OA compatibility & non-standard auth_item rows (10, 11, 12, 13~18) so zero dict items are lost
    oa_compat_rows = [
        ("6", "数据与咨询(独立枚举)", "枚举映射说明", "S (高端咨询)", "S", "数据与咨询", "S-C", "单委咨询(1+6) · 高端咨询/检索/导航/FTO/无效类", "对应上方 auth_item=1 中实属 S-C 数据与咨询的8套专项报告/无效/顾问模板", "按咨询项目或固定费结算", "CNSC0001~CNSC0025 全系列数据与咨询产品", "010501~010508、010111", "旧OA wtsx=10/13 | 申请表30单/归档表27单", 8, "MB20251205020；MB20251205022；MB20251205023；MB20251205025；MB20251205026；MB20251205028；MB20251205029；MB20251229002", "数据库模板表将这些模板挂在 auth_item=1 下，但产品表属 S-C(数据与咨询)，按第5级产品编码 CNSC* 可1对1精准路由至上述8套模板"),
        ("10", "非标准产品", "标准在用(非标)", "M (综合业务)", "M", "非标准产品", "-", "非固定模板起草 (draft_type=2)", "非标定制服务，无固定标准模板", "按定制合同约定", "非标手动报价产品", "-", "旧OA wtsx=14 | 申请表530单", 0, "采用 draft_type=2 上传自定义合同（或借用 MB20251205082 技术咨询 / MB20251205073 非诉专项）", "非标准产品不强制走固定模板，由法务审核自定义文本"),
        ("11", "第三方业务(独立枚举)", "枚举映射说明", "P (政策辅导)", "P", "第三方业务", "P-R", "单委服务(1+6) · 科技查新/软件测试/ISO/贯标/评估", "对应上方 auth_item=4/1 中承担 P-R 第三方业务的3套模板", "固定收费或分阶段收费", "CNPR0001~CNPR0066 全系列第三方业务产品", "050101、050201", "新系统原生29单", 3, "MB20251205063(软件查新服务合同)；MB20251205082(技术咨询服务合同)；MB20251205024(专利评价报告)", "查新/软件测试走 063；ISO/贯标/绿色工厂/资产评估走 082；专利评价报告走 024"),
        ("12", "IP运营(独立枚举)", "枚举映射说明", "O (IP运营)", "O", "IP运营", "O-P", "单委交易(1+6) · 专利/商标买卖交易与许可运营", "对应上方运营主体买方/卖方专利商标转让合同及许可备案合同", "按交易买卖价款或运营分成结算", "CNIP0019、CNIT0003、CNOP0001~0003", "010701~020702", "新系统原生9单", 6, "MB20251205045；MB20251205046；MB20251205043；MB20251205044；MB20251205015；MB20251205053", "买专利走045、卖专利走046；买商标走043、卖商标走044；许可走015/053"),
        ("13", "交易(历史OA遗留)", "来源oa未映射字典", "C (合作) / O (IP运营)", "C / O", "知产交易(历史)", "C / O-P", "历史OA迁移数据兼容 (旧OA wtsx=6)", "历史OA知产买卖或转让代理合同", "按转让合同约定", "映射至 CNIP0019 / CNIP0023 / CNIT0003", "010402、020202", "旧OA迁移996单", 7, "MB20251205045；MB20251205046；MB20251205043；MB20251205044；MB20251205027；MB20251205058；MB20251205068", "RAG清洗时归一化至 IP运营(12) 或 专利/商标转让(1/2)"),
        ("14", "合作(历史OA遗留)", "来源oa未映射字典", "D (电商) / H (渠道合作)", "D / H", "渠道/战略合作", "D / H-Q", "历史OA迁移数据兼容 (旧OA wtsx=8)", "外部渠道合作、战略合作框架", "按合作协议约定", "渠道合作协议", "-", "旧OA迁移434单", 2, "MB20251205070(知识产权战略保护合作协议)；MB20251205089(知识产权服务合同)", "外部渠道与战略合作协议"),
        ("15", "工商财会(历史OA遗留)", "来源oa未映射字典", "P (政策辅导)", "P", "财税审计(历史)", "P-S", "历史OA迁移数据兼容 (旧OA wtsx=9)", "年度财审/专审/代理记账（注意排查2020年旧OA误选单）", "固定审计费", "映射至 CNPS0001~0024", "060101", "旧OA迁移1223单(归档修正后1034单)", 2, "MB20251205061(广东审计报告合同)；MB20251205062(非广东审计报告合同)", "【避坑】209单旧OA误选已由归档表修正，务必以 contract_archive.auth_item 为准"),
        ("16", "发明专利授权宝(历史OA)", "来源oa未映射字典", "I (知识产权)", "I", "国内专利(授权宝)", "I-P", "历史OA迁移数据兼容 (旧OA wtsx=11)", "发明专利风险代理/不过退费产品", "风险代理收费", "映射至 CNIP0013~0015", "010101-风险", "旧OA迁移4475单", 1, "MB20251209001(国内专利风险代理服务合同（申请）)", "现统一归入 auth_item=1 下的 MB20251209001 风险代理模板"),
        ("17", "知识产权风险代理产品(历史OA)", "来源oa未映射字典", "I (知识产权)", "I", "国内专利/商标(风险)", "I-P / I-T", "历史OA迁移数据兼容 (旧OA wtsx=12)", "专利/商标/诉讼风险代理产品", "风险代理收费", "映射至 CNIP0013~0015 / CNIT0040", "100126", "旧OA迁移1296单", 3, "MB20251209001(专利风险)；MB20251205059(商标风险包过)；MB20251205076(民事维权全风险)", "按专利/商标/诉讼细分归入对应风险模板"),
        ("18", "农业项目(历史OA遗留)", "来源oa未映射字典", "P (政策辅导)", "P", "科创项目(农业)", "P-B", "历史OA迁移数据兼容 (旧OA wtsx=16)", "农业政策补贴与科创项目申报", "项目咨询费", "映射至 CNPB* 科创项目产品", "040101", "旧OA迁移4单", 1, "MB20251205082(技术咨询服务合同)", "现统一归入 auth_item=4 科创项目通用模板 MB20251205082"),
    ]
    for r_compat in oa_compat_rows:
        s2_rows.append(list(r_compat))

    # =========================================================================
    # SHEET 3: 03_标准合同模板路由总表 (80 Unique Template Configs)
    # =========================================================================
    with conn_contract.cursor() as cur:
        cur.execute("SELECT id, name, company_code FROM contract_my_mainbody")
        mb_map = {str(r["id"]): f"{r['name']}({r['company_code'] or '无代码'})" for r in cur.fetchall()}

        cur.execute("""
            SELECT t.template_code, p.product_code, p.product_name
            FROM contract_template_products p
            JOIN contract_template t ON p.contract_template_id = t.id
            WHERE p.deleted = 0 AND t.deleted = 0
            GROUP BY t.template_code, p.product_code, p.product_name
            ORDER BY t.template_code, p.product_code
        """)
        prod_by_tpl = defaultdict(list)
        for r in cur.fetchall():
            prod_by_tpl[r["template_code"]].append(f"{r['product_code']}:{r['product_name']}")

        cur.execute("""
            SELECT contract_template_code, COUNT(*) as cnt
            FROM contract_apply
            WHERE deleted = 0 AND contract_template_code IS NOT NULL AND contract_template_code != ''
            GROUP BY contract_template_code
        """)
        tpl_use_cnt = {r["contract_template_code"]: r["cnt"] for r in cur.fetchall()}

        cur.execute("""
            SELECT t.template_code, a.doc_name, a.oss_url
            FROM contract_template t
            LEFT JOIN contract_attachment a ON a.source_id = t.id AND a.source_type = 3 AND a.deleted = 0
            WHERE t.deleted = 0 AND t.is_current = 1
        """)
        tpl_file_map = {r["template_code"]: (r.get("doc_name") or "", r.get("oss_url") or "") for r in cur.fetchall()}

    from template_5th_layer_data import TEMPLATE_5TH_LAYER_RULES, DISAMBIGUATION_GROUPS

    ref_existing_map = {}
    for root_dir, _, files in os.walk("参考"):
        for fn in sorted(files):
            if fn.endswith(".docx") and fn.startswith("MB"):
                code_part = fn.split("_")[0]
                folder_name = os.path.basename(root_dir)
                ref_existing_map[code_part] = f"{folder_name}/{fn}"

    ct_label = {1: "1-销售合同", 2: "2-采购合同", 3: "3-服务协议", 5: "5-律所合同"}
    bt_label = {3: "3-生态合作伙伴协议", 6: "6-普通新增产品", 7: "7-总委托业务", 8: "8-保密协议", 9: "9-合作协议"}
    auth_label_map = {
        "1": "国内专利", "2": "国内商标", "3": "国内著作权", "4": "科创项目",
        "5": "财税审计", "6": "数据与咨询", "7": "法律服务", "8": "国际专利",
        "9": "国际商标", "10": "非标准产品", "11": "第三方业务", "12": "IP运营"
    }

    s3_headers = [
        "模板编码 (template_code)", "标准模板名称 (template_name)", "是否在[参考]文件夹中",
        "[参考]文件夹对应路径", "数据库最新模板附件名 (doc_name)", "数据库最新模板下载链接 (oss_url)",
        "合同类型 (contract_type)", "业务类型 (business_type)", "适用委托事项编码 (auth_item)", "适用委托事项中文",
        "最新版本号", "历史迭代版本数", "新系统已调用次数",
        "绑定产品数 (含第5层补齐)", "绑定的产品明细 (新系统产品+ERP编码，已全量补齐)",
        "适用签约主体数量", "适用我方签约主体明细 (match_body_id)", "最后更新时间",
        "【第5层补齐】细分业务场景定位", "【第5层补齐】收费与结算模式约束", "【第5层补齐】委托标的与交易方向约束",
        "【第5层核心】同分类易混模板排他决策规则", "数据库原表绑定状态"
    ]
    s3_rows = []
    for t in tpl_rows:
        tcode = t["template_code"]
        ai_raw = str(t["auth_item"] or "")
        ai_cn = " + ".join(auth_label_map.get(x.strip(), x.strip()) for x in ai_raw.split(",") if x.strip()) or "通用/无特定事项"
        mb_ids = [x.strip() for x in str(t["match_body_id"] or "").split(",") if x.strip()]
        mb_names = [f"{mid}:{mb_map.get(mid, '未知主体')}" for mid in mb_ids]
        db_prods = prod_by_tpl.get(tcode, [])
        ref_fn = ref_existing_map.get(tcode, "")
        doc_name, oss_url = tpl_file_map.get(tcode, ("", ""))
        rule5 = TEMPLATE_5TH_LAYER_RULES.get(tcode, {})

        # Merge DB bound products + 5th-layer supplemented new & ERP products for Column N & Column O
        combined_prods = list(db_prods)
        seen_p = {p.split(":")[0] for p in db_prods}
        for npc in rule5.get("new_products", []):
            if npc not in seen_p:
                np_name = all_new_prods_meta.get(npc, {}).get("product_name", "新系统标准产品")
                combined_prods.append(f"{npc}:{np_name}")
                seen_p.add(npc)
        for epc in rule5.get("erp_products", []):
            if epc not in seen_p:
                combined_prods.append(f"ERP-{epc}")
                seen_p.add(epc)

        if tcode == "MB20251205011":
            col_o_text = "无绑定产品（纯商业秘密与技术信息保密协议NDA，按contract_type=4直接触发）"
        elif tcode == "MB20251226001":
            col_o_text = "测试模板（已忽略）"
        else:
            col_o_text = "；".join(combined_prods)

        db_bind_status = f"数据库已绑({len(db_prods)}项)" if db_prods else f"原库未绑，已补齐({len(combined_prods)}项)"

        s3_rows.append([
            tcode,
            t["template_name"],
            "已就绪 (78套标准模板之一)" if ref_fn else ("测试模板(已忽略)" if tcode == "MB20251226001" else "待核实"),
            ref_fn or "-",
            doc_name,
            oss_url,
            ct_label.get(t["contract_type"], str(t["contract_type"])),
            bt_label.get(t["business_type"], str(t["business_type"])),
            ai_raw if ai_raw else "(空)",
            ai_cn,
            t["latest_ver"],
            t["ver_cnt"],
            tpl_use_cnt.get(tcode, 0),
            len(combined_prods),
            col_o_text,
            len(mb_ids),
            "；".join(mb_names[:10]) + (f" 等共{len(mb_names)}家" if len(mb_names) > 10 else ""),
            clean_xml_str(t["last_upd"]),
            rule5.get("sub_scene", "测试模板(不参与路由)"),
            rule5.get("fee_mode", "-"),
            rule5.get("target_constraint", "-"),
            rule5.get("disambiguation_rule", "-"),
            db_bind_status,
        ])

    # =========================================================================
    # SHEET 4: 04_模板与ERP产品绑定明细 (contract_template_products + 补齐明细)
    # =========================================================================
    s4_headers = [
        "模板编码 (template_code)", "模板名称 (template_name)", "适用委托事项 (auth_item)",
        "产品编码 (product_code)", "产品名称 (product_name)", "产品ID (product_id)",
        "绑定模板版本数", "绑定来源状态"
    ]
    s4_rows = []
    existing_s4_pairs = set()
    with conn_contract.cursor() as cur:
        cur.execute("""
            SELECT t.template_code, t.template_name, t.auth_item,
                   p.product_code, p.product_name, p.product_id, COUNT(DISTINCT t.id) as ver_cnt
            FROM contract_template_products p
            JOIN contract_template t ON p.contract_template_id = t.id
            WHERE p.deleted = 0 AND t.deleted = 0
            GROUP BY t.template_code, t.template_name, t.auth_item, p.product_code, p.product_name, p.product_id
            ORDER BY t.template_code, p.product_code
        """)
        for r in cur.fetchall():
            existing_s4_pairs.add((r["template_code"], r["product_code"]))
            s4_rows.append([
                r["template_code"], r["template_name"], r["auth_item"],
                r["product_code"], r["product_name"], str(r["product_id"]), r["ver_cnt"],
                "数据库已绑定 (孙梦婷/阳佳佳原配)"
            ])

    for t in tpl_rows:
        tcode = t["template_code"]
        rule5 = TEMPLATE_5TH_LAYER_RULES.get(tcode, {})
        for npc in rule5.get("new_products", []):
            if (tcode, npc) not in existing_s4_pairs:
                pmeta = all_new_prods_meta.get(npc, {})
                s4_rows.append([
                    tcode, t["template_name"], str(t["auth_item"] or ""),
                    npc, pmeta.get("product_name", "新系统标准产品"), str(pmeta.get("product_id", "")),
                    1, "第5层补齐绑定 (历史实签/条款精准匹配)"
                ])
                existing_s4_pairs.add((tcode, npc))

    # =========================================================================
    # SHEET 5: 05_我方签约主体映射表 (contract_my_mainbody & Old OA)
    # =========================================================================
    s5_headers = [
        "新库主体ID (id)", "我方签约主体全称 (name)", "合同编号主体代码 (company_code)",
        "统一社会信用代码", "法定代表人", "注册地址", "联系电话",
        "状态", "全库合同使用次数 (contract_apply)", "说明"
    ]
    s5_rows = []
    with conn_contract.cursor() as cur:
        cur.execute("SELECT * FROM contract_my_mainbody ORDER BY id")
        for r in cur.fetchall():
            mid = str(r["id"])
            is_del = r.get("deleted") in (b"\x01", 1)
            cnt = apply_counts["my_main_body"].get(mid, 0)
            s5_rows.append([
                r["id"],
                r.get("name") or "",
                r.get("company_code") or "",
                r.get("unified_social_credit_code") or "",
                r.get("legal_representative") or "",
                r.get("address") or "",
                r.get("contact_number") or r.get("phone") or "",
                "已停用/删除" if is_del else "在用",
                cnt,
                f"对应旧OA wfqygs={r['id']}（部分早期主体在旧OA wfqy 有独立编号）"
            ])

    # =========================================================================
    # SHEET 6: 06_合同编号前缀分类映射表 (All 83 Prefixes in data/contract_merged)
    # =========================================================================
    import re
    s6_headers = [
        "排名", "合同编号前缀", "归档文件夹 (cases/子目录)", "业务分类名称", "合并库案件数量",
        "占全库比例 (%)", "前缀编码体系类型", "前缀业务/主体含义解析", "样本合同编号", "样本合同名称"
    ]
    prefix_meanings = {
        "CIP": ("旧OA综合知识产权前缀", "综合知识产权/专利/商标/版权/法律合同"),
        "HC": ("2026新制度主体前缀", "集团专利商标核心代理主体（主体代码 HC）"),
        "SJ": ("2026新制度主体前缀", "集团专利代理事务所主体（主体代码 SJ）"),
        "P": ("旧OA单字母板块前缀", "政策辅导/科创项目板块合同（Policy/Project）"),
        "I": ("旧OA单字母板块前缀", "知识产权板块合同（IP：专利/商标/版权）"),
        "Z": ("旧OA单字母板块前缀", "总委托框架业务合同（ZongWei）"),
        "C": ("旧OA单字母板块前缀", "合作/知识产权交易类合同（Cooperation）"),
        "L": ("旧OA单字母板块前缀", "法律服务/诉讼维权板块合同（Legal）"),
        "HCKJ": ("旧OA主体+业务前缀", "集团科创项目服务主体（主体代码 HCKJ）"),
        "S": ("旧OA单字母板块前缀", "高端咨询/数据分析板块合同（Strategy/Service）"),
        "QH": ("旧OA主体前缀", "集团网络科技/电商版权服务主体（主体代码 QH）"),
        "GW": ("2026新制度主体前缀", "集团知产交易与资产运营主体（主体代码 GW）"),
        "BJ": ("2026新制度主体前缀", "北京分支代理主体（主体代码 BJ）"),
        "GZ": ("2026新制度主体前缀", "广州分支服务主体（主体代码 GZ）"),
        "WX": ("2026新制度主体前缀", "无锡分支服务主体（主体代码 WX）"),
        "WH": ("2026新制度主体前缀", "武汉分支服务主体（主体代码 WH）"),
        "HS": ("2026新制度主体前缀", "集团企业管理咨询主体（主体代码 HS）"),
        "HK": ("2026新制度主体前缀", "香港国际知产服务主体（主体代码 HK）"),
        "HZ": ("2026新制度主体前缀", "杭州分支科创主体（主体代码 HZ）"),
        "MG": ("2026新制度主体前缀", "集团网络技术服务主体（主体代码 MG）"),
        "TZ": ("2026新制度主体前缀", "集团合作律师事务所主体（主体代码 TZ，专用于法律诉讼维权）"),
        "SZ": ("2026新制度主体前缀", "苏州分支服务主体（主体代码 SZ）"),
        "CD": ("2026新制度主体前缀", "成都分支服务主体（主体代码 CD）"),
        "FS": ("2026新制度主体前缀", "佛山分支服务主体（主体代码 FS）"),
        "CS": ("2026新制度主体前缀", "长沙分支服务主体（主体代码 CS）"),
        "HF": ("2026新制度主体前缀", "合肥分支服务主体（主体代码 HF）"),
        "BQ": ("2026新制度主体前缀", "集团科创咨询主体（主体代码 BQ）"),
        "TC": ("2026新制度主体前缀", "集团科创研究主体（主体代码 TC）"),
        "ZQ": ("2026新制度主体前缀", "合作税务师事务所主体（主体代码 ZQ）"),
        "MS": ("2026新制度主体前缀", "合作会计师事务所主体（主体代码 MS）"),
        "XZ": ("2026新制度主体前缀", "合作专项审计会计师事务所主体（主体代码 XZ）"),
        "YSP": ("2026新制度主体前缀", "区域网络技术服务主体（主体代码 YSP）"),
    }
    prefix_counter = Counter()
    prefix_folder = {}
    prefix_cat_name = {}
    prefix_sample_no = {}
    prefix_sample_name = {}
    cls_csv = "data/contract_merged/contracts_classification.csv"
    if os.path.exists(cls_csv):
        with open(cls_csv, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cno = (row.get("合同编号") or "").strip()
                m = re.match(r"^([A-Za-z]+)", cno)
                p = m.group(1).upper() if m else (row.get("业务分类代字") or "数字/无前缀")
                prefix_counter[p] += 1
                prefix_folder[p] = row.get("归档文件夹", "")
                prefix_cat_name[p] = row.get("业务分类名称", "")
                if p not in prefix_sample_no:
                    prefix_sample_no[p] = cno
                    prefix_sample_name[p] = row.get("合同名称", "")

    total_cases = sum(prefix_counter.values()) or 1
    s6_rows = []
    for rank, (p, cnt) in enumerate(prefix_counter.most_common(), start=1):
        ptype, pdesc = prefix_meanings.get(
            p,
            ("分支机构/历史专项前缀", f"对应 {prefix_cat_name.get(p, '专项业务')}（{'独立文件夹归档' if cnt > 50 else '<=50件归入其他业务文件夹'}）")
        )
        s6_rows.append([
            rank, p, prefix_folder.get(p, ""), prefix_cat_name.get(p, ""), cnt,
            round(cnt * 100.0 / total_cases, 2),
            ptype, pdesc, prefix_sample_no.get(p, ""), prefix_sample_name.get(p, "")
        ])

    # =========================================================================
    # SHEET 7: 07_模板占位符与表单字段映射 (contract_config_fill_field & template_file_key_map)
    # =========================================================================
    s7_headers = [
        "映射表来源", "记录ID", "字段/占位符编码 (file_key / fill_name)",
        "字段中文名称 (file_key_msg / name)", "父级节点 / 填充默认值 (file_parent_key / value)",
        "类型标识 (file_type / type_id)", "备注说明 (remark)"
    ]
    s7_rows = []
    with conn_contract.cursor() as cur:
        cur.execute("SELECT * FROM template_file_key_map WHERE deleted = 0 ORDER BY id")
        for r in cur.fetchall():
            s7_rows.append([
                "Word模板域变量映射 (template_file_key_map)",
                r.get("id"),
                r.get("file_key") or "",
                r.get("file_key_msg") or "",
                r.get("file_parent_key") or "顶层变量",
                f"file_type={r.get('file_type')}",
                "Word合同模板正文自动替换域变量"
            ])
        cur.execute("SELECT * FROM contract_config_fill_field WHERE deleted = 0 ORDER BY sort, id")
        for r in cur.fetchall():
            s7_rows.append([
                "合同模板在线填充字段 (contract_config_fill_field)",
                r.get("id"),
                r.get("fill_name") or "",
                r.get("name") or "",
                r.get("value") or "",
                f"type_id={r.get('type_id')}",
                r.get("remark") or ""
            ])

    # =========================================================================
    # SHEET 8: 08_旧OA表单下拉字典对照 (ecologyE9 workflow_selectitem)
    # =========================================================================
    s8_headers = [
        "旧OA表单表名", "流程BillID", "字段ID (fieldid)", "旧OA字段英文名 (fieldname)",
        "旧OA字段中文标签 (labelname)", "下拉选项值 (selectvalue)", "下拉选项中文名 (selectname)",
        "是否已作废 (cancel)", "对应新系统表与字段映射说明"
    ]
    s8_rows = []
    oa_field_map_notes = {
        "wtsx": "映射至 contract_apply.auth_item / contract_archive.auth_item（注意旧OA 0=国内专利->1, 5=国内商标->2, 1=著作权->3, 3=法律->7, 4=国际专利->8, 7=国际商标->9）",
        "htlx": "在-130中 0=产品合同->business_type=6；在-474中 0=总委->7, 1=保密->8, 2=合作->9, 3=生态合作->3",
        "htzt": "映射至 contract_status（-130中 0=签署->1审批中, 2=归档->3已归档, 3=作废->5作废, 4=异常归档->4）",
        "htbb": "映射至 contract_version（0=标准版->1, 1=可退款版->2）",
        "fkfa": "映射至 pay_scheme（0~4 依次 +1 映射为 1~5）",
        "yclx": "映射至 contract_flow_type（0=正常->1, 1=价格偏低->2, 2=条款变更->3）",
        "htlzlx": "映射至 contract_flow_type（0=正常->1, 1=价格偏低->2, 2=条款变更->3）",
        "hklx": "映射至 contract_apply_performance_item.repayment_type（财务回款/业绩拆分类型）",
        "hklxa": "映射至 contract_apply_performance_item.repayment_type（财务回款/业绩拆分类型）",
        "wfqygs": "映射至 my_main_body_id（我方签约主体ID）",
        "qygsycj": "映射至 my_main_body_id（我方签约主体ID）",
        "zq": "映射至 contract_payment_term（账期：0~4 依次 +1 映射为 1~5）",
    }
    try:
        ms_conn = pytds.connect(
            dsn=env["DB_HOST"],
            port=int(env.get("DB_PORT", 1433)),
            user=env["DB_USER"],
            password=env["DB_PWD_READONLY"],
            database=env.get("DB_NAME", "ecologyE9"),
            as_dict=True,
        )
        with ms_conn.cursor() as mcur:
            mcur.execute("""
                SELECT bf.billid, b.tablename, bf.id as fieldid, bf.fieldname, hl.labelname,
                       si.selectvalue, si.selectname, si.cancel
                FROM workflow_billfield bf
                INNER JOIN workflow_bill b ON bf.billid = b.id
                LEFT JOIN HtmlLabelInfo hl ON bf.fieldlabel = hl.indexid AND hl.languageid = 7
                INNER JOIN workflow_selectitem si ON bf.id = si.fieldid
                WHERE bf.billid IN (-130, -474)
                ORDER BY bf.billid DESC, bf.id, si.selectvalue
            """)
            for r in mcur.fetchall():
                fname = r["fieldname"] or ""
                s8_rows.append([
                    r["tablename"],
                    r["billid"],
                    r["fieldid"],
                    fname,
                    r["labelname"] or "",
                    r["selectvalue"],
                    r["selectname"] or "",
                    "已作废(1)" if r["cancel"] == 1 else "在用(0)",
                    oa_field_map_notes.get(fname, "旧OA流程表单辅助控制字段")
                ])
        ms_conn.close()
    except Exception as e:
        s8_rows.append(["SQL Server 连接异常", "", "", "", "", "", str(e), "", ""])

    # =========================================================================
    # SHEET 9: 09_系统全局字典表全量明细 (tender_system.system_dict_type & data)
    # =========================================================================
    s9_headers = [
        "字典类型ID", "字典类型编码 (dict_type)", "字典类型名称 (dict_name)",
        "字典项排序 (sort)", "字典项值 (value)", "字典项标签 (label)",
        "状态", "备注说明 (remark)"
    ]
    s9_rows = []
    with conn_sys.cursor() as scur:
        scur.execute("""
            SELECT t.id as type_id, t.type as dict_type, t.name as dict_name,
                   d.sort, d.value, d.label, d.status, d.remark
            FROM system_dict_type t
            JOIN system_dict_data d ON t.type = d.dict_type
            WHERE t.deleted = 0 AND d.deleted = 0
              AND (
                  t.type LIKE '%contract%' OR t.type LIKE '%entrust%' OR t.type LIKE '%fllow%'
                  OR t.type LIKE '%finance%' OR t.type LIKE '%pay%' OR t.type LIKE '%crm%'
                  OR t.type LIKE '%seal%' OR t.type LIKE '%invoice%' OR t.type LIKE '%business%'
              )
            ORDER BY t.id, d.sort, d.id
        """)
        for r in scur.fetchall():
            s9_rows.append([
                r["type_id"],
                r["dict_type"],
                r["dict_name"],
                r["sort"],
                r["value"],
                r["label"],
                "正常(0)" if r["status"] == 0 else f"停用({r['status']})",
                r["remark"] or ""
            ])

    # =========================================================================
    # SHEET 10: 10_申请与归档委托事项错位明细 (209 Discrepancy Records)
    # =========================================================================
    s10_headers = [
        "合同编号 (contract_no)", "合同名称 (contract_name)", "申请表ID (apply_id)",
        "申请表委托事项 (apply.auth_item)", "申请表委托事项译名",
        "归档表ID (archive_id)", "归档表委托事项 (archive.auth_item - 权威修正值)",
        "归档表委托事项译名", "业务类型 (business_type)", "错位原因说明"
    ]
    s10_rows = []
    full_auth_label = {
        "1": "国内专利", "2": "国内商标", "3": "国内著作权", "4": "科创项目",
        "5": "财税审计", "6": "数据与咨询", "7": "法律服务", "8": "国际专利",
        "9": "国际商标", "10": "非标准产品", "11": "第三方业务", "12": "IP运营",
        "13": "交易(OA遗留)", "14": "合作(OA遗留)", "15": "工商财会(OA遗留)",
        "16": "发明专利授权宝(OA遗留)", "17": "知识产权风险代理(OA遗留)", "18": "农业项目(OA遗留)"
    }
    with conn_contract.cursor() as cur:
        cur.execute("""
            SELECT a.contract_no, a.contract_name, a.id as apply_id, a.auth_item as apply_auth,
                   ar.id as archive_id, ar.auth_item as archive_auth, a.business_type
            FROM contract_apply a
            JOIN contract_archive ar ON a.id = ar.contract_apply_id
            WHERE a.deleted = 0 AND ar.deleted = 0 AND a.auth_item != ar.auth_item
            ORDER BY a.contract_no
        """)
        for r in cur.fetchall():
            ap_raw = str(r["apply_auth"] or "")
            ar_raw = str(r["archive_auth"] or "")
            ap_cn = "+".join(full_auth_label.get(x.strip(), x.strip()) for x in ap_raw.split(",") if x.strip())
            ar_cn = "+".join(full_auth_label.get(x.strip(), x.strip()) for x in ar_raw.split(",") if x.strip())
            s10_rows.append([
                r["contract_no"] or "",
                r["contract_name"] or "",
                r["apply_id"],
                ap_raw,
                ap_cn,
                r["archive_id"],
                ar_raw,
                ar_cn,
                bt_label.get(r["business_type"], str(r["business_type"])),
                "旧OA发起流程(formtable_main_130)选错wtsx，归档入台账(uf_hettz)时由管理员修正，RAG应以归档表(archive.auth_item)为准"
            ])

    conn_contract.close()
    conn_sys.close()

    s11_headers = [
        "辨析组号",
        "易混业务板块与辨析主题",
        "数据库1~4层完全相同的字段值(导致冲突原因)",
        "涉及的冲突/候选模板编码清单",
        "【第5层排他决策树】精准二选一/多选一判定逻辑",
    ]
    s11_rows = [
        [
            g["group_id"],
            g["group_name"],
            g["shared_db_mapping"],
            g["conflict_templates"],
            g["decision_logic"],
        ]
        for g in DISAMBIGUATION_GROUPS
    ]

    sheets = [
        ("01_核心分类字段枚举总表", s1_headers, s1_rows),
        ("02_委托事项与业务板块映射", s2_headers, s2_rows),
        ("03_标准合同模板路由总表", s3_headers, s3_rows),
        ("04_模板与ERP产品绑定明细", s4_headers, s4_rows),
        ("05_我方签约主体映射表", s5_headers, s5_rows),
        ("06_合同编号前缀分类映射表", s6_headers, s6_rows),
        ("07_模板占位符与表单字段", s7_headers, s7_rows),
        ("08_旧OA表单下拉字典对照", s8_headers, s8_rows),
        ("09_系统全局字典表全量明细", s9_headers, s9_rows),
        ("10_申请与归档事项错位核对", s10_headers, s10_rows),
        ("11_同分类易混模板排他决策树", s11_headers, s11_rows),
    ]

    for target_xlsx in [
        "data/contract_merged/合同分类与模板全量映射字典.xlsx",
        "data/contract_merged/合同分类与模板全量映射字典_v2.xlsx",
        "data/contract_merged/合同分类与模板全量映射字典_v3.xlsx",
        "data/contract_merged/合同分类与模板全量映射字典_v4.xlsx",
    ]:
        try:
            write_styled_xlsx(target_xlsx, sheets)
            print(f"Successfully exported {target_xlsx}")
        except PermissionError:
            print(f"Skipped locked file: {target_xlsx}")
    for name, _, rows in sheets:
        print(f"  - {name}: {len(rows)} rows")


if __name__ == "__main__":
    main()
