# -*- coding: utf-8 -*-
"""导入管线：扫描源目录 → 安全校验 → 解析 → 分类 → 条款/Schema 抽取 → 清单报告。

安全校验（§14）：扩展名/MIME/文件大小/ZIP 结构与解压边界（若提供 ZIP）。
DOCX 按 OOXML 纯 XML 解析，绝不触发宏或外部链接。
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .docx_parser import parse_docx, ParsedDocument
from .classifier import classify
from .clause_extractor import extract_clauses, extract_table_schemas


@dataclass
class SecurityCheckResult:
    passed: bool
    checks: list = field(default_factory=list)
    rejected_reason: str | None = None


def security_check_file(path: Path) -> SecurityCheckResult:
    res = SecurityCheckResult(passed=True)
    size = path.stat().st_size
    res.checks.append(
        {
            "check": "file_size",
            "value": size,
            "limit": 20 * 1024 * 1024,
            "passed": size <= 20 * 1024 * 1024,
        }
    )
    if size > 20 * 1024 * 1024:
        res.passed = False
        res.rejected_reason = "file_too_large"
        return res
    res.checks.append(
        {
            "check": "extension",
            "value": path.suffix.lower(),
            "passed": path.suffix.lower() == ".docx",
        }
    )
    if path.suffix.lower() != ".docx":
        res.passed = False
        res.rejected_reason = "bad_extension"
        return res
    # MIME：以魔数校验（DOCX=PK\x03\x04）
    head = path.read_bytes()[:4]
    res.checks.append(
        {"check": "magic_bytes", "value": head.hex(), "passed": head == b"PK\x03\x04"}
    )
    if head != b"PK\x03\x04":
        res.passed = False
        res.rejected_reason = "bad_magic"
        return res
    # ZIP 边界：条目数、压缩比、路径穿越
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            res.checks.append(
                {
                    "check": "zip_entry_count",
                    "value": len(names),
                    "limit": 500,
                    "passed": len(names) <= 500,
                }
            )
            total_c = sum(i.compress_size for i in z.infolist()) or 1
            total_u = sum(i.file_size for i in z.infolist())
            ratio = total_u / total_c
            res.checks.append(
                {
                    "check": "zip_compression_ratio",
                    "value": round(ratio, 2),
                    "limit": 200,
                    "passed": ratio <= 200,
                }
            )
            traversal = [n for n in names if n.startswith("/") or ".." in Path(n).parts]
            res.checks.append(
                {
                    "check": "zip_path_traversal",
                    "value": traversal,
                    "passed": not traversal,
                }
            )
            if len(names) > 500 or ratio > 200 or traversal:
                res.passed = False
                res.rejected_reason = "zip_boundary_violation"
    except zipfile.BadZipFile:
        res.passed = False
        res.rejected_reason = "bad_zip"
    return res


def _matter_text(parsed: ParsedDocument) -> str:
    """第一条合同事项正文（含表格列头）。"""
    parts = []
    in_matter = False
    for n in parsed.nodes:
        if n.node_type == "paragraph":
            if n.text.strip().startswith("第一条"):
                in_matter = True
            elif n.text.strip().startswith("第二条"):
                in_matter = False
            if in_matter:
                parts.append(n.text)
        elif n.node_type == "table_cell" and in_matter is False and n.table_index == 0:
            parts.append(n.text)
    # 第一条表格兜底：table 0 的表头
    for n in parsed.nodes:
        if n.node_type == "table_cell" and n.table_index == 0 and n.row_index == 0:
            parts.append(n.text)
    return "\n".join(parts)


def _table_headers(parsed: ParsedDocument) -> list[str]:
    return [
        n.text.strip()
        for n in parsed.nodes
        if n.node_type == "table_cell" and n.row_index == 0 and n.text.strip()
    ]


@dataclass
class ImportedDoc:
    template_id: str
    source_file: str
    file_size: int
    sha256: str
    document_type: str
    paragraph_count: int
    table_count: int
    category: str
    subtype: str
    main_sections: list
    parse_success: bool
    unhandled_structures: list
    classification_confidence: float
    classification_evidence: list
    classification_conflicts: list
    clause_count: int
    table_schema_count: int
    ast: dict | None = None
    clauses: list = field(default_factory=list)
    table_schemas: list = field(default_factory=list)

    def to_dict(self, include_ast: bool = False):
        d = asdict(self)
        if not include_ast:
            d.pop("ast", None)
        return d


def import_template_file(path: Path) -> tuple[ImportedDoc | None, str | None]:
    """导入单个模板文件。返回 (记录, 错误原因)。"""
    sec = security_check_file(path)
    if not sec.passed:
        return None, sec.rejected_reason
    try:
        parsed = parse_docx(path)
    except Exception as e:  # 解析失败不静默丢弃
        return None, f"parse_error: {type(e).__name__}: {e}"

    full_text = "\n".join(n.text for n in parsed.nodes if n.node_type == "paragraph")
    matter = _matter_text(parsed)
    headers = _table_headers(parsed)
    cls = classify(
        parsed.document_id,
        path.name,
        parsed.title,
        parsed.subtitle,
        matter,
        headers,
        full_text,
    )
    clauses = extract_clauses(parsed, parsed.document_id)
    schemas = extract_table_schemas(parsed, parsed.document_id)

    sections = []
    for n in parsed.nodes:
        if (
            n.node_type == "paragraph"
            and n.text.strip().startswith("第")
            and "条" in n.text[:6]
        ):
            t = n.text.strip()
            if re.match(r"^第[一二三四五六七八九十]+条", t) and t not in sections:
                sections.append(t[:40])

    unhandled = []
    if parsed.textboxes_present:
        unhandled.append("textboxes")
    if parsed.comments_present:
        unhandled.append("comments")
    if parsed.revisions_present:
        unhandled.append("revisions")
    if parsed.has_images:
        unhandled.append("images")
    if parsed.has_embedded_objects:
        unhandled.append("embedded_objects")
    unhandled.extend(parsed.parse_warnings)

    rec = ImportedDoc(
        template_id=parsed.document_id,
        source_file=path.name,
        file_size=parsed.file_size,
        sha256=parsed.source_hash,
        document_type="contract_template",
        paragraph_count=parsed.paragraph_count,
        table_count=parsed.table_count,
        category=cls.category,
        subtype=cls.subtype,
        main_sections=sections,
        parse_success=True,
        unhandled_structures=unhandled,
        classification_confidence=cls.classification_confidence,
        classification_evidence=cls.classification_evidence,
        classification_conflicts=cls.classification_conflicts,
        clause_count=len(clauses),
        table_schema_count=len(schemas),
        ast=parsed.to_dict(),
        clauses=[c.to_dict() for c in clauses],
        table_schemas=schemas,
    )
    return rec, None


def scan_and_import(template_dir: Path) -> dict:
    """扫描目录（兼容直接目录或 ZIP 落盘目录），返回导入结果包。

    仅处理允许扩展名（.docx）；其他文件记录为 skipped 而非失败。
    """
    from . import config as _cfg

    all_files = sorted(
        p for p in template_dir.iterdir() if p.is_file() and not p.name.startswith("~")
    )
    files = [p for p in all_files if p.suffix.lower() in _cfg.ALLOWED_EXT]
    skipped = [p.name for p in all_files if p.suffix.lower() not in _cfg.ALLOWED_EXT]
    docs, failures = [], []
    for p in files:
        rec, err = import_template_file(p)
        if rec:
            docs.append(rec)
        else:
            failures.append({"file": p.name, "size": p.stat().st_size, "error": err})
    return {
        "scanned_dir": str(template_dir),
        "file_count": len(files),
        "skipped_non_docx": skipped,
        "imported": [d.to_dict() for d in docs],
        "failures": failures,
        "_raw": docs,
    }
