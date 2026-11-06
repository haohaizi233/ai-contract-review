# -*- coding: utf-8 -*-
"""
生成《78套标准模板第5层细粒度路由与产品绑定规则表.xlsx》
并同步更新：
1. data/contract_merged/合同分类与模板全量映射字典_v2.xlsx
2. data/contract_merged/contract_mapping_dictionary.json
3. data/contract_merged/contract_template_products_supplement.sql
"""

import os
import re
import sys
import json
from datetime import datetime
from collections import defaultdict

import pymysql

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tender_fullsync import load_env
from export_contract_mappings_excel import write_styled_xlsx
from template_5th_layer_data import TEMPLATE_5TH_LAYER_RULES, DISAMBIGUATION_GROUPS

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = r"d:\Users\Administrator\Desktop\AI合同"
SCRATCH_DIR = r"C:\Users\Administrator\.gemini\antigravity\brain\7d2ac74e-aac3-470e-bc9e-fe6de56d5876\scratch"
OUT_DIR = os.path.join(BASE_DIR, "data", "contract_merged")


def extract_clause_summaries(content_dict):
    """从解析出的 docx 段落中提取【第一条 委托事项】与【费用/退费条款】摘要"""
    paras = content_dict.get("paragraphs", [])
    clause1_lines = []
    payment_lines = []
    in_c1 = False
    for p in paras:
        p_clean = p.strip()
        if not p_clean:
            continue
        if re.match(r"^(第一条|一、|1[\.、]|委托事项)", p_clean) or "受甲方委托" in p_clean or "甲方向乙方购买" in p_clean:
            in_c1 = True
        elif re.match(r"^(第二条|第三条|二、|三、)", p_clean) and len(clause1_lines) >= 1:
            in_c1 = False

        if in_c1 and len(clause1_lines) < 3:
            clause1_lines.append(p_clean)

        if any(k in p_clean for k in ["退还", "退费", "全额退", "预付款", "尾款", "包干", "分成", "不予退还", "后付费", "授权后"]):
            if len(payment_lines) < 3 and p_clean not in payment_lines:
                payment_lines.append(p_clean)

    if not clause1_lines and len(paras) > 2:
        clause1_lines = paras[1:4]

    c1_str = " / ".join(clause1_lines)[:260]
    pay_str = " / ".join(payment_lines)[:260] if payment_lines else "常规固定收费（按表列总金额一次性或按约支付，无特殊退费条款）"
    return c1_str, pay_str


def style_worksheet(ws, col_widths, freeze="A2", header_fill_hex="1F4E78"):
    header_font = Font(name="微软雅黑", size=10, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color=header_fill_hex, end_color=header_fill_hex, fill_type="solid")
    body_font = Font(name="微软雅黑", size=9)
    bold_font = Font(name="微软雅黑", size=9, bold=True)
    thin_side = Side(border_style="thin", color="D9D9D9")
    border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

    ws.freeze_panes = freeze
    ws.views.sheetView[0].showGridLines = True

    for col_idx, width in enumerate(col_widths, start=1):
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = width

    for row_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=len(col_widths)), start=1):
        if row_idx == 1:
            ws.row_dimensions[row_idx].height = 28
            for cell in row:
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                cell.border = border
        else:
            ws.row_dimensions[row_idx].height = 42
            for col_i, cell in enumerate(row, start=1):
                cell.font = bold_font if col_i in (1, 3, 4) else body_font
                cell.alignment = Alignment(
                    horizontal="center" if col_i in (1, 3, 5, 17, 18) else "left",
                    vertical="center",
                    wrap_text=True,
                )
                cell.border = border


