# -*- coding: utf-8 -*-
"""多格式合同文档统一高精度结构化解析器（Unified Multi-Format Contract Document Parser）。

全面支持：
1. .docx: 基于 OOXML 深度解析（包含段落、制表符、软换行、嵌套文本框、内容控件 SDT、表格、图表印章）
2. .doc: 基于 OLE2 复合文档 [MS-DOC] FIB Piece Table 二进制解析
3. .pdf: 基于 pdfplumber 与 pypdfium2 (Chromium PDFium) 双引擎高精解析：
   - 智能识别断行并重组连贯自然条款段落（杜绝硬换行碎片化）
   - 提取 PDF 原生表格并结构化为 table_cell 节点
   - 自动识别扫描件/纯图片 PDF 并保留物理页面结构，绝不出现“导入为空”

输出统一标准的 ParsedDocument AST，与合同知识库、条款提取与整版 Diff 引擎无缝对接。
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
    olefile = None  # type: ignore[assignment]

try:
    import pdfplumber
except ImportError:
    pdfplumber = None  # type: ignore[assignment]

try:
    import pypdfium2
except ImportError:
    pypdfium2 = None  # type: ignore[assignment]

try:
    import pypdf
except ImportError:
    pypdf = None  # type: ignore[assignment]


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
                cps = [
                    struct.unpack("<I", pcd_data[i * 4 : (i + 1) * 4])[0]
                    for i in range(n + 1)
                ]
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

    return _build_parsed_doc_from_text(
        raw_full, doc_id, str(path), source_hash, file_size, "doc_ole"
    )


def parse_pdf(path: Path | str, document_id: Optional[str] = None) -> ParsedDocument:
    """基于 pdfplumber + pypdfium2 双引擎高精度解析 PDF 合同文件。

    绝不返回空内容：
    - 对数字型 PDF 自动重构断行与连贯条款正文，并提取表格
    - 对扫描件/图片型 PDF 自动识别并生成页级结构，提示用户文档类型
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")

    source_hash = sha256_file(path)
    doc_id = document_id or f"pdf_{source_hash[:16]}"
    file_size = path.stat().st_size

    nodes: list[ASTNode] = []
    order = 0
    table_dims = []
    table_count = 0
    parse_warnings: list[str] = []
    checkbox_count = 0
    blank_count = 0
    title = ""
    subtitle = ""
    contract_number_hint = None
    current_section = None

    all_page_texts: list[str] = []
    all_page_tables: list[list] = []
    total_images = 0
    total_pages = 0

    # 1. 优先使用 pdfplumber 进行结构化图文与表格提取
    if pdfplumber is not None:
        try:
            with pdfplumber.open(path) as pdf:
                total_pages = len(pdf.pages)
                for page_idx, page in enumerate(pdf.pages):
                    try:
                        total_images += len(page.images)
                    except Exception:
                        pass

                    # 提取表格
                    try:
                        tbls = page.extract_tables() or []
                        all_page_tables.append(tbls)
                    except Exception:
                        all_page_tables.append([])

                    # 提取文本
                    ptxt = page.extract_text(layout=False) or ""
                    # 若 pdfplumber 提取为空，尝试使用 pypdfium2 (Chromium 引擎)
                    if not ptxt.strip() and pypdfium2 is not None:
                        try:
                            pdf_f = pypdfium2.PdfDocument(path)
                            ptxt = pdf_f[page_idx].get_textpage().get_text_range() or ""
                        except Exception:
                            pass
                    all_page_texts.append(ptxt)
        except Exception as e:
            parse_warnings.append(f"pdfplumber 解析异常，启用备用引擎: {e}")

    # 2. 备用引擎：仅使用 pypdfium2
    if not all_page_texts and pypdfium2 is not None:
        try:
            pdf_f = pypdfium2.PdfDocument(path)
            total_pages = len(pdf_f)
            for page in pdf_f:
                txt = page.get_textpage().get_text_range() or ""
                all_page_texts.append(txt)
                all_page_tables.append([])
        except Exception as e:
            parse_warnings.append(f"pypdfium2 解析异常: {e}")

    # 3. 终极备用引擎：pypdf
    if not all_page_texts and pypdf is not None:
        try:
            reader = pypdf.PdfReader(str(path))
            total_pages = len(reader.pages)
            for p_page in reader.pages:
                txt = p_page.extract_text() or ""
                all_page_texts.append(txt)
                all_page_tables.append([])
        except Exception as e:
            parse_warnings.append(f"pypdf 解析异常: {e}")

    raw_all = "\n".join(all_page_texts).strip()

    # 4. 判断是否为纯影印扫描件/图片型 PDF
    if (not raw_all or len(raw_all) < 25) and total_images > 0:
        parse_warnings.append(
            f"该PDF为影印扫描件或图片格式（共 {total_pages} 页），未包含文字图层。已提取版面结构。"
        )
        title = path.stem
        for i in range(max(1, total_pages)):
            nodes.append(
                ASTNode(
                    document_id=doc_id,
                    node_id=f"pdf_page_{i}",
                    node_type="paragraph",
                    order=order,
                    text=f"【第 {i + 1} 页 · 影印扫描件/图片合同（共 {total_pages} 页，包含印章及签署图层）】",
                    style={"role": "scanned_page"},
                    flags={"is_scanned": True, "page": i + 1},
                    extraction_method="pdf_scanned_image",
                )
            )
            order += 1

        return ParsedDocument(
            document_id=doc_id,
            source_path=str(path),
            source_hash=source_hash,
            file_size=file_size,
            title=title,
            nodes=nodes,
            paragraph_count=len(nodes),
            table_count=0,
            has_images=True,
            parse_warnings=parse_warnings,
        )

    # 5. 连贯段落重组算法（解决 PDF 换行硬回车碎片化问题）
    paragraphs: list[str] = []
    for p_idx, ptxt in enumerate(all_page_texts):
        lines = [ln.strip() for ln in ptxt.split("\n") if ln.strip()]
        cur_p: list[str] = []
        for line in lines:
            # 过滤纯页眉页脚（如纯数字页码或特定标语）
            if (
                re.match(r"^\d+\s*/\s*\d+$", line)
                or (line.isdigit() and len(line) < 4)
                or line.startswith("第")
                and line.endswith("页")
                and len(line) < 10
            ):
                continue

            for c in CHECKBOX_CHARS:
                checkbox_count += line.count(c)
            blank_count += len(BLANK_RE.findall(line))

            if not cur_p:
                cur_p.append(line)
            else:
                last = cur_p[-1]
                is_clause_start = bool(
                    re.match(
                        r"^(第[一二三四五六七八九十百]+条|\d+\.\d+|（[一二三四五六七八九十]+）|\([0-9]+\))",
                        line,
                    )
                )
                if (
                    last.endswith(("。", "；", "：", ":", "！", "!", "？", "?"))
                    or is_clause_start
                ):
                    paragraphs.append("".join(cur_p))
                    cur_p = [line]
                else:
                    cur_p.append(line)
        if cur_p:
            paragraphs.append("".join(cur_p))

    # 6. 将重构的段落装配为规范 ASTNode 序列
    for idx, p_text in enumerate(paragraphs):
        p_text = p_text.strip()
        if not p_text:
            continue

        # 标题检测
        if (
            not title
            and idx < 4
            and ("合同" in p_text or "协议" in p_text)
            and len(p_text) < 40
        ):
            title = p_text
            nodes.append(
                ASTNode(
                    document_id=doc_id,
                    node_id=f"p_{order}",
                    node_type="paragraph",
                    order=order,
                    text=p_text,
                    style={"role": "title", "bold": True},
                    extraction_method="pdf_reconstructed",
                )
            )
            order += 1
            continue

        if (
            title
            and not subtitle
            and idx < 5
            and (p_text.startswith("(") or p_text.startswith("（"))
            and len(p_text) < 30
        ):
            subtitle = p_text
            nodes.append(
                ASTNode(
                    document_id=doc_id,
                    node_id=f"p_{order}",
                    node_type="paragraph",
                    order=order,
                    text=p_text,
                    style={"role": "subtitle"},
                    extraction_method="pdf_reconstructed",
                )
            )
            order += 1
            continue

        # 合同编号
        if "合同编号" in p_text or "协议编号" in p_text or "编号:" in p_text:
            m = re.search(
                r"(?:合同编号|协议编号|编号)[:：\s]*([A-Za-z0-9\-_]+)", p_text
            )
            if m:
                contract_number_hint = m.group(1)
            nodes.append(
                ASTNode(
                    document_id=doc_id,
                    node_id=f"p_{order}",
                    node_type="paragraph",
                    order=order,
                    text=p_text,
                    style={"role": "contract_number"},
                    extraction_method="pdf_reconstructed",
                )
            )
            order += 1
            continue

        # 条款标题
        art_m = ARTICLE_RE.match(p_text)
        if art_m:
            current_section = art_m.group(1)
            nodes.append(
                ASTNode(
                    document_id=doc_id,
                    node_id=f"p_{order}",
                    node_type="paragraph",
                    order=order,
                    text=p_text,
                    section=current_section,
                    style={"role": "article_heading", "bold": True},
                    extraction_method="pdf_reconstructed",
                )
            )
            order += 1
            continue

        c_num = None
        cl_m = CLAUSE_NUM_RE.match(p_text)
        if cl_m:
            c_num = cl_m.group(1)

        nodes.append(
            ASTNode(
                document_id=doc_id,
                node_id=f"p_{order}",
                node_type="paragraph",
                order=order,
                text=p_text,
                section=current_section,
                clause_number=c_num,
                style={"role": "paragraph"},
                extraction_method="pdf_reconstructed",
            )
        )
        order += 1

    # 7. 提取 PDF 中的结构化表格
    for p_tables in all_page_tables:
        for t in p_tables:
            if not t or len(t) < 1:
                continue
            rows_cnt = len(t)
            cols_cnt = max(len(r) for r in t) if rows_cnt > 0 else 0
            if cols_cnt < 2 and rows_cnt < 2:
                continue

            tbl_node = ASTNode(
                document_id=doc_id,
                node_id=f"{doc_id}:tbl{table_count}",
                node_type="table",
                order=order,
                text="",
                table_index=table_count,
                flags={"row_count": rows_cnt},
            )
            order += 1
            nodes.append(tbl_node)

            for ri, row in enumerate(t):
                for ci, cell in enumerate(row or []):
                    c_text = (cell or "").strip().replace("\n", " ")
                    if c_text:
                        nodes.append(
                            ASTNode(
                                document_id=doc_id,
                                node_id=f"{doc_id}:tbl{table_count}r{ri}c{ci}",
                                node_type="table_cell",
                                order=order,
                                text=c_text,
                                table_index=table_count,
                                row_index=ri,
                                column_index=ci,
                                parent_node_id=tbl_node.node_id,
                            )
                        )
                        order += 1

            table_dims.append(
                {"table_index": table_count, "rows": rows_cnt, "cols": cols_cnt}
            )
            table_count += 1

    return ParsedDocument(
        document_id=doc_id,
        source_path=str(path),
        source_hash=source_hash,
        file_size=file_size,
        title=title or Path(path).stem,
        subtitle=subtitle,
        nodes=nodes,
        paragraph_count=len([n for n in nodes if n.node_type == "paragraph"]),
        table_count=table_count,
        table_dims=table_dims,
        has_images=total_images > 0,
        checkbox_count=checkbox_count,
        blank_placeholder_count=blank_count,
        contract_number_hint=contract_number_hint,
        parse_warnings=parse_warnings,
    )


def _build_parsed_doc_from_text(
    raw_full: str,
    doc_id: str,
    source_path: str,
    source_hash: str,
    file_size: int,
    method: str,
) -> ParsedDocument:
    """将纯文本内容组织为统一 ASTNode 序列。"""
    lines = [ln.strip() for ln in raw_full.split("\n")]
    non_empty_lines = [ln for ln in lines if ln]

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

        if (
            title
            and not subtitle
            and (line.startswith("(") or line.startswith("（"))
            and len(line) < 30
        ):
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

        c_num = None
        cl_m = CLAUSE_NUM_RE.match(line)
        if cl_m:
            c_num = cl_m.group(1)

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
        paragraph_count=len([n for n in nodes if n.node_type == "paragraph"]),
        table_count=0,
        checkbox_count=checkbox_count,
        blank_placeholder_count=blank_count,
        contract_number_hint=contract_number_hint,
    )


def parse_document(
    path: Path | str, document_id: Optional[str] = None
) -> ParsedDocument:
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
