# -*- coding: utf-8 -*-
"""合同对比、表格对齐与版式审核模块（Contract Comparison）。"""

from .clause_diff import ClauseDelta, diff_clause_sets, diff_clauses
from .format_auditor import DEFAULT_STD, FormatIssue, audit_format
from .full_diff import (
    build_full_contract_diff,
    escape_html,
    generate_word_diff_html,
)
from .table_aligner import (
    align_contract_tables,
    build_table_diff_block,
    extract_tables_from_nodes,
)

__all__ = [
    "ClauseDelta",
    "DEFAULT_STD",
    "FormatIssue",
    "align_contract_tables",
    "audit_format",
    "build_full_contract_diff",
    "build_table_diff_block",
    "diff_clause_sets",
    "diff_clauses",
    "escape_html",
    "extract_tables_from_nodes",
    "generate_word_diff_html",
]
