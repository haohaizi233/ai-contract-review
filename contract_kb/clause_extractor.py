# -*- coding: utf-8 -*-
"""条款级知识抽取：从文档 AST 切分条款 → 归类 → 归一化摘要（保留原文）→ 差异标签。

原则：
- original_text 一字不改；normalized_summary 由确定性规则生成（非 LLM），并标注生成方式；
- 企业惯例与法律要求分离：is_company_standard 表示"企业模板惯例"，绝不表述为法律强制；
- 未获法务审批：approval_status 默认 PENDING_REVIEW。
"""

from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass, field, asdict

ARTICLE_RE = re.compile(r"^(第[一二三四五六七八九十]+条)\s*(.*)$")
SUB_RE = re.compile(r"^(\d+\.\d+)([\.、]?)\s*(.*)$")

# 条款类别判定：按文章标题 + 内容关键词
CATEGORY_RULES = [
    ("CONTRACT_SUBJECT", ["合同事项"]),
    ("SERVICE_SCOPE", ["服务事项"]),
    ("SERVICE_INSTRUCTION", ["服务指示"]),
    ("PARTY_B_OBLIGATIONS", ["乙方权利与义务"]),
    ("PARTY_A_OBLIGATIONS", ["甲方权利与义务"]),
    ("FEE_AND_PAYMENT", ["费用及支付方式"]),
    ("CONFIDENTIALITY", ["保密"]),
    ("LIABILITY", ["违约责任"]),
    ("DISPUTE_RESOLUTION", ["争议解决"]),
    ("EFFECTIVENESS_AND_TERMINATION", ["合同生效"]),
]

SUBCATEGORY_RULES = [
    ("PAYMENT_DEADLINE", ["工作日内向乙方支付", "一次性支付", "支付上述全部费用"]),
    ("INVOICE_AND_TAX", ["发票", "税率", "官方收据", "印花税"]),
    ("BANK_ACCOUNT", ["账户信息", "账号", "开户行"]),
    ("NOTICE_AND_DELIVERY", ["视为送达", "特快专递", "挂号信"]),
    ("COMPANY_CONTACT", ["专属联系人", "总服务邮箱", "投诉"]),
    ("CANCEL_REFUND", ["中途放弃或取消", "不予退还", "不予返还", "退还甲方"]),
    ("LIQUIDATED_DAMAGES", ["违约金", "日率", "日利率"]),
    ("FORCE_MAJEURE", ["不可抗力"]),
    ("PRESCREEN_UNDERTAKING", ["预审", "永久取消资格"]),
    ("OVER_CLAIM_FEE", ["超权费", "权利要求超过", "说明书超过"]),
    ("ANNUAL_FEE_EXCLUSION", ["授权当年年费", "费减"]),
    ("EXAMINATION_RESPONSE_FEE", ["审查答复"]),
    ("SERVICE_LEVEL", ["工作日内", "交付", "初稿"]),
    ("RISK_DISCLOSURE", ["风险", "在先近似", "显著性"]),
    ("REPORT_DISCLAIMER", ["参考意见", "不具备指导意义"]),
    ("INTELLECTUAL_PROPERTY_OWNERSHIP", ["知识产权均归甲方", "智力成果"]),
    ("TERMINATION_POINT", ["终止于", "之日起终止", "有效期至"]),
]

MONEY_RE = re.compile(
    r"(?:人民币|RMB|¥)\s*([\d,]+(?:\.\d+)?)\s*(?:圆整|元)?|(\d[\d,]*(?:\.\d+)?)\s*元"
)
MONEY_PLACEHOLDER_RE = re.compile(
    r"人民币\s*[壹贰叁肆伍陆柒捌玖拾佰仟万亿零一二三四五六七八九十百千万]*\s*圆整[（(]¥?\s*[元）)]|人民币\s{2,}圆整"
)
RATE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*‰|(\d+(?:\.\d+)?)\s*%")
DAYS_RE = re.compile(r"(\d+|[一二三四五六七八九十]+)\s*个?工作日")
BANK_ACCT_RE = re.compile(r"\d{10,}")
LAW_RE = re.compile(r"《([^》]+)》")


@dataclass
class ClauseRecord:
    clause_id: str
    template_id: str
    clause_number: str | None
    article_title: str | None
    clause_category: str
    clause_subcategory: str | None
    original_text: str
    normalized_summary: str
    summary_method: str
    entities: list = field(default_factory=list)
    conditions: list = field(default_factory=list)
    exceptions: list = field(default_factory=list)
    source_node_ids: list = field(default_factory=list)
    source_locator: dict = field(default_factory=dict)
    is_company_standard: bool = False  # 企业模板惯例（非法律强制）
    legal_requirement: bool = False  # 仅当条文援引法律义务时为 True
    approval_status: str = "PENDING_REVIEW"
    extraction_confidence: float = 1.0
    raw_text: str = ""
    display_text: str = ""
    retrieval_text: str = ""
    text_hash: str = ""
    legacy_clause_id: str = ""

    def to_dict(self):
        return asdict(self)


