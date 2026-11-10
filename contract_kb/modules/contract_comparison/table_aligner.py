# -*- coding: utf-8 -*-
"""真实合同表格对齐、单元格级 Diff 与原貌保真重构引擎 (Table Reconstruction & Alignment Engine) - Phase 13R1。

核心设计哲学：
1. 【Source of Truth 原则】：所有表格、行、列、单元格文本均直接来自源文档 AST，
   严禁向正式比对流程中混入任何预设、示例、演示主体名称或固定金额；
2. 【高保真表格结构对齐】：
   真实母本 AST + 真实送审 AST → 表格对齐 → 单元格级文本 Diff → HTML 呈现；
3. 【不可靠对齐标记 Fail-Closed】：
   若表格结构严重冲突、行列语义无法可靠建立对齐关系，返回明确的 ALIGNMENT_UNCERTAIN 状态，
   完整保留两侧原始表格，供法务人工对照决断，严禁强行对齐导致内容错位；
4. 【HTML 注入免疫与 XSS 防护】：
   所有来自原始合同的单元格内容均经严格字符实体转义，防止恶意内容攻击。
"""

from __future__ import annotations

import difflib
import html
from typing import Any

from ...domain.models.source_node import ContractSourceNode


def escape_html(text: str) -> str:
    """对合同原始文本进行严格的 HTML 实体转义。"""
    if not text:
        return ""
    return html.escape(str(text), quote=True)


class ExtractedTable:
    """从 AST 节点集合中重构的纯净表格对象。"""

    def __init__(
        self,
        table_index: int,
        document_id: str,
        document_hash: str,
        nodes: list[ContractSourceNode],
    ):
        self.table_index = table_index
        self.document_id = document_id
        self.document_hash = document_hash
        self.nodes = nodes

        # 整理行与列
        self.cell_map: dict[tuple[int, int], ContractSourceNode] = {}
        max_r, max_c = 0, 0
        for n in nodes:
            if (
                n.node_type == "table_cell"
                and n.row_index is not None
                and n.column_index is not None
            ):
                r, c = n.row_index, n.column_index
                self.cell_map[(r, c)] = n
                max_r = max(max_r, r + 1)
                max_c = max(max_c, c + n.grid_span)

        self.row_count = max_r
        self.col_count = max_c

        # 提取表头（第0行文字）
        self.headers: list[str] = []
        for c in range(self.col_count):
            cell = self.cell_map.get((0, c))
            self.headers.append(cell.original_text.strip() if cell else "")

    @property
    def full_text(self) -> str:
        return " ".join(n.original_text for n in self.nodes if n.original_text).strip()

    def get_cell(self, row: int, col: int) -> ContractSourceNode | None:
        return self.cell_map.get((row, col))

    def get_row_texts(self, row: int) -> list[str]:
        return [
            (
                self.cell_map[(row, c)].original_text.strip()
                if (row, c) in self.cell_map
                else ""
            )
            for c in range(self.col_count)
        ]


def extract_tables_from_nodes(
    nodes: list[Any],
    document_id: str,
    document_hash: str,
    source_path: str = "",
) -> list[ExtractedTable]:
    """从源文档 ASTNode 或 dict 列表中抽取完整表格。"""
    src_nodes: list[ContractSourceNode] = []
    for n in nodes:
        if isinstance(n, ContractSourceNode):
            src_nodes.append(n)
        else:
            src_nodes.append(
                ContractSourceNode.from_ast_node(
                    n, document_hash, source_path=source_path
                )
            )

    tbl_groups: dict[int, list[ContractSourceNode]] = {}
    for n in src_nodes:
        if n.table_index is not None:
            tbl_groups.setdefault(n.table_index, []).append(n)

    tables: list[ExtractedTable] = []
    for ti in sorted(tbl_groups.keys()):
        tables.append(ExtractedTable(ti, document_id, document_hash, tbl_groups[ti]))
    return tables


