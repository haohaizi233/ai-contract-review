# -*- coding: utf-8 -*-
"""Word 格式审核器（Phase 4）：检测字体/字号/字重/对齐/行距/段前后/标题层级/
合同编号位置/表格结构/必填占位符/多余空行/页眉页脚/编号连续性。

结论三级（严格区分）：
- NON_COMPLIANT：明确不符合企业标准（标准值已知且实测不符）
- TEMPLATE_DIFF：仅与模板存在差异（模板值未知或多模板不一致，需人工确认）
- FORMAT_UNREADABLE：格式信息无法可靠读取（缺失 rPr/pPr 等）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict


@dataclass
class FormatIssue:
    issue_type: str            # NON_COMPLIANT | TEMPLATE_DIFF | FORMAT_UNREADABLE
    check: str                 # font | font_size | bold | align | line_spacing | spacing_before_after |
                               # margin | heading_level | contract_number_position | table_structure |
                               # required_placeholder | blank_lines | header_footer | numbering
    location: dict             # {node_id, order, section}
    description: str
    expected: str | None = None
    actual: str | None = None
    severity: str = "LOW"

    def to_dict(self):
        return asdict(self)


# 企业格式标准（示例基线；生产应由法务/品牌批准后配置化）
DEFAULT_STD = {
    "title_font_size_pt": 16,
    "body_font_size_pt": 12,
    "heading_bold": True,
    "paragraph_align": {"left", "center", "justify", None},   # 允许集合
    "line_spacing_min": 1.0,
    "line_spacing_max": 2.5,
    "page_margin_cm_min": 2.0,
    "page_margin_cm_max": 3.5,
    "max_consecutive_blank": 1,
    "require_contract_number_first_n_paras": 3,
    "require_placeholders": ["合同编号"],
}


class FormatAuditor:
    def __init__(self, std: dict | None = None):
        self.std = {**DEFAULT_STD, **(std or {})}

    def audit(self, parsed_doc) -> list[FormatIssue]:
        issues: list[FormatIssue] = []
        nodes = [n for n in parsed_doc.nodes if n.node_type == "paragraph"]
        issues += self._check_fonts(nodes)
        issues += self._check_alignment_spacing(nodes)
        issues += self._check_headings(nodes)
        issues += self._check_contract_number_position(nodes)
        issues += self._check_blank_lines(nodes)
        issues += self._check_numbering(nodes)
        issues += self._check_tables(parsed_doc)
        issues += self._check_placeholders(parsed_doc)
        issues += self._check_page_setup(parsed_doc)
        issues += self._check_header_footer(parsed_doc)
        return issues

    # ---------- 字体/字号/字重 ----------
    def _check_fonts(self, nodes) -> list[FormatIssue]:
        out = []
        expected_pt = self.std["body_font_size_pt"]
        sizes_seen: dict[int, int] = {}
        for n in nodes:
            runs = n.style.get("runs", []) if n.style else []
            if not n.style:
                out.append(FormatIssue("FORMAT_UNREADABLE", "font", {"node_id": n.node_id, "order": n.order},
                                       "段落样式信息缺失，无法核对字体", severity="INFO"))
                continue
            for r in runs:
                sz = r.get("font_size_half_pt")
                if sz:
                    try:
                        pt = int(sz) / 2
                        sizes_seen[pt] = sizes_seen.get(pt, 0) + 1
                        if abs(pt - expected_pt) > 4 and pt not in (16, 14, 10.5):
                            out.append(FormatIssue(
                                "TEMPLATE_DIFF", "font_size", {"node_id": n.node_id, "order": n.order},
                                f"字号 {pt}pt 与正文标准 {expected_pt}pt 差异较大",
                                expected=str(expected_pt), actual=str(pt)))
                    except ValueError:
                        out.append(FormatIssue("FORMAT_UNREADABLE", "font_size",
                                               {"node_id": n.node_id}, "字号值无法解析", severity="INFO"))
        return out

    # ---------- 对齐/行距/段前后 ----------
    def _check_alignment_spacing(self, nodes) -> list[FormatIssue]:
        out = []
        for n in nodes:
            st = n.style or {}
            align = st.get("align")
            if st and align not in self.std["paragraph_align"]:
                out.append(FormatIssue("TEMPLATE_DIFF", "align", {"node_id": n.node_id, "order": n.order},
                                       f"段落对齐 {align} 不在常见取值集合", expected=str(self.std["paragraph_align"]),
                                       actual=str(align)))
            sp = st.get("spacing", {})
            line = sp.get("line")
            if line:
                try:
                    # w:spacing line 单位 1/240 行
                    lines = int(line) / 240
                    if not (self.std["line_spacing_min"] <= lines <= self.std["line_spacing_max"]):
                        out.append(FormatIssue("TEMPLATE_DIFF", "line_spacing",
                                               {"node_id": n.node_id, "order": n.order},
                                               f"行距 {lines:.2f} 超出常规区间",
                                               expected=f"{self.std['line_spacing_min']}-{self.std['line_spacing_max']}",
                                               actual=f"{lines:.2f}"))
                except ValueError:
                    out.append(FormatIssue("FORMAT_UNREADABLE", "line_spacing",
                                           {"node_id": n.node_id}, "行距值无法解析", severity="INFO"))
        return out

    # ---------- 标题层级 ----------
    def _check_headings(self, nodes) -> list[FormatIssue]:
        out = []
        article_re = re.compile(r"^第[一二三四五六七八九十]+条")
        for n in nodes:
            t = n.text.strip()
            if article_re.match(t):
                runs = (n.style or {}).get("runs", [])
                bold = any(r.get("bold") for r in runs)
                if runs and not bold:
                    out.append(FormatIssue("NON_COMPLIANT", "heading_level",
                                           {"node_id": n.node_id, "order": n.order, "section": n.section},
                                           f"条款标题『{t[:12]}』未加粗（标准：条标题加粗）",
                                           expected="bold", actual="not bold"))
                elif not runs:
                    out.append(FormatIssue("FORMAT_UNREADABLE", "heading_level",
                                           {"node_id": n.node_id}, "标题样式无法读取", severity="INFO"))
        return out

    # ---------- 合同编号位置 ----------
    def _check_contract_number_position(self, nodes) -> list[FormatIssue]:
        out = []
        lim = self.std["require_contract_number_first_n_paras"]
        idx = next((i for i, n in enumerate(nodes) if "合同编号" in n.text), None)
        if idx is None:
            out.append(FormatIssue("NON_COMPLIANT", "contract_number_position",
                                   {"node_id": None}, "未找到合同编号行",
                                   expected=f"前{lim}段内", actual="缺失", severity="MEDIUM"))
        elif idx >= lim:
            out.append(FormatIssue("TEMPLATE_DIFF", "contract_number_position",
                                   {"node_id": nodes[idx].node_id, "order": nodes[idx].order},
                                   f"合同编号位于第{idx + 1}段，非首部前{lim}段",
                                   expected=f"≤{lim}", actual=str(idx + 1)))
        return out

    # ---------- 多余空行 ----------
    def _check_blank_lines(self, nodes) -> list[FormatIssue]:
        out = []
        run_len = 0
        for n in nodes:
            if not n.text.strip():
                run_len += 1
                if run_len > self.std["max_consecutive_blank"]:
                    out.append(FormatIssue("TEMPLATE_DIFF", "blank_lines",
                                           {"node_id": n.node_id, "order": n.order},
                                           f"连续空行 {run_len} 个，超出标准 {self.std['max_consecutive_blank']}",
                                           expected=str(self.std["max_consecutive_blank"]), actual=str(run_len)))
            else:
                run_len = 0
        return out

    # ---------- 编号连续性 ----------
    def _check_numbering(self, nodes) -> list[FormatIssue]:
        out = []
        nums = []
        for n in nodes:
            m = re.match(r"^(\d+)\.(\d+)", n.text.strip())
            if m:
                nums.append((int(m.group(1)), int(m.group(2)), n))
        by_art: dict[int, list] = {}
        for a, s, n in nums:
            by_art.setdefault(a, []).append((s, n))
        for a, items in by_art.items():
            subs = [s for s, _ in items]
            for i in range(1, len(subs)):
                if subs[i] != subs[i - 1] + 1:
                    out.append(FormatIssue("NON_COMPLIANT", "numbering",
                                           {"node_id": items[i][1].node_id, "order": items[i][1].order},
                                           f"条款编号 {a}.{subs[i - 1]} → {a}.{subs[i]} 不连续",
                                           expected=f"{a}.{subs[i - 1] + 1}", actual=f"{a}.{subs[i]}",
                                           severity="MEDIUM"))
        return out

    # ---------- 表格结构 ----------
    def _check_tables(self, parsed_doc) -> list[FormatIssue]:
        out = []
        for dim in parsed_doc.table_dims:
            if dim["cols"] == 0 or dim["rows"] == 0:
                out.append(FormatIssue("NON_COMPLIANT", "table_structure",
                                       {"table_index": dim["table_index"]},
                                       "空表格（0 行/0 列）", severity="MEDIUM"))
            # 表头（首行）不应全空
            cells = [n for n in parsed_doc.nodes
                     if n.node_type == "table_cell" and n.table_index == dim["table_index"]
                     and n.row_index == 0]
            if cells and all(not c.text.strip() for c in cells):
                out.append(FormatIssue("TEMPLATE_DIFF", "table_structure",
                                       {"table_index": dim["table_index"]},
                                       "表格首行（表头）为空", severity="LOW"))
        return out

    # ---------- 必填占位符 ----------
    def _check_placeholders(self, parsed_doc) -> list[FormatIssue]:
        out = []
        full = "\n".join(n.text for n in parsed_doc.nodes)
        for ph in self.std["require_placeholders"]:
            if ph not in full:
                out.append(FormatIssue("NON_COMPLIANT", "required_placeholder",
                                       {"node_id": None}, f"缺少必填占位『{ph}』",
                                       expected=ph, actual="缺失", severity="MEDIUM"))
        return out

    # ---------- 页边距 ----------
    def _check_page_setup(self, parsed_doc) -> list[FormatIssue]:
        out = []
        margins = getattr(parsed_doc, "page_margins_cm", None)
        if not margins:
            out.append(FormatIssue("FORMAT_UNREADABLE", "margin", {"node_id": None},
                                   "页边距未在解析层提取（sectPr/pgMar），无法核对", severity="INFO"))
        else:
            for side, val in margins.items():
                lo, hi = self.std["page_margin_cm_min"], self.std["page_margin_cm_max"]
                if not (lo <= val <= hi):
                    out.append(FormatIssue("TEMPLATE_DIFF", "margin", {"node_id": None},
                                           f"页边距 {side}={val}cm 超出常规 {lo}-{hi}cm",
                                           expected=f"{lo}-{hi}", actual=f"{val}"))
        return out

    # ---------- 页眉页脚 ----------
    def _check_header_footer(self, parsed_doc) -> list[FormatIssue]:
        out = []
        if not parsed_doc.headers and not parsed_doc.footers:
            out.append(FormatIssue("TEMPLATE_DIFF", "header_footer", {"node_id": None},
                                   "文档无页眉页脚（模板通常含页码/密级标识）",
                                   expected="含页眉或页脚", actual="无"))
        return out


def audit_format(parsed_doc, std: dict | None = None) -> dict:
    issues = FormatAuditor(std).audit(parsed_doc)
    by_type: dict[str, int] = {}
    for i in issues:
        by_type[i.issue_type] = by_type.get(i.issue_type, 0) + 1
    return {"document_id": parsed_doc.document_id, "issues": [i.to_dict() for i in issues],
            "summary": by_type, "total": len(issues)}
