# -*- coding: utf-8 -*-
"""
根据从 D:\\APP\\mubu\\resources\\app.asar 逆向解析出的幕布 (Mubu) 导入引擎规则：
1. Markdown 导入引擎 (h8 函数):
   - # / ## / ### 映射为 level 1~3 且自动带 heading 1~3 样式
   - 列表项 (- ) 必须使用【偶数个空格 (2空格/级)】缩进 (K = 10 + Math.floor(W/2))，不能用 Tab
   - 支持 **加粗**、`行内代码(codespan)`、++下划线++、~~删除线~~
2. OPML 2.0 导入引擎 (e_ 函数):
   - 支持 outline[text, _mubu_text, _note, _mubu_note, _heading, _color]
   - 可将排他规则、合同第一条摘要、绑定的产品明细放入节点 _note 备注中，让思维导图主干清爽不臃肿！
"""

import html
import os
import urllib.parse
from template_5th_layer_data import DISAMBIGUATION_GROUPS
from build_knowledge_graph_html import build_hierarchy_data

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_MD_CLEAN = os.path.join(
    BASE_DIR,
    "data",
    "contract_merged",
    "合同分类与78套模板五级思维导图_幕布精简导图版.md",
)
OUT_MD_FULL = os.path.join(
    BASE_DIR,
    "data",
    "contract_merged",
    "合同分类与78套模板五级思维导图_幕布全量产品版.md",
)
OUT_OPML = os.path.join(
    BASE_DIR,
    "data",
    "contract_merged",
    "合同分类与78套模板五级思维导图_幕布满血备注版.opml",
)


def mubu_encode(s: str) -> str:
    """Equivalent to JavaScript encodeURI() used by Mubu's _mubu_text and _mubu_note"""
    return urllib.parse.quote(s, safe="~@#$&()*!+=:;,.?/'")


def export_mubu_markdown(data_obj, include_all_products=False):
    """
    严格遵循幕布 app.asar 中 h8() 解析器的 Markdown 规则：
    - # 第1级：一级业务板块 (H1)
    - ## 第2级：二级业务板块与委托事项 auth_item (H2)
    - ### 第3级：三级业务子类与合同性质 (H3)
    - - 第4级：四级细分场景 + 收费/方向约束 (0空格列表)
    -   - 第5级：唯一标准合同模板 MB... (2空格列表)
    -     - 排他触发规则与绑定产品编码明细 (4空格列表)
    """
    tree_root = data_obj["tree"]
    lines = []

    for l1 in tree_root["children"]:
        l1_tpl_cnt = sum(
            len(l3["children"]) for l2 in l1["children"] for l3 in l2["children"]
        )
        lines.append(f"# 【第1级】{l1['name']}（共 {l1_tpl_cnt} 套标准模板）")

        for l2 in l1["children"]:
            l2_tpl_cnt = sum(len(l3["children"]) for l3 in l2["children"])
            lines.append(f"## 【第2级】{l2['name']}（{l2_tpl_cnt} 套）")

            for l3 in l2["children"]:
                lines.append(f"### 【第3级】{l3['name']}（{len(l3['children'])} 套）")

                for l4 in l3["children"]:
                    l5_node = l4["children"][0]
                    leaf = l5_node["leaf"]
                    # Level 4: 0 spaces before '-'
                    lines.append(
                        f"- **【第4级·场景与模式】** 细分场景：{leaf['sub_scene']} ｜ **收费/结算**：{leaf['fee_mode']} ｜ **客体/方向**：{leaf['target_constraint']}"
                    )
                    # Level 5: 2 spaces before '-'
                    lines.append(
                        f"  - **【第5级·唯一模板】** `{leaf['code']}` **《{leaf['template_name']}》**（版本 `{leaf['version']}` · 绑定新产品 **{len(leaf['new_products'])}** 个 · 历史合同 **{leaf['hist_cnt']}** 单）"
                    )
                    # Level 6 details under Level 5: 4 spaces before '-'
                    lines.append(
                        f"    - **排他触发红线**：{leaf['disambiguation_rule']}"
                    )
                    if leaf.get("clause1"):
                        lines.append(f"    - **合同第一条摘要**：{leaf['clause1']}")
                    if leaf.get("payment"):
                        lines.append(f"    - **付款与风险条款**：{leaf['payment']}")

                    prods = [
                        f"{p['code']} {p['name']}".strip() for p in leaf["new_products"]
                    ]
                    if include_all_products:
                        lines.append(
                            f"    - **新系统绑定产品编码（共 {len(prods)} 个）**："
                        )
                        for p_str in prods:
                            lines.append(f"      - `{p_str}`")
                    else:
                        preview = "、".join(f"`{p}`" for p in prods[:6])
                        more = f" 等共 {len(prods)} 个产品" if len(prods) > 6 else ""
                        lines.append(
                            f"    - **新系统代表产品（共 {len(prods)} 个）**：{preview}{more}"
                        )

                    if leaf.get("erp_products"):
                        erp_str = "、".join(leaf["erp_products"][:8])
                        lines.append(f"    - **老ERP产品映射**：{erp_str}")
        lines.append("")

    # Append 12 Disambiguation Groups at the end
    lines.append("# 【附：12组同分类易混模板 · 第4~5级排他决策树】")
    for grp in DISAMBIGUATION_GROUPS:
        lines.append(f"## `{grp['group_id']}` {grp['group_name']}")
        lines.append(
            f"### 数据库共用粗分类：`{grp['shared_db_mapping']}` ｜ 涉及模板：`{grp['conflict_templates']}`"
        )
        for logic_line in grp["decision_logic"].split("\n"):
            s = logic_line.strip()
            if not s:
                continue
            if logic_line.startswith("   -"):
                lines.append(f"  - {s.lstrip('- ').strip()}")
            else:
                lines.append(f"- {s}")
    return "\n".join(lines)


