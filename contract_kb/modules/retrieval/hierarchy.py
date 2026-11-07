# -*- coding: utf-8 -*-
"""多粒度条款分层树与 Parent-Child 上下文关联检索（Hierarchical Chunking & Parent-Child Retrieval）。

支持五级知识粒度：
1. DOCUMENT: 整篇合同元数据与路由（类型/主体/大纲）
2. ARTICLE: 章条级（“第五条 违约责任”，宏观章节上下文）
3. SUBCLAUSE: 子款级（“5.1”、“5.2”，精准匹配单元）
4. SEMANTIC_UNIT: 语义单元（长款项内的不可分割语义块）
5. TABLE: 结构化表格（保留行列坐标与表头）

核心能力：
- Parent-Child 关系网络（parent_id, child_ids, previous_id, next_id）；
- 上下文扩展：根据命中子条款，向上获取父级章条生效条件，向外关联引用的跨条款目标；
- 原文保真：分离原始 XML 节点、规范化检索文本与摘要，绝不以摘要篡改原文。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from .relations import LegalRelationExtractor


class ChunkGranularity(str, Enum):
    DOCUMENT = "DOCUMENT"
    ARTICLE = "ARTICLE"
    SUBCLAUSE = "SUBCLAUSE"
    SEMANTIC_UNIT = "SEMANTIC_UNIT"
    TABLE = "TABLE"


@dataclass
class ClauseNode:
    """分层知识树节点。"""

    node_id: str
    document_id: str
    granularity: str  # ChunkGranularity
    clause_number: str | None = None
    title: str | None = None
    text: str = ""
    normalized_summary: str = ""

    parent_id: str | None = None
    child_ids: list[str] = field(default_factory=list)
    previous_id: str | None = None
    next_id: str | None = None

    conditions: list[str] = field(default_factory=list)
    exceptions: list[str] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)

    source_node_ids: list[str] = field(default_factory=list)
    source_locator: dict[str, Any] = field(default_factory=dict)
    text_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HierarchicalKnowledgeTree:
    """多粒度合同知识树。"""

    def __init__(
        self,
        document_id: str,
        business_category: str | None = None,
        contract_subtype: str | None = None,
    ):
        self.document_id = document_id
        self.business_category = business_category
        self.contract_subtype = contract_subtype
        self.nodes: dict[str, ClauseNode] = {}
        self.article_index: dict[str, str] = {}  # "第五条" -> node_id
        self.subclause_index: dict[str, str] = {}  # "5.1" -> node_id
        self.root_id: str = f"doc_{document_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "business_category": self.business_category,
            "contract_subtype": self.contract_subtype,
            "root_id": self.root_id,
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "article_index": dict(self.article_index),
            "subclause_index": dict(self.subclause_index),
        }

    @classmethod
    def build_from_clauses(
        cls,
        document_id: str,
        clauses: list[dict[str, Any] | Any],
        table_schemas: list[dict[str, Any]] | None = None,
        relation_extractor: LegalRelationExtractor | None = None,
        business_category: str | None = None,
        contract_subtype: str | None = None,
    ) -> HierarchicalKnowledgeTree:
        """从扁平条款列表与表格抽取物构建分层知识树。"""
        tree = cls(
            document_id,
            business_category=business_category,
            contract_subtype=contract_subtype,
        )
        extractor = relation_extractor or LegalRelationExtractor()

        # 1. 建立 Document 根节点
        root_node = ClauseNode(
            node_id=tree.root_id,
            document_id=document_id,
            granularity=ChunkGranularity.DOCUMENT.value,
            title=f"ContractDocument_{document_id}",
            text="",
        )
        tree.nodes[tree.root_id] = root_node

        article_nodes: dict[str, ClauseNode] = {}
        subclause_nodes: list[ClauseNode] = []
        prev_article_id: str | None = None

        # 2. 遍历条款建立 Article 与 Subclause 节点
        for c in clauses:
            c_dict = c if isinstance(c, dict) else c.to_dict()
            cid = str(c_dict.get("clause_id") or "")
            cnum = c_dict.get("clause_number")
            art_title = c_dict.get("article_title") or "首部"
            text = str(c_dict.get("original_text") or "")
            summary = str(c_dict.get("normalized_summary") or "")
            conds = list(c_dict.get("conditions") or [])
            excs = list(c_dict.get("exceptions") or [])
            snodes = list(c_dict.get("source_node_ids") or [])
            sloc = dict(c_dict.get("source_locator") or {})
            thash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

            # 抽取法律关系
            relations = [r.to_dict() for r in extractor.extract_relations(c_dict, clause_id=cid)]

            # 确保父级 Article 节点存在
            art_key = art_title.split()[0] if art_title else "首部"
            if art_key not in article_nodes:
                art_id = f"art_{document_id}_{hashlib.sha1(art_key.encode()).hexdigest()[:8]}"
                art_node = ClauseNode(
                    node_id=art_id,
                    document_id=document_id,
                    granularity=ChunkGranularity.ARTICLE.value,
                    title=art_title,
                    text=art_title,
                    parent_id=tree.root_id,
                    previous_id=prev_article_id,
                )
                if prev_article_id and prev_article_id in tree.nodes:
                    tree.nodes[prev_article_id].next_id = art_id
                prev_article_id = art_id

                article_nodes[art_key] = art_node
                tree.nodes[art_id] = art_node
                tree.article_index[art_key] = art_id
                tree.nodes[tree.root_id].child_ids.append(art_id)

            parent_art = article_nodes[art_key]
            if not cnum:
                parent_art.text = text
                for c_item in conds:
                    if c_item not in parent_art.conditions:
                        parent_art.conditions.append(c_item)
                for e_item in excs:
                    if e_item not in parent_art.exceptions:
                        parent_art.exceptions.append(e_item)
                parent_art.relations.extend(relations)

            # 创建 Subclause 节点
            sub_node = ClauseNode(
                node_id=cid,
                document_id=document_id,
                granularity=ChunkGranularity.SUBCLAUSE.value,
                clause_number=cnum,
                title=f"{art_title} {cnum or ''}".strip(),
                text=text,
                normalized_summary=summary,
                parent_id=parent_art.node_id,
                conditions=conds,
                exceptions=excs,
                relations=relations,
                source_node_ids=snodes,
                source_locator=sloc,
                text_hash=thash,
            )
            # 建立同级链表连接
            if parent_art.child_ids:
                last_child_id = parent_art.child_ids[-1]
                if last_child_id in tree.nodes:
                    tree.nodes[last_child_id].next_id = cid
                sub_node.previous_id = last_child_id

            parent_art.child_ids.append(cid)
            tree.nodes[cid] = sub_node
            if cnum:
                tree.subclause_index[cnum] = cid
            subclause_nodes.append(sub_node)

        # 3. 挂载 Table 节点（若存在）
        if table_schemas:
            for idx, tbl in enumerate(table_schemas):
                tid = f"tbl_{document_id}_{idx}"
                headers = tbl.get("headers") or []
                tbl_text = f"表格_{idx}: 列名=[{', '.join(headers)}]"
                tbl_node = ClauseNode(
                    node_id=tid,
                    document_id=document_id,
                    granularity=ChunkGranularity.TABLE.value,
                    title=f"表格 {idx}",
                    text=tbl_text,
                    parent_id=tree.root_id,
                    source_locator={"table_index": idx},
                )
                tree.nodes[tid] = tbl_node
                tree.nodes[tree.root_id].child_ids.append(tid)

        return tree

    def get_node(self, node_id: str) -> ClauseNode | None:
        return self.nodes.get(node_id)

    def find_by_clause_reference(self, ref_name: str) -> ClauseNode | None:
        """根据 '第十二条' 或 '5.1' 定位节点。"""
        cleaned = ref_name.strip()
        # 1. 直接匹配子条款编号
        if cleaned in self.subclause_index:
            return self.nodes.get(self.subclause_index[cleaned])
        # 2. 匹配章条名（例如“第十二条”）
        m_art = re.search(r"第[一二三四五六七八九十0-9]+条", cleaned)
        if m_art:
            art_k = m_art.group(0)
            if art_k in self.article_index:
                return self.nodes.get(self.article_index[art_k])
        # 3. 按 title 模糊前缀
        for n in self.nodes.values():
            if n.title and cleaned in n.title:
                return n
        return None

    def expand_parent_child_context(
        self,
        node_id_or_cid: str,
        *,
        include_parent_conditions: bool = True,
        include_referenced_clauses: bool = True,
        max_cross_ref_depth: int = 1,
    ) -> dict[str, Any]:
        """Parent-Child 关联扩展：定位子条款 -> 向上补充父级章条与前提 -> 向外关联引用目标。"""
        node = self.nodes.get(node_id_or_cid)
        if not node:
            # 尝试通过条款编号查找
            node = self.find_by_clause_reference(node_id_or_cid)
        if not node:
            return {"target_node": None, "parent_article": None, "expanded_text": ""}

        parent_node: ClauseNode | None = None
        inherited_conditions: list[str] = []
        inherited_exceptions: list[str] = []

        if node.parent_id and node.parent_id in self.nodes:
            p = self.nodes[node.parent_id]
            if p.granularity == ChunkGranularity.ARTICLE.value:
                parent_node = p
                inherited_conditions.extend(p.conditions)
                inherited_exceptions.extend(p.exceptions)

        # 查找跨条款引用 (CROSS_REFERENCE)
        referenced_nodes: list[ClauseNode] = []
        if include_referenced_clauses and max_cross_ref_depth > 0:
            for rel in node.relations:
                if rel.get("relation_type") == "CROSS_REFERENCE":
                    target_ref = rel.get("target_reference")
                    if target_ref:
                        ref_target_node = self.find_by_clause_reference(target_ref)
                        if ref_target_node and ref_target_node.node_id != node.node_id:
                            referenced_nodes.append(ref_target_node)

        # 构造增强上下文文本（保留原文完整性，标记父级归属）
        parts: list[str] = []
        if parent_node and parent_node.title:
            parts.append(f"【所属章节】{parent_node.title}")
        if inherited_conditions:
            parts.append(f"【前置生效前提】{'；'.join(inherited_conditions)}")
        parts.append(f"【条款正文】{node.text}")
        if inherited_exceptions:
            parts.append(f"【通则免责例外】{'；'.join(inherited_exceptions)}")
        if referenced_nodes:
            ref_texts = [f"{rn.title or rn.node_id}: {rn.text[:60]}…" for rn in referenced_nodes]
            parts.append(f"【援引外部条款】{' | '.join(ref_texts)}")

        return {
            "target_node": node,
            "parent_article": parent_node,
            "inherited_conditions": inherited_conditions,
            "inherited_exceptions": inherited_exceptions,
            "referenced_nodes": referenced_nodes,
            "expanded_context_text": "\n".join(parts),
        }
