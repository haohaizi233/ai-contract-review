# -*- coding: utf-8 -*-
"""合同数据来源可追溯节点模型 (ContractSourceNode) - Phase 13R1。

确保每一个比对块、段落、表格与单元格均具备可逆向回溯至原始文档的强类型来源标识：
- 严格遵循 Source of Truth 原则；
- 保留行列号、合并单元格、提取方式与解析警告；
- 杜绝向比对结果中凭空伪造、推测或混入示例内容。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ContractSourceNode:
    """可精确追溯到源文档的合同 AST 节点。"""

    document_id: str
    document_hash: str
    node_id: str
    node_type: str  # paragraph | table | table_row | table_cell | header | footer | scanned_placeholder
    original_text: str = ""
    document_version: str | None = None
    page_index: int | None = None
    table_index: int | None = None
    row_index: int | None = None
    column_index: int | None = None
    grid_span: int = 1
    v_merge: str | None = None
    source_path: str = ""
    source_range: dict[str, Any] = field(default_factory=dict)
    extraction_method: str = "ooxml"
    parse_warnings: list[str] = field(default_factory=list)
    style: dict[str, Any] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def node_hash(self) -> str:
        """根据节点关键内容计算确定性哈希指纹。"""
        import hashlib

        raw = f"{self.document_id}:{self.document_hash}:{self.node_id}:{self.original_text}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def from_ast_node(
        cls,
        node: Any,
        document_hash: str,
        document_version: str | None = None,
        source_path: str = "",
    ) -> ContractSourceNode:
        """从解析器的 ASTNode 对象安全转换。"""
        if isinstance(node, dict):
            flags = node.get("flags") or {}
            source_locator = node.get("source_locator") or {}
            return cls(
                document_id=str(node.get("document_id") or ""),
                document_hash=document_hash,
                document_version=document_version,
                node_id=str(node.get("node_id") or ""),
                node_type=str(node.get("node_type") or "paragraph"),
                original_text=str(node.get("text") or ""),
                page_index=flags.get("page") or source_locator.get("page"),
                table_index=node.get("table_index"),
                row_index=node.get("row_index"),
                column_index=node.get("column_index"),
                grid_span=int(flags.get("grid_span") or 1),
                v_merge=flags.get("v_merge"),
                source_path=source_path,
                source_range=source_locator,
                extraction_method=str(node.get("extraction_method") or "ooxml"),
                parse_warnings=list(node.get("parse_warnings") or []),
                style=dict(node.get("style") or {}),
                flags=dict(flags),
            )

        flags = getattr(node, "flags", {}) or {}
        source_locator = getattr(node, "source_locator", {}) or {}
        return cls(
            document_id=str(getattr(node, "document_id", "")),
            document_hash=document_hash,
            document_version=document_version,
            node_id=str(getattr(node, "node_id", "")),
            node_type=str(getattr(node, "node_type", "paragraph")),
            original_text=str(getattr(node, "text", "")),
            page_index=flags.get("page") or source_locator.get("page"),
            table_index=getattr(node, "table_index", None),
            row_index=getattr(node, "row_index", None),
            column_index=getattr(node, "column_index", None),
            grid_span=int(flags.get("grid_span", 1) or 1),
            v_merge=flags.get("v_merge"),
            source_path=source_path,
            source_range=source_locator,
            extraction_method=str(getattr(node, "extraction_method", "ooxml")),
            parse_warnings=list(getattr(node, "parse_warnings", []) or []),
            style=dict(getattr(node, "style", {}) or {}),
            flags=dict(flags),
        )
