# -*- coding: utf-8 -*-
"""
生成《AI合同分类与78套标准模板 · 五级知识图谱与决策树》交互式可视化 HTML
针对用户反馈彻底优化：
1. 解决大规模截断问题：
   - 彻底废除省略号截断（...），采用自适应多行文本排版与安全字宽计算（中文字符按13.5px测量+充足外边距）；
   - 卡片宽度大幅增加（L4: 520px, L5: 500px），卡片高度自适应文本行数；
   - L4与L5为1对1严格锁定关系，同行卡片等高严密对齐。
2. 解决线条交叉与层级紊乱问题：
   - 一级分类严格收敛为 8 大标准业务板块（01_国内专利 ~ 08_总委框架与保密协议）；
   - 二级分类严格按 1.1~1.10, 2.1~2.7, 3.1, 4.1~4.4, 5.1~5.4, 6.1~6.3, 7.1~7.3, 8.1~8.2 连续编号顺序组织，彻底消除跨板块穿插与同名分裂；
   - 重新设计 SVG 走线算法：L4到L5采用纯水平极简刚性直线连接；父子分支采用平滑S型贝塞尔曲线，并在列间走廊严格分道，实现 0 交叉、0 缠绕、0 重叠！
"""

import os
import json
import re
from template_5th_layer_data import TEMPLATE_5TH_LAYER_RULES, DISAMBIGUATION_GROUPS

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH_DIR = os.environ.get("CONTRACT_KB_SCRATCH_DIR", os.path.join(BASE_DIR, "data", "scratch"))
OUT_DIR = os.path.join(BASE_DIR, "data", "contract_merged")
ARTIFACT_DIR = os.environ.get("CONTRACT_KB_ARTIFACT_DIR", OUT_DIR)


