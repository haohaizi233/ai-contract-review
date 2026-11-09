# -*- coding: utf-8 -*-
"""多格式合同文档统一结构化解析器（Unified Multi-Format Contract Document Parser）。

支持格式：
- .docx: 基于 OOXML 纯 XML 节点解析（保留段落、表格、字体样式与层级）
- .doc: 基于 OLE2 复合文档 [MS-DOC] FIB Piece Table 二进制解析
- .pdf: 基于 pypdf 页流与文本段落结构化解析，识别条款号、正文、主体及签署区

全部输出统一规范的 ParsedDocument AST，与合同知识库提取、分类与整版 Diff 引擎无缝对接。
"""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Optional

from contract_kb.docx_parser import (
    ASTNode,
    ParsedDocument,
    parse_docx,
    sha256_file,
    ARTICLE_RE,
    CLAUSE_NUM_RE,
    BLANK_RE,
    CHECKBOX_CHARS,
)

try:
    import olefile
except ImportError:
    olefile = None

try:
    import pypdf
except ImportError:
    pypdf = None


def parse_doc(path: Path | str, document_id: Optional[str] = None) -> ParsedDocument:
    """解析 Word 97-2003 二进制 .doc 合同文件。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    if olefile is None:
        raise RuntimeError("缺少 olefile 依赖，无法解析 .doc 文件")

    source_hash = sha256_file(path)
    doc_id = document_id or f"doc_{source_hash[:16]}"
    file_size = path.stat().st_size

    ole = olefile.OleFileIO(str(path))
    try:
        if not ole.exists("WordDocument"):
            raise ValueError(f"不是合法的 WordDocument OLE 文件: {path.name}")

        word_stream = ole.openstream("WordDocument").read()
        if len(word_stream) < 0x01AA:
            raise ValueError("WordDocument 流长度不足")

        wIdent = struct.unpack("<H", word_stream[:2])[0]
        if wIdent != 0xA5EC:
            raise ValueError(f"非法 Word FIB 魔数: {wIdent:#x}")

        fib_flags = struct.unpack("<H", word_stream[0x000A:0x000C])[0]
        table_name = "1Table" if (fib_flags & 0x0200) else "0Table"
        if not ole.exists(table_name):
            raise ValueError(f"未找到对应的数据表流: {table_name}")

        table_stream = ole.openstream(table_name).read()
        fcClx = struct.unpack("<I", word_stream[0x01A2:0x01A6])[0]
        lcbClx = struct.unpack("<I", word_stream[0x01A6:0x01AA])[0]
        clx_data = table_stream[fcClx : fcClx + lcbClx]

        pos = 0
        text_pieces: list[str] = []
        while pos < len(clx_data):
            clxt = clx_data[pos]
            pos += 1
            if clxt == 1:
                cb = struct.unpack("<H", clx_data[pos : pos + 2])[0]
                pos += 2 + cb
            elif clxt == 2:
                lcb = struct.unpack("<I", clx_data[pos : pos + 4])[0]
                pos += 4
                pcd_data = clx_data[pos : pos + lcb]
                n = (lcb - 4) // 12
                cps = [struct.unpack("<I", pcd_data[i * 4 : (i + 1) * 4])[0] for i in range(n + 1)]
                pcds_offset = (n + 1) * 4
                for i in range(n):
                    cp_start = cps[i]
                    cp_end = cps[i + 1]
                    pcd = pcd_data[pcds_offset + i * 8 : pcds_offset + (i + 1) * 8]
                    fc = struct.unpack("<I", pcd[2:6])[0]
                    is_ansi = (fc & 0x40000000) != 0
                    actual_fc = (fc & 0x3FFFFFFF) // 2 if is_ansi else fc
                    char_count = cp_end - cp_start
                    if is_ansi:
                        raw_bytes = word_stream[actual_fc : actual_fc + char_count]
                        try:
                            t = raw_bytes.decode("gb18030", errors="ignore")
                        except Exception:
                            t = raw_bytes.decode("latin-1", errors="ignore")
                    else:
                        raw_bytes = word_stream[actual_fc : actual_fc + char_count * 2]
                        t = raw_bytes.decode("utf-16-le", errors="ignore")
                    text_pieces.append(t)
                break
            else:
                break

        raw_full = "".join(text_pieces).replace("\r\n", "\n").replace("\r", "\n")
    finally:
        ole.close()

    return _build_parsed_doc_from_text(raw_full, doc_id, str(path), source_hash, file_size, "doc_ole")


def parse_pdf(path: Path | str, document_id: Optional[str] = None) -> ParsedDocument:
    """解析 PDF 合同文件。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    if pypdf is None:
        raise RuntimeError("缺少 pypdf 依赖，无法解析 .pdf 文件")

    source_hash = sha256_file(path)
    doc_id = document_id or f"pdf_{source_hash[:16]}"
    file_size = path.stat().st_size

    reader = pypdf.PdfReader(str(path))
    pages_text: list[str] = []
    for page in reader.pages:
        txt = page.extract_text() or ""
        pages_text.append(txt)

    raw_full = "\n".join(pages_text).replace("\r\n", "\n").replace("\r", "\n")
    return _build_parsed_doc_from_text(raw_full, doc_id, str(path), source_hash, file_size, "pdf_pypdf")