def compute_table_similarity(std_tbl: ExtractedTable, rev_tbl: ExtractedTable) -> float:
    """计算两张表格的结构与表头语义相似度。"""
    # 1. 表头文本相似度
    std_h = " ".join(std_tbl.headers)
    rev_h = " ".join(rev_tbl.headers)
    if not std_h and not rev_h:
        h_sim = 1.0 if std_tbl.col_count == rev_tbl.col_count else 0.5
    else:
        h_sim = difflib.SequenceMatcher(None, std_h, rev_h).ratio()

    # 2. 列数匹配度
    c_ratio = min(std_tbl.col_count, rev_tbl.col_count) / max(
        1, max(std_tbl.col_count, rev_tbl.col_count)
    )

    # 3. 全文词汇重合度
    t_sim = difflib.SequenceMatcher(
        None, std_tbl.full_text[:400], rev_tbl.full_text[:400]
    ).ratio()

    return round(h_sim * 0.5 + c_ratio * 0.2 + t_sim * 0.3, 3)


def align_contract_tables(
    std_tables: list[ExtractedTable],
    rev_tables: list[ExtractedTable],
) -> list[dict[str, Any]]:
    """对母本与送审稿的所有表格进行高保真对齐匹配。"""
    aligned_pairs: list[dict[str, Any]] = []
    matched_rev = set()

    for s_idx, s_tbl in enumerate(std_tables):
        best_match_idx = None
        best_score = 0.0

        for r_idx, r_tbl in enumerate(rev_tables):
            if r_idx in matched_rev:
                continue
            score = compute_table_similarity(s_tbl, r_tbl)
            # 若表格序号相同且具备一定相似度，优先匹配
            if s_idx == r_idx and score >= 0.4:
                score += 0.2
            if score > best_score:
                best_score = score
                best_match_idx = r_idx

        if best_match_idx is not None and best_score >= 0.45:
            matched_rev.add(best_match_idx)
            aligned_pairs.append(
                {
                    "status": "ALIGNED",
                    "confidence": min(1.0, best_score),
                    "std_table": s_tbl,
                    "rev_table": rev_tables[best_match_idx],
                    "alignment_method": "AST_TABLE_STRUCTURE_ALIGNMENT",
                }
            )
        else:
            aligned_pairs.append(
                {
                    "status": "MISSING_IN_REVIEWED",
                    "confidence": 1.0,
                    "std_table": s_tbl,
                    "rev_table": None,
                    "alignment_method": "AST_TABLE_DELETED_IN_REVIEW",
                }
            )

    for r_idx, r_tbl in enumerate(rev_tables):
        if r_idx not in matched_rev:
            aligned_pairs.append(
                {
                    "status": "NEW_IN_REVIEWED",
                    "confidence": 1.0,
                    "std_table": None,
                    "rev_table": r_tbl,
                    "alignment_method": "AST_TABLE_NEW_IN_REVIEW",
                }
            )

    return aligned_pairs


def _classify_table_category(table: ExtractedTable | None) -> tuple[str, str]:
    """根据真实表头与内容语义判断表格业务类别（绝不使用固定 block-ID）。"""
    if not table:
        return "TABLE", "合同业务表格"
    txt = (" ".join(table.headers) + " " + table.full_text[:300]).lower()

    if any(
        k in txt
        for k in ["甲方", "乙方", "委托人", "受托人", "签约主体", "地址", "联系人"]
    ):
        return "PARTIES", "双方主体与基本信息表"
    if any(
        k in txt
        for k in ["专利名称", "专利号", "申请号", "专利权人", "发明名称", "标的清单"]
    ):
        return "PATENT_LIST", "专利与标的清单表"
    if any(
        k in txt
        for k in [
            "官费",
            "服务费",
            "代理费",
            "税率",
            "金额",
            "小计",
            "合计",
            "费用项目",
        ]
    ):
        return "FEE_AND_PAYMENT", "费用项目与金额明细表"
    if any(k in txt for k in ["法定代表人", "委托代理人", "盖章", "签署日期", "签字"]):
        return "SIGNATURE", "合同签署栏"
    return "TABLE", "合同业务数据表"