def build_hierarchy_data():
    with open(os.path.join(SCRATCH_DIR, "extracted_78_templates_and_products.json"), "r", encoding="utf-8") as f:
        extracted = json.load(f)
    with open(os.path.join(SCRATCH_DIR, "all_new_products_with_ids.json"), "r", encoding="utf-8") as f:
        all_new_prods = json.load(f)
    with open(os.path.join(OUT_DIR, "contract_mapping_dictionary.json"), "r", encoding="utf-8") as f:
        mapping_dict = json.load(f)

    tpl_entries = {t["template_code"]: t for t in mapping_dict.get("templates", [])}

    # 规范化五级链路定义：8大业务板块 -> 26大二级子类 -> 细分子项 -> 78套四级约束 -> 78套五级锁定模板
    l1_l2_l3_config = {
        # 01_国内专利 (24)
        "MB20251205014": ("01 · 国内专利板块 (auth=1)", "1.1 专利新申请类 (常规/风险)", "国内专利常规新申请 (发明/实用新型/外观设计)"),
        "MB20251209001": ("01 · 国内专利板块 (auth=1)", "1.1 专利新申请类 (常规/风险)", "国内专利风险代理新申请 (未授权退款/后付费)"),
        "MB20251205012": ("01 · 国内专利板块 (auth=1)", "1.2 专利加速审查类 (预审/优审)", "知识产权保护中心快速预审专项服务"),
        "MB20251205018": ("01 · 国内专利板块 (auth=1)", "1.2 专利加速审查类 (预审/优审)", "国家知识产权局优先审查专项服务"),
        "MB20251205021": ("01 · 国内专利板块 (auth=1)", "1.3 专利复审与确权诉无类", "专利驳回复审请求及答辩专项服务"),
        "MB20251205029": ("01 · 国内专利板块 (auth=1)", "1.3 专利复审与确权诉无类", "专利侵权诉讼及专利无效宣告联动服务"),
        "MB20251205013": ("01 · 国内专利板块 (auth=1)", "1.4 专利年费维持与变更类", "国内专利年费代缴与维持托管服务"),
        "MB20251205017": ("01 · 国内专利板块 (auth=1)", "1.4 专利年费维持与变更类", "优先权副本/登记簿副本/DAS码官方证明代办"),
        "MB20251205019": ("01 · 国内专利板块 (auth=1)", "1.4 专利年费维持与变更类", "专利著录项目变更 (更名/改址/改人/改所)"),
        "MB20251205015": ("01 · 国内专利板块 (auth=1)", "1.5 专利实施许可备案类", "专利实施许可合同新增备案代理服务"),
        "MB20251205016": ("01 · 国内专利板块 (auth=1)", "1.5 专利实施许可备案类", "专利实施许可合同备案注销代理服务"),
        "MB20251205027": ("01 · 国内专利板块 (auth=1)", "1.6 专利转让代办类 (非买卖)", "专利转让著录变更登记代办手续 (非买卖)"),
        "MB20251205045": ("01 · 国内专利板块 (auth=1)", "1.7 专利交易买卖类 (运营主体)", "运营主体作为买方受让外部国内有效专利"),
        "MB20251205046": ("01 · 国内专利板块 (auth=1)", "1.7 专利交易买卖类 (运营主体)", "运营主体作为卖方转让自有专利给客户"),
        "MB20251205024": ("01 · 国内专利板块 (auth=1)", "1.8 专利评价与布图设计类", "实用新型/外观设计专利权评价报告代办"),
        "MB20251205042": ("01 · 国内专利板块 (auth=1)", "1.8 专利评价与布图设计类", "集成电路布图设计专有权登记代理服务"),
        "MB20251205086": ("01 · 国内专利板块 (auth=1)", "1.9 国内专利多事项打包组合类", "国内专利多事项综合打包全流程服务"),
        "MB20251205020": ("01 · 国内专利板块 (auth=1)", "1.10 专利检索分析与尽调类", "企业专利导航分析与产业布局报告"),
        "MB20251205022": ("01 · 国内专利板块 (auth=1)", "1.10 专利检索分析与尽调类", "专利查新检索与技术分析服务报告"),
        "MB20251205023": ("01 · 国内专利板块 (auth=1)", "1.10 专利检索分析与尽调类", "专利法律状态及稳定性分析报告"),
        "MB20251205025": ("01 · 国内专利板块 (auth=1)", "1.10 专利检索分析与尽调类", "专利侵权分析与风险规避分析报告"),
        "MB20251205026": ("01 · 国内专利板块 (auth=1)", "1.10 专利检索分析与尽调类", "企业上市/投资并购专利尽职调查报告"),
        "MB20251205028": ("01 · 国内专利板块 (auth=1)", "1.10 专利检索分析与尽调类", "买方专利资产包价值评估与尽调服务"),
        "MB20251229002": ("01 · 国内专利板块 (auth=1)", "1.10 专利检索分析与尽调类", "专利FTO自由实施全面尽职调查报告"),

        # 02_国内商标 (17)
        "MB20251205057": ("02 · 国内商标板块 (auth=2)", "2.1 商标注册与风险包过类", "国内商标常规新申请注册代理"),
        "MB20251205059": ("02 · 国内商标板块 (auth=2)", "2.1 商标注册与风险包过类", "国内商标风险包过注册 (驳回全额退款)"),
        "MB20251205047": ("02 · 国内商标板块 (auth=2)", "2.2 商标变更类 (图文vs文字)", "国内商标变更服务 (图文版，含图样附表)"),
        "MB20260729001": ("02 · 国内商标板块 (auth=2)", "2.2 商标变更类 (图文vs文字)", "国内商标变更服务 (纯文字版，快速精简)"),
        "MB20251205054": ("02 · 国内商标板块 (auth=2)", "2.3 商标续展、补证与许可类", "国内商标期满续展注册代理服务"),
        "MB20251205049": ("02 · 国内商标板块 (auth=2)", "2.3 商标续展、补证与许可类", "商标注册证遗失补发代办服务"),
        "MB20251205053": ("02 · 国内商标板块 (auth=2)", "2.3 商标续展、补证与许可类", "国内商标使用许可合同备案代理"),
        "MB20251205058": ("02 · 国内商标板块 (auth=2)", "2.4 商标转让代办类 (非买卖)", "国内商标转让著录变更登记代办 (非买卖)"),
        "MB20251205043": ("02 · 国内商标板块 (auth=2)", "2.5 商标交易买卖类 (运营主体)", "运营主体作为买方受让外部国内有效商标"),
        "MB20251205044": ("02 · 国内商标板块 (auth=2)", "2.5 商标交易买卖类 (运营主体)", "运营主体作为卖方转让自有商标给客户"),
        "MB20251205048": ("02 · 国内商标板块 (auth=2)", "2.6 商标确权攻防与评审类", "商标初审公告期异议申请专项服务"),
        "MB20251205050": ("02 · 国内商标板块 (auth=2)", "2.6 商标确权攻防与评审类", "被他人提出商标异议的答辩代理服务"),
        "MB20251205051": ("02 · 国内商标板块 (auth=2)", "2.6 商标确权攻防与评审类", "商标局驳回通知后的驳回复审申请"),
        "MB20251205052": ("02 · 国内商标板块 (auth=2)", "2.6 商标确权攻防与评审类", "连续三年不使用注册商标撤销申请 (撤三)"),
        "MB20251205055": ("02 · 国内商标板块 (auth=2)", "2.6 商标确权攻防与评审类", "商标撤三答辩与提供使用证据代理"),
        "MB20251205056": ("02 · 国内商标板块 (auth=2)", "2.6 商标确权攻防与评审类", "针对已注册商标提起无效宣告申请"),
        "MB20251205085": ("02 · 国内商标板块 (auth=2)", "2.7 国内商标多事项打包组合类", "国内商标多事项综合打包全流程服务"),

        # 03_国内著作权 (2)
        "MB20251205087": ("03 · 国内著作权板块 (auth=3)", "3.1 软件与作品著作权登记类", "计算机软件著作权与作品登记 (单项申请)"),
        "MB20251205088": ("03 · 国内著作权板块 (auth=3)", "3.1 软件与作品著作权登记类", "著作权多项登记与咨询组合包年服务"),

        # 04_跨板块组合合同 (6)
        "MB20251205069": ("04 · 跨板块组合业务 (auth=1,2,3)", "4.1 专利+商标双板块常规组合", "专利新申请 + 商标新申请组合代理"),
        "MB20251205072": ("04 · 跨板块组合业务 (auth=1,2,3)", "4.1 专利+商标双板块常规组合", "专利著录变更 + 商标变更组合代办"),
        "MB20251205068": ("04 · 跨板块组合业务 (auth=1,2,3)", "4.1 专利+商标双板块常规组合", "专利转让 + 商标转让过户代办组合"),
        "MB20251205078": ("04 · 跨板块组合业务 (auth=1,2,3)", "4.2 专利+著作权双板块组合", "专利代理 + 软件/作品著作权组合合同"),
        "MB20251205065": ("04 · 跨板块组合业务 (auth=1,2,3)", "4.3 商标+著作权双板块组合", "商标代理 + 软件/作品著作权组合合同"),
        "MB20251205074": ("04 · 跨板块组合业务 (auth=1,2,3)", "4.4 专利+商标+著作权三通组合", "专利 + 商标 + 著作权三栖综合服务合同"),

        # 05_国际专利与国际商标 (8)
        "MB20251205041": ("05 · 国际专利与商标 (auth=8,9)", "5.1 PCT国际申请阶段类", "PCT国际专利申请阶段递交服务 (RO/CN/IB)"),
        "MB20251205037": ("05 · 国际专利与商标 (auth=8,9)", "5.2 涉外专利进国家阶段/巴黎公约", "涉外专利进入国家阶段/巴黎公约 (包干固定总价)"),
        "MB20251205039": ("05 · 国际专利与商标 (auth=8,9)", "5.2 涉外专利进国家阶段/巴黎公约", "涉外专利进入国家阶段/巴黎公约 (分阶段实报实销)"),
        "MB20251205038": ("05 · 国际专利与商标 (auth=8,9)", "5.3 涉外专利OA答复与复审类", "涉外专利审查答复/复审 (包干固定总价版)"),
        "MB20251205040": ("05 · 国际专利与商标 (auth=8,9)", "5.3 涉外专利OA答复与复审类", "涉外专利审查答复/复审 (分阶段收费版)"),
        "MB20251205035": ("05 · 国际专利与商标 (auth=8,9)", "5.4 国际商标注册与涉外维护类", "涉外商标单一国家注册与马德里体系申请"),
        "MB20251205036": ("05 · 国际专利与商标 (auth=8,9)", "5.4 国际商标注册与涉外维护类", "中国港澳台商标注册与公证认证手续"),
        "MB20251205030": ("05 · 国际专利与商标 (auth=8,9)", "5.4 国际商标注册与涉外维护类", "涉外商标变更/转让/续展/监控后续维护服务"),

        # 06_科创项目与财税审计 (7)
        "MB20251205082": ("06 · 科创项目与财税审计 (auth=4,5,11)", "6.1 政策项目申报与体系认证", "国家高新技术企业认定咨询与申报辅导"),
        "MB20251205080": ("06 · 科创项目与财税审计 (auth=4,5,11)", "6.1 政策项目申报与体系认证", "专精特新/研发机构/技术中心梯度培育申报"),
        "MB20251205084": ("06 · 科创项目与财税审计 (auth=4,5,11)", "6.1 政策项目申报与体系认证", "知识产权贯标/ISO/绿色工厂/两化融合认证"),
        "MB20251205064": ("06 · 科创项目与财税审计 (auth=4,5,11)", "6.1 政策项目申报与体系认证", "香港发展品牌升级转型(BUD专项基金)申报"),
        "MB20251205063": ("06 · 科创项目与财税审计 (auth=4,5,11)", "6.2 科技查新与软件登记测试", "第三方科技查新报告代办与软件产品测试"),
        "MB20251205061": ("06 · 科创项目与财税审计 (auth=4,5,11)", "6.3 财税专项审计报告类", "广东省内高企财税专项审计与研发辅助账"),
        "MB20251205062": ("06 · 科创项目与财税审计 (auth=4,5,11)", "6.3 财税专项审计报告类", "全国各省市高企财税专项审计与研发辅助账"),

        # 07_法律服务与维权诉讼 (11)
        "MB20251205071": ("07 · 法律服务与维权诉讼 (auth=7)", "7.1 单案维权诉讼与诉无联动", "知识产权侵权纠纷民事一审诉讼代理"),
        "MB20251205066": ("07 · 法律服务与维权诉讼 (auth=7)", "7.1 单案维权诉讼与诉无联动", "知识产权民事纠纷二审诉讼代理服务"),
        "MB20251205067": ("07 · 法律服务与维权诉讼 (auth=7)", "7.1 单案维权诉讼与诉无联动", "知识产权行政诉讼案件代理专项"),
        "MB20251205075": ("07 · 法律服务与维权诉讼 (auth=7)", "7.1 单案维权诉讼与诉无联动", "专利诉讼与专利无效宣告联动全包代理"),
        "MB20251205073": ("07 · 法律服务与维权诉讼 (auth=7)", "7.2 非诉专项法律服务类", "常年知产法顾/合同审查/律师函告/电商维权"),
        "MB20251205076": ("07 · 法律服务与维权诉讼 (auth=7)", "7.3 批量维权打假维权总委", "知识产权批量维权常规总委委托服务"),
        "MB20251205077": ("07 · 法律服务与维权诉讼 (auth=7)", "7.3 批量维权打假维权总委", "侵犯商业秘密与知识产权刑民维权总委"),
        "MB20251205079": ("07 · 法律服务与维权诉讼 (auth=7)", "7.3 批量维权打假维权总委", "知识产权侵权证据调查与公证取证专项"),
        "MB20251205081": ("07 · 法律服务与维权诉讼 (auth=7)", "7.3 批量维权打假维权总委", "知识产权批量维权全风险代理专项协议"),
        "MB20251205083": ("07 · 法律服务与维权诉讼 (auth=7)", "7.3 批量维权打假维权总委", "知识产权行政查处与市监执法打假专项"),
        "MB20251205070": ("07 · 法律服务与维权诉讼 (auth=7)", "7.3 批量维权打假维权总委", "战略合作框架及知识产权维权综合专项"),

        # 08_总委框架与保密协议 (3)
        "MB20251205089": ("08 · 总委框架与保密协议 (type=2,4)", "8.1 长期总委框架协议类", "年度知识产权服务长期总委框架 (纯国内业务)"),
        "MB20251225003": ("08 · 总委框架与保密协议 (type=2,4)", "8.1 长期总委框架协议类", "年度知识产权服务长期总委框架 (含国际专利版)"),
        "MB20251205011": ("08 · 总委框架与保密协议 (type=2,4)", "8.2 技术秘密与商业保密协议", "技术交底与商业秘密保密协议 (NDA)"),
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

    for tcode, (l1_name, l2_name, l3_name) in l1_l2_l3_config.items():
        rule = TEMPLATE_5TH_LAYER_RULES[tcode]
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

        # L4 node: 完整场景 + 收费模式，不截断
        l4_label = f"【场景】{rule['sub_scene']}\n【收费/结算】{rule['fee_mode']}"

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

        # L5: 完整模板全称 + 版本 + 绑定产品数 + 实签数，不截断
        l5_label = f"{tcode} 《{tname}》\n版本 {ver} · 绑定 {len(new_prods)} 个新系统产品 · 历史实签 {hist_cnt} 单"

        node4 = {
            "id": f"L4_{tcode}",
            "name": l4_label,
            "level": 4,
            "tpl_code": tcode,
            "children": [
                {
                    "id": f"L5_{tcode}",
                    "name": l5_label,
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
<body class="bg-[var(--background)] text-[var(--foreground)] antialiased select-none overflow-hidden h-screen flex flex-col font-sans">

  <!-- 顶部导航与全局搜索栏 -->
  <header class="bg-[var(--card)] border-b border-[var(--border)] px-5 py-3 flex flex-wrap items-center justify-between gap-3 shrink-0 z-20 shadow-sm">
    <div class="flex items-center gap-3">
      <div class="w-3 h-8 rounded-full bg-blue-600"></div>
      <div>
        <h1 class="text-base font-bold tracking-tight flex items-center gap-2">
          <span>AI合同分类与78套标准模板 · 五级路由知识图谱</span>
          <span class="text-xs px-2 py-0.5 rounded-full bg-blue-500/15 text-blue-500 font-medium">1级→2级→3级→4级→5级 (1对1精准锁定)</span>
        </h1>
        <p class="text-xs text-[var(--muted-foreground)]">涵盖 8大标准业务板块 · 26大二级事项 · 78套四级业务场景 · 78套标准合同模板 · 603个产品编码</p>
      </div>
    </div>

    <!-- 视图切换器 -->
    <div class="flex items-center gap-1.5 bg-[var(--background)] p-1 rounded-lg border border-[var(--border)]">
      <button id="btn-view-graph" onclick="switchView('graph')" class="px-3 py-1.5 rounded-md text-xs font-semibold bg-blue-600 text-white shadow-sm transition">
        🌳 交互式五级知识图谱树 (无截断/无交叉)
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
          placeholder="搜产品编码(CNIP0057)、FTO、交易运营、包干、MB..."
          oninput="handleSearch(this.value)"
          class="w-72 px-3 py-1.5 text-xs rounded-lg bg-[var(--background)] border border-[var(--border)] text-[var(--foreground)] focus:outline-none focus:border-blue-500"
        />
        <button id="clear-search" onclick="clearSearch()" class="hidden absolute right-2 top-1.5 text-xs text-[var(--muted-foreground)] hover:text-[var(--foreground)]">✕</button>
      </div>
      <div id="graph-controls" class="flex items-center gap-1">
        <button onclick="expandUpToLevel(1)" class="px-2.5 py-1.5 text-xs rounded-md border border-[var(--border)] bg-[var(--background)] hover:bg-blue-500/10" title="仅展示8大板块">收起(1级)</button>
        <button onclick="expandUpToLevel(2)" class="px-2.5 py-1.5 text-xs rounded-md border border-[var(--border)] bg-[var(--background)] hover:bg-blue-500/10" title="展开到第2级">展开2级</button>
        <button onclick="expandUpToLevel(3)" class="px-2.5 py-1.5 text-xs rounded-md border border-[var(--border)] bg-[var(--background)] hover:bg-blue-500/10" title="展开到第3级">展开3级</button>
        <button onclick="expandUpToLevel(5)" class="px-2.5 py-1.5 text-xs rounded-md border border-[var(--border)] bg-[var(--background)] hover:bg-blue-500/10" title="展开全部5级">全展开(5级)</button>
        <button onclick="resetZoom()" class="px-2.5 py-1.5 text-xs rounded-md border border-[var(--border)] bg-[var(--background)] hover:bg-blue-500/10">自适应居中</button>
      </div>
      <button id="btn-toggle-drawer" onclick="toggleDrawer()" class="px-2.5 py-1.5 text-xs rounded-md border border-emerald-500/40 bg-emerald-500/10 text-emerald-500 font-semibold hover:bg-emerald-500/20" title="收起或展开右侧详情抽屉以释放画布空间">
        📐 收起右侧栏
      </button>
    </div>
  </header>

  <!-- 顶部五级图例说明条 -->
  <div class="bg-[var(--card)]/60 border-b border-[var(--border)] px-5 py-1.5 flex items-center justify-between text-xs shrink-0">
    <div class="flex items-center gap-4 flex-wrap">
      <span class="font-semibold text-[var(--muted-foreground)]">五级图谱节点图例：</span>
      <span class="flex items-center gap-1.5"><span class="w-3 h-3 rounded bg-indigo-600 inline-block"></span><b>第1级</b>：8大业务板块 (01~08)</span>
      <span class="text-[var(--muted-foreground)]">→</span>
      <span class="flex items-center gap-1.5"><span class="w-3 h-3 rounded bg-blue-600 inline-block"></span><b>第2级</b>：26大业务分类 (1.1~8.2)</span>
      <span class="text-[var(--muted-foreground)]">→</span>
      <span class="flex items-center gap-1.5"><span class="w-3 h-3 rounded bg-cyan-600 inline-block"></span><b>第3级</b>：细分子项与合同性质</span>
      <span class="text-[var(--muted-foreground)]">→</span>
      <span class="flex items-center gap-1.5"><span class="w-3 h-3 rounded bg-amber-600 inline-block"></span><b>第4级</b>：细分业务场景与收费结算约束</span>
      <span class="text-[var(--muted-foreground)]">→</span>
      <span class="flex items-center gap-1.5"><span class="w-3 h-3 rounded bg-emerald-600 inline-block"></span><b>第5级(终节点)</b>：唯一标准合同模板 + 绑定产品编码</span>
    </div>
    <div id="search-stat" class="text-xs font-medium text-blue-500">已彻底优化：0文本截断 · 0跨线交叉 · 4级与5级严格1对1水平锁定</div>
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
          <span>【第1级】8大业务板块</span>
          <span id="cnt-col1" class="px-1.5 py-0.5 rounded bg-indigo-500/20 text-xs">8</span>
        </div>
        <div id="miller-col1" class="flex-1 overflow-y-auto p-2 space-y-1.5"></div>
      </div>
      <!-- 第2级列 -->
      <div class="flex flex-col h-full overflow-hidden">
        <div class="px-3 py-2 bg-blue-600/10 border-b border-[var(--border)] text-xs font-bold text-blue-500 flex justify-between items-center">
          <span>【第2级】26大业务分类</span>
          <span id="cnt-col2" class="px-1.5 py-0.5 rounded bg-blue-500/20 text-xs">-</span>
        </div>
        <div id="miller-col2" class="flex-1 overflow-y-auto p-2 space-y-1.5"></div>
      </div>
      <!-- 第3级列 -->
      <div class="flex flex-col h-full overflow-hidden">
        <div class="px-3 py-2 bg-cyan-600/10 border-b border-[var(--border)] text-xs font-bold text-cyan-500 flex justify-between items-center">
          <span>【第3级】细分子项与性质</span>
          <span id="cnt-col3" class="px-1.5 py-0.5 rounded bg-cyan-500/20 text-xs">-</span>
        </div>
        <div id="miller-col3" class="flex-1 overflow-y-auto p-2 space-y-1.5"></div>
      </div>
      <!-- 第4级列 -->
      <div class="flex flex-col h-full overflow-hidden">
        <div class="px-3 py-2 bg-amber-600/10 border-b border-[var(--border)] text-xs font-bold text-amber-500 flex justify-between items-center">
          <span>【第4级】细分业务场景与约束</span>
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
          <p class="text-xs text-[var(--muted-foreground)] mt-1">针对数据库中前3级分类完全相同导致模板冲突的场景，提炼出的硬性排他决策图谱</p>
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
            <span id="d-ver" class="text-xs text-[var(--muted-foreground)] font-mono">V9.0</span>
            <span id="d-cnt" class="text-xs px-2 py-0.5 rounded-full bg-blue-500/15 text-blue-500 font-semibold">实签 61 单</span>
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

    // 默认展开到第2级（展示8大板块及26大业务分类，清晰美观不拥挤）
    function initCollapseState(node, maxOpenLevel = 2) {{
      node._collapsed = (node.level >= maxOpenLevel && node.children && node.children.length > 0);
      if (node.children) {{
        node.children.forEach(c => initCollapseState(c, maxOpenLevel));
      }}
    }}
    initCollapseState(DATA.tree, 2);

    // 默认展开第一个板块（01国内专利）的1.1类至第5级，方便用户立即查看五级全链路示范
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

    // ==================== 彻底消除截断与交叉的 SVG 树状图核心排版算法 ====================
    // 各层基准最小宽度：L0=190, L1=220, L2=260, L3=320, L4=520, L5=490
    const BASE_MIN_WIDTHS = [190, 220, 260, 320, 520, 490];
    const COL_GAP = 82;   // 列间充裕通道宽度
    const V_GAP = 20;     // 纵向节点间距（充裕呼吸感）

    function escSvg(s) {{
      return String(s || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
    }}

    // 采用保守安全的字宽估计（中文字符 13.5px，英文字符 7.8px），杜绝任何文字溢出截断
    function measureTextPx(str) {{
      let w = 0;
      for (let i = 0; i < str.length; i++) {{
        const code = str.charCodeAt(i);
        w += code > 255 ? 13.5 : 7.8;
      }}
      return Math.ceil(w);
    }}

    // 严密自动折行：在 maxPx 内折行，绝不使用省略号（...）截断任何有效信息！
    function wrapSingleLine(str, maxPx) {{
      if (measureTextPx(str) <= maxPx) return [str];
      const result = [];
      let cur = '';
      let curW = 0;
      for (let i = 0; i < str.length; i++) {{
        const ch = str[i];
        const chW = str.charCodeAt(i) > 255 ? 13.5 : 7.8;
        if (curW + chW > maxPx && cur.length > 0) {{
          result.push(cur);
          cur = ch;
          curW = chW;
        }} else {{
          cur += ch;
          curW += chW;
        }}
      }}
      if (cur) result.push(cur);
      return result;
    }}

    function prepareNodeLinesAndSize(node) {{
      // 赋予各层最舒适的单行最大字宽限制
      const maxLinePx = node.level === 4 ? 470 : (node.level === 5 ? 440 : (node.level === 3 ? 290 : 230));
      const rawLines = String(node.name || '').split('\\n');
      const wrapped = [];
      rawLines.forEach(line => {{
        wrapSingleLine(line, maxLinePx).forEach(sub => wrapped.push(sub));
      }});
      node._lines = wrapped;
      const maxTextW = wrapped.reduce((m, l) => Math.max(m, measureTextPx(l)), 0);
      const hasChildren = node.children && node.children.length > 0;
      // 预留右侧徽标（64px）或留白（32px）
      node._reqW = Math.max(BASE_MIN_WIDTHS[node.level] || 240, maxTextW + (hasChildren ? 64 : 34));
      // 行高设定为 20px，上下内边距 16px
      node._h = Math.max(42, 16 + wrapped.length * 20);
    }}

    function shiftSubtreeY(node, deltaY) {{
      if (!deltaY) return;
      node._y += deltaY;
      const isCollapsed = node._collapsed;
      const children = (!isCollapsed && node.children) ? node.children : [];
      children.forEach(c => shiftSubtreeY(c, deltaY));
    }}

    function getSubtreeBoundsY(node) {{
      let minY = node._y;
      let maxY = node._y + node._h;
      const isCollapsed = node._collapsed;
      const children = (!isCollapsed && node.children) ? node.children : [];
      children.forEach(c => {{
        const b = getSubtreeBoundsY(c);
        if (b.minY < minY) minY = b.minY;
        if (b.maxY > maxY) maxY = b.maxY;
      }});
      return {{ minY, maxY }};
    }}

    let lastTreeBounds = {{ width: 1600, height: 900 }};

    function renderTreeGraph() {{
      let currentY = 0;
      const visibleNodes = [];
      const visibleLinks = [];
      const colWidths = [...BASE_MIN_WIDTHS];

      // 第一遍：深度优先收集可见节点，精确度量卡片尺寸并对齐L4-L5
      function collectAndMeasure(node, parent = null) {{
        node._parent = parent;
        prepareNodeLinesAndSize(node);
        colWidths[node.level] = Math.max(colWidths[node.level] || 240, node._reqW);
        visibleNodes.push(node);

        const isCollapsed = node._collapsed;
        const children = (!isCollapsed && node.children) ? node.children : [];
        children.forEach(c => {{
          visibleLinks.push({{ source: node, target: c }});
          collectAndMeasure(c, node);
        }});

        // 核心对齐：第4级与第5级是1对1完全锁定关系，强制令同行卡片等高，水平直线连线对齐！
        if (node.level === 4 && children.length === 1) {{
          const sharedH = Math.max(node._h, children[0]._h);
          node._h = sharedH;
          children[0]._h = sharedH;
        }}
      }}

      collectAndMeasure(DATA.tree, null);

      // 计算每列 X 坐标
      const levelX = [0];
      for (let lvl = 1; lvl <= 5; lvl++) {{
        levelX[lvl] = levelX[lvl - 1] + colWidths[lvl - 1] + COL_GAP;
      }}

      // 第二遍：无重叠树形纵向布局（父节点垂直居中子树重心，严格包围盒防碰撞平移）
      function layoutY(node) {{
        const startY = currentY;
        const isCollapsed = node._collapsed;
        const children = (!isCollapsed && node.children) ? node.children : [];

        if (children.length === 0) {{
          node._y = startY;
          currentY = startY + node._h + V_GAP;
        }} else {{
          children.forEach(c => layoutY(c));
          const firstCenter = children[0]._y + children[0]._h / 2;
          const lastCenter = children[children.length - 1]._y + children[children.length - 1]._h / 2;
          node._y = (firstCenter + lastCenter) / 2 - node._h / 2;

          const bounds = getSubtreeBoundsY(node);
          if (bounds.minY < startY) {{
            shiftSubtreeY(node, startY - bounds.minY);
          }}
          const finalBounds = getSubtreeBoundsY(node);
          currentY = Math.max(currentY, finalBounds.maxY + V_GAP);
        }}
        node._x = levelX[node.level] || 0;
        node._w = colWidths[node.level];
      }}

      layoutY(DATA.tree);

      const maxRight = Math.max(...visibleNodes.map(n => n._x + n._w), 1200);
      const maxBottom = Math.max(currentY, 600);
      lastTreeBounds = {{ width: maxRight, height: maxBottom }};

      // 渲染走线：
      // 1. 若为 L4 到 L5（1对1严格锁定），采用纯水平极简直线：M sx sy L tx ty；
      // 2. 其余分支采用分段三次贝塞尔平滑S曲线，完全约束在列间走廊内，杜绝交叉与遮挡！
      linksGroup.innerHTML = visibleLinks.map(l => {{
        const sx = l.source._x + l.source._w;
        const sy = l.source._y + l.source._h / 2;
        const tx = l.target._x;
        const ty = l.target._y + l.target._h / 2;
        const isL4toL5 = l.source.level === 4 && l.target.level === 5;
        const isHit = l.target._searchHit;
        const strokeColor = isHit ? '#3b82f6' : (isL4toL5 ? 'rgba(16, 185, 129, 0.65)' : 'rgba(148, 163, 184, 0.55)');
        const strokeW = isHit ? '2.5' : (isL4toL5 ? '2.0' : '1.5');

        let dPath = '';
        if (isL4toL5 || Math.abs(sy - ty) < 0.5) {{
          dPath = `M ${{sx}} ${{sy}} L ${{tx}} ${{ty}}`;
        }} else {{
          const midX = (sx + tx) / 2;
          dPath = `M ${{sx}} ${{sy}} C ${{midX}} ${{sy}}, ${{midX}} ${{ty}}, ${{tx}} ${{ty}}`;
        }}
        return `<path class="link-path" d="${{dPath}}" stroke="${{strokeColor}}" stroke-width="${{strokeW}}" />`;
      }}).join('');

      // 颜色主题
      const levelColors = [
        {{ bg: '#312e81', border: '#6366f1', text: '#ffffff', badge: '#4f46e5' }}, // L0 Root
        {{ bg: '#1e1b4b', border: '#6366f1', text: '#e0e7ff', badge: '#4338ca' }}, // L1 (8大板块)
        {{ bg: '#172554', border: '#3b82f6', text: '#dbeafe', badge: '#1d4ed8' }}, // L2 (26大分类)
        {{ bg: '#083344', border: '#06b6d4', text: '#cffafe', badge: '#0e7490' }}, // L3 (细分子项)
        {{ bg: '#451a03', border: '#f59e0b', text: '#fef3c7', badge: '#b45309' }}, // L4 (场景约束)
        {{ bg: '#064e3b', border: '#10b981', text: '#d1fae5', badge: '#047857' }}, // L5 (唯一模板)
      ];

      nodesGroup.innerHTML = visibleNodes.map(n => {{
        const w = n._w;
        const h = n._h;
        const c = levelColors[n.level] || levelColors[5];
        const hasChildren = n.children && n.children.length > 0;
        const leafCnt = countLeafTemplates(n);
        const isHit = n._searchHit;
        const borderStroke = isHit ? '#facc15' : c.border;
        const strokeW = isHit ? '3' : '1.5';

        const lines = n._lines || [n.name];
        const textBlockH = (lines.length - 1) * 20;
        const firstLineY = Math.round(h / 2 - textBlockH / 2 + 4.5);
        const textSvg = lines.map((lineText, idx) => {{
          const yPos = firstLineY + idx * 20;
          const isBold = idx === 0;
          const opacity = idx === 0 ? '1' : '0.9';
          return `<text x="14" y="${{yPos}}" fill="${{c.text}}" opacity="${{opacity}}" font-size="12" font-weight="${{isBold ? '600' : '400'}}">${{escSvg(lineText)}}</text>`;
        }}).join('');

        const badgeSvg = hasChildren ? `
          <g transform="translate(${{w - 42}}, ${{h / 2 - 10}})">
            <rect width="32" height="20" rx="10" fill="${{c.badge}}" />
            <text x="16" y="14" text-anchor="middle" fill="#ffffff" font-size="11" font-weight="bold">${{n._collapsed ? '+' + leafCnt : leafCnt}}</text>
          </g>
        ` : '';

        return `
          <g class="node-group" transform="translate(${{n._x}}, ${{n._y}})" onclick="onNodeClick('${{n.id}}', '${{n.tpl_code || ''}}')">
            <rect class="node-rect" width="${{w}}" height="${{h}}" rx="8" fill="${{c.bg}}" stroke="${{borderStroke}}" stroke-width="${{strokeW}}" />
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
        // 如果展开第3级节点，自动展开其第4级子节点，直观展示第5级绿色模板节点
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
      const rect = svg.getBoundingClientRect();
      const availW = Math.max(rect.width - 60, 600);
      const availH = Math.max(rect.height - 60, 450);
      const scaleX = availW / Math.max(lastTreeBounds.width, 800);
      const scaleY = availH / Math.max(lastTreeBounds.height, 500);
      scale = Math.min(1.0, Math.max(0.32, Math.min(scaleX, scaleY * 1.15)));
      panX = 28;
      panY = Math.max(24, (rect.height - lastTreeBounds.height * scale) / 2);
      updateTransform();
    }}

    let drawerOpen = true;
    function toggleDrawer() {{
      drawerOpen = !drawerOpen;
      const drawer = document.getElementById('detail-drawer');
      const btn = document.getElementById('btn-toggle-drawer');
      if (drawerOpen) {{
        drawer.classList.remove('hidden');
        btn.textContent = '📐 收起右侧栏';
      }} else {{
        drawer.classList.add('hidden');
        btn.textContent = '📐 展开右侧栏';
      }}
      setTimeout(() => resetZoom(), 50);
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
            <span class="leading-snug pr-2">${{n1.name}}</span>
            <span class="px-1.5 py-0.5 rounded text-xs shrink-0 ${{active ? 'bg-white/20' : 'bg-indigo-500/15 text-indigo-400'}}">${{cnt}}套</span>
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

      document.getElementById('cnt-col5').textContent = `${{l4List.length}}套候选`;
      col5.innerHTML = l4List.map(n4 => {{
        const t = TPL_MAP[n4.tpl_code];
        if (!t) return '';
        const active = n4.id === selL4;
        return `
          <div onclick="showTemplateDetail('${{t.code}}')" class="p-3 rounded-xl border cursor-pointer transition space-y-2 ${{active ? 'bg-emerald-600/15 border-emerald-500 shadow-md' : 'bg-[var(--card)] border-[var(--border)] hover:border-emerald-500/40'}}">
            <div class="flex items-center justify-between">
              <span class="px-2 py-0.5 rounded bg-emerald-600 text-white font-mono font-bold text-xs">${{t.code}}</span>
              <span class="text-xs text-[var(--muted-foreground)] font-mono">${{t.version}} · 实签${{t.hist_cnt}}单</span>
            </div>
            <div class="font-bold text-xs leading-snug">${{t.template_name}}</div>
            <div class="p-2 rounded bg-[var(--background)] text-xs text-amber-500 border border-amber-500/20 leading-relaxed">
              <b>排他界限：</b>${{t.disambiguation_rule}}
            </div>
            <div class="flex items-center justify-between text-xs pt-1 border-t border-[var(--border)]">
              <span class="text-[var(--muted-foreground)]">绑定新系统产品：</span>
              <span class="px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400 font-bold">${{t.new_products.length}} 个</span>
            </div>
          </div>
        `;
      }}).join('');
    }}

    function selectMiller(col, id, tplCode) {{
      if (col === 1) {{ selL1 = id; selL2 = null; selL3 = null; selL4 = null; }}
      else if (col === 2) {{ selL2 = id; selL3 = null; selL4 = null; }}
      else if (col === 3) {{ selL3 = id; selL4 = null; }}
      else if (col === 4) {{
        selL4 = id;
        if (tplCode) showTemplateDetail(tplCode);
      }}
      renderMillerColumns();
    }}

    // ==================== 视图3：12组排他决策树卡片 ====================
    function renderDecisionCards() {{
      const grid = document.getElementById('decision-grid');
      const groups = DATA.disambiguation_groups || [];
      grid.innerHTML = groups.map((g, idx) => {{
        return `
          <div class="p-4 rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-sm space-y-3">
            <div class="flex items-start justify-between gap-2 border-b border-[var(--border)] pb-2.5">
              <div>
                <span class="text-xs font-bold text-blue-500 uppercase tracking-wide">冲突组 #${{idx + 1}} · ${{g.group_id}}</span>
                <h4 class="font-bold text-sm mt-0.5">${{g.group_name}}</h4>
              </div>
              <span class="px-2 py-0.5 rounded bg-amber-500/15 text-amber-500 text-xs font-semibold shrink-0">${{g.templates.length}}套候选冲突</span>
            </div>
            <div class="p-2.5 rounded-lg bg-amber-500/10 border border-amber-500/20 text-xs space-y-1">
              <div class="font-bold text-amber-500">🎯 核心排他分流规则：</div>
              <div class="leading-relaxed">${{g.core_rule}}</div>
            </div>
            <div class="space-y-2">
              ${{g.templates.map(tcode => {{
                const t = TPL_MAP[tcode];
                if (!t) return '';
                return `
                  <div onclick="showTemplateDetail('${{tcode}}')" class="p-2.5 rounded-lg border border-[var(--border)] bg-[var(--background)] hover:border-emerald-500/60 cursor-pointer transition text-xs space-y-1">
                    <div class="flex items-center justify-between">
                      <span class="font-mono font-bold text-emerald-500">${{tcode}}</span>
                      <span class="text-xs text-[var(--muted-foreground)] font-mono">${{t.version}}</span>
                    </div>
                    <div class="font-semibold">${{t.template_name}}</div>
                    <div class="text-[var(--muted-foreground)] leading-relaxed"><b>适用场景：</b>${{t.sub_scene}}</div>
                    <div class="text-[var(--muted-foreground)]"><b>收费模式：</b>${{t.fee_mode}}</div>
                  </div>
                `;
              }}).join('')}}
            </div>
          </div>
        `;
      }}).join('');
    }}

    // ==================== 搜索与路径高亮 ====================
    function handleSearch(q) {{
      searchQuery = (q || '').trim().toLowerCase();
      const clearBtn = document.getElementById('clear-search');
      if (searchQuery) {{
        clearBtn.classList.remove('hidden');
      }} else {{
        clearBtn.classList.add('hidden');
      }}

      matchedCodes.clear();
      if (!searchQuery) {{
        function clearHit(n) {{
          n._searchHit = false;
          if (n.children) n.children.forEach(clearHit);
        }}
        clearHit(DATA.tree);
        document.getElementById('search-stat').textContent = '点击任意节点可展开/折叠下级；点击绿色模板节点查看右侧完整第5级绑定卡片';
        renderTreeGraph();
        return;
      }}

      // 搜索全部模板
      DATA.templates.forEach(t => {{
        const corpus = [
          t.code, t.template_name, t.sub_scene, t.fee_mode,
          t.disambiguation_rule, ...(t.keywords || []),
          ...(t.new_products || []).map(p => p.code + ' ' + (p.name || '')),
          ...(t.erp_products || [])
        ].join(' ').toLowerCase();

        if (corpus.includes(searchQuery)) {{
          matchedCodes.add(t.code);
        }}
      }});

      document.getElementById('search-stat').textContent = `🔍 搜索 “${{q}}” 匹配到 ${{matchedCodes.size}} 套模板 (已在图谱中黄色高亮并自动展开链路)`;

      // 在树中标记高亮并强制展开命中的路径
      function markTree(n) {{
        let hit = false;
        if (n.tpl_code && matchedCodes.has(n.tpl_code)) {{
          hit = true;
        }}
        if (n.children) {{
          n.children.forEach(c => {{
            if (markTree(c)) hit = true;
          }});
          if (hit) {{
            n._collapsed = false;
          }}
        }}
        n._searchHit = hit;
        return hit;
      }}

      markTree(DATA.tree);
      renderTreeGraph();
      resetZoom();
    }}

    function clearSearch() {{
      const inp = document.getElementById('global-search');
      inp.value = '';
      handleSearch('');
    }}

    // ==================== 视图切换 ====================
    function switchView(view) {{
      currentView = view;
      ['graph', 'miller', 'decision'].forEach(v => {{
        const btn = document.getElementById(`btn-view-${{v}}`);
        const el = document.getElementById(`view-${{v}}`);
        if (v === view) {{
          btn.className = 'px-3 py-1.5 rounded-md text-xs font-semibold bg-blue-600 text-white shadow-sm transition';
          el.classList.remove('hidden');
        }} else {{
          btn.className = 'px-3 py-1.5 rounded-md text-xs font-semibold text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition';
          el.classList.add('hidden');
        }}
      }});
      if (view === 'graph') {{
        setTimeout(() => resetZoom(), 50);
      }}
    }}

    // 初始化渲染
    renderTreeGraph();
    resetZoom();
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