def make_outline_xml(
    text_plain,
    text_html=None,
    note_plain="",
    note_html="",
    heading=None,
    color=None,
    children_xml="",
):
    attrs = [f'text="{html.escape(text_plain, quote=True)}"']
    if text_html:
        attrs.append(f'_mubu_text="{mubu_encode(text_html)}"')
    else:
        attrs.append(f'_mubu_text="{mubu_encode(html.escape(text_plain))}"')
    if note_plain:
        attrs.append(f'_note="{html.escape(note_plain, quote=True)}"')
        attrs.append(
            f'_mubu_note="{mubu_encode(note_html or html.escape(note_plain))}"'
        )
    if heading:
        attrs.append(f'_heading="{heading}"')
    if color:
        attrs.append(f'_color="{color}"')

    attr_str = " ".join(attrs)
    if children_xml:
        return f"<outline {attr_str}>\n{children_xml}\n</outline>"
    else:
        return f"<outline {attr_str}/>"


def export_mubu_opml(data_obj):
    """
    生成幕布原生满血 OPML 2.0 格式：
    - 主干严格保持 5 级树结构（第1级 -> 第2级 -> 第3级 -> 第4级 -> 第5级唯一模板）
    - 将每套模板的【排他规则】【第一条摘要】【全量603个产品编码明细】放入第5级节点的 _note (幕布节点备注) 中！
      这样在幕布里切【思维导图视图】时，导图干净利落、5级层次分明；鼠标点开节点备注或切【大纲视图】即可查看全部产品编码！
    """
    tree_root = data_obj["tree"]
    l1_colors = ["blue", "purple", "green", "orange", "red", "yellow", "blue"]
    body_outlines = []

    for idx, l1 in enumerate(tree_root["children"]):
        color = l1_colors[idx % len(l1_colors)]
        l1_tpl_cnt = sum(
            len(l3["children"]) for l2 in l1["children"] for l3 in l2["children"]
        )
        l2_xml_list = []

        for l2 in l1["children"]:
            l2_tpl_cnt = sum(len(l3["children"]) for l3 in l2["children"])
            l3_xml_list = []

            for l3 in l2["children"]:
                l4_xml_list = []

                for l4 in l3["children"]:
                    leaf = l4["children"][0]["leaf"]
                    prods = [
                        f"{p['code']} {p['name']}".strip() for p in leaf["new_products"]
                    ]

                    # Level 5 leaf node with full rich note
                    t_plain = f"【第5级·唯一模板】{leaf['code']} 《{leaf['template_name']}》（{len(prods)}个产品 / {leaf['hist_cnt']}单）"
                    t_html = (
                        f"<b>【第5级·唯一模板】</b>"
                        f'<span class="codespan">{html.escape(leaf["code"])}</span> '
                        f"<b>《{html.escape(leaf['template_name'])}》</b>"
                        f"（<b>{len(prods)}</b>个产品 / {leaf['hist_cnt']}单）"
                    )
                    note_lines_plain = [
                        f"【排他触发红线】{leaf['disambiguation_rule']}",
                        f"【细分场景】{leaf['sub_scene']}",
                        f"【收费与结算】{leaf['fee_mode']}",
                        f"【标的与交易方向】{leaf['target_constraint']}",
                    ]
                    if leaf.get("clause1"):
                        note_lines_plain.append(f"【合同第一条摘要】{leaf['clause1']}")
                    if leaf.get("payment"):
                        note_lines_plain.append(f"【付款与风险条款】{leaf['payment']}")
                    if leaf.get("erp_products"):
                        note_lines_plain.append(
                            f"【老ERP产品映射】{'、'.join(leaf['erp_products'])}"
                        )
                    note_lines_plain.append(
                        f"【新系统绑定产品明细（共{len(prods)}个）】" + "；".join(prods)
                    )

                    note_plain = "\n".join(note_lines_plain)
                    note_html = "<br/>".join(
                        html.escape(line) for line in note_lines_plain
                    )

                    l5_xml = make_outline_xml(
                        text_plain=t_plain,
                        text_html=t_html,
                        note_plain=note_plain,
                        note_html=note_html,
                    )

                    # Level 4 node
                    l4_plain = f"【第4级】场景：{leaf['sub_scene']} ｜ 模式：{leaf['fee_mode']}"
                    l4_html = (
                        f"<b>【第4级·场景】</b>{html.escape(leaf['sub_scene'])} "
                        f"｜ <b>【收费/方向】</b>{html.escape(leaf['fee_mode'])}"
                    )
                    l4_note = f"客体/交易方向约束：{leaf['target_constraint']}\n排他边界：{leaf['disambiguation_rule']}"
                    l4_xml = make_outline_xml(
                        text_plain=l4_plain,
                        text_html=l4_html,
                        note_plain=l4_note,
                        children_xml=l5_xml,
                    )
                    l4_xml_list.append(l4_xml)

                l3_plain = f"【第3级】{l3['name']}（{len(l3['children'])}套）"
                l3_html = f"<b>【第3级】{html.escape(l3['name'])}（{len(l3['children'])}套）</b>"
                l3_xml = make_outline_xml(
                    text_plain=l3_plain,
                    text_html=l3_html,
                    heading=3,
                    children_xml="\n".join(l4_xml_list),
                )
                l3_xml_list.append(l3_xml)

            l2_plain = f"【第2级】{l2['name']}（{l2_tpl_cnt}套）"
            l2_html = f"<b>【第2级】{html.escape(l2['name'])}（{l2_tpl_cnt}套）</b>"
            l2_xml = make_outline_xml(
                text_plain=l2_plain,
                text_html=l2_html,
                heading=2,
                children_xml="\n".join(l3_xml_list),
            )
            l2_xml_list.append(l2_xml)

        l1_plain = f"【第1级】{l1['name']}（{l1_tpl_cnt}套模板）"
        l1_html = f"<b>【第1级】{html.escape(l1['name'])}（{l1_tpl_cnt}套模板）</b>"
        l1_xml = make_outline_xml(
            text_plain=l1_plain,
            text_html=l1_html,
            heading=1,
            color=color,
            children_xml="\n".join(l2_xml_list),
        )
        body_outlines.append(l1_xml)

    # Disambiguation section
    disamb_children = []
    for grp in DISAMBIGUATION_GROUPS:
        g_plain = f"{grp['group_id']} {grp['group_name']}"
        g_html = f'<span class="codespan">{html.escape(grp["group_id"])}</span> <b>{html.escape(grp["group_name"])}</b>'
        g_note = f"【数据库共用粗分类】{grp['shared_db_mapping']}\n【涉及易混模板】{grp['conflict_templates']}\n【排他决策树规则】\n{grp['decision_logic']}"
        disamb_children.append(
            make_outline_xml(
                text_plain=g_plain,
                text_html=g_html,
                note_plain=g_note,
                heading=2,
            )
        )
    body_outlines.append(
        make_outline_xml(
            text_plain="【附】12组同分类易混模板 · 排他决策速查表",
            text_html="<b>【附】12组同分类易混模板 · 排他决策速查表</b>",
            heading=1,
            color="red",
            children_xml="\n".join(disamb_children),
        )
    )

    body_str = "\n".join(body_outlines)
    opml = f"""<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head>
    <title>AI合同分类与78套标准模板 · 五级路由知识图谱 (1级→5级)</title>
  </head>
  <body>
{body_str}
  </body>
</opml>"""
    return opml


def main():
    data_obj = build_hierarchy_data()
    md_clean = export_mubu_markdown(data_obj, include_all_products=False)
    md_full = export_mubu_markdown(data_obj, include_all_products=True)
    opml_str = export_mubu_opml(data_obj)

    with open(OUT_MD_CLEAN, "w", encoding="utf-8") as f:
        f.write(md_clean)
    with open(OUT_MD_FULL, "w", encoding="utf-8") as f:
        f.write(md_full)
    with open(OUT_OPML, "w", encoding="utf-8") as f:
        f.write(opml_str)

    print("SUCCESS_MUBU_EXPORT")


if __name__ == "__main__":
    main()
