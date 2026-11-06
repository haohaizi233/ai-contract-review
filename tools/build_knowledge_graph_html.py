# -*- coding: utf-8 -*-
"""
生成《AI合同分类与78套标准模板 · 五级知识图谱与决策树》交互式可视化 HTML
支持：
1. 视图一：交互式五级横向树状知识图谱（SVG矢量可缩放、可拖拽、点击节点展开/折叠、悬停高亮全链路、点击查看详情抽屉）
2. 视图二：五级分栏级联透视器（Miller Columns 1级->2级->3级->4级->5级产品与唯一模板联动）
3. 视图三：12组同分类易混模板排他决策树图谱
4. 支持按产品编码(CNIP0057)、关键词(FTO/港湾/包干/文字商标)、模板号(MB...)实时搜索并高亮知识图谱路径
"""

import os
import json
from collections import defaultdict
from template_5th_layer_data import TEMPLATE_5TH_LAYER_RULES, DISAMBIGUATION_GROUPS

BASE_DIR = r"d:\Users\Administrator\Desktop\AI合同"
SCRATCH_DIR = r"C:\Users\Administrator\.gemini\antigravity\brain\7d2ac74e-aac3-470e-bc9e-fe6de56d5876\scratch"
OUT_DIR = os.path.join(BASE_DIR, "data", "contract_merged")
ARTIFACT_DIR = r"C:\Users\Administrator\.gemini\antigravity\brain\7d2ac74e-aac3-470e-bc9e-fe6de56d5876"


