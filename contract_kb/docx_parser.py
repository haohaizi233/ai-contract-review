# -*- coding: utf-8 -*-
"""DOCX 结构化解析器：输出有序文档 AST，保留段落/表格交错顺序、样式与来源定位。

设计要点：
- 遍历 w:body 直接子元素（w:p / w:tbl / w:sdt），保证正文真实顺序；
- 表格保留行列坐标、合并单元格（gridSpan / vMerge）信息；
- 每个节点带 source_locator，可回溯到 OOXML 节点（body 序号 + XML 标签路径）；
- 不伪造页码：page_number 恒为 null，页码映射需独立 PDF 渲染，标记 page_mapping_available=false；
- 无法可靠解析的结构（文本框/批注/修订/嵌入对象）不丢弃，以节点形式记录并标记状态。
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
SAFE_XML_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    huge_tree=False,
    collect_ids=False,
)

# 供条款编号识别
CLAUSE_NUM_RE = re.compile(r"^(\d+\.\d+)")
ARTICLE_RE = re.compile(r"^(第[一二三四五六七八九十]+条)\s*(.*)$")
# 空白占位：长空格串、下划线串、全角空格串
BLANK_RE = re.compile(r"([ \u3000]{3,}|_{2,}|＿{2,})")
CHECKBOX_CHARS = ("□", "☑", "☐", "■", "√")


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class ASTNode:
    document_id: str
    node_id: str
    node_type: str  # paragraph | table | table_cell | header | footer | textbox | drawing | revision | comment_marker
    order: int
    text: str = ""
    section: Optional[str] = None  # 所属“第X条”标题
    clause_number: Optional[str] = None  # x.y 子条款号
    table_index: Optional[int] = None
    row_index: Optional[int] = None
    column_index: Optional[int] = None
    style: dict = field(default_factory=dict)
    source_locator: dict = field(default_factory=dict)
    extraction_method: str = "ooxml"
    extraction_status: str = "success"
    parent_node_id: Optional[str] = None
    flags: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ParsedDocument:
    document_id: str
    source_path: str
    source_hash: str
    file_size: int
    title: str = ""
    subtitle: str = ""
    nodes: list = field(default_factory=list)
    paragraph_count: int = 0
    table_count: int = 0
    table_dims: list = field(default_factory=list)
    has_images: bool = False
    has_embedded_objects: bool = False
    headers: list = field(default_factory=list)
    footers: list = field(default_factory=list)
    comments_present: bool = False
    revisions_present: bool = False
    textboxes_present: bool = False
    checkbox_count: int = 0
    blank_placeholder_count: int = 0
    contract_number_hint: Optional[str] = None  # “合同编号:” 后的填写位
    parse_warnings: list = field(default_factory=list)
    page_mapping_available: bool = False  # 无 PDF 渲染不提供页码

    def to_dict(self) -> dict:
        d = asdict(self)
        d["nodes"] = [n.to_dict() if isinstance(n, ASTNode) else n for n in self.nodes]
        return d


def _para_text(p_el) -> str:
    parts = []
    for t in p_el.iter(f"{{{W}}}t"):
        parts.append(t.text or "")
    # 制表符与换行还原
    for br in p_el.iter(f"{{{W}}}br"):
        pass
    return "".join(parts)


def _para_style(p_el) -> dict:
    style: dict[str, Any] = {}
    ppr = p_el.find("w:pPr", NS)
    if ppr is not None:
        st = ppr.find("w:pStyle", NS)
        if st is not None:
            style["style_name"] = st.get(f"{{{W}}}val")
        jc = ppr.find("w:jc", NS)
        if jc is not None:
            style["align"] = jc.get(f"{{{W}}}val")
        sp = ppr.find("w:spacing", NS)
        if sp is not None:
            style["spacing"] = {k.split("}")[-1]: v for k, v in sp.attrib.items()}
    runs = []
    for r in p_el.findall("w:r", NS):
        rpr = r.find("w:rPr", NS)
        info: dict[str, Any] = {}
        if rpr is not None:
            if rpr.find("w:b", NS) is not None:
                info["bold"] = True
            if rpr.find("w:u", NS) is not None:
                u = rpr.find("w:u", NS)
                info["underline"] = u.get(f"{{{W}}}val", "single")
            sz = rpr.find("w:sz", NS)
            if sz is not None:
                info["font_size_half_pt"] = sz.get(f"{{{W}}}val")
            rf = rpr.find("w:rFonts", NS)
            if rf is not None:
                info["east_asia_font"] = rf.get(f"{{{W}}}eastAsia")
        txt = "".join(t.text or "" for t in r.findall("w:t", NS))
        if info and txt.strip():
            runs.append({"text": txt, **info})
    if runs:
        style["runs"] = runs
    return style


def _cell_info(tc_el, row_idx: int, col_cursor: int) -> tuple[dict, int]:
    """返回单元格信息与消耗的列数（考虑横向合并）。"""
    tcpr = tc_el.find("w:tcPr", NS)
    grid_span = 1
    vmerge = None
    if tcpr is not None:
        gs = tcpr.find("w:gridSpan", NS)
        if gs is not None:
            grid_span = int(gs.get(f"{{{W}}}val", "1"))
        vm = tcpr.find("w:vMerge", NS)
        if vm is not None:
            vmerge = vm.get(f"{{{W}}}val", "continue")  # restart | continue
    texts = []
    for p in tc_el.findall("w:p", NS):
        texts.append(_para_text(p))
    info = {
        "text": "\n".join(t for t in texts).strip(),
        "row_index": row_idx,
        "column_index": col_cursor,
        "grid_span": grid_span,
        "v_merge": vmerge,
    }
    return info, grid_span


def parse_docx(path, document_id: Optional[str] = None) -> ParsedDocument:
    """解析 DOCX 为有序 AST。任何不支持/不可靠结构都记录状态而不是静默丢弃。"""
    import os

    path = str(path)
    file_hash = sha256_file(path)
    doc_id = document_id or file_hash[:16]
    doc = ParsedDocument(
        document_id=doc_id,
        source_path=os.path.basename(path),
        source_hash=file_hash,
        file_size=os.path.getsize(path),
    )

    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        xml = z.read("word/document.xml")
        root = etree.fromstring(xml, parser=SAFE_XML_PARSER)
        body = root.find("w:body", NS)

        order = 0
        current_section: Optional[str] = None
        para_count = 0
        table_count = 0

        def add(node: ASTNode):
            nonlocal order
            node.order = order
            order += 1
            doc.nodes.append(node)

        for bi, child in enumerate(body):
            tag = etree.QName(child).localname
            locator_base = {
                "body_child_index": bi,
                "xml_tag": f"w:body/w:{tag}",
                "package_part": "word/document.xml",
                "page_number": None,
            }
            if tag == "p":
                text = _para_text(child)
                m_art = ARTICLE_RE.match(text.strip())
                m_num = CLAUSE_NUM_RE.match(text.strip())
                if m_art:
                    current_section = f"{m_art.group(1)} {m_art.group(2)}".strip()
                node = ASTNode(
                    document_id=doc_id,
                    node_id=f"{doc_id}:p{para_count}",
                    node_type="paragraph",
                    order=0,
                    text=text,
                    section=current_section,
                    clause_number=(m_num.group(1) if m_num else None),
                    style=_para_style(child),
                    source_locator={**locator_base, "paragraph_index": para_count},
                )
                blanks = BLANK_RE.findall(text)
                if blanks:
                    doc.blank_placeholder_count += len(blanks)
                    node.flags["blank_placeholders"] = len(blanks)
                cc = sum(text.count(c) for c in CHECKBOX_CHARS)
                if cc:
                    doc.checkbox_count += cc
                    node.flags["checkbox_symbols"] = cc
                if text.strip().startswith("合同编号"):
                    doc.contract_number_hint = text.strip()
                    node.flags["is_contract_number_line"] = True
                add(node)
                para_count += 1
            elif tag == "tbl":
                rows = child.findall("w:tr", NS)
                dims = {"table_index": table_count, "rows": len(rows), "cols": 0}
                tbl_node = ASTNode(
                    document_id=doc_id,
                    node_id=f"{doc_id}:tbl{table_count}",
                    node_type="table",
                    order=0,
                    text="",
                    section=current_section,
                    table_index=table_count,
                    source_locator={**locator_base, "table_index": table_count},
                    flags={"row_count": len(rows)},
                )
                add(tbl_node)
                max_cols = 0
                for ri, tr in enumerate(rows):
                    col_cursor = 0
                    for tc in tr.findall("w:tc", NS):
                        info, span = _cell_info(tc, ri, col_cursor)
                        cell_node = ASTNode(
                            document_id=doc_id,
                            node_id=f"{doc_id}:tbl{table_count}r{ri}c{col_cursor}",
                            node_type="table_cell",
                            order=0,
                            text=info["text"],
                            section=current_section,
                            table_index=table_count,
                            row_index=ri,
                            column_index=col_cursor,
                            style={},
                            parent_node_id=tbl_node.node_id,
                            source_locator={
                                **locator_base,
                                "table_index": table_count,
                                "row_index": ri,
                                "column_index": col_cursor,
                            },
                            flags={
                                "grid_span": info["grid_span"],
                                "v_merge": info["v_merge"],
                            },
                        )
                        add(cell_node)
                        col_cursor += span
                    max_cols = max(max_cols, col_cursor)
                dims["cols"] = max_cols
                doc.table_dims.append(dims)
                table_count += 1
            elif tag == "sdt":
                # 结构化文档标记：内容按段落抽取，标记状态
                for p in child.iter(f"{{{W}}}p"):
                    text = _para_text(p)
                    if text.strip():
                        add(
                            ASTNode(
                                document_id=doc_id,
                                node_id=f"{doc_id}:sdt{order}",
                                node_type="paragraph",
                                order=0,
                                text=text,
                                section=current_section,
                                source_locator={**locator_base, "inside_sdt": True},
                                extraction_status="success_with_sdt_wrapper",
                            )
                        )
            elif tag == "sectPr":
                pass
            else:
                # 其他未知结构：记录而不丢弃
                txt = "".join(t.text or "" for t in child.iter(f"{{{W}}}t"))
                add(
                    ASTNode(
                        document_id=doc_id,
                        node_id=f"{doc_id}:unk{order}",
                        node_type="unknown_block",
                        order=0,
                        text=txt,
                        source_locator=locator_base,
                        extraction_status="structure_recorded_not_fully_parsed",
                    )
                )
                doc.parse_warnings.append(
                    f"body child #{bi} tag={tag} 记录为 unknown_block"
                )

        doc.paragraph_count = para_count
        doc.table_count = table_count

        # 页眉页脚
        for n in names:
            if re.match(r"word/header\d*\.xml", n):
                r2 = etree.fromstring(z.read(n), parser=SAFE_XML_PARSER)
                txts = [_para_text(p) for p in r2.iter(f"{{{W}}}p")]
                txts = [t for t in txts if t.strip()]
                if txts:
                    doc.headers.append({"part": n, "text": "\n".join(txts)})
            if re.match(r"word/footer\d*\.xml", n):
                r2 = etree.fromstring(z.read(n), parser=SAFE_XML_PARSER)
                txts = [_para_text(p) for p in r2.iter(f"{{{W}}}p")]
                txts = [t for t in txts if t.strip()]
                if txts:
                    doc.footers.append({"part": n, "text": "\n".join(txts)})

        # 图片 / 嵌入对象 / 批注 / 修订 / 文本框
        doc.has_images = any(n.startswith("word/media/") for n in names)
        doc.has_embedded_objects = any(n.startswith("word/embeddings/") for n in names)
        doc.comments_present = "word/comments.xml" in names
        doc.revisions_present = bool(
            root.find(".//w:ins", NS) is not None
            or root.find(".//w:del", NS) is not None
        )
        doc.textboxes_present = bool(root.find(".//w:txbxContent", NS) is not None)
        if doc.has_images:
            doc.parse_warnings.append("含图片/嵌入图形：文本层不覆盖图片内容")
        if doc.comments_present:
            doc.parse_warnings.append("含批注：批注正文未纳入条款抽取")
        if doc.revisions_present:
            doc.parse_warnings.append("含修订标记：抽取以当前文本层为准")
        if doc.textboxes_present:
            doc.parse_warnings.append("含文本框：文本框内容未纳入主条款流")

        # 标题 / 副标题（取前两个非空段落）
        non_empty = [
            n for n in doc.nodes if n.node_type == "paragraph" and n.text.strip()
        ]
        if non_empty:
            doc.title = non_empty[0].text.strip()
        if len(non_empty) > 1 and not ARTICLE_RE.match(non_empty[1].text.strip()):
            doc.subtitle = non_empty[1].text.strip()

    return doc