def main():
    # 1. Load extracted docx + db products
    with open(os.path.join(SCRATCH_DIR, "extracted_78_templates_and_products.json"), "r", encoding="utf-8") as f:
        extracted = json.load(f)
    with open(os.path.join(SCRATCH_DIR, "all_new_products_with_ids.json"), "r", encoding="utf-8") as f:
        all_new_products = json.load(f)
    with open(os.path.join(OUT_DIR, "contract_mapping_dictionary.json"), "r", encoding="utf-8") as f:
        mapping_dict = json.load(f)

    # Build ERP product name lookup
    erp_lookup = {}
    for r in extracted.get("erp_products_top", []):
        code = r.get("production_code")
        name = r.get("production_name")
        if code and code not in erp_lookup:
            erp_lookup[code] = name
    for r in extracted.get("ipeasy_products", []):
        code = r.get("product_code")
        name = r.get("product_name")
        if code and code not in erp_lookup:
            erp_lookup[code] = name

    # Query DB for latest active template IDs and historical contract counts
    env = load_env()
    conn = pymysql.connect(
        host=env["MYSQL_HOST"],
        port=int(env.get("MYSQL_PORT", 3306)),
        user=env["MYSQL_USER"],
        password=env["MYSQL_PWD"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    latest_tpl_db = {}
    actual_contract_counts = defaultdict(int)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT *
            FROM tender_contract.contract_template
            WHERE deleted = 0
            ORDER BY id ASC
        """)
        for r in cur.fetchall():
            latest_tpl_db[r["template_code"]] = r

        cur.execute("""
            SELECT contract_template_code, COUNT(*) as cnt
            FROM tender_contract.contract_apply
            WHERE deleted = 0 AND contract_template_code IS NOT NULL AND contract_template_code != ''
            GROUP BY contract_template_code
        """)
        for r in cur.fetchall():
            actual_contract_counts[r["contract_template_code"]] = int(r["cnt"])
    conn.close()

    # Enum dictionaries from mapping_dict
    ct_map = {str(k): v for k, v in mapping_dict["enums"].get("contract_type", {}).items()}
    bt_map = {str(k): v for k, v in mapping_dict["enums"].get("business_type", {}).items()}
    ai_map = {str(k): v for k, v in mapping_dict["enums"].get("auth_item", {}).items()}

    def decode_multi(val_str, d_map):
        if val_str is None or val_str == "":
            return "未限定(通用)"
        parts = [p.strip() for p in str(val_str).split(",") if p.strip()]
        return " + ".join(f"{p}:{d_map.get(p, p)}" for p in parts)

    # Verify all 78 templates are covered
    docx_templates = extracted["templates"]
    missing = [c for c in docx_templates if c not in TEMPLATE_5TH_LAYER_RULES]
    if missing:
        raise RuntimeError(f"Missing 5th layer rules for templates: {missing}")

    # Prepare rows for Sheet 1 (78 templates master table) and Sheet 3 (DB product binding rows)
    sheet1_rows = []
    sheet3_binding_rows = []
    sql_lines = [
        "-- ============================================================================",
        f"-- 78套标准合同模板 - 待补录产品绑定关系 SQL 脚本 (tender_contract.contract_template_products)",
        f"-- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "-- 说明: 供业务/法务(孙梦婷、阳佳佳)核对确认后，由研发(管理员yu)执行导入",
        "-- ============================================================================",
        "",
    ]

    product_to_templates = defaultdict(list)

    sorted_codes = sorted(docx_templates.keys(), key=lambda c: (docx_templates[c]["folder"], c))
    for idx, code in enumerate(sorted_codes, start=1):
        t_info = docx_templates[code]
        rule = TEMPLATE_5TH_LAYER_RULES[code]
        db_row = latest_tpl_db.get(code, {})
        tpl_id = str(db_row.get("id", ""))
        tpl_name = db_row.get("template_name") or t_info["filename"].split("_", 2)[-1].replace(".docx", "")
        version = db_row.get("version") or t_info["filename"].split("_")[1]

        ct_val = db_row.get("contract_type")
        bt_val = db_row.get("business_type")
        ai_val = db_row.get("auth_item")
        ct_label = f"{ct_val}:{ct_map.get(str(ct_val), ct_val)}" if ct_val is not None else ""
        bt_label = decode_multi(bt_val, bt_map)
        ai_label = decode_multi(ai_val, ai_map)
        layer1_to_4_str = f"【合同类型】{ct_label}\n【项目类型】{bt_label}\n【授权事项】{ai_label}"

        c1_summary, pay_summary = extract_clause_summaries(t_info["content"])
        placeholders_str = ", ".join(t_info["content"].get("placeholders", [])[:15])

        # Existing DB bindings
        existing_bindings = extracted["existing_template_products"].get(code, [])
        existing_codes = {b["product_code"] for b in existing_bindings if b.get("product_code")}

        # Actual historical usage products
        actual_usages = extracted["template_actual_usage_products"].get(code, [])
        actual_codes = {u["production_code"] for u in actual_usages if u.get("production_code")}
        hist_contract_cnt = actual_contract_counts.get(code, 0)

        # Format recommended new products
        new_prod_Display = []
        for pcode in rule["new_products"]:
            p_meta = all_new_products.get(pcode, {})
            pname = p_meta.get("product_name", "")
            pid = p_meta.get("product_id", "")
            tag = ""
            if pcode in existing_codes:
                tag = "[库已绑]"
            elif pcode in actual_codes:
                tag = "[历史实签]"
            else:
                tag = "[条款匹配]"
            new_prod_Display.append(f"{pcode} {pname}{tag}" if pname else f"{pcode}{tag}")

            # Record reverse index
            product_to_templates[pcode].append({
                "template_code": code,
                "template_name": tpl_name,
                "folder": t_info["folder"],
                "sub_scene": rule["sub_scene"],
                "fee_mode": rule["fee_mode"],
                "source_tag": tag,
            })

            # Add to Sheet 3 binding rows
            sheet3_binding_rows.append([
                len(sheet3_binding_rows) + 1,
                tpl_id,
                code,
                tpl_name,
                version,
                pid or "待查/新增",
                pcode,
                pname or "见产品字典",
                p_meta.get("segment", ""),
                "已绑定(无需重导)" if pcode in existing_codes else ("历史签约已验证(强烈推荐落库)" if pcode in actual_codes else "模板正文条款匹配(推荐落库)"),
            ])

            if pcode not in existing_codes and tpl_id and pid:
                safe_pname = (pname or "").replace("'", "''")
                sql_lines.append(
                    f"INSERT INTO tender_contract.contract_template_products "
                    f"(contract_template_id, product_id, product_code, product_name, sort, creator, create_time, updater, update_time, deleted, tenant_id) "
                    f"SELECT {tpl_id}, {pid}, '{pcode}', '{safe_pname}', 0, '1', NOW(), '1', NOW(), b'0', 0 "
                    f"FROM DUAL WHERE NOT EXISTS ("
                    f"SELECT 1 FROM tender_contract.contract_template_products "
                    f"WHERE contract_template_id = {tpl_id} AND product_code = '{pcode}' AND deleted = 0);"
                )

        # Format ERP products
        erp_prod_display = []
        for ecode in rule["erp_products"]:
            ename = erp_lookup.get(ecode, "")
            etag = "[库已绑]" if ecode in existing_codes else ("[历史实签]" if ecode in actual_codes else "")
            erp_prod_display.append(f"{ecode} {ename}{etag}".strip())

        if existing_codes:
            db_status = f"已绑定({len(existing_codes)}个产品)"
        elif hist_contract_cnt > 0:
            db_status = f"未绑产品表(但有{hist_contract_cnt}单真实合同反查)"
        else:
            db_status = "未绑定(0产品/0历史单，靠条款补齐)"

        sheet1_rows.append([
            idx,
            t_info["folder"],
            code,
            tpl_name,
            version,
            layer1_to_4_str,
            rule["sub_scene"],
            rule["fee_mode"],
            rule["target_constraint"],
            rule["disambiguation_rule"],
            c1_summary,
            pay_summary,
            "、".join(rule["rag_keywords"]),
            "\n".join(new_prod_Display) if new_prod_Display else "无(非商品类协议)",
            "\n".join(erp_prod_display) if erp_prod_display else "无",
            placeholders_str,
            hist_contract_cnt,
            db_status,
            "□ 确认无误\n□ 需微调(请批注)",
        ])

    # Save SQL file
    sql_path = os.path.join(OUT_DIR, "contract_template_products_supplement.sql")
    with open(sql_path, "w", encoding="utf-8") as f:
        f.write("\n".join(sql_lines) + "\n")

    # Build Sheet 4: All 603 New Products -> Template Reverse Routing Table
    sheet4_rows = []
    for p_idx, (pcode, pmeta) in enumerate(sorted(all_new_products.items()), start=1):
        matched_tpls = product_to_templates.get(pcode, [])
        seg = pmeta.get("segment", "")
        pname = pmeta.get("product_name", "")

        # Fallback routing rule if a country-specific minor code wasn't explicitly listed in top codes
        if not matched_tpls:
            if pcode.startswith("PCTIP"):
                fallback = "MB20251205041 (PCT国际申请服务合同)"
            elif pcode.endswith("IP") or "IP0" in pcode and not pcode.startswith(("CNIP", "IP0")):
                if "包干" in pname:
                    fallback = "MB20251205037 ((包干费版)PCT巴黎公约国家阶段服务合同)"
                elif any(k in pname for k in ["答复", "答辩", "复审", "IDS"]):
                    fallback = "MB20251205038(包干) / MB20251205040(分阶段) 涉外专利答复复审合同"
                else:
                    fallback = "MB20251205039 ((分阶段费用版)涉外专利申请服务合同)"
            elif "IT0" in pcode and not pcode.startswith("CNIT"):
                if "新申请" in pname or "首类" in pname or "包三类" in pname:
                    fallback = "MB20251205035 (单一国际商标注册服务合同)"
                else:
                    fallback = "MB20251205030 (变更等国际商标案件服务合同)"
            elif pcode.startswith("CNPB") or pcode.startswith("CNPR"):
                fallback = "MB20251205082 (技术咨询服务合同)"
            elif pcode.startswith("CNPS"):
                fallback = "MB20251205061(广东) / MB20251205062(非广东) 审计报告服务合同"
            elif pcode.startswith("CNLS"):
                fallback = "MB20251205075(诉讼类) / MB20251205073(非诉类)"
            elif pcode.startswith("CNIP") or pcode.startswith("IP"):
                fallback = "MB20251205014(专利申请) / MB20251205086(专利组合)"
            elif pcode.startswith("CNIT"):
                fallback = "MB20251205057(商标注册) / MB20251205085(商标组合)"
            elif pcode.startswith("CNIC"):
                fallback = "MB20251205087(著作权) / MB20251205088(著作权组合)"
            else:
                fallback = "MB20251205082 (技术咨询服务合同)"
            primary_str = fallback
            combo_str = "同板块多事项选对应组合模板；跨板块选04_跨板块组合模板"
            match_type = "前缀规则自动推导"
        else:
            primary_list = [f"{m['template_code']}《{m['template_name']}》({m['sub_scene'][:18]})" for m in matched_tpls if "组合" not in m["template_name"] and "总委" not in m["folder"]]
            combo_list = [f"{m['template_code']}《{m['template_name']}》" for m in matched_tpls if "组合" in m["template_name"] or "总委" in m["folder"]]
            primary_str = "\n".join(primary_list) if primary_list else "\n".join(f"{m['template_code']}《{m['template_name']}》" for m in matched_tpls)
            combo_str = "\n".join(combo_list) if combo_list else "同板块多事项选组合模板(085/086/088)；跨板块选065~078"
            match_type = "核心产品精准绑定"

        sheet4_rows.append([
            p_idx,
            pcode,
            pname,
            seg or "自动归类",
            pmeta.get("product_id", ""),
            pmeta.get("cnt", 0),
            primary_str,
            combo_str,
            match_type,
        ])

    # ==================== Create Standalone Excel Workbook ====================
    ws1_headers = [
        "序号",
        "所属分类文件夹",
        "模板编码",
        "标准模板名称",
        "最新启用版本",
        "数据库第1~4层已有映射(合同类型/项目类型/授权事项)",
        "【第5层补齐】细分业务场景定位",
        "【第5层补齐】收费与结算模式约束(全款/分阶段/风险退费/后付费)",
        "【第5层补齐】委托标的与交易方向约束",
        "【第5层核心】同分类易混模板排他决策规则(RAG与业务选型必看)",
        "模板Word正文【第一条 委托事项】原文摘要",
        "模板Word正文【费用与退费条款】原文摘要",
        "RAG检索与意图触发关键词",
        "推荐绑定【新系统产品编码(CNIP/CNIT等)】(含库已绑/历史实签/条款匹配标记)",
        "推荐绑定【ERP/OA历史产品编码(01/02等)】",
        "Word模板核心动态占位符(${...})",
        "历史真实签约单数(contract_apply)",
        "数据库当前绑定状态(contract_template_products)",
        "业务/法务核对确认栏(孙梦婷/阳佳佳)",
    ]

    ws2_headers = [
        "辨析组号",
        "易混业务板块与辨析主题",
        "数据库1~4层完全相同的字段值(导致冲突原因)",
        "涉及的冲突/候选模板编码清单",
        "【第5层排他决策树】精准二选一/多选一判定逻辑",
    ]
    sheet2_rows = [
        [
            g["group_id"],
            g["group_name"],
            g["shared_db_mapping"],
            g["conflict_templates"],
            g["decision_logic"],
        ]
        for g in DISAMBIGUATION_GROUPS
    ]

    ws3_headers = [
        "序号",
        "contract_template_id(最新启用版本ID)",
        "template_code(模板编码)",
        "template_name(模板名称)",
        "version(版本号)",
        "product_id(19位产品ID)",
        "product_code(产品编码)",
        "product_name(产品名称)",
        "所属业务板块",
        "映射数据来源与落库建议",
    ]

    ws4_headers = [
        "序号",
        "新系统产品编码(product_code)",
        "产品标准名称(product_name)",
        "所属二级业务板块",
        "19位产品主键ID(product_id)",
        "历史使用频次",
        "【首选单事项合同模板】(单买该产品时匹配的模板)",
        "【多事项/跨板块组合模板】(与其他产品同签时升级的模板)",
        "映射匹配方式",
    ]

    standalone_sheets = [
        ("01_78套模板第5层精准路由总表", ws1_headers, sheet1_rows),
        ("02_同分类易混模板排他决策树", ws2_headers, sheet2_rows),
        ("03_待落库模板产品绑定明细", ws3_headers, sheet3_binding_rows),
        ("04_新系统603个产品反查模板表", ws4_headers, sheet4_rows),
    ]
    standalone_xlsx = os.path.join(OUT_DIR, "78套标准模板第5层细粒度路由与产品绑定规则表.xlsx")
    write_styled_xlsx(standalone_xlsx, standalone_sheets)
    print(f"[OK] 已生成独立核对表: {standalone_xlsx}")

    # ==================== Update JSON Mapping Dictionary ====================
    mapping_dict["_meta"]["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mapping_dict["_meta"]["fifth_layer_routing_completed"] = True
    mapping_dict["_meta"]["total_active_official_templates"] = 78

    for tpl_entry in mapping_dict.get("templates", []):
        code = tpl_entry.get("template_code")
        if code in TEMPLATE_5TH_LAYER_RULES:
            rule = TEMPLATE_5TH_LAYER_RULES[code]
            t_info = docx_templates.get(code, {})
            c1_summary, pay_summary = extract_clause_summaries(t_info.get("content", {}))
            tpl_entry["fifth_layer_routing"] = {
                "folder": t_info.get("folder", ""),
                "sub_scene": rule["sub_scene"],
                "fee_mode": rule["fee_mode"],
                "target_constraint": rule["target_constraint"],
                "disambiguation_rule": rule["disambiguation_rule"],
                "docx_clause1_summary": c1_summary,
                "docx_payment_summary": pay_summary,
                "rag_trigger_keywords": rule["rag_keywords"],
                "historical_contract_count": actual_contract_counts.get(code, 0),
            }
            tpl_entry["recommended_product_bindings"] = {
                "new_system_products": [
                    {
                        "product_code": pc,
                        "product_name": all_new_products.get(pc, {}).get("product_name", ""),
                        "product_id": all_new_products.get(pc, {}).get("product_id", ""),
                    }
                    for pc in rule["new_products"]
                ],
                "erp_legacy_products": [
                    {
                        "product_code": ec,
                        "product_name": erp_lookup.get(ec, ""),
                    }
                    for ec in rule["erp_products"]
                ],
            }

    mapping_dict["fifth_layer_disambiguation_groups"] = DISAMBIGUATION_GROUPS
    mapping_dict["product_to_template_reverse_index"] = {
        r[1]: {
            "product_name": r[2],
            "segment": r[3],
            "product_id": r[4],
            "primary_templates": r[6],
            "combo_templates": r[7],
        }
        for r in sheet4_rows
    }

    json_path = os.path.join(OUT_DIR, "contract_mapping_dictionary.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(mapping_dict, f, ensure_ascii=False, indent=2)
    print(f"[OK] 已更新RAG全量映射JSON字典: {json_path}")
    print(f"[OK] 已生成待落库SQL脚本: {sql_path}")
    print(f"汇总统计: 78套标准模板100%补齐第5层规则 | 生成待落库产品绑定 {len(sheet3_binding_rows)} 条 | 覆盖新系统产品 {len(sheet4_rows)} 个")


if __name__ == "__main__":
    main()