def build_hierarchy_data():
    with open(os.path.join(SCRATCH_DIR, "extracted_78_templates_and_products.json"), "r", encoding="utf-8") as f:
        extracted = json.load(f)
    with open(os.path.join(SCRATCH_DIR, "all_new_products_with_ids.json"), "r", encoding="utf-8") as f:
        all_new_prods = json.load(f)
    with open(os.path.join(OUT_DIR, "contract_mapping_dictionary.json"), "r", encoding="utf-8") as f:
        mapping_dict = json.load(f)

    # Map template_code -> template entry from mapping_dict
    tpl_entries = {t["template_code"]: t for t in mapping_dict.get("templates", [])}

    # Define clean 5-level tree structure:
    # Root -> L1 (一级业务板块) -> L2 (二级业务板块 & auth_item) -> L3 (三级业务子类 & 合同性质) -> L4 (四级细分场景 & 收费/交易方向) -> L5 (唯一标准模板 + 绑定产品编码)
    l1_l2_l3_config = {
        # 01_国内专利 (24)
        "MB20251205014": ("I · 知识产权板块", "I-P · 国内专利 (auth_item=1)", "1.1 国内专利新申请类 (单委1+6)"),
        "MB20251209001": ("I · 知识产权板块", "I-P · 国内专利 (auth_item=1)", "1.1 国内专利新申请类 (单委1+6)"),
        "MB20251205012": ("I · 知识产权板块", "I-P · 国内专利 (auth_item=1)", "1.2 专利加速审查类 (预审/优审)"),
        "MB20251205018": ("I · 知识产权板块", "I-P · 国内专利 (auth_item=1)", "1.2 专利加速审查类 (预审/优审)"),
        "MB20251205021": ("I · 知识产权板块", "I-P · 国内专利 (auth_item=1)", "1.3 专利驳回复审与无效宣告类"),
        "MB20251205029": ("S · 高端咨询与数据板块 (兼归I-P)", "S-C · 数据与咨询 / 专利确权 (auth=1/6)", "1.3 专利驳回复审与无效宣告类"),
        "MB20251205013": ("I · 知识产权板块", "I-P · 国内专利 (auth_item=1)", "1.4 专利年费维持、副本与著录变更类"),
        "MB20251205017": ("I · 知识产权板块", "I-P · 国内专利 (auth_item=1)", "1.4 专利年费维持、副本与著录变更类"),
        "MB20251205019": ("I · 知识产权板块", "I-P · 国内专利 (auth_item=1)", "1.4 专利年费维持、副本与著录变更类"),
        "MB20251205015": ("I · 知识产权板块", "I-P · 国内专利 (auth_item=1)", "1.5 专利实施许可备案与注销类"),
        "MB20251205016": ("I · 知识产权板块", "I-P · 国内专利 (auth_item=1)", "1.5 专利实施许可备案与注销类"),
        "MB20251205027": ("I · 知识产权板块", "I-P · 国内专利 (auth_item=1)", "1.6 专利转让代理过户类 (非买卖)"),
        "MB20251205045": ("O · IP运营与知产交易板块", "O-P · 专利权买卖交易 (库标auth=1/实属12)", "1.7 专利权交易买卖类 (港湾买方/卖方)"),
        "MB20251205046": ("O · IP运营与知产交易板块", "O-P · 专利权买卖交易 (库标auth=1/实属12)", "1.7 专利权交易买卖类 (港湾买方/卖方)"),
        "MB20251205024": ("I · 知识产权板块", "I-P · 国内专利 (auth_item=1)", "1.8 专利官方评价报告与集成电路布图类"),
        "MB20251205042": ("I · 知识产权板块", "I-P · 国内专利 (auth_item=1)", "1.8 专利官方评价报告与集成电路布图类"),
        "MB20251205086": ("I · 知识产权板块", "I-P · 国内专利 (auth_item=1)", "1.9 国内专利多事项打包组合类"),
        "MB20251205020": ("S · 高端咨询与数据板块 (兼归I-P)", "S-C · 数据与咨询 (库标auth=1/实属6)", "1.10 专利检索/导航/FTO/稳定性/尽调/顾问类"),
        "MB20251205022": ("S · 高端咨询与数据板块 (兼归I-P)", "S-C · 数据与咨询 (库标auth=1/实属6)", "1.10 专利检索/导航/FTO/稳定性/尽调/顾问类"),
        "MB20251205023": ("S · 高端咨询与数据板块 (兼归I-P)", "S-C · 数据与咨询 (库标auth=1/实属6)", "1.10 专利检索/导航/FTO/稳定性/尽调/顾问类"),
        "MB20251205025": ("S · 高端咨询与数据板块 (兼归I-P)", "S-C · 数据与咨询 (库标auth=1/实属6)", "1.10 专利检索/导航/FTO/稳定性/尽调/顾问类"),
        "MB20251205026": ("S · 高端咨询与数据板块 (兼归I-P)", "S-C · 数据与咨询 (库标auth=1/实属6)", "1.10 专利检索/导航/FTO/稳定性/尽调/顾问类"),
        "MB20251205028": ("S · 高端咨询与数据板块 (兼归I-P)", "S-C · 数据与咨询 (库标auth=1/实属6)", "1.10 专利检索/导航/FTO/稳定性/尽调/顾问类"),
        "MB20251229002": ("S · 高端咨询与数据板块 (兼归I-P)", "S-C · 数据与咨询 (库标auth=1/实属6)", "1.10 专利检索/导航/FTO/稳定性/尽调/顾问类"),

        # 02_国内商标 (17)
        "MB20251205057": ("I · 知识产权板块", "I-T · 国内商标 (auth_item=2)", "2.1 商标新注册与风险包过套餐类"),
        "MB20251205059": ("I · 知识产权板块", "I-T · 国内商标 (auth_item=2)", "2.1 商标新注册与风险包过套餐类"),
        "MB20251205047": ("I · 知识产权板块", "I-T · 国内商标 (auth_item=2)", "2.2 商标变更类 (图文版 vs 纯文字版)"),
        "MB20260729001": ("I · 知识产权板块", "I-T · 国内商标 (auth_item=2)", "2.2 商标变更类 (图文版 vs 纯文字版)"),
        "MB20251205054": ("I · 知识产权板块", "I-T · 国内商标 (auth_item=2)", "2.3 商标续展、补证与许可备案类"),
        "MB20251205049": ("I · 知识产权板块", "I-T · 国内商标 (auth_item=2)", "2.3 商标续展、补证与许可备案类"),
        "MB20251205053": ("I · 知识产权板块", "I-T · 国内商标 (auth_item=2)", "2.3 商标续展、补证与许可备案类"),
        "MB20251205058": ("I · 知识产权板块", "I-T · 国内商标 (auth_item=2)", "2.4 商标转让代理过户类 (非买卖)"),
        "MB20251205043": ("O · IP运营与知产交易板块", "O-P · 商标权买卖交易 (库标auth=2/实属12)", "2.5 商标权交易买卖类 (港湾买方/卖方)"),
        "MB20251205044": ("O · IP运营与知产交易板块", "O-P · 商标权买卖交易 (库标auth=2/实属12)", "2.5 商标权交易买卖类 (港湾买方/卖方)"),
        "MB20251205048": ("I · 知识产权板块", "I-T · 国内商标 (auth_item=2)", "2.6 商标评审与确权攻防类 (复审/异议/撤三/无效)"),
        "MB20251205050": ("I · 知识产权板块", "I-T · 国内商标 (auth_item=2)", "2.6 商标评审与确权攻防类 (复审/异议/撤三/无效)"),
        "MB20251205051": ("I · 知识产权板块", "I-T · 国内商标 (auth_item=2)", "2.6 商标评审与确权攻防类 (复审/异议/撤三/无效)"),
        "MB20251205052": ("I · 知识产权板块", "I-T · 国内商标 (auth_item=2)", "2.6 商标评审与确权攻防类 (复审/异议/撤三/无效)"),
        "MB20251205055": ("I · 知识产权板块", "I-T · 国内商标 (auth_item=2)", "2.6 商标评审与确权攻防类 (复审/异议/撤三/无效)"),
        "MB20251205056": ("I · 知识产权板块", "I-T · 国内商标 (auth_item=2)", "2.6 商标评审与确权攻防类 (复审/异议/撤三/无效)"),
        "MB20251205085": ("I · 知识产权板块", "I-T · 国内商标 (auth_item=2)", "2.7 国内商标多事项打包组合类"),

        # 03_国内著作权 (2)
        "MB20251205087": ("I · 知识产权板块", "I-C · 国内著作权 (auth_item=3)", "3.1 软著与作品著作权登记类 (单项 vs 组合)"),
        "MB20251205088": ("I · 知识产权板块", "I-C · 国内著作权 (auth_item=3)", "3.1 软著与作品著作权登记类 (单项 vs 组合)"),

        # 04_跨板块组合合同 (6)
        "MB20251205069": ("M · 跨板块综合组合业务", "M-12 · 专利+商标组合 (auth_item=1,2)", "4.1 专利+商标双板块组合类 (申请/变更/转让)"),
        "MB20251205072": ("M · 跨板块综合组合业务", "M-12 · 专利+商标组合 (auth_item=1,2)", "4.1 专利+商标双板块组合类 (申请/变更/转让)"),
        "MB20251205068": ("M · 跨板块综合组合业务", "M-12 · 专利+商标组合 (auth_item=1,2)", "4.1 专利+商标双板块组合类 (申请/变更/转让)"),
        "MB20251205078": ("M · 跨板块综合组合业务", "M-13 · 专利+著作权组合 (auth_item=1,3)", "4.2 专利+著作权双板块组合类"),
        "MB20251205065": ("M · 跨板块综合组合业务", "M-23 · 商标+著作权组合 (auth_item=2,3)", "4.3 商标+著作权双板块组合类"),
        "MB20251205074": ("M · 跨板块综合组合业务", "M-123 · 专利+商标+著作权 (auth=1,2,3)", "4.4 专利+商标+著作权三栖组合类"),

        # 05_国际专利与国际商标 (8)
        "MB20251205041": ("I · 知识产权板块", "G-I-P · 国际专利 (auth_item=8)", "5.1 PCT国际申请阶段类"),
        "MB20251205037": ("I · 知识产权板块", "G-I-P · 国际专利 (auth_item=8)", "5.2 涉外专利进国家阶段/巴黎公约申请类 (包干vs分期)"),
        "MB20251205039": ("I · 知识产权板块", "G-I-P · 国际专利 (auth_item=8)", "5.2 涉外专利进国家阶段/巴黎公约申请类 (包干vs分期)"),
        "MB20251205038": ("I · 知识产权板块", "G-I-P · 国际专利 (auth_item=8)", "5.3 涉外专利OA答复与复审类 (包干vs分期)"),
        "MB20251205040": ("I · 知识产权板块", "G-I-P · 国际专利 (auth_item=8)", "5.3 涉外专利OA答复与复审类 (包干vs分期)"),
        "MB20251205035": ("I · 知识产权板块", "G-I-T · 国际商标 (auth_item=9)", "5.4 国际商标新注册与后续变更转让类"),
        "MB20251205036": ("I · 知识产权板块", "G-I-T · 国际商标 (auth_item=9)", "5.4 国际商标新注册与后续变更转让类"),
        "MB20251205030": ("I · 知识产权板块", "G-I-T · 国际商标 (auth_item=9)", "5.4 国际商标新注册与后续变更转让类"),

        # 06_科创项目与财税审计 (7)
        "MB20251205082": ("P · 政策辅导与财税认证板块", "P-B · 科创项目与体系认证 (auth=4/11)", "6.1 科创政策申报/贯标/ISO/绿色工厂/BUD/数据知产/双软"),
        "MB20251205080": ("P · 政策辅导与财税认证板块", "P-B · 科创项目与体系认证 (auth=4/11)", "6.1 科创政策申报/贯标/ISO/绿色工厂/BUD/数据知产/双软"),
        "MB20251205084": ("P · 政策辅导与财税认证板块", "P-B · 科创项目与体系认证 (auth=4/11)", "6.1 科创政策申报/贯标/ISO/绿色工厂/BUD/数据知产/双软"),
        "MB20251205064": ("P · 政策辅导与财税认证板块", "P-B · 科创项目与体系认证 (auth=4/11)", "6.1 科创政策申报/贯标/ISO/绿色工厂/BUD/数据知产/双软"),
        "MB20251205063": ("P · 政策辅导与财税认证板块", "P-R · 第三方科技查新与软件测试 (auth=11/4)", "6.2 科技查新递交与软件登记测试类"),
        "MB20251205061": ("P · 政策辅导与财税认证板块", "P-S · 财税审计报告 (auth_item=5)", "6.3 财税审计与研发辅助账类 (广东版 vs 非广东版)"),
        "MB20251205062": ("P · 政策辅导与财税认证板块", "P-S · 财税审计报告 (auth_item=5)", "6.3 财税审计与研发辅助账类 (广东版 vs 非广东版)"),

        # 07_法律服务与维权诉讼 (11)
        "MB20251205071": ("L · 法律服务与维权诉讼板块", "L-S · 法律单案诉讼与非诉 (auth=7, 单委)", "7.1 专利/商标/民事单案诉讼与诉无联动类"),
        "MB20251205066": ("L · 法律服务与维权诉讼板块", "L-S · 法律单案诉讼与非诉 (auth=7, 单委)", "7.1 专利/商标/民事单案诉讼与诉无联动类"),
        "MB20251205067": ("L · 法律服务与维权诉讼板块", "L-S · 法律单案诉讼与非诉 (auth=7, 单委)", "7.1 专利/商标/民事单案诉讼与诉无联动类"),
        "MB20251205075": ("L · 法律服务与维权诉讼板块", "L-S · 法律单案诉讼与非诉 (auth=7, 单委)", "7.1 专利/商标/民事单案诉讼与诉无联动类"),
        "MB20251205073": ("L · 法律服务与维权诉讼板块", "L-S · 法律单案诉讼与非诉 (auth=7, 单委)", "7.2 非诉专项法律服务类 (合同/律师函/电商投诉/法顾)"),
        "MB20251205076": ("L · 法律服务与维权诉讼板块", "L-S · 批量维权打假总委 (auth=7, 总委)", "7.3 批量维权打假总委类 (民/行/刑/全风险/战略合作)"),
        "MB20251205077": ("L · 法律服务与维权诉讼板块", "L-S · 批量维权打假总委 (auth=7, 总委)", "7.3 批量维权打假总委类 (民/行/刑/全风险/战略合作)"),
        "MB20251205079": ("L · 法律服务与维权诉讼板块", "L-S · 批量维权打假总委 (auth=7, 总委)", "7.3 批量维权打假总委类 (民/行/刑/全风险/战略合作)"),
        "MB20251205081": ("L · 法律服务与维权诉讼板块", "L-S · 批量维权打假总委 (auth=7, 总委)", "7.3 批量维权打假总委类 (民/行/刑/全风险/战略合作)"),
        "MB20251205083": ("L · 法律服务与维权诉讼板块", "L-S · 批量维权打假总委 (auth=7, 总委)", "7.3 批量维权打假总委类 (民/行/刑/全风险/战略合作)"),
        "MB20251205070": ("L · 法律服务与维权诉讼板块", "L-S · 批量维权打假总委 (auth=7, 总委)", "7.3 批量维权打假总委类 (民/行/刑/全风险/战略合作)"),

        # 08_总委框架与保密协议 (3)
        "MB20251205089": ("Z & BM · 总委框架与保密协议板块", "Z · 知识产权总委框架 (contract_type=2)", "8.1 年度长期总委框架类 (纯国内版 vs 含国际专利版)"),
        "MB20251225003": ("Z & BM · 总委框架与保密协议板块", "Z · 知识产权总委框架 (contract_type=2)", "8.1 年度长期总委框架类 (纯国内版 vs 含国际专利版)"),
        "MB20251205011": ("Z & BM · 总委框架与保密协议板块", "BM · 商业秘密保密协议 (contract_type=4)", "8.2 技术交底与商业秘密保密协议类 (NDA)"),
    }

    tree_root = {
        "id": "ROOT",
        "name": "AI合同分类与78套模板\n五级路由知识图谱",
        "level": 0,
        "children": [],
    }

    l1_nodes = {}
    l2_nodes = {}
    l3_nodes = {}
    flat_templates = []

    for tcode, rule in sorted(TEMPLATE_5TH_LAYER_RULES.items(), key=lambda x: (l1_l2_l3_config[x[0]][0], l1_l2_l3_config[x[0]][1], l1_l2_l3_config[x[0]][2], x[0])):
        l1_name, l2_name, l3_name = l1_l2_l3_config[tcode]
        t_entry = tpl_entries.get(tcode, {})
        f5 = t_entry.get("fifth_layer_routing", {})
        tname = t_entry.get("template_name", tcode)
        ver = t_entry.get("latest_version", "V1.0")
        hist_cnt = f5.get("historical_contract_count", 0)
        folder = f5.get("folder", "")

        if l1_name not in l1_nodes:
            node1 = {"id": f"L1_{len(l1_nodes)}", "name": l1_name, "level": 1, "children": []}
            l1_nodes[l1_name] = node1
            tree_root["children"].append(node1)
        node1 = l1_nodes[l1_name]

        l2_key = (l1_name, l2_name)
        if l2_key not in l2_nodes:
            node2 = {"id": f"L2_{len(l2_nodes)}", "name": l2_name, "level": 2, "children": []}
            l2_nodes[l2_key] = node2
            node1["children"].append(node2)
        node2 = l2_nodes[l2_key]

        l3_key = (l1_name, l2_name, l3_name)
        if l3_key not in l3_nodes:
            node3 = {"id": f"L3_{len(l3_nodes)}", "name": l3_name, "level": 3, "children": []}
            l3_nodes[l3_key] = node3
            node2["children"].append(node3)
        node3 = l3_nodes[l3_key]

        # Format products
        new_prods = []
        for pc in rule["new_products"]:
            pn = all_new_prods.get(pc, {}).get("product_name", "")
            new_prods.append({"code": pc, "name": pn})

        # L4 node (细分场景 + 收费/方向约束) -> L5 leaf (唯一标准模板 + 绑定产品)
        short_scene = rule["sub_scene"].split("（")[0][:22]
        short_fee = rule["fee_mode"].split("（")[0][:18]
        l4_label = f"【场景】{short_scene}\n【模式】{short_fee}"

        leaf_data = {
            "id": tcode,
            "code": tcode,
            "name": f"{tcode} 《{tname}》",
            "template_name": tname,
            "version": ver,
            "folder": folder,
            "l1": l1_name,
            "l2": l2_name,
            "l3": l3_name,
            "sub_scene": rule["sub_scene"],
            "fee_mode": rule["fee_mode"],
            "target_constraint": rule["target_constraint"],
            "disambiguation_rule": rule["disambiguation_rule"],
            "clause1": f5.get("docx_clause1_summary", ""),
            "payment": f5.get("docx_payment_summary", ""),
            "keywords": rule["rag_keywords"],
            "new_products": new_prods,
            "erp_products": rule["erp_products"],
            "hist_cnt": hist_cnt,
            "auth_item": str(t_entry.get("auth_item", "")),
            "contract_type": t_entry.get("contract_type", 1),
            "business_type": t_entry.get("business_type", 6),
        }
        flat_templates.append(leaf_data)

        node4 = {
            "id": f"L4_{tcode}",
            "name": l4_label,
            "level": 4,
            "tpl_code": tcode,
            "children": [
                {
                    "id": f"L5_{tcode}",
                    "name": f"{tcode} {tname} ({len(new_prods)}个新产品/{hist_cnt}单)",
                    "level": 5,
                    "tpl_code": tcode,
                    "leaf": leaf_data,
                }
            ],
        }
        node3["children"].append(node4)

    return {
        "tree": tree_root,
        "templates": flat_templates,
        "disambiguation_groups": DISAMBIGUATION_GROUPS,
    }