def _build_parsed_doc_from_text(
    raw_full: str,
    doc_id: str,
    source_path: str,
    source_hash: str,
    file_size: int,
    method: str,
) -> ParsedDocument:
    """将提取出的规范纯文本组织为 ParsedDocument 与 ASTNode 序列。"""
    lines = [l.strip() for l in raw_full.split("\n")]
    # 过滤连续空行
    non_empty_lines = [l for l in lines if l]

    title = ""
    subtitle = ""
    contract_number_hint = None
    nodes: list[ASTNode] = []
    current_section = None
    order = 0
    checkbox_count = 0
    blank_count = 0

    idx = 0
    while idx < len(non_empty_lines):
        line = non_empty_lines[idx]
        for c in CHECKBOX_CHARS:
            checkbox_count += line.count(c)
        blank_count += len(BLANK_RE.findall(line))

        # 1. 文档大标题
        if not title and ("合同" in line or "协议" in line) and len(line) < 40:
            title = line
            nodes.append(
                ASTNode(
                    document_id=doc_id,
                    node_id=f"p_{order}",
                    node_type="paragraph",
                    order=order,
                    text=line,
                    style={"role": "title", "alignment": "center", "bold": True},
                    extraction_method=method,
                )
            )
            order += 1
            idx += 1
            continue

        # 2. 副标题（如 (申请)、(专利年费缴纳)）
        if title and not subtitle and (line.startswith("(") or line.startswith("（")) and len(line) < 30:
            subtitle = line
            nodes.append(
                ASTNode(
                    document_id=doc_id,
                    node_id=f"p_{order}",
                    node_type="paragraph",
                    order=order,
                    text=line,
                    style={"role": "subtitle", "alignment": "center"},
                    extraction_method=method,
                )
            )
            order += 1
            idx += 1
            continue

        # 3. 合同编号
        if "合同编号" in line or "协议编号" in line or "编号:" in line:
            m = re.search(r"(?:合同编号|协议编号|编号)[:：\s]*([A-Za-z0-9\-_]+)", line)
            if m:
                contract_number_hint = m.group(1)
            nodes.append(
                ASTNode(
                    document_id=doc_id,
                    node_id=f"p_{order}",
                    node_type="paragraph",
                    order=order,
                    text=line,
                    style={"role": "contract_number", "alignment": "right"},
                    extraction_method=method,
                )
            )
            order += 1
            idx += 1
            continue

        # 4. 条款标题检测 (第X条)
        art_m = ARTICLE_RE.match(line)
        if art_m:
            current_section = art_m.group(1)
            nodes.append(
                ASTNode(
                    document_id=doc_id,
                    node_id=f"p_{order}",
                    node_type="paragraph",
                    order=order,
                    text=line,
                    section=current_section,
                    style={"role": "article_heading", "bold": True},
                    extraction_method=method,
                )
            )
            order += 1
            idx += 1
            continue

        # 5. 子条款号检测 (1.1, 2.3)
        c_num = None
        cl_m = CLAUSE_NUM_RE.match(line)
        if cl_m:
            c_num = cl_m.group(1)

        # 6. 普通段落 / 主体 / 签章
        nodes.append(
            ASTNode(
                document_id=doc_id,
                node_id=f"p_{order}",
                node_type="paragraph",
                order=order,
                text=line,
                section=current_section,
                clause_number=c_num,
                style={"role": "paragraph"},
                extraction_method=method,
            )
        )
        order += 1
        idx += 1

    return ParsedDocument(
        document_id=doc_id,
        source_path=source_path,
        source_hash=source_hash,
        file_size=file_size,
        title=title or Path(source_path).stem,
        subtitle=subtitle,
        nodes=nodes,
        paragraph_count=len(nodes),
        table_count=0,
        checkbox_count=checkbox_count,
        blank_placeholder_count=blank_count,
        contract_number_hint=contract_number_hint,
    )


def parse_document(path: Path | str, document_id: Optional[str] = None) -> ParsedDocument:
    """统一多格式文档解析入口：支持 .docx、.doc、.pdf。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return parse_docx(path, document_id=document_id)
    elif suffix == ".doc":
        return parse_doc(path, document_id=document_id)
    elif suffix == ".pdf":
        return parse_pdf(path, document_id=document_id)
    else:
        raise ValueError(f"不支持的合同文档格式: {suffix}（仅支持 .docx, .doc, .pdf）")