def build_table_diff_block(
    aligned_pair: dict[str, Any],
    block_order: int,
    ann_counter_start: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """将对齐的真实表格对转换为具备双栏对比保真呈现与法务批注的对齐块。"""
    std_tbl: ExtractedTable | None = aligned_pair.get("std_table")
    rev_tbl: ExtractedTable | None = aligned_pair.get("rev_table")
    status = aligned_pair["status"]
    confidence = aligned_pair["confidence"]
    align_method = aligned_pair["alignment_method"]

    cat, cat_zh = _classify_table_category(std_tbl or rev_tbl)
    block_id = f"block-table-{block_order}"
    annotations: list[dict[str, Any]] = []
    ann_counter = ann_counter_start
    table_ann_ids: list[str] = []

    # 1. 送审稿整表删除
    if status == "MISSING_IN_REVIEWED":
        assert std_tbl is not None
        std_html = _render_single_table_html(
            std_tbl, role="standard", badge="🏛️ 标准母本表格（送审稿已删除）"
        )
        rev_html = (
            f'<div class="contract-table-card deleted-card" data-block-id="{block_id}">'
            f'  <div class="table-badge badge-deleted">⚠️ 送审稿已整表删除该表格</div>'
            f'  <div class="table-empty-placeholder">（原文对应位置的表格在送审合同中已被删除）</div>'
            f"</div>"
        )
        ann_counter += 1
        ann_id = f"ann-tbl-{ann_counter}"
        table_ann_ids.append(ann_id)
        annotations.append(
            {
                "id": ann_id,
                "index": ann_counter,
                "block_id": block_id,
                "clause_category": cat,
                "clause_category_zh": cat_zh,
                "clause_number": None,
                "clause_title": cat_zh,
                "delta_type": "MISSING_CLAUSE",
                "risk_level": "HIGH",
                "risk_label": "表格被整段删除",
                "title": f"【风控警示】母本《{cat_zh}》在送审稿中被删除",
                "standard_span": std_tbl.full_text[:120],
                "reviewed_span": "（整表已删除）",
                "quote_standard": std_tbl.full_text[:300],
                "quote_reviewed": "",
                "ai_analysis": f"送审合同完全删除了母本中约定的《{cat_zh}》，包含 {std_tbl.row_count} 行、{std_tbl.col_count} 列的原始标准约定。表格缺失可能导致收费基准、主体责任或标的范围出现法律约束真空。",
                "action_advice": "【法务处置建议】要求送审方恢复母本标准表格约定，核实删表原因与商务意图。",
                "review_status": "PENDING",
                "standard_source_node_ids": [n.node_id for n in std_tbl.nodes],
                "reviewed_source_node_ids": [],
            }
        )
        return (
            {
                "block_id": block_id,
                "order": block_order,
                "clause_category": cat,
                "clause_category_zh": cat_zh,
                "clause_number": None,
                "clause_title": cat_zh,
                "status": "MISSING_IN_REVIEWED",
                "standard_text": std_tbl.full_text,
                "reviewed_text": "",
                "standard_html": std_html,
                "reviewed_html": rev_html,
                "annotation_ids": table_ann_ids,
                "standard_source_node_ids": [n.node_id for n in std_tbl.nodes],
                "reviewed_source_node_ids": [],
                "alignment_method": align_method,
                "alignment_confidence": confidence,
                "content_integrity_status": "SOURCE_TRUTH_VERIFIED",
            },
            annotations,
            ann_counter,
        )

    # 2. 送审稿整表新增
    if status == "NEW_IN_REVIEWED":
        assert rev_tbl is not None
        std_html = (
            f'<div class="contract-table-card empty-card" data-block-id="{block_id}">'
            f'  <div class="table-badge">🏛️ 标准母本无此表格</div>'
            f'  <div class="table-empty-placeholder">（标准母本库在此处无对应表格约定）</div>'
            f"</div>"
        )
        rev_html = _render_single_table_html(
            rev_tbl, role="reviewed", badge="✏️ 送审新增表格"
        )
        ann_counter += 1
        ann_id = f"ann-tbl-{ann_counter}"
        table_ann_ids.append(ann_id)
        annotations.append(
            {
                "id": ann_id,
                "index": ann_counter,
                "block_id": block_id,
                "clause_category": cat,
                "clause_category_zh": cat_zh,
                "clause_number": None,
                "clause_title": cat_zh,
                "delta_type": "NEW_CLAUSE",
                "risk_level": "MEDIUM",
                "risk_label": "送审新增表格",
                "title": f"送审稿新增《{cat_zh}》",
                "standard_span": "（母本无此表）",
                "reviewed_span": rev_tbl.full_text[:120],
                "quote_standard": "",
                "quote_reviewed": rev_tbl.full_text[:300],
                "ai_analysis": f"送审稿在母本之外新增了《{cat_zh}》（{rev_tbl.row_count} 行 × {rev_tbl.col_count} 列）。需重点核实新增表单是否创设了非标准业务义务或商务让利。",
                "action_advice": "【法务处置建议】通读新增表格内容，评估新增收费项或义务分摊是否已通过业务主管部门审批。",
                "review_status": "PENDING",
                "standard_source_node_ids": [],
                "reviewed_source_node_ids": [n.node_id for n in rev_tbl.nodes],
            }
        )
        return (
            {
                "block_id": block_id,
                "order": block_order,
                "clause_category": cat,
                "clause_category_zh": cat_zh,
                "clause_number": None,
                "clause_title": cat_zh,
                "status": "NEW_IN_REVIEWED",
                "standard_text": "",
                "reviewed_text": rev_tbl.full_text,
                "standard_html": std_html,
                "reviewed_html": rev_html,
                "annotation_ids": table_ann_ids,
                "standard_source_node_ids": [],
                "reviewed_source_node_ids": [n.node_id for n in rev_tbl.nodes],
                "alignment_method": align_method,
                "alignment_confidence": confidence,
                "content_integrity_status": "SOURCE_TRUTH_VERIFIED",
            },
            annotations,
            ann_counter,
        )

    # 3. 双端已对齐表格：执行严格单元格差异比对
    assert std_tbl is not None and rev_tbl is not None

    # 若行列数悬殊过大导致无法可靠对应，标记为 ALIGNMENT_UNCERTAIN
    if abs(std_tbl.col_count - rev_tbl.col_count) > 2 and confidence < 0.6:
        std_html = _render_single_table_html(
            std_tbl, role="standard", badge="🏛️ 母本表格（结构差异过大）"
        )
        rev_html = _render_single_table_html(
            rev_tbl, role="reviewed", badge="⚠️ 送审表格（无法可靠对齐，保留原始形态）"
        )
        ann_counter += 1
        ann_id = f"ann-tbl-{ann_counter}"
        table_ann_ids.append(ann_id)
        annotations.append(
            {
                "id": ann_id,
                "index": ann_counter,
                "block_id": block_id,
                "clause_category": cat,
                "clause_category_zh": cat_zh,
                "clause_number": None,
                "clause_title": cat_zh,
                "delta_type": "STRUCTURE_UNCERTAIN",
                "risk_level": "HIGH",
                "risk_label": "表格结构严重失配",
                "title": f"《{cat_zh}》结构失配，建议法务人工对照",
                "standard_span": f"母本 {std_tbl.row_count}行{std_tbl.col_count}列",
                "reviewed_span": f"送审 {rev_tbl.row_count}行{rev_tbl.col_count}列",
                "quote_standard": std_tbl.full_text[:200],
                "quote_reviewed": rev_tbl.full_text[:200],
                "ai_analysis": f"母本（{std_tbl.row_count}行{std_tbl.col_count}列）与送审稿（{rev_tbl.row_count}行{rev_tbl.col_count}列）表格结构差异过大，系统已拒绝强行自动合并，保留两侧原始表格以杜绝信息失真。",
                "action_advice": "【法务处置建议】请法务人员人工对照两侧原始表格，确认是否为对方私自重构了格式。",
                "review_status": "PENDING",
                "standard_source_node_ids": [n.node_id for n in std_tbl.nodes],
                "reviewed_source_node_ids": [n.node_id for n in rev_tbl.nodes],
            }
        )
        return (
            {
                "block_id": block_id,
                "order": block_order,
                "clause_category": cat,
                "clause_category_zh": cat_zh,
                "clause_number": None,
                "clause_title": cat_zh,
                "status": "ALIGNMENT_UNCERTAIN",
                "standard_text": std_tbl.full_text,
                "reviewed_text": rev_tbl.full_text,
                "standard_html": std_html,
                "reviewed_html": rev_html,
                "annotation_ids": table_ann_ids,
                "standard_source_node_ids": [n.node_id for n in std_tbl.nodes],
                "reviewed_source_node_ids": [n.node_id for n in rev_tbl.nodes],
                "alignment_method": "AST_TABLE_STRUCTURE_UNCERTAIN",
                "alignment_confidence": confidence,
                "content_integrity_status": "ALIGNMENT_UNCERTAIN",
                "unresolved_mapping_reason": f"列数差异过大 (母本 {std_tbl.col_count} 列 vs 送审 {rev_tbl.col_count} 列)",
            },
            annotations,
            ann_counter,
        )

    # 4. 可靠对齐：执行单元格级精确比对与原貌 HTML 重建
    max_rows = max(std_tbl.row_count, rev_tbl.row_count)
    max_cols = max(std_tbl.col_count, rev_tbl.col_count)

    has_diff = False
    std_tr_list: list[str] = []
    rev_tr_list: list[str] = []

    for r in range(max_rows):
        std_cells_html: list[str] = []
        rev_cells_html: list[str] = []
        s_skip = 0
        r_skip = 0

        for c in range(max_cols):
            if s_skip > 0 and r_skip > 0:
                s_skip -= 1
                r_skip -= 1
                continue

            s_node = std_tbl.get_cell(r, c) if s_skip == 0 else None
            r_node = rev_tbl.get_cell(r, c) if r_skip == 0 else None

            s_span = s_node.grid_span if (s_node and s_node.grid_span > 1) else 1
            r_span = r_node.grid_span if (r_node and r_node.grid_span > 1) else 1

            s_span_attr = f' colspan="{s_span}"' if s_span > 1 else ""
            r_span_attr = f' colspan="{r_span}"' if r_span > 1 else ""

            s_text = (s_node.original_text if s_node else "").strip()
            r_text = (r_node.original_text if r_node else "").strip()

            if s_span > 1:
                s_skip = s_span - 1
            elif s_skip > 0:
                s_skip -= 1

            if r_span > 1:
                r_skip = r_span - 1
            elif r_skip > 0:
                r_skip -= 1

            is_th = r == 0
            tag = "th" if is_th else "td"

            if s_text == r_text:
                # 完全一致
                cell_val = escape_html(s_text) if s_text else "&nbsp;"
                std_cells_html.append(f"<{tag}{s_span_attr}>{cell_val}</{tag}>")
                rev_cells_html.append(f"<{tag}{r_span_attr}>{cell_val}</{tag}>")
            else:
                has_diff = True
                ann_counter += 1
                cell_ann_id = f"ann-cell-{ann_counter}"
                table_ann_ids.append(cell_ann_id)

                # 判断变动性质（数值变化、主体变化或通用文本变动）
                is_num = any(ch.isdigit() for ch in (s_text + r_text)) and any(
                    k in (s_text + r_text)
                    for k in ["元", "万", "千", "件", "%", "‰", "年"]
                )
                is_party = cat == "PARTIES" or any(
                    p in (s_text + r_text) for p in ["公司", "所", "甲方", "乙方"]
                )

                if is_num:
                    delta_type = "NUMBER_CHANGE"
                    risk_level = "HIGH"
                    risk_label = "表格数值篡改"
                elif is_party:
                    delta_type = "PARTY_CHANGE"
                    risk_level = "LOW" if not s_text else "MEDIUM"
                    risk_label = "主体信息填报"
                else:
                    delta_type = "TEXT_DELTA"
                    risk_level = "MEDIUM"
                    risk_label = "表格内容变更"

                # 渲染差异标记
                s_esc = (
                    escape_html(s_text)
                    if s_text
                    else "<span class='doc-slot-placeholder'>（待填）</span>"
                )
                if not s_text and r_text:
                    r_esc = f"<span class='doc-text-added'>{escape_html(r_text)}</span>"
                elif s_text and not r_text:
                    r_esc = f"<del class='diff-del'>{escape_html(s_text)}</del>"
                else:
                    ins_cls = "diff-ins diff-ins-number" if is_num else "diff-ins"
                    r_esc = f"<del class='diff-del'>{escape_html(s_text)}</del> <ins class='{ins_cls}'>{escape_html(r_text)}</ins>"

                std_cells_html.append(f"<{tag}{s_span_attr}>{s_esc}</{tag}>")
                rev_cells_html.append(
                    f'<{tag}{r_span_attr} class="table-diff-cell" data-ann-id="{cell_ann_id}">{r_esc}</{tag}>'
                )

                # 生成单元格级真实批注
                col_name = (
                    std_tbl.headers[c]
                    if c < len(std_tbl.headers) and std_tbl.headers[c]
                    else f"第{c + 1}列"
                )
                row_label = f"第 {r} 行" if r > 0 else "表头"

                s_src = [s_node.node_id] if s_node else []
                r_src = [r_node.node_id] if r_node else []

                annotations.append(
                    {
                        "id": cell_ann_id,
                        "index": ann_counter,
                        "block_id": block_id,
                        "clause_category": cat,
                        "clause_category_zh": cat_zh,
                        "clause_number": None,
                        "clause_title": f"{cat_zh} · {row_label} · {col_name}",
                        "delta_type": delta_type,
                        "risk_level": risk_level,
                        "risk_label": risk_label,
                        "title": f"《{cat_zh}》{row_label}【{col_name}】变更",
                        "standard_span": s_text or "（母本未填写）",
                        "reviewed_span": r_text or "（已清空）",
                        "quote_standard": s_text or "（母本基准为空）",
                        "quote_reviewed": r_text or "（送审稿内容为空）",
                        "ai_analysis": f"在《{cat_zh}》{row_label}【{col_name}】中，送审内容为“{r_text}”，与母本约定“{s_text}”存在实质差异。需核查填报真实性与财务/法务合规性。",
                        "action_advice": "【法务处置建议】请业务负责人通读确认表格填报数据无误，与真实标的、报价单或企业主体资信保持一致。",
                        "review_status": "PENDING",
                        "standard_source_node_ids": s_src,
                        "reviewed_source_node_ids": r_src,
                    }
                )

        std_tr_list.append(f"<tr>{''.join(std_cells_html)}</tr>")
        rev_tr_list.append(f"<tr>{''.join(rev_cells_html)}</tr>")

    block_status = "DIFF" if has_diff else "MATCH"
    badge_s = (
        "🏛️ 双方主体基本信息（母本基准）"
        if cat == "PARTIES"
        else f"🏛️ {cat_zh}（母本基准）"
    )
    badge_r = (
        "✏️ 双方主体基本信息（送审填报）"
        if (cat == "PARTIES" and has_diff)
        else (f"🏛️ {cat_zh}（完全一致）" if not has_diff else f"✏️ {cat_zh}（送审修改）")
    )

    std_html = (
        f'<div class="contract-table-card" data-block-id="{block_id}">'
        f'  <div class="table-badge">{badge_s}</div>'
        f'  <table class="contract-doc-table">'
        f"    {''.join(std_tr_list)}"
        f"  </table>"
        f"</div>"
    )
    rev_html = (
        f'<div class="contract-table-card" data-block-id="{block_id}">'
        f'  <div class="table-badge">{badge_r}</div>'
        f'  <table class="contract-doc-table">'
        f"    {''.join(rev_tr_list)}"
        f"  </table>"
        f"</div>"
    )

    return (
        {
            "block_id": block_id,
            "order": block_order,
            "clause_category": cat,
            "clause_category_zh": cat_zh,
            "clause_number": None,
            "clause_title": cat_zh,
            "status": block_status,
            "standard_text": std_tbl.full_text,
            "reviewed_text": rev_tbl.full_text,
            "standard_html": std_html,
            "reviewed_html": rev_html,
            "annotation_ids": table_ann_ids if has_diff else [],
            "standard_source_node_ids": [n.node_id for n in std_tbl.nodes],
            "reviewed_source_node_ids": [n.node_id for n in rev_tbl.nodes],
            "alignment_method": align_method,
            "alignment_confidence": confidence,
            "content_integrity_status": "SOURCE_TRUTH_VERIFIED",
        },
        annotations,
        ann_counter,
    )


def _render_single_table_html(table: ExtractedTable, role: str, badge: str) -> str:
    """安全渲染单侧表格 HTML，杜绝外部硬编码。"""
    tr_list: list[str] = []
    for r in range(table.row_count):
        cell_parts: list[str] = []
        is_th = r == 0
        tag = "th" if is_th else "td"
        skip_cols = 0
        for c in range(table.col_count):
            if skip_cols > 0:
                skip_cols -= 1
                continue
            cell = table.get_cell(r, c)
            span = cell.grid_span if (cell and cell.grid_span > 1) else 1
            span_attr = f' colspan="{span}"' if span > 1 else ""
            txt = (
                escape_html(cell.original_text.strip())
                if cell and cell.original_text
                else "&nbsp;"
            )
            cell_parts.append(f"<{tag}{span_attr}>{txt}</{tag}>")
            if span > 1:
                skip_cols = span - 1
        tr_list.append(f"<tr>{''.join(cell_parts)}</tr>")

    return (
        f'<div class="contract-table-card {role}-card">'
        f'  <div class="table-badge">{badge}</div>'
        f'  <table class="contract-doc-table">'
        f"    {''.join(tr_list)}"
        f"  </table>"
        f"</div>"
    )
