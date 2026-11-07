# -*- coding: utf-8 -*-
"""确定性审核规则策略库（Strategy Pattern）。

将原 `review_engine.py` 内嵌的 12 类硬编码确定性检查解耦为独立、单一职责、可单测的 `RuleStrategy` 实现，
保留 100% 原有金额（Decimal）、付款期限、主体一致性、必备条款、管辖、发票、违约金、编号回填等校验语义。
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any

from ...domain.models.review import (
    CheckStatus,
    EvidenceReference,
    Finding,
    FindingSource,
)
from ...domain.value_objects.amounts import chinese_num_to_int, parse_decimal


def _first_clause(clauses: list[dict[str, Any]], pred: Any) -> dict[str, Any] | None:
    for c in clauses:
        if pred(c):
            return c
    return None


def _ref_from_clause(c: dict[str, Any], fallback_doc_id: str = "") -> EvidenceReference:
    return EvidenceReference(
        document_id=c.get("document_id") or fallback_doc_id,
        node_ids=list(c.get("source_node_ids") or []),
        clause_number=c.get("clause_number"),
        original_text=c.get("original_text", ""),
    )


class RuleStrategy(ABC):
    """单条确定性审核规则策略抽象契约。"""

    strategy_id: str = "ABSTRACT_STRATEGY"
    version: int = 1
    is_core: bool = False

    @abstractmethod
    def evaluate(
        self,
        rec: Any,
        clauses: list[dict[str, Any]],
        subtype: str,
    ) -> list[Finding]:
        """对已解析合同记录与归一化条款集合执行确定性评估，返回 Finding 列表。"""


class ContractNumberBackfillStrategy(RuleStrategy):
    strategy_id = "CONTRACT_NUMBER_BACKFILL"
    version = 1
    is_core = True

    def evaluate(
        self, rec: Any, clauses: list[dict[str, Any]], subtype: str
    ) -> list[Finding]:
        out: list[Finding] = []
        node = _first_clause(clauses, lambda c: "合同编号" in c["original_text"])
        if not node:
            out.append(
                Finding(
                    issue_id="",
                    rule_id="CONTRACT_NUMBER_BACKFILL",
                    severity="MEDIUM",
                    category="FIELD_REQUIRED",
                    title="缺少合同编号标识",
                    description="待审文档未找到『合同编号』标识行。证据不足判断是否违规："
                    "若为合同附件或摘录文档可能不含编号，需人工确认。",
                    expected_value="合同编号: <编号>",
                    actual_value=None,
                    check_status=CheckStatus.INSUFFICIENT_EVIDENCE.value,
                    requires_human_review=True,
                )
            )
        else:
            text = node["original_text"]
            m = re.search(r"合同编号[:：]\s*(\S+)", text)
            if not m or not m.group(1).strip():
                out.append(
                    Finding(
                        issue_id="",
                        rule_id="CONTRACT_NUMBER_BACKFILL",
                        severity="MEDIUM",
                        category="FIELD_REQUIRED",
                        title="合同编号为空",
                        description="『合同编号』存在但未填写。来源：《新系统合同审批注意事项》"
                        "『合同编号需自动体现到合同文件里面』",
                        expected_value="已填写的合同编号",
                        actual_value=text.strip(),
                        source_reference=EvidenceReference(
                            rec.template_id,
                            list(node.get("source_node_ids") or []),
                            node.get("clause_number"),
                            text,
                        ),
                        suggestion="在合同首部填写合同编号（编码前缀须对应我方签约主体）",
                    )
                )
        return out


class AmountCaseConsistencyStrategy(RuleStrategy):
    strategy_id = "AMOUNT_CASE_CONSISTENCY"
    version = 1
    is_core = True

    def evaluate(
        self, rec: Any, clauses: list[dict[str, Any]], subtype: str
    ) -> list[Finding]:
        out: list[Finding] = []
        pat = re.compile(
            r"人民币\s*([壹贰叁肆伍陆柒捌玖拾佰仟万亿零一二三四五六七八九十百千万]+)"
            r"\s*圆整[（(]¥\s*([\d,]+(?:\.\d+)?)\s*元[）)]"
        )
        for c in clauses:
            t = c["original_text"]
            m = pat.search(t)
            if not m:
                continue
            cn_raw, num_raw = m.group(1), m.group(2)
            cn_val = chinese_num_to_int(cn_raw)
            num_val = parse_decimal(num_raw)
            if cn_val is None or num_val is None:
                out.append(
                    Finding(
                        issue_id="",
                        rule_id="AMOUNT_CASE_CONSISTENCY",
                        severity="INFO",
                        category="INSUFFICIENT_EVIDENCE",
                        title="金额大小写无法自动解析",
                        description=f"『人民币{cn_raw}圆整（¥{num_raw}元）』中的大写或小写无法解析，"
                        "标记人工核对，不推定不一致",
                        actual_value=t[:80],
                        check_status=CheckStatus.INSUFFICIENT_EVIDENCE.value,
                        requires_human_review=True,
                        source_reference=_ref_from_clause(c, rec.template_id),
                    )
                )
                continue
            if Decimal(cn_val) != num_val:
                out.append(
                    Finding(
                        issue_id="",
                        rule_id="AMOUNT_CASE_CONSISTENCY",
                        severity="HIGH",
                        category="AMOUNT_MISMATCH",
                        title="合同金额大小写不一致",
                        description=f"大写『{cn_raw}』= {cn_val}，小写 ¥{num_raw} = {num_val}，两值不一致",
                        expected_value=f"¥{cn_val}",
                        actual_value=f"¥{num_raw}",
                        suggestion="修正大写或小写金额使两值一致（金额使用 Decimal 精确比较）",
                        source_reference=_ref_from_clause(c, rec.template_id),
                    )
                )
        return out


class CrossAmountConsistencyStrategy(RuleStrategy):
    strategy_id = "CROSS_AMOUNT_CONSISTENCY"
    version = 1
    is_core = True

    def evaluate(
        self, rec: Any, clauses: list[dict[str, Any]], subtype: str
    ) -> list[Finding]:
        out: list[Finding] = []
        pair_re = re.compile(
            r"人民币\s*([壹贰叁肆伍陆柒捌玖拾佰仟万亿零一二三四五六七八九十百千万]+)\s*圆整"
            r"[（(]\s*¥\s*([\d,]+(?:\.\d+)?)\s*元?\s*[）)]"
        )
        found: list[tuple[str, str, dict[str, Any]]] = []
        for c in clauses:
            for m in pair_re.finditer(c["original_text"]):
                found.append((m.group(1), m.group(2), c))
        if len(found) < 2:
            return out
        values: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
        for cn, num, c in found:
            d = parse_decimal(num)
            if d is not None:
                values.setdefault(str(d), []).append((cn, num, c))
        if len(values) > 1:
            keys = list(values)
            sample = values[keys[0]][0][2]
            detail = "；".join(f"{k}（{len(v)}处）" for k, v in values.items())
            out.append(
                Finding(
                    issue_id="",
                    rule_id="AMOUNT_CASE_CONSISTENCY",
                    severity="HIGH",
                    category="AMOUNT_MISMATCH",
                    title="合同内金额表述互相不一致",
                    description=f"合同中出现多个不同的总价金额：{detail}。"
                    "金额以 Decimal 精确比较；请核对费用条款与合计行",
                    expected_value=keys[0],
                    actual_value=" / ".join(keys),
                    suggestion="统一费用总额与合计行金额（大小写一致且各处一致）",
                    execution_mode="DETERMINISTIC",
                    source_reference=_ref_from_clause(sample, rec.template_id),
                )
            )
        return out


class FeeTableIntegrityStrategy(RuleStrategy):
    strategy_id = "FEE_TABLE_TOTAL"
    version = 1
    is_core = False

    def evaluate(
        self, rec: Any, clauses: list[dict[str, Any]], subtype: str
    ) -> list[Finding]:
        out: list[Finding] = []
        pat = re.compile(
            r"合计[（(]?元?[）)]?\s*[|｜]?\s*人民币\s*"
            r"([壹贰叁肆伍陆柒捌玖拾佰仟万亿零一二三四五六七八九十百千万]+)"
            r"\s*圆整[（(]¥\s*([\d,]+(?:\.\d+)?)"
        )
        for c in clauses:
            t = c["original_text"]
            if "合计" not in t or "圆整" not in t:
                continue
            m = pat.search(t)
            if m:
                cn_val = chinese_num_to_int(m.group(1))
                num_val = parse_decimal(m.group(2))
                if (
                    cn_val is not None
                    and num_val is not None
                    and Decimal(cn_val) != num_val
                ):
                    out.append(
                        Finding(
                            issue_id="",
                            rule_id="FEE_TABLE_TOTAL",
                            severity="HIGH",
                            category="AMOUNT_MISMATCH",
                            title="费用表合计大小写不一致",
                            description=f"费用表合计大写= {cn_val}，小写= {num_val}",
                            expected_value=str(cn_val),
                            actual_value=str(num_val),
                            source_reference=_ref_from_clause(c, rec.template_id),
                        )
                    )
        return out


class RequiredClauseStrategy(RuleStrategy):
    strategy_id = "CLAUSE_REQUIRED"
    version = 1
    is_core = True

    def evaluate(
        self, rec: Any, clauses: list[dict[str, Any]], subtype: str
    ) -> list[Finding]:
        out: list[Finding] = []
        cats = {c["clause_category"] for c in clauses}
        for cat, name in [
            ("CONFIDENTIALITY", "保密条款"),
            ("LIABILITY", "违约责任条款"),
            ("EFFECTIVENESS_AND_TERMINATION", "生效与终止条款"),
        ]:
            if cat not in cats:
                out.append(
                    Finding(
                        issue_id="",
                        rule_id=f"CLAUSE_REQUIRED_{cat}",
                        severity="MEDIUM",
                        category="CLAUSE_MISSING",
                        title=f"缺少{name}",
                        description=f"待审文档未发现『{name}』（标准模板 26/26 均含该条款）。"
                        "缺失原因可能是删除或摘录，需人工确认",
                        expected_value=name,
                        actual_value=None,
                        suggestion=f"补充{name}（参照企业标准模板文本）",
                        requires_human_review=True,
                        check_status=CheckStatus.INSUFFICIENT_EVIDENCE.value,
                    )
                )
        if subtype in ("PATENT_FAST_PRESCREEN", "PATENT_APPLICATION"):
            if not any(
                c.get("clause_subcategory") == "PRESCREEN_UNDERTAKING" for c in clauses
            ):
                out.append(
                    Finding(
                        issue_id="",
                        rule_id="PRESCREEN_UNDERTAKING_REQUIRED",
                        severity="HIGH",
                        category="CLAUSE_MISSING",
                        title="缺少重复预审承诺条款",
                        description=f"业务类型 {subtype} 的标准模板含『重复预审…永久取消资格』承诺条款，"
                        "待审文档未发现该条款",
                        expected_value="PRESCREEN_UNDERTAKING 承诺",
                        actual_value=None,
                        suggestion="补充预审承诺条款（含隐瞒预审历史全额赔偿及费用不退约定）",
                    )
                )
        return out


class DisputeJurisdictionStrategy(RuleStrategy):
    strategy_id = "DISPUTE_JURISDICTION_STANDARD"
    version = 1
    is_core = True

    def evaluate(
        self, rec: Any, clauses: list[dict[str, Any]], subtype: str
    ) -> list[Finding]:
        out: list[Finding] = []
        c = _first_clause(
            clauses, lambda x: x["clause_category"] == "DISPUTE_RESOLUTION"
        )
        if not c:
            return out
        t = c["original_text"]
        if "深圳市南山区人民法院" in t:
            return out
        if "乙方所在地" in t:
            out.append(
                Finding(
                    issue_id="",
                    rule_id="DISPUTE_JURISDICTION_STANDARD",
                    severity="HIGH",
                    category="CLAUSE_VARIATION",
                    title="争议管辖条款偏离标准",
                    description="管辖约定为『乙方所在地有管辖权的人民法院』，与 23/26 模板的"
                    "『深圳市南山区人民法院』不一致，管辖法院不确定",
                    expected_value="深圳市南山区人民法院",
                    actual_value="乙方所在地有管辖权的人民法院",
                    suggestion="修改为『深圳市南山区人民法院』（或经法务确认的管辖条款）",
                    requires_human_review=True,
                    source_reference=_ref_from_clause(c, rec.template_id),
                )
            )
        else:
            out.append(
                Finding(
                    issue_id="",
                    rule_id="DISPUTE_JURISDICTION_STANDARD",
                    severity="MEDIUM",
                    category="CLAUSE_VARIATION",
                    title="争议管辖条款与标准文本不一致",
                    description=f"管辖条款未出现标准法院名称，实际文本：{t[:100]}",
                    expected_value="深圳市南山区人民法院",
                    actual_value=t[:100],
                    requires_human_review=True,
                    source_reference=_ref_from_clause(c, rec.template_id),
                )
            )
        return out


class InvoiceTermsStrategy(RuleStrategy):
    strategy_id = "INVOICE_TYPE_CONSISTENCY"
    version = 1
    is_core = False

    def evaluate(
        self, rec: Any, clauses: list[dict[str, Any]], subtype: str
    ) -> list[Finding]:
        out: list[Finding] = []
        inv = _first_clause(clauses, lambda c: "发票" in c["original_text"])
        if not inv:
            return out
        t = inv["original_text"]
        if "增值税专用发票" in t and "6%" in t:
            return out
        out.append(
            Finding(
                issue_id="",
                rule_id="INVOICE_TYPE_CONSISTENCY",
                severity="MEDIUM",
                category="INVOICE_TERM_VARIANT",
                title="发票条款未明示 6% 增值税专用发票",
                description=f"发票条款为变体写法：{t[:110]}。其他模板标准写法为"
                "『服务费增值税专用发票（税率6%）并提供官费官方收据』。"
                "开票类型须与审批流程填报一致，需人工确认口径",
                expected_value="服务费增值税专用发票（税率6%）+ 官费官方收据",
                actual_value=t[:110],
                requires_human_review=True,
                check_status=CheckStatus.NEEDS_MANUAL_REVIEW.value,
                source_reference=_ref_from_clause(inv, rec.template_id),
            )
        )
        return out


class LateFeeStrategy(RuleStrategy):
    strategy_id = "LATE_FEE_RATE"
    version = 1
    is_core = False

    def evaluate(
        self, rec: Any, clauses: list[dict[str, Any]], subtype: str
    ) -> list[Finding]:
        out: list[Finding] = []
        c = _first_clause(
            clauses,
            lambda x: "违约金" in x["original_text"] and "‰" in x["original_text"],
        )
        if not c:
            return out
        t = c["original_text"]
        m = re.search(r"(\d+)\s*‰", t)
        if m and m.group(1) != "3":
            out.append(
                Finding(
                    issue_id="",
                    rule_id="LATE_FEE_RATE",
                    severity="MEDIUM",
                    category="CLAUSE_VARIATION",
                    title="逾期违约金比率偏离模板惯例",
                    description=f"模板惯例为日 3‰，待审文档为日 {m.group(1)}‰（企业惯例值，非法务强制）",
                    expected_value="3‰/日",
                    actual_value=m.group(1) + "‰/日",
                    requires_human_review=True,
                    source_reference=_ref_from_clause(c, rec.template_id),
                )
            )
        return out


class PartyConsistencyStrategy(RuleStrategy):
    strategy_id = "PARTY_ATTACHMENT_CONSISTENCY"
    version = 1
    is_core = True

    def evaluate(
        self, rec: Any, clauses: list[dict[str, Any]], subtype: str
    ) -> list[Finding]:
        out: list[Finding] = []
        table_names: list[str] = []
        for ts in getattr(rec, "table_schemas", None) or []:
            if ts["table_index"] == 0:
                for h in ts["headers"]:
                    if h and any(
                        kw in h for kw in ("公司", "事务所", "研究院", "厂", "中心")
                    ):
                        table_names.append(h.strip())
                    m = re.search(
                        r"(?:甲方|乙方)[:：]\s*([^\s，。；、|]{2,30}?(?:公司|事务所|研究院|厂|中心))",
                        h or "",
                    )
                    if m:
                        table_names.append(m.group(1).strip())
                for f in ts["fields"]:
                    v = f.get("sample_value") or ""
                    if v and any(kw in v for kw in ("公司", "事务所", "研究院")):
                        table_names.append(v.strip())
        text = "\n".join(c["original_text"] for c in clauses) if clauses else ""
        inline = re.findall(
            r"甲方[:：]\s*([^\s，。；、|（）()]{2,30}?(?:公司|事务所|研究院|厂|中心))",
            text,
        )
        for name in inline:
            if not table_names:
                continue
            if not any(name in t or t in name for t in table_names):
                out.append(
                    Finding(
                        issue_id="",
                        rule_id="PARTY_ATTACHMENT_CONSISTENCY",
                        severity="HIGH",
                        category="PARTY_MISMATCH",
                        title="甲方名称与首部/附件不一致",
                        description=f"首部表格主体为『{'、'.join(dict.fromkeys(table_names))[:60]}』，"
                        f"正文出现『{name}』，需与合同附件保持一致",
                        expected_value=table_names[0],
                        actual_value=name,
                        suggestion="统一甲方主体名称（《新系统合同审批注意事项》：我方主体、他方主体需与合同附件一致）",
                        requires_human_review=True,
                    )
                )
        return out


class PaymentDeadlineStrategy(RuleStrategy):
    strategy_id = "PAYMENT_DEADLINE_CHECK"
    version = 1
    is_core = False

    def evaluate(
        self, rec: Any, clauses: list[dict[str, Any]], subtype: str
    ) -> list[Finding]:
        out: list[Finding] = []
        c = _first_clause(
            clauses,
            lambda x: "工作日内" in x["original_text"] and "支付" in x["original_text"],
        )
        if not c:
            return out
        m = re.search(
            r"(\d+|[一二三四五六七八九十]+)\s*个工作日内向乙方支付", c["original_text"]
        )
        if not m:
            return out
        raw = m.group(1)
        days: int | None = int(raw) if raw.isdigit() else chinese_num_to_int(raw)
        if days is not None and days != 3:
            out.append(
                Finding(
                    issue_id="",
                    rule_id="PAYMENT_DEADLINE_CHECK",
                    severity="LOW",
                    category="CLAUSE_VARIATION",
                    title="付款期限偏离模板惯例",
                    description=f"模板惯例为合同生效起 3 个工作日内付款，待审文档为 {raw} 个工作日。"
                    "付款期限可另行约定，仅作偏离提示",
                    expected_value="3 个工作日",
                    actual_value=f"{raw} 个工作日",
                    requires_human_review=True,
                    check_status=CheckStatus.NEEDS_MANUAL_REVIEW.value,
                    source_reference=_ref_from_clause(c, rec.template_id),
                )
            )
        return out


class IdentifierFormatStrategy(RuleStrategy):
    strategy_id = "IDENTIFIER_FORMAT_CHECK"
    version = 1
    is_core = False

    def evaluate(
        self, rec: Any, clauses: list[dict[str, Any]], subtype: str
    ) -> list[Finding]:
        out: list[Finding] = []
        pat = re.compile(r"(专利号|申请号)[:：]?\s*([A-Za-z0-9\.\-]+)")
        for c in clauses:
            for m in pat.finditer(c["original_text"]):
                val = m.group(2)
                if not re.match(
                    r"^(\d{8,14}(\.\d)?|ZL\s*\d{8,14}(\.\d)?|CN\s*\d+)$", val
                ):
                    out.append(
                        Finding(
                            issue_id="",
                            rule_id="IDENTIFIER_FORMAT_CHECK",
                            severity="LOW",
                            category="FIELD_FORMAT",
                            title=f"{m.group(1)}格式疑似异常",
                            description=f"『{m.group(1)}：{val}』不符合常见专利/申请号格式（纯数字 8-14 位可含校验位）。"
                            "格式校验为宽松启发式，不推定违规，需人工核对",
                            expected_value="8-14 位数字（可含 .校验位）",
                            actual_value=val,
                            requires_human_review=True,
                            check_status=CheckStatus.NEEDS_MANUAL_REVIEW.value,
                            source_reference=_ref_from_clause(c, rec.template_id),
                        )
                    )
        return out


class ClauseContradictionStrategy(RuleStrategy):
    strategy_id = "CONTRADICTION_CHECK"
    version = 1
    is_core = False

    def evaluate(
        self, rec: Any, clauses: list[dict[str, Any]], subtype: str
    ) -> list[Finding]:
        out: list[Finding] = []
        pays = [
            c
            for c in clauses
            if "支付" in c["original_text"] and "费用" in c["original_text"]
        ]
        frees = [
            c
            for c in clauses
            if "无需支付" in c["original_text"] or "无偿" in c["original_text"]
        ]
        if pays and frees:
            p, f = pays[0], frees[0]
            out.append(
                Finding(
                    issue_id="",
                    rule_id="CONTRADICTION_CHECK",
                    severity="MEDIUM",
                    category="SEMANTIC_RISK",
                    title="疑似矛盾条款：付费与无偿并存",
                    description=f"存在付款条款『{p['original_text'][:60]}…』与无偿条款『{f['original_text'][:60]}…』。"
                    "语义矛盾判定需 LLM 或人工，本次仅做关键词级提示",
                    actual_value={
                        "payment_clause": p["original_text"][:100],
                        "free_clause": f["original_text"][:100],
                    },
                    requires_human_review=True,
                    check_status=CheckStatus.NEEDS_MANUAL_REVIEW.value,
                    execution_mode="MANUAL_REVIEW",
                    finding_source=FindingSource.DETERMINISTIC.value,
                    source_reference=_ref_from_clause(f, rec.template_id),
                )
            )
        return out


DEFAULT_STRATEGIES: tuple[type[RuleStrategy], ...] = (
    ContractNumberBackfillStrategy,
    AmountCaseConsistencyStrategy,
    CrossAmountConsistencyStrategy,
    FeeTableIntegrityStrategy,
    RequiredClauseStrategy,
    DisputeJurisdictionStrategy,
    InvoiceTermsStrategy,
    LateFeeStrategy,
    PartyConsistencyStrategy,
    PaymentDeadlineStrategy,
    IdentifierFormatStrategy,
    ClauseContradictionStrategy,
)