def _categorize(article_title: str | None, text: str) -> tuple[str, str | None]:
    cat = "OTHER"
    if article_title:
        for c, kws in CATEGORY_RULES:
            if any(k in article_title for k in kws):
                cat = c
                break
    sub = None
    for sc, kws in SUBCATEGORY_RULES:
        if any(k in text for k in kws):
            sub = sc
            break
    return cat, sub


def _extract_entities(text: str) -> list[dict]:
    ents = []
    for m in MONEY_RE.finditer(text):
        val = m.group(1) or m.group(2)
        ents.append({"type": "MONEY", "value": val, "raw": m.group(0)})
    for m in MONEY_PLACEHOLDER_RE.finditer(text):
        ents.append(
            {"type": "MONEY_PLACEHOLDER", "value": None, "raw": m.group(0)[:40]}
        )
    for m in RATE_RE.finditer(text):
        val = (m.group(1) or m.group(2)) + ("‰" if m.group(1) else "%")
        ents.append({"type": "RATE", "value": val, "raw": m.group(0)})
    for m in DAYS_RE.finditer(text):
        ents.append({"type": "WORKDAYS", "value": m.group(1), "raw": m.group(0)})
    for m in BANK_ACCT_RE.finditer(text):
        ents.append({"type": "BANK_ACCOUNT", "value": m.group(0), "raw": m.group(0)})
    for m in LAW_RE.finditer(text):
        ents.append({"type": "LAW_CITATION", "value": m.group(1), "raw": m.group(0)})
    return ents


def _extract_conditions(text: str) -> list[str]:
    conds = []
    for pat in [
        r"自[^，。；]{2,30}起",
        r"如[^，。；]{2,40}[，,]",
        r"若[^，。；]{2,40}[，,]",
        r"在[^，。；]{2,30}后",
        r"收到[^，。；]{2,30}后",
    ]:
        for m in re.finditer(pat, text):
            conds.append(m.group(0))
    return conds[:8]


def _extract_exceptions(text: str) -> list[str]:
    excs = []
    for pat in [
        r"但[^，。；]{2,60}",
        r"除[^，。；]{2,40}外",
        r"另有约定除外",
        r"不包括[^，。；]{2,50}",
        r"不含[^，。；]{2,50}",
        r"除外[。；]",
    ]:
        for m in re.finditer(pat, text):
            excs.append(m.group(0))
    return excs[:8]


def _normalize_summary(text: str, cat: str, sub: str | None) -> tuple[str, str]:
    """确定性归一化摘要：只做压缩与槽位抽取，不做法律解释。"""
    t = re.sub(r"\s+", " ", text).strip()
    money = [a or b for a, b in MONEY_RE.findall(t)]
    days = DAYS_RE.findall(t)
    slots = []
    if money:
        slots.append("金额=" + "/".join(money[:3]))
    if days:
        slots.append("期限=" + "/".join(days[:3]) + "个工作日")
    rate = RATE_RE.search(t)
    if rate:
        slots.append(
            "比率=" + (rate.group(1) or rate.group(2)) + ("‰" if rate.group(1) else "%")
        )
    head = t[:48] + ("…" if len(t) > 48 else "")
    summary = f"[{cat}" + (f"/{sub}" if sub else "") + f"] {head}"
    if slots:
        summary += " {" + "; ".join(slots) + "}"
    return summary, "deterministic_rule_v1"