def generate_html(data_obj):
    json_payload = json.dumps(data_obj, ensure_ascii=False)
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AI合同分类与78套标准模板 · 五级知识图谱与决策树</title>
  <script src="https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"></script>
  <style>
    :root {{
      --background: #f8fafc;
      --card: #ffffff;
      --foreground: #0f172a;
      --muted-foreground: #64748b;
      --border: #e2e8f0;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --background: #0f172a;
        --card: #1e293b;
        --foreground: #f8fafc;
        --muted-foreground: #94a3b8;
        --border: #334155;
      }}
    }}
    * {{ box-sizing: border-box; }}
    .no-scrollbar::-webkit-scrollbar {{ display: none; }}
    .no-scrollbar {{ -ms-overflow-style: none; scrollbar-width: none; }}
    .node-rect {{ transition: all 0.2s ease; cursor: pointer; }}
    .node-rect:hover {{ filter: brightness(1.08); stroke-width: 2.5px; }}
    .link-path {{ fill: none; transition: stroke 0.2s ease, stroke-width 0.2s ease, opacity 0.2s ease; }}
  </style>
</head>
<body class="bg-[var(--background)] text-[var(--foreground)] antialiased select-none overflow-hidden h-screen flex flex-col">

  <!-- 顶部导航与全局搜索栏 -->
  <header class="bg-[var(--card)] border-b border-[var(--border)] px-5 py-3 flex flex-wrap items-center justify-between gap-3 shrink-0 z-20 shadow-sm">
    <div class="flex items-center gap-3">
      <div class="w-3 h-8 rounded-full bg-blue-600"></div>
      <div>
        <h1 class="text-base font-bold tracking-tight flex items-center gap-2">
          <span>AI合同分类与78套标准模板 · 五级路由知识图谱</span>
          <span class="text-xs px-2 py-0.5 rounded-full bg-blue-500/15 text-blue-500 font-medium">1级→2级→3级→4级→5级 (1对1锁定)</span>
        </h1>
        <p class="text-xs text-[var(--muted-foreground)]">涵盖 7大顶层板块 · 15类二级事项 · 31个三级子类 · 78套四级场景约束 · 603个五级新系统产品编码</p>
      </div>
    </div>

    <!-- 视图切换器 -->
    <div class="flex items-center gap-1.5 bg-[var(--background)] p-1 rounded-lg border border-[var(--border)]">
      <button id="btn-view-graph" onclick="switchView('graph')" class="px-3 py-1.5 rounded-md text-xs font-semibold bg-blue-600 text-white shadow-sm transition">
        🌳 交互式五级知识图谱树 (可缩放/展开)
      </button>
      <button id="btn-view-miller" onclick="switchView('miller')" class="px-3 py-1.5 rounded-md text-xs font-semibold text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition">
        🗂️ 五级分栏级联透视树 (1→5级联动)
      </button>
      <button id="btn-view-decision" onclick="switchView('decision')" class="px-3 py-1.5 rounded-md text-xs font-semibold text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition">
        🧭 12组同分类易混排他决策树
      </button>
    </div>

    <!-- 搜索框与快捷操作 -->
    <div class="flex items-center gap-2">
      <div class="relative">
        <input
          id="global-search"
          type="text"
          placeholder="搜产品编码(CNIP0057)、FTO、港湾、包干、MB..."
          oninput="handleSearch(this.value)"
          class="w-72 px-3 py-1.5 text-xs rounded-lg bg-[var(--background)] border border-[var(--border)] text-[var(--foreground)] focus:outline-none focus:border-blue-500"
        />
        <button id="clear-search" onclick="clearSearch()" class="hidden absolute right-2 top-1.5 text-xs text-[var(--muted-foreground)] hover:text-[var(--foreground)]">✕</button>
      </div>
      <div id="graph-controls" class="flex items-center gap-1">
        <button onclick="expandUpToLevel(2)" class="px-2.5 py-1.5 text-xs rounded-md border border-[var(--border)] bg-[var(--background)] hover:bg-blue-500/10" title="仅展开到第2级">展开2级</button>
        <button onclick="expandUpToLevel(3)" class="px-2.5 py-1.5 text-xs rounded-md border border-[var(--border)] bg-[var(--background)] hover:bg-blue-500/10" title="展开到第3级">展开3级</button>
        <button onclick="expandUpToLevel(5)" class="px-2.5 py-1.5 text-xs rounded-md border border-[var(--border)] bg-[var(--background)] hover:bg-blue-500/10" title="展开全部5级">全展开(5级)</button>
        <button onclick="resetZoom()" class="px-2.5 py-1.5 text-xs rounded-md border border-[var(--border)] bg-[var(--background)] hover:bg-blue-500/10">居中复位</button>
      </div>
    </div>
  </header>

  <!-- 顶部五级图例说明条 -->
  <div class="bg-[var(--card)]/60 border-b border-[var(--border)] px-5 py-1.5 flex items-center justify-between text-xs shrink-0">
    <div class="flex items-center gap-4 flex-wrap">
      <span class="font-semibold text-[var(--muted-foreground)]">五级图谱节点图例：</span>
      <span class="flex items-center gap-1.5"><span class="w-3 h-3 rounded bg-indigo-600 inline-block"></span><b>第1级</b>：一级业务板块 (I/P/L/S/O/M/Z)</span>
      <span class="text-[var(--muted-foreground)]">→</span>
      <span class="flex items-center gap-1.5"><span class="w-3 h-3 rounded bg-blue-600 inline-block"></span><b>第2级</b>：二级板块与委托事项 (auth_item)</span>
      <span class="text-[var(--muted-foreground)]">→</span>
      <span class="flex items-center gap-1.5"><span class="w-3 h-3 rounded bg-cyan-600 inline-block"></span><b>第3级</b>：合同性质与三级业务子类</span>
      <span class="text-[var(--muted-foreground)]">→</span>
      <span class="flex items-center gap-1.5"><span class="w-3 h-3 rounded bg-amber-600 inline-block"></span><b>第4级</b>：细分场景 + 收费/交易方向约束</span>
      <span class="text-[var(--muted-foreground)]">→</span>
      <span class="flex items-center gap-1.5"><span class="w-3 h-3 rounded bg-emerald-600 inline-block"></span><b>第5级(终节点)</b>：唯一标准合同模板 + 绑定产品编码(CNIP/ERP)</span>
    </div>
    <div id="search-stat" class="text-xs font-medium text-blue-500">点击任意节点可展开/折叠下级；点击绿色模板节点查看右侧完整第5级绑定卡片</div>
  </div>

  <!-- 主体内容区 -->
  <div class="flex-1 flex overflow-hidden relative">

    <!-- ==================== 视图1：SVG 交互式横向知识图谱树 ==================== -->
    <div id="view-graph" class="flex-1 relative overflow-hidden bg-[var(--background)]">
      <svg id="tree-svg" class="w-full h-full cursor-grab active:cursor-grabbing">
        <g id="tree-viewport">
          <g id="tree-links"></g>
          <g id="tree-nodes"></g>
        </g>
      </svg>

      <!-- 左下角缩放提示 -->
      <div class="absolute bottom-4 left-4 bg-[var(--card)]/90 backdrop-blur border border-[var(--border)] rounded-lg px-3 py-2 text-xs text-[var(--muted-foreground)] shadow-sm flex items-center gap-3">
        <span>🖱️ 滚轮缩放 · 按住左键拖拽画布 · 点击带数字圆点展开/收起分支</span>
        <button onclick="zoomBy(1.25)" class="px-2 py-0.5 rounded border border-[var(--border)] hover:bg-blue-500/10 text-[var(--foreground)] font-bold">＋</button>
        <button onclick="zoomBy(0.8)" class="px-2 py-0.5 rounded border border-[var(--border)] hover:bg-blue-500/10 text-[var(--foreground)] font-bold">－</button>
      </div>
    </div>

    <!-- ==================== 视图2：五级分栏级联透视器 (Miller Columns) ==================== -->
    <div id="view-miller" class="hidden flex-1 grid grid-cols-5 divide-x divide-[var(--border)] overflow-hidden bg-[var(--background)]">
      <!-- 第1级列 -->
      <div class="flex flex-col h-full overflow-hidden">
        <div class="px-3 py-2 bg-indigo-600/10 border-b border-[var(--border)] text-xs font-bold text-indigo-500 flex justify-between items-center">
          <span>【第1级】一级业务板块</span>
          <span id="cnt-col1" class="px-1.5 py-0.5 rounded bg-indigo-500/20 text-xs">7</span>
        </div>
        <div id="miller-col1" class="flex-1 overflow-y-auto p-2 space-y-1.5"></div>
      </div>
      <!-- 第2级列 -->
      <div class="flex flex-col h-full overflow-hidden">
        <div class="px-3 py-2 bg-blue-600/10 border-b border-[var(--border)] text-xs font-bold text-blue-500 flex justify-between items-center">
          <span>【第2级】二级板块 / 委托事项</span>
          <span id="cnt-col2" class="px-1.5 py-0.5 rounded bg-blue-500/20 text-xs">-</span>
        </div>
        <div id="miller-col2" class="flex-1 overflow-y-auto p-2 space-y-1.5"></div>
      </div>
      <!-- 第3级列 -->
      <div class="flex flex-col h-full overflow-hidden">
        <div class="px-3 py-2 bg-cyan-600/10 border-b border-[var(--border)] text-xs font-bold text-cyan-500 flex justify-between items-center">
          <span>【第3级】合同性质与业务子类</span>
          <span id="cnt-col3" class="px-1.5 py-0.5 rounded bg-cyan-500/20 text-xs">-</span>
        </div>
        <div id="miller-col3" class="flex-1 overflow-y-auto p-2 space-y-1.5"></div>
      </div>
      <!-- 第4级列 -->
      <div class="flex flex-col h-full overflow-hidden">
        <div class="px-3 py-2 bg-amber-600/10 border-b border-[var(--border)] text-xs font-bold text-amber-500 flex justify-between items-center">
          <span>【第4级】细分场景与收费/方向约束</span>
          <span id="cnt-col4" class="px-1.5 py-0.5 rounded bg-amber-500/20 text-xs">-</span>
        </div>
        <div id="miller-col4" class="flex-1 overflow-y-auto p-2 space-y-1.5"></div>
      </div>
      <!-- 第5级列 -->
      <div class="flex flex-col h-full overflow-hidden">
        <div class="px-3 py-2 bg-emerald-600/10 border-b border-[var(--border)] text-xs font-bold text-emerald-500 flex justify-between items-center">
          <span>【第5级】唯一锁定模板 & 绑定产品</span>
          <span id="cnt-col5" class="px-1.5 py-0.5 rounded bg-emerald-500/20 text-xs">-</span>
        </div>
        <div id="miller-col5" class="flex-1 overflow-y-auto p-2.5 space-y-2"></div>
      </div>
    </div>

    <!-- ==================== 视图3：12组同分类易混模板排他决策树 ==================== -->
    <div id="view-decision" class="hidden flex-1 overflow-y-auto p-6 bg-[var(--background)]">
      <div class="max-w-7xl mx-auto">
        <div class="mb-5">
          <h2 class="text-lg font-bold">🧭 12组同分类易混合同模板 · 第4~5级排他决策树</h2>
          <p class="text-xs text-[var(--muted-foreground)] mt-1">针对数据库中前3级分类（合同类型 + 业务类型 + 委托事项）完全相同、导致多个模板冲突的场景，提炼出的硬性二选一/多选一决策图谱</p>
        </div>
        <div id="decision-grid" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
      </div>
    </div>

    <!-- ==================== 右侧详情抽屉 (点击任意模板节点弹出) ==================== -->
    <aside id="detail-drawer" class="w-96 bg-[var(--card)] border-l border-[var(--border)] h-full flex flex-col shrink-0 overflow-hidden shadow-xl transition-all duration-200">
      <div class="p-4 border-b border-[var(--border)] flex items-start justify-between gap-2 bg-emerald-500/5">
        <div>
          <div class="flex items-center gap-2">
            <span id="d-code" class="px-2 py-0.5 rounded bg-emerald-600 text-white text-xs font-bold font-mono">MB20251205014</span>
            <span id="d-ver" class="text-xs text-[var(--muted-foreground)] font-mono">V13.0</span>
            <span id="d-cnt" class="text-xs px-2 py-0.5 rounded-full bg-blue-500/15 text-blue-500 font-semibold">实签 656 单</span>
          </div>
          <h3 id="d-name" class="text-sm font-bold mt-1.5 leading-snug">国内专利服务合同（申请）</h3>
        </div>
      </div>

      <div class="flex-1 overflow-y-auto p-4 space-y-4 text-xs">
        <!-- 五级路径面包屑 -->
        <div class="p-3 rounded-lg bg-[var(--background)] border border-[var(--border)] space-y-1.5">
          <div class="font-bold text-[var(--muted-foreground)] mb-1">🔗 五级完整路由链路：</div>
          <div class="flex items-start gap-2"><span class="px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-400 font-semibold shrink-0">第1级</span><span id="d-l1" class="font-medium"></span></div>
          <div class="flex items-start gap-2"><span class="px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400 font-semibold shrink-0">第2级</span><span id="d-l2" class="font-medium"></span></div>
          <div class="flex items-start gap-2"><span class="px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-400 font-semibold shrink-0">第3级</span><span id="d-l3" class="font-medium"></span></div>
          <div class="flex items-start gap-2"><span class="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400 font-semibold shrink-0">第4级</span><span id="d-l4" class="font-medium"></span></div>
          <div class="flex items-start gap-2"><span class="px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400 font-semibold shrink-0">第5级</span><span id="d-l5" class="font-bold text-emerald-500"></span></div>
        </div>

        <!-- 同分类排他核心规则 -->
        <div class="p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 space-y-1">
          <div class="font-bold text-amber-500 flex items-center gap-1">⚡ 同分类易混模板排他规则 (核心决策边界)</div>
          <p id="d-rule" class="leading-relaxed text-[var(--foreground)]"></p>
        </div>

        <!-- 第4级约束详情 -->
        <div class="space-y-2">
          <div class="p-2.5 rounded-lg bg-[var(--background)] border border-[var(--border)]">
            <div class="font-semibold text-[var(--muted-foreground)]">🎯 委托标的与交易方向约束：</div>
            <div id="d-target" class="mt-1 leading-relaxed"></div>
          </div>
          <div class="p-2.5 rounded-lg bg-[var(--background)] border border-[var(--border)]">
            <div class="font-semibold text-[var(--muted-foreground)]">💰 收费与结算模式约束：</div>
            <div id="d-fee" class="mt-1 leading-relaxed"></div>
          </div>
        </div>

        <!-- 第5级绑定产品编码 -->
        <div class="p-3 rounded-lg bg-[var(--background)] border border-[var(--border)] space-y-2">
          <div class="flex items-center justify-between">
            <span class="font-bold text-emerald-500">📦 【第5级】绑定的新系统产品编码 (CNIP/CNIT等)</span>
            <span id="d-prod-cnt" class="px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400 font-mono text-xs">0</span>
          </div>
          <div id="d-new-prods" class="flex flex-wrap gap-1.5 max-h-48 overflow-y-auto"></div>
          <div class="pt-2 border-t border-[var(--border)]">
            <div class="font-semibold text-[var(--muted-foreground)] mb-1">🗂️ 兼容绑定旧ERP/OA产品编码：</div>
            <div id="d-erp-prods" class="font-mono text-[var(--muted-foreground)] break-all"></div>
          </div>
        </div>

        <!-- Word模板正文第一条摘要 -->
        <div class="p-3 rounded-lg bg-[var(--background)] border border-[var(--border)] space-y-1.5">
          <div class="font-semibold text-[var(--muted-foreground)]">📄 Word模板正文【第一条 委托事项】原文摘要：</div>
          <p id="d-clause1" class="text-[var(--muted-foreground)] leading-relaxed"></p>
          <div class="font-semibold text-[var(--muted-foreground)] pt-1">💳 费用与退费条款原文摘要：</div>
          <p id="d-payment" class="text-[var(--muted-foreground)] leading-relaxed"></p>
        </div>
      </div>
    </aside>

  </div>

  <script>
    const DATA = {json_payload};
    const TPL_MAP = {{}};
    DATA.templates.forEach(t => {{ TPL_MAP[t.code] = t; }});

    // ==================== 状态管理 ====================
    let currentView = 'graph';
    let searchQuery = '';
    let matchedCodes = new Set();

    // 初始化节点折叠状态：默认展开到第3级（让首屏清晰不拥挤，点击任意第3级即展开第4、5级）
    function initCollapseState(node, maxOpenLevel = 2) {{
      node._collapsed = (node.level >= maxOpenLevel && node.children && node.children.length > 0);
      if (node.children) {{
        node.children.forEach(c => initCollapseState(c, maxOpenLevel));
      }}
    }}
    initCollapseState(DATA.tree, 2);
    // 默认把第一个板块（知识产权 -> 国内专利 -> 1.1新申请）展开到第5级作为示范！
    if (DATA.tree.children[0]) {{
      DATA.tree.children[0]._collapsed = false;
      if (DATA.tree.children[0].children[0]) {{
        DATA.tree.children[0].children[0]._collapsed = false;
        if (DATA.tree.children[0].children[0].children[0]) {{
          DATA.tree.children[0].children[0].children[0]._collapsed = false;
          DATA.tree.children[0].children[0].children[0].children.forEach(n4 => n4._collapsed = false);
        }}
      }}
    }}

    // ==================== 右侧详情抽屉渲染 ====================
    function showTemplateDetail(code) {{
      const t = TPL_MAP[code];
      if (!t) return;
      document.getElementById('d-code').textContent = t.code;
      document.getElementById('d-ver').textContent = t.version;
      document.getElementById('d-cnt').textContent = `实签 ${{t.hist_cnt}} 单`;
      document.getElementById('d-name').textContent = t.template_name;
      document.getElementById('d-l1').textContent = t.l1;
      document.getElementById('d-l2').textContent = t.l2;
      document.getElementById('d-l3').textContent = t.l3;
      document.getElementById('d-l4').textContent = t.sub_scene;
      document.getElementById('d-l5').textContent = `${{t.code}} 《${{t.template_name}}》 (文件夹: ${{t.folder}})`;
      document.getElementById('d-rule').textContent = t.disambiguation_rule;
      document.getElementById('d-target').textContent = t.target_constraint;
      document.getElementById('d-fee').textContent = t.fee_mode;
      document.getElementById('d-clause1').textContent = t.clause1 || '见标准模板正文';
      document.getElementById('d-payment').textContent = t.payment || '常规固定收费条款';

      const npContainer = document.getElementById('d-new-prods');
      document.getElementById('d-prod-cnt').textContent = `${{t.new_products.length}} 项`;
      if (t.new_products.length === 0) {{
        npContainer.innerHTML = `<span class="text-[var(--muted-foreground)]">无特定产品编码（按合同类型直接触发）</span>`;
      }} else {{
        npContainer.innerHTML = t.new_products.map(p =>
          `<span class="px-2 py-1 rounded bg-emerald-500/10 border border-emerald-500/30 text-xs">
            <b class="font-mono text-emerald-500">${{p.code}}</b> ${{p.name || ''}}
          </span>`
        ).join('');
      }}
      document.getElementById('d-erp-prods').textContent = (t.erp_products && t.erp_products.length) ? t.erp_products.join(' 、 ') : '无';
    }}

    // 默认展示 MB20251205014
    showTemplateDetail('MB20251205014');

    // ==================== SVG 树状图布局与渲染引擎 ====================
    const svg = document.getElementById('tree-svg');
    const viewport = document.getElementById('tree-viewport');
    const linksGroup = document.getElementById('tree-links');
    const nodesGroup = document.getElementById('tree-nodes');

    let panX = 40, panY = 60, scale = 0.88;
    let isDragging = false, startX = 0, startY = 0;

    function updateTransform() {{
      viewport.setAttribute('transform', `translate(${{panX}}, ${{panY}}) scale(${{scale}})`);
    }}

    svg.addEventListener('mousedown', e => {{
      if (e.target.closest('.node-group')) return;
      isDragging = true;
      startX = e.clientX - panX;
      startY = e.clientY - panY;
    }});
    window.addEventListener('mousemove', e => {{
      if (!isDragging) return;
      panX = e.clientX - startX;
      panY = e.clientY - startY;
      updateTransform();
    }});
    window.addEventListener('mouseup', () => {{ isDragging = false; }});
    svg.addEventListener('wheel', e => {{
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.12 : 0.89;
      const rect = svg.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const newScale = Math.min(2.5, Math.max(0.22, scale * factor));
      panX = mx - (mx - panX) * (newScale / scale);
      panY = my - (my - panY) * (newScale / scale);
      scale = newScale;
      updateTransform();
    }}, {{ passive: false }});

    function zoomBy(factor) {{
      const rect = svg.getBoundingClientRect();
      const cx = rect.width / 2, cy = rect.height / 2;
      const newScale = Math.min(2.5, Math.max(0.22, scale * factor));
      panX = cx - (cx - panX) * (newScale / scale);
      panY = cy - (cy - panY) * (newScale / scale);
      scale = newScale;
      updateTransform();
    }}

    function countLeafTemplates(node) {{
      if (node.level === 5) return 1;
      if (!node.children || node.children.length === 0) return 0;
      return node.children.reduce((acc, c) => acc + countLeafTemplates(c), 0);
    }}

    // 计算可见树的坐标 (Tidy Horizontal Tree Layout)
    const LEVEL_X = [0, 210, 480, 780, 1110, 1430];
    const NODE_WIDTHS = [165, 220, 250, 280, 270, 320];

    function renderTreeGraph() {{
      let currentY = 0;
      const visibleNodes = [];
      const visibleLinks = [];

      function layout(node, parent = null) {{
        node._parent = parent;
        const isCollapsed = node._collapsed;
        const children = (!isCollapsed && node.children) ? node.children : [];

        if (children.length === 0) {{
          const spacing = node.level >= 4 ? 54 : 46;
          node._y = currentY;
          currentY += spacing;
        }} else {{
          children.forEach(c => layout(c, node));
          node._y = (children[0]._y + children[children.length - 1]._y) / 2;
        }}
        node._x = LEVEL_X[node.level] || (node.level * 280);
        visibleNodes.push(node);
        if (parent) {{
          visibleLinks.push({{ source: parent, target: node }});
        }}
      }}

      layout(DATA.tree, null);

      // 渲染连线
      linksGroup.innerHTML = visibleLinks.map(l => {{
        const sx = l.source._x + NODE_WIDTHS[l.source.level];
        const sy = l.source._y + 20;
        const tx = l.target._x;
        const ty = l.target._y + 20;
        const mx = (sx + tx) / 2;
        const isHit = l.target._searchHit;
        const strokeColor = isHit ? '#3b82f6' : 'rgba(148, 163, 184, 0.45)';
        const strokeW = isHit ? '2.5' : '1.4';
        return `<path class="link-path" d="M ${{sx}} ${{sy}} C ${{mx}} ${{sy}}, ${{mx}} ${{ty}}, ${{tx}} ${{ty}}" stroke="${{strokeColor}}" stroke-width="${{strokeW}}" />`;
      }}).join('');

      // 颜色主题按层级区分
      const levelColors = [
        {{ bg: '#312e81', border: '#6366f1', text: '#ffffff', badge: '#4f46e5' }}, // L0 Root
        {{ bg: '#1e1b4b', border: '#6366f1', text: '#e0e7ff', badge: '#4338ca' }}, // L1
        {{ bg: '#172554', border: '#3b82f6', text: '#dbeafe', badge: '#1d4ed8' }}, // L2
        {{ bg: '#083344', border: '#06b6d4', text: '#cffafe', badge: '#0e7490' }}, // L3
        {{ bg: '#451a03', border: '#f59e0b', text: '#fef3c7', badge: '#b45309' }}, // L4
        {{ bg: '#064e3b', border: '#10b981', text: '#d1fae5', badge: '#047857' }}, // L5
      ];

      nodesGroup.innerHTML = visibleNodes.map(n => {{
        const w = NODE_WIDTHS[n.level];
        const h = n.level === 4 ? 44 : 38;
        const c = levelColors[n.level] || levelColors[5];
        const hasChildren = n.children && n.children.length > 0;
        const leafCnt = countLeafTemplates(n);
        const isHit = n._searchHit;
        const borderStroke = isHit ? '#facc15' : c.border;
        const strokeW = isHit ? '3' : '1.5';

        const lines = n.name.split('\\n');
        let textSvg = '';
        if (lines.length === 1) {{
          const cleanText = lines[0].length > 25 ? lines[0].slice(0, 24) + '…' : lines[0];
          textSvg = `<text x="10" y="23" fill="${{c.text}}" font-size="12" font-weight="600">${{cleanText}}</text>`;
        }} else {{
          const l1 = lines[0].length > 24 ? lines[0].slice(0, 23) + '…' : lines[0];
          const l2 = lines[1].length > 25 ? lines[1].slice(0, 24) + '…' : lines[1];
          textSvg = `
            <text x="10" y="17" fill="${{c.text}}" font-size="12" font-weight="600">${{l1}}</text>
            <text x="10" y="33" fill="${{c.text}}" opacity="0.85" font-size="12">${{l2}}</text>
          `;
        }}

        const badgeSvg = hasChildren ? `
          <g transform="translate(${{w - 34}}, ${{h/2 - 9}})">
            <rect width="28" height="18" rx="9" fill="${{c.badge}}" />
            <text x="14" y="13" text-anchor="middle" fill="#ffffff" font-size="12" font-weight="bold">${{n._collapsed ? '+' + leafCnt : leafCnt}}</text>
          </g>
        ` : '';

        return `
          <g class="node-group" transform="translate(${{n._x}}, ${{n._y}})" onclick="onNodeClick('${{n.id}}', '${{n.tpl_code || ''}}')">
            <rect class="node-rect" width="${{w}}" height="${{h}}" rx="7" fill="${{c.bg}}" stroke="${{borderStroke}}" stroke-width="${{strokeW}}" />
            ${{textSvg}}
            ${{badgeSvg}}
          </g>
        `;
      }}).join('');
    }}

    function findNodeById(node, id) {{
      if (node.id === id) return node;
      if (node.children) {{
        for (const c of node.children) {{
          const found = findNodeById(c, id);
          if (found) return found;
        }}
      }}
      return null;
    }}

    function onNodeClick(nodeId, tplCode) {{
      if (tplCode) {{
        showTemplateDetail(tplCode);
      }}
      const node = findNodeById(DATA.tree, nodeId);
      if (!node) return;
      if (node.children && node.children.length > 0) {{
        node._collapsed = !node._collapsed;
        // 如果展开第3级节点，顺便把它的第4级子节点也展开，直接露出第5级绿色模板节点！
        if (!node._collapsed && node.level === 3) {{
          node.children.forEach(n4 => {{ n4._collapsed = false; }});
        }}
        renderTreeGraph();
      }}
    }}

    function expandUpToLevel(maxLevel) {{
      function walk(n) {{
        n._collapsed = (n.level >= maxLevel && n.children && n.children.length > 0);
        if (n.children) n.children.forEach(walk);
      }}
      walk(DATA.tree);
      renderTreeGraph();
      resetZoom();
    }}

    function resetZoom() {{
      panX = 35;
      panY = 40;
      scale = 0.85;
      updateTransform();
    }}

    // ==================== 视图2：五级分栏级联透视器 (Miller Columns) ====================
    let selL1 = DATA.tree.children[0]?.id;
    let selL2 = DATA.tree.children[0]?.children[0]?.id;
    let selL3 = DATA.tree.children[0]?.children[0]?.children[0]?.id;
    let selL4 = DATA.tree.children[0]?.children[0]?.children[0]?.children[0]?.id;

    function renderMillerColumns() {{
      const col1 = document.getElementById('miller-col1');
      const col2 = document.getElementById('miller-col2');
      const col3 = document.getElementById('miller-col3');
      const col4 = document.getElementById('miller-col4');
      const col5 = document.getElementById('miller-col5');

      const l1List = DATA.tree.children || [];
      document.getElementById('cnt-col1').textContent = l1List.length;
      col1.innerHTML = l1List.map(n1 => {{
        const active = n1.id === selL1;
        const cnt = countLeafTemplates(n1);
        return `
          <div onclick="selectMiller(1, '${{n1.id}}')" class="p-2.5 rounded-lg border cursor-pointer transition text-xs flex items-center justify-between ${{active ? 'bg-indigo-600 text-white border-indigo-500 font-bold shadow-sm' : 'bg-[var(--card)] border-[var(--border)] hover:border-indigo-500/50'}}">
            <span class="truncate pr-2">${{n1.name}}</span>
            <span class="px-1.5 py-0.5 rounded text-xs ${{active ? 'bg-white/20' : 'bg-indigo-500/15 text-indigo-400'}}">${{cnt}}套</span>
          </div>
        `;
      }}).join('');

      const activeN1 = l1List.find(x => x.id === selL1) || l1List[0];
      const l2List = activeN1 ? (activeN1.children || []) : [];
      if (!l2List.find(x => x.id === selL2)) selL2 = l2List[0]?.id;
      document.getElementById('cnt-col2').textContent = l2List.length;
      col2.innerHTML = l2List.map(n2 => {{
        const active = n2.id === selL2;
        const cnt = countLeafTemplates(n2);
        return `
          <div onclick="selectMiller(2, '${{n2.id}}')" class="p-2.5 rounded-lg border cursor-pointer transition text-xs flex items-center justify-between ${{active ? 'bg-blue-600 text-white border-blue-500 font-bold shadow-sm' : 'bg-[var(--card)] border-[var(--border)] hover:border-blue-500/50'}}">
            <span class="leading-snug pr-2">${{n2.name}}</span>
            <span class="px-1.5 py-0.5 rounded text-xs shrink-0 ${{active ? 'bg-white/20' : 'bg-blue-500/15 text-blue-400'}}">${{cnt}}套</span>
          </div>
        `;
      }}).join('');

      const activeN2 = l2List.find(x => x.id === selL2) || l2List[0];
      const l3List = activeN2 ? (activeN2.children || []) : [];
      if (!l3List.find(x => x.id === selL3)) selL3 = l3List[0]?.id;
      document.getElementById('cnt-col3').textContent = l3List.length;
      col3.innerHTML = l3List.map(n3 => {{
        const active = n3.id === selL3;
        const cnt = countLeafTemplates(n3);
        return `
          <div onclick="selectMiller(3, '${{n3.id}}')" class="p-2.5 rounded-lg border cursor-pointer transition text-xs flex items-center justify-between ${{active ? 'bg-cyan-600 text-white border-cyan-500 font-bold shadow-sm' : 'bg-[var(--card)] border-[var(--border)] hover:border-cyan-500/50'}}">
            <span class="leading-snug pr-2">${{n3.name}}</span>
            <span class="px-1.5 py-0.5 rounded text-xs shrink-0 ${{active ? 'bg-white/20' : 'bg-cyan-500/15 text-cyan-400'}}">${{cnt}}套</span>
          </div>
        `;
      }}).join('');

      const activeN3 = l3List.find(x => x.id === selL3) || l3List[0];
      const l4List = activeN3 ? (activeN3.children || []) : [];
      if (!l4List.find(x => x.id === selL4)) selL4 = l4List[0]?.id;
      document.getElementById('cnt-col4').textContent = l4List.length;
      col4.innerHTML = l4List.map(n4 => {{
        const active = n4.id === selL4;
        const t = TPL_MAP[n4.tpl_code];
        return `
          <div onclick="selectMiller(4, '${{n4.id}}', '${{n4.tpl_code}}')" class="p-2.5 rounded-lg border cursor-pointer transition text-xs space-y-1 ${{active ? 'bg-amber-600 text-white border-amber-500 font-semibold shadow-sm' : 'bg-[var(--card)] border-[var(--border)] hover:border-amber-500/50'}}">
            <div class="font-bold">${{t ? t.sub_scene : n4.name}}</div>
            <div class="text-xs ${{active ? 'text-amber-100' : 'text-[var(--muted-foreground)]'}}">💰 ${{t ? t.fee_mode : ''}}</div>
          </div>
        `;
      }}).join('');

      // 第5级展示当前第3级下全部候选模板（高亮当前选中的第4级对应的唯一模板）
      document.getElementById('cnt-col5').textContent = `${{l4List.length}}套候选`;
      col5.innerHTML = l4List.map(n4 => {{
        const active = n4.id === selL4;
        const t = TPL_MAP[n4.tpl_code];
        if (!t) return '';
        return `
          <div onclick="selectMiller(4, '${{n4.id}}', '${{t.code}}')" class="p-3 rounded-xl border cursor-pointer transition space-y-2 ${{active ? 'bg-emerald-600/15 border-emerald-500 ring-2 ring-emerald-500/30' : 'bg-[var(--card)] border-[var(--border)] opacity-75 hover:opacity-100'}}">
            <div class="flex items-center justify-between">
              <span class="px-2 py-0.5 rounded bg-emerald-600 text-white font-mono text-xs font-bold">${{t.code}}</span>
              <span class="text-xs text-emerald-500 font-semibold">${{active ? '🎯 当前唯一锁定' : '同组对比模板'}} · 实签${{t.hist_cnt}}单</span>
            </div>
            <div class="text-xs font-bold">${{t.template_name}}</div>
            <div class="text-xs text-[var(--muted-foreground)] leading-relaxed bg-[var(--background)] p-2 rounded border border-[var(--border)]">
              <b class="text-amber-500">排他边界：</b>${{t.disambiguation_rule}}
            </div>
            <div class="flex flex-wrap gap-1 pt-1">
              ${{t.new_products.slice(0, 6).map(p => `<span class="px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-500 font-mono text-xs">${{p.code}} ${{p.name}}</span>`).join('')}}
              ${{t.new_products.length > 6 ? `<span class="px-1.5 py-0.5 text-xs text-[var(--muted-foreground)]">+${{t.new_products.length - 6}}项...</span>` : ''}}
            </div>
          </div>
        `;
      }}).join('');
    }}

    function selectMiller(level, id, tplCode) {{
      if (level === 1) selL1 = id;
      if (level === 2) selL2 = id;
      if (level === 3) selL3 = id;
      if (level === 4) selL4 = id;
      renderMillerColumns();
      const activeN1 = DATA.tree.children.find(x => x.id === selL1);
      const activeN2 = activeN1?.children?.find(x => x.id === selL2);
      const activeN3 = activeN2?.children?.find(x => x.id === selL3);
      const activeN4 = activeN3?.children?.find(x => x.id === selL4);
      const code = tplCode || activeN4?.tpl_code;
      if (code) showTemplateDetail(code);
    }}

    // ==================== 视图3：12组排他决策树渲染 ====================
    function renderDecisionCards() {{
      const grid = document.getElementById('decision-grid');
      grid.innerHTML = DATA.disambiguation_groups.map(g => {{
        const codes = g.conflict_templates.split('、');
        const lines = g.decision_logic.split('\\n');
        return `
          <div class="bg-[var(--card)] border border-[var(--border)] rounded-xl p-4 shadow-sm space-y-3 flex flex-col justify-between">
            <div class="space-y-2">
              <div class="flex items-center justify-between gap-2">
                <span class="px-2 py-0.5 rounded bg-blue-600 text-white text-xs font-bold">${{g.group_id}}</span>
                <span class="text-xs font-mono text-[var(--muted-foreground)]">${{g.shared_db_mapping}}</span>
              </div>
              <h3 class="text-sm font-bold">${{g.group_name}}</h3>
              <div class="bg-[var(--background)] border border-[var(--border)] rounded-lg p-3 space-y-1.5 text-xs leading-relaxed">
                ${{lines.map(l => `<div>${{l}}</div>`).join('')}}
              </div>
            </div>
            <div class="pt-2 border-t border-[var(--border)] flex flex-wrap items-center gap-1.5">
              <span class="text-xs text-[var(--muted-foreground)]">点击查看涉及模板：</span>
              ${{codes.map(c => `<button onclick="showTemplateDetail('${{c.trim()}}')" class="px-2 py-0.5 rounded bg-emerald-500/15 hover:bg-emerald-500/30 text-emerald-500 font-mono text-xs font-semibold transition">${{c.trim()}}</button>`).join('')}}
            </div>
          </div>
        `;
      }}).join('');
    }}

    // ==================== 全局搜索与路径高亮 ====================
    function handleSearch(q) {{
      searchQuery = (q || '').trim().toLowerCase();
      const clearBtn = document.getElementById('clear-search');
      const statEl = document.getElementById('search-stat');

      if (!searchQuery) {{
        clearBtn.classList.add('hidden');
        statEl.textContent = '点击任意节点可展开/折叠下级；点击绿色模板节点查看右侧完整第5级绑定卡片';
        function clearHits(n) {{
          n._searchHit = false;
          if (n.children) n.children.forEach(clearHits);
        }}
        clearHits(DATA.tree);
        renderTreeGraph();
        return;
      }}

      clearBtn.classList.remove('hidden');
      let hitTemplates = [];

      function markSearch(node) {{
        let hit = false;
        if (node.leaf) {{
          const t = node.leaf;
          const hay = [
            t.code, t.template_name, t.sub_scene, t.fee_mode, t.target_constraint,
            t.disambiguation_rule, ...(t.keywords || []),
            ...(t.new_products || []).map(p => p.code + ' ' + p.name),
            ...(t.erp_products || [])
          ].join(' ').toLowerCase();
          if (hay.includes(searchQuery)) {{
            hit = true;
            hitTemplates.push(t.code);
          }}
        }} else if (node.name.toLowerCase().includes(searchQuery)) {{
          hit = true;
        }}

        if (node.children) {{
          for (const c of node.children) {{
            if (markSearch(c)) hit = true;
          }}
        }}
        node._searchHit = hit;
        if (hit && node.children && node.children.length > 0) {{
          node._collapsed = false;
        }}
        return hit;
      }}

      markSearch(DATA.tree);
      statEl.textContent = `🔍 搜索 "${{q}}" 命中 ${{hitTemplates.length}} 套标准模板，已在图谱中自动展开并高亮黄色边框路径！`;
      if (hitTemplates.length > 0) {{
        showTemplateDetail(hitTemplates[0]);
      }}
      renderTreeGraph();
    }}

    function clearSearch() {{
      const inp = document.getElementById('global-search');
      inp.value = '';
      handleSearch('');
    }}

    function switchView(view) {{
      currentView = view;
      document.getElementById('view-graph').classList.toggle('hidden', view !== 'graph');
      document.getElementById('view-miller').classList.toggle('hidden', view !== 'miller');
      document.getElementById('view-decision').classList.toggle('hidden', view !== 'decision');
      document.getElementById('graph-controls').classList.toggle('hidden', view !== 'graph');

      const btns = {{
        graph: document.getElementById('btn-view-graph'),
        miller: document.getElementById('btn-view-miller'),
        decision: document.getElementById('btn-view-decision'),
      }};
      Object.keys(btns).forEach(k => {{
        if (k === view) {{
          btns[k].className = 'px-3 py-1.5 rounded-md text-xs font-semibold bg-blue-600 text-white shadow-sm transition';
        }} else {{
          btns[k].className = 'px-3 py-1.5 rounded-md text-xs font-semibold text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition';
        }}
      }});
    }}

    // 初始化渲染
    updateTransform();
    renderTreeGraph();
    renderMillerColumns();
    renderDecisionCards();
  </script>
</body>
</html>
"""
    return html


def main():
    data_obj = build_hierarchy_data()
    html_str = generate_html(data_obj)

    local_html_path = os.path.join(OUT_DIR, "合同分类与78套模板五级知识图谱.html")
    with open(local_html_path, "w", encoding="utf-8") as f:
        f.write(html_str)
    print(f"[OK] 已生成本地可双击打开的知识图谱 HTML: {local_html_path}")

    artifact_html_path = os.path.join(ARTIFACT_DIR, "contract_template_knowledge_graph.html")
    with open(artifact_html_path, "w", encoding="utf-8") as f:
        f.write(html_str)
    print(f"[OK] 已生成 Artifact HTML: {artifact_html_path}")


if __name__ == "__main__":
    main()
