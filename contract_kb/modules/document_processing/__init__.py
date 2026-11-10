# -*- coding: utf-8 -*-
"""文档处理与分类提取模块（Document Processing）。"""

from .classifier import Classification, TEMPLATE_CODE_SUBTYPE_MAP, classify
from .clause_extractor import (
    ClauseRecord,
    extract_clauses,
    extract_table_schemas,
)
from .document_parser import parse_document
from .docx_parser import ASTNode, ParsedDocument, parse_docx, sha256_file
from .ingestion import (
    ImportedDoc,
    SecurityCheckResult,
    import_template_file,
    scan_and_import,
    security_check_file,
)

__all__ = [
    "ASTNode",
    "Classification",
    "ClauseRecord",
    "ImportedDoc",
    "ParsedDocument",
    "SecurityCheckResult",
    "TEMPLATE_CODE_SUBTYPE_MAP",
    "classify",
    "extract_clauses",
    "extract_table_schemas",
    "import_template_file",
    "parse_document",
    "parse_docx",
    "scan_and_import",
    "security_check_file",
    "sha256_file",
]