def extract_clauses(
    parsed_doc, template_id: str, is_company_standard: bool = False
) -> list[ClauseRecord]:
    """从 ParsedDocument 抽取条款记录。段落流按 article / x.y 组装。"""
    records: list[ClauseRecord] = []
    article_title: str | None = None
    buffer: list[str] = []
    buffer_nodes: list[str] = []
    buffer_loc: dict = {}
    buffer_num: str | None = None

    def flush():
        nonlocal buffer, buffer_nodes, buffer_loc, buffer_num
        text = "\n".join(b for b in buffer if b).strip()
        if text:
            cat, sub = _categorize(article_title, text)
            summary, method = _normalize_summary(text, cat, sub)
            thash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            cid = hashlib.sha1(
                f"{template_id}:{article_title}:{buffer_num}:{text[:80]}".encode()
            ).hexdigest()[:12]
            law_cited = bool(LAW_RE.search(text))
            rec = ClauseRecord(
                clause_id=cid,
                template_id=template_id,
                clause_number=buffer_num,
                article_title=article_title,
                clause_category=cat,
                clause_subcategory=sub,
                original_text=text,
                normalized_summary=summary,
                summary_method=method,
                entities=_extract_entities(text),
                conditions=_extract_conditions(text),
                exceptions=_extract_exceptions(text),
                source_node_ids=list(buffer_nodes),
                source_locator=buffer_loc,
                is_company_standard=bool(is_company_standard),
                legal_requirement=law_cited and ("应当" in text or "必须" in text),
                extraction_confidence=0.95 if buffer_num else 0.85,
                raw_text=text,
                display_text=text,
                retrieval_text=f"{article_title or ''} {text}".strip(),
                text_hash=thash,
                legacy_clause_id=cid,
            )
            records.append(rec)
        buffer, buffer_nodes, buffer_loc, buffer_num = [], [], {}, None

    for node in parsed_doc.nodes:
        if node.node_type == "paragraph":
            text = node.text.strip()
            if not text:
                continue
            m_art = ARTICLE_RE.match(text)
            m_sub = SUB_RE.match(text)
            if m_art:
                flush()
                article_title = f"{m_art.group(1)} {m_art.group(2)}".strip()
                buffer = [text]
                buffer_nodes = [node.node_id]
                buffer_loc = node.source_locator
                buffer_num = None
            elif m_sub:
                flush()
                buffer_num = m_sub.group(1)
                buffer = [text]
                buffer_nodes = [node.node_id]
                buffer_loc = node.source_locator
            else:
                if article_title:
                    buffer.append(text)
                    buffer_nodes.append(node.node_id)
                else:
                    # 首部（合同编号/甲乙方/前言）在第一条之前：独立成条款，保证合同编号可抽取
                    cat0, sub0 = _categorize(None, text)
                    if cat0 == "OTHER":
                        cat0 = "FRONT_MATTER"
                    summary0, method0 = _normalize_summary(text, cat0, sub0)
                    thash0 = hashlib.sha256(text.encode("utf-8")).hexdigest()
                    cid0 = hashlib.sha1(
                        f"{template_id}:front:{text[:80]}".encode()
                    ).hexdigest()[:12]
                    records.append(
                        ClauseRecord(
                            clause_id=cid0,
                            template_id=template_id,
                            clause_number=None,
                            article_title=None,
                            clause_category=cat0,
                            clause_subcategory=sub0,
                            original_text=text,
                            normalized_summary=summary0,
                            summary_method=method0,
                            entities=_extract_entities(text),
                            conditions=_extract_conditions(text),
                            exceptions=_extract_exceptions(text),
                            source_node_ids=[node.node_id],
                            source_locator=node.source_locator,
                            is_company_standard=bool(is_company_standard),
                            extraction_confidence=0.8,
                            raw_text=text,
                            display_text=text,
                            retrieval_text=text,
                            text_hash=thash0,
                            legacy_clause_id=cid0,
                        )
                    )
        elif node.node_type == "table_cell":
            # 表格内长文本（如费用备注、超权费规则）也是合同内容，抽为条款
            text = (node.text or "").strip()
            if len(text) < 15:
                continue
            cat0, sub0 = _categorize(node.section, text)
            if cat0 == "OTHER":
                cat0 = "TABLE_NOTE"
            summary0, method0 = _normalize_summary(text, cat0, sub0)
            cid0 = hashlib.sha1(
                f"{template_id}:cell:{node.node_id}:{text[:60]}".encode()
            ).hexdigest()[:12]
            records.append(
                ClauseRecord(
                    clause_id=cid0,
                    template_id=template_id,
                    clause_number=node.clause_number,
                    article_title=node.section,
                    clause_category=cat0,
                    clause_subcategory=sub0,
                    original_text=text,
                    normalized_summary=summary0,
                    summary_method=method0,
                    entities=_extract_entities(text),
                    conditions=_extract_conditions(text),
                    exceptions=_extract_exceptions(text),
                    source_node_ids=[node.node_id],
                    source_locator=node.source_locator,
                    is_company_standard=True,
                    extraction_confidence=0.8,
                )
            )
    flush()
    return records


def extract_table_schemas(parsed_doc, template_id: str) -> list[dict]:
    """抽取表格字段 Schema 候选：列头 + 首行样例 + 填写位。"""
    schemas = []
    for dim in parsed_doc.table_dims:
        ti = dim["table_index"]
        cells = [
            n
            for n in parsed_doc.nodes
            if n.node_type == "table_cell" and n.table_index == ti
        ]
        header_cells = sorted(
            [c for c in cells if c.row_index == 0], key=lambda c: c.column_index
        )
        headers = [c.text.strip() for c in header_cells]
        sample_cells = sorted(
            [c for c in cells if c.row_index == 1], key=lambda c: c.column_index
        )
        samples = [c.text.strip() for c in sample_cells]
        fields = []
        for ci, h in enumerate(headers):
            if not h:
                continue
            sample = samples[ci] if ci < len(samples) else ""
            fields.append(
                {
                    "field_key": f"tbl{ti}_col{ci}",
                    "field_name": h,
                    "data_type": "string",
                    "required_candidate": True,
                    "required_approved": None,
                    "sample_value": sample,
                    "source_node_ids": [header_cells[ci].node_id]
                    if ci < len(header_cells)
                    else [],
                    "validation_rules": [],
                    "semantic_description": f"表格{ti}第{ci}列业务字段",
                }
            )
        schemas.append(
            {
                "template_id": template_id,
                "table_index": ti,
                "row_count": dim["rows"],
                "col_count": dim["cols"],
                "headers": headers,
                "fields": fields,
            }
        )
    return schemas
