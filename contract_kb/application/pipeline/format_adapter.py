# -*- coding: utf-8 -*-
"""解析 AST 到格式审核器的轻量视图适配器（零循环依赖）。"""

from __future__ import annotations

from typing import Any


def _table_dim(table_index: int, nodes: list["ParsedNodeView"]) -> dict[str, int]:
    cells = [
        n for n in nodes if n.node_type == "table_cell" and n.table_index == table_index
    ]
    row_ids = [n.row_index for n in cells if n.row_index is not None]
    col_ids = [n.column_index for n in cells if n.column_index is not None]
    return {
        "table_index": table_index,
        "rows": (max(row_ids) + 1) if row_ids else 0,
        "cols": (max(col_ids) + 1) if col_ids else 0,
    }


class ParsedNodeView:
    """单节点只读视图对象。"""

    node_id: str | None
    node_type: str | None
    order: int | None
    text: str | None
    section: str | None
    clause_number: str | None
    table_index: int | None
    row_index: int | None
    column_index: int | None
    style: dict[str, Any] | None
    source_locator: dict[str, Any] | None

    def __init__(self, d: dict[str, Any]):
        for k in (
            "node_id",
            "node_type",
            "order",
            "text",
            "section",
            "clause_number",
            "table_index",
            "row_index",
            "column_index",
            "style",
            "source_locator",
        ):
            setattr(self, k, d.get(k))


class ParsedDocumentView:
    """把 ImportedDoc.ast 字典适配为 format_auditor 期望的结构化视图。"""

    def __init__(self, ast: dict[str, Any], document_id: str):
        self.document_id = document_id
        self.nodes = [ParsedNodeView(n) for n in ast.get("nodes", [])]
        self.table_dims = [
            _table_dim(i, self.nodes)
            for i in sorted(
                {
                    n.table_index
                    for n in self.nodes
                    if n.node_type == "table_cell" and n.table_index is not None
                }
            )
        ]
        self.headers = ast.get("headers", [])
        self.footers = ast.get("footers", [])
        self.page_margins_cm = None


def audit_format_from_record(
    rec: Any, standard_record: dict[str, Any] | None = None
) -> dict[str, Any]:
    """调用确定性格式审核器并返回结构化差异与约束级别。"""
    from ...modules.contract_comparison.format_auditor import audit_format

    view = ParsedDocumentView(rec.ast or {}, rec.template_id)
    if standard_record:
        return audit_format(
            view,
            std=standard_record.get("params") or {},
            standard_record=standard_record,
        )
    return audit_format(view)
