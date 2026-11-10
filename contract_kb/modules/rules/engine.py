# -*- coding: utf-8 -*-
"""规则执行器（Phase 1.1）：确定性规则真正执行。

设计约束：
- rule_type → 执行器显式映射；执行器仅消费声明式 parameters，**禁止 eval/exec 动态执行用户代码**；
- 仅 APPROVED 且在生效期内的规则参与强制检查；DRAFT / 过期 / 未来生效 → SKIP（记录原因）；
- 支持版本、优先级、例外条件（exceptions）、适用合同类型；
- 每次执行留存 rule_id、version、result、evidence（node_ids/clause_number）。

规则结果枚举：
  PASS / FAIL / NOT_APPLICABLE / SKIPPED_DRAFT / SKIPPED_NOT_EFFECTIVE /
  SKIPPED_TYPE_MISMATCH / SKIPPED_EXCEPTION / MANUAL_REQUIRED
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from typing import Callable

from pydantic import BaseModel, Field


class RuleSpec(BaseModel):
    """规则元模型（Layer C）。"""

    rule_id: str
    version: int = 1
    name: str = ""
    rule_type: str
    execution_mode: str = (
        "DETERMINISTIC"  # DETERMINISTIC | LLM_ASSISTED | MANUAL_REVIEW
    )
    severity: str = "MEDIUM"
    priority: int = 100  # 数值小优先
    contract_types: list[str] = Field(default_factory=lambda: ["*"])
    parameters: dict = Field(default_factory=dict)
    exceptions: list[dict] = Field(
        default_factory=list
    )  # 例外条件：{"when_clause_contains": "...", "reason": "..."}
    source_clauses: list = Field(default_factory=list)
    status: str = "DRAFT"  # DRAFT | APPROVED | RETIRED
    approved_by: str | None = None
    effective_at: float | None = None
    effective_until: float | None = None


@dataclass
class RuleExecution:
    rule_id: str
    rule_version: int
    rule_type: str
    result: str
    severity: str
    message: str = ""
    evidence: dict = field(default_factory=dict)
    executed_at: float = field(default_factory=time.time)

    def to_dict(self):
        return asdict(self)


@dataclass
class ReviewContext:
    """执行器输入：待审文档的结构化视图。"""

    document_id: str
    subtype: str
    category: str
    clauses: list[dict]  # {clause_id, clause_category, clause_subcategory,
    #  clause_number, original_text, source_node_ids}
    fields: dict = field(default_factory=dict)

    def by_category(self, cat: str) -> list[dict]:
        return [c for c in self.clauses if c.get("clause_category") == cat]

    def by_subcategory(self, sub: str) -> list[dict]:
        return [c for c in self.clauses if c.get("clause_subcategory") == sub]

    def all_text(self) -> str:
        return "\n".join(c["original_text"] for c in self.clauses)


# ---------------- 执行器实现（声明式，无 eval） ----------------


def _exec_field_required(spec: RuleSpec, ctx: ReviewContext) -> RuleExecution:
    """FIELD_REQUIRED：参数 {keyword|clause_category|clause_subcategory} 存在性。"""
    p = spec.parameters
    target = None
    if "clause_subcategory" in p:
        found = ctx.by_subcategory(p["clause_subcategory"])
    elif "clause_category" in p:
        found = ctx.by_category(p["clause_category"])
    elif "keyword" in p:
        found = [c for c in ctx.clauses if p["keyword"] in c["original_text"]]
    else:
        return RuleExecution(
            spec.rule_id,
            spec.version,
            spec.rule_type,
            "MANUAL_REQUIRED",
            spec.severity,
            "参数不足：需 keyword/clause_category/clause_subcategory",
        )
    target = found
    if target:
        return RuleExecution(
            spec.rule_id,
            spec.version,
            spec.rule_type,
            "PASS",
            spec.severity,
            f"必备项存在（{len(target)} 处）",
            {"node_ids": target[0].get("source_node_ids", [])},
        )
    return RuleExecution(
        spec.rule_id,
        spec.version,
        spec.rule_type,
        "FAIL",
        spec.severity,
        f"缺少必备项：{p}",
        {},
    )


def _exec_clause_required(spec: RuleSpec, ctx: ReviewContext) -> RuleExecution:
    return _exec_field_required(spec, ctx)


def _exec_clause_variation(spec: RuleSpec, ctx: ReviewContext) -> RuleExecution:
    """CLAUSE_VARIATION：参数 {clause_category, must_contain[], must_not_contain[]}。"""
    p = spec.parameters
    cat = p.get("clause_category")
    targets = ctx.by_category(cat) if cat else ctx.clauses
    if not targets:
        return RuleExecution(
            spec.rule_id,
            spec.version,
            spec.rule_type,
            "FAIL",
            spec.severity,
            f"未找到条款类别 {cat}",
            {},
        )
    text = "\n".join(t["original_text"] for t in targets)
    for kw in p.get("must_contain", []):
        if kw not in text:
            return RuleExecution(
                spec.rule_id,
                spec.version,
                spec.rule_type,
                "FAIL",
                spec.severity,
                f"条款缺少关键要素『{kw}』",
                {
                    "node_ids": targets[0].get("source_node_ids", []),
                    "clause_number": targets[0].get("clause_number"),
                },
            )
    for kw in p.get("must_not_contain", []):
        if kw in text:
            return RuleExecution(
                spec.rule_id,
                spec.version,
                spec.rule_type,
                "FAIL",
                spec.severity,
                f"条款含禁用要素『{kw}』",
                {"node_ids": targets[0].get("source_node_ids", [])},
            )
    return RuleExecution(
        spec.rule_id,
        spec.version,
        spec.rule_type,
        "PASS",
        spec.severity,
        "条款要素符合",
        {"node_ids": targets[0].get("source_node_ids", [])},
    )


def _exec_field_format(spec: RuleSpec, ctx: ReviewContext) -> RuleExecution:
    """FIELD_FORMAT：参数 {keyword, pattern}。"""
    p = spec.parameters
    pat = re.compile(p["pattern"])
    hits = [c for c in ctx.clauses if p.get("keyword", "") in c["original_text"]]
    for c in hits:
        for m in re.finditer(
            re.escape(p["keyword"]) + r"[:：]?\s*([A-Za-z0-9\.\-]+)", c["original_text"]
        ):
            if not pat.match(m.group(1)):
                return RuleExecution(
                    spec.rule_id,
                    spec.version,
                    spec.rule_type,
                    "FAIL",
                    spec.severity,
                    f"『{p['keyword']}：{m.group(1)}』格式不符 {p['pattern']}",
                    {"node_ids": c.get("source_node_ids", [])},
                )
    return RuleExecution(
        spec.rule_id,
        spec.version,
        spec.rule_type,
        "PASS" if hits else "NOT_APPLICABLE",
        spec.severity,
        "格式符合/文档无该字段",
        {},
    )


def _exec_financial(spec: RuleSpec, ctx: ReviewContext) -> RuleExecution:
    """FINANCIAL_CALCULATION：参数 {check: amount_case_consistency | fee_table_total}（Decimal）。"""
    from ...domain.value_objects.amounts import (
        chinese_num_to_int as _cn_num_to_int,
        parse_decimal as dec,
    )

    check = spec.parameters.get("check", "amount_case_consistency")
    pair_re = re.compile(
        r"人民币\s*([壹贰叁肆伍陆柒捌玖拾佰仟万亿零一二三四五六七八九十百千万]+)\s*圆整"
        r"[（(]\s*¥\s*([\d,]+(?:\.\d+)?)"
    )
    pairs = []
    for c in ctx.clauses:
        for m in pair_re.finditer(c["original_text"]):
            pairs.append((m.group(2), c))
    if check == "amount_case_consistency":
        pass

        values: dict[str, list] = {}
        for num, c in pairs:
            d = dec(num)
            if d is not None:
                values.setdefault(str(d), []).append(c)
        if len(values) > 1:
            return RuleExecution(
                spec.rule_id,
                spec.version,
                spec.rule_type,
                "FAIL",
                spec.severity,
                f"金额不一致：{list(values)}",
                {"node_ids": values[list(values)[0]][0].get("source_node_ids", [])},
            )
        # 大写与小写交叉核对（Decimal）
        pair_full = re.compile(
            r"人民币\s*([壹贰叁肆伍陆柒捌玖拾佰仟万亿零一二三四五六七八九十百千万]+)\s*圆整"
            r"[（(]\s*¥\s*([\d,]+(?:\.\d+)?)"
        )
        for c in ctx.clauses:
            for m in pair_full.finditer(c["original_text"]):
                cn_val = _cn_num_to_int(m.group(1))
                num_val = dec(m.group(2))
                if (
                    cn_val is not None
                    and num_val is not None
                    and Decimal(cn_val) != num_val
                ):
                    return RuleExecution(
                        spec.rule_id,
                        spec.version,
                        spec.rule_type,
                        "FAIL",
                        spec.severity,
                        f"大小写金额不一致：大写={cn_val} 小写={num_val}",
                        {"node_ids": c.get("source_node_ids", [])},
                    )
        return RuleExecution(
            spec.rule_id,
            spec.version,
            spec.rule_type,
            "PASS" if pairs else "NOT_APPLICABLE",
            spec.severity,
            "金额一致/无金额表述",
            {},
        )
    return RuleExecution(
        spec.rule_id,
        spec.version,
        spec.rule_type,
        "MANUAL_REQUIRED",
        spec.severity,
        f"检查项 {check} 需外部数据或人工核对",
        {},
    )


def _exec_cross_field(spec: RuleSpec, ctx: ReviewContext) -> RuleExecution:
    """CROSS_FIELD_CONSISTENCY：参数 {check: invoice_type | party_attachment | ...}。"""
    check = spec.parameters.get("check", "")
    if check == "invoice_type":
        inv = [c for c in ctx.clauses if "发票" in c["original_text"]]
        if not inv:
            return RuleExecution(
                spec.rule_id,
                spec.version,
                spec.rule_type,
                "NOT_APPLICABLE",
                spec.severity,
                "无发票条款",
            )
        t = inv[0]["original_text"]
        expect = spec.parameters.get("must_contain", ["增值税专用发票", "6%"])
        missing = [k for k in expect if k not in t]
        if missing:
            return RuleExecution(
                spec.rule_id,
                spec.version,
                spec.rule_type,
                "FAIL",
                spec.severity,
                f"发票条款缺少要素 {missing}",
                {"node_ids": inv[0].get("source_node_ids", [])},
            )
        return RuleExecution(
            spec.rule_id,
            spec.version,
            spec.rule_type,
            "PASS",
            spec.severity,
            "发票要素符合",
            {"node_ids": inv[0].get("source_node_ids", [])},
        )
    return RuleExecution(
        spec.rule_id,
        spec.version,
        spec.rule_type,
        "MANUAL_REQUIRED",
        spec.severity,
        f"交叉校验 {check} 需外部业务系统数据",
        {},
    )


def _exec_manual(spec: RuleSpec, ctx: ReviewContext) -> RuleExecution:
    return RuleExecution(
        spec.rule_id,
        spec.version,
        spec.rule_type,
        "MANUAL_REQUIRED",
        spec.severity,
        spec.parameters.get("note", "需人工复核"),
        {},
    )


# 显式映射表（禁止动态执行）
EXECUTORS: dict[str, Callable[[RuleSpec, ReviewContext], RuleExecution]] = {
    "FIELD_REQUIRED": _exec_field_required,
    "CLAUSE_REQUIRED": _exec_clause_required,
    "CLAUSE_VARIATION": _exec_clause_variation,
    "FIELD_FORMAT": _exec_field_format,
    "FINANCIAL_CALCULATION": _exec_financial,
    "CROSS_FIELD_CONSISTENCY": _exec_cross_field,
    "BUSINESS_CONSTRAINT": _exec_manual,
    "STYLE_REQUIREMENT": _exec_manual,
    "MANUAL_REVIEW": _exec_manual,
    "SEMANTIC_RISK": _exec_manual,
}


class RuleEngine:
    """规则引擎：筛选生效规则 → 例外判定 → 执行 → 留痕。"""

    def __init__(self, now: float | None = None):
        self.now = now or time.time()

    def select_enforceable(
        self, rules: list[RuleSpec], ctx: ReviewContext
    ) -> tuple[list[RuleSpec], list[RuleExecution]]:
        enforceable, skipped = [], []
        for r in sorted(rules, key=lambda x: (x.priority, x.rule_id)):
            if r.status != "APPROVED":
                skipped.append(
                    RuleExecution(
                        r.rule_id,
                        r.version,
                        r.rule_type,
                        "SKIPPED_DRAFT",
                        r.severity,
                        "规则未审批，不参与强制检查",
                    )
                )
                continue
            if r.effective_at and self.now < r.effective_at:
                skipped.append(
                    RuleExecution(
                        r.rule_id,
                        r.version,
                        r.rule_type,
                        "SKIPPED_NOT_EFFECTIVE",
                        r.severity,
                        "规则尚未生效",
                    )
                )
                continue
            if r.effective_until and self.now > r.effective_until:
                skipped.append(
                    RuleExecution(
                        r.rule_id,
                        r.version,
                        r.rule_type,
                        "SKIPPED_NOT_EFFECTIVE",
                        r.severity,
                        "规则已过期",
                    )
                )
                continue
            if "*" not in r.contract_types and ctx.subtype not in r.contract_types:
                skipped.append(
                    RuleExecution(
                        r.rule_id,
                        r.version,
                        r.rule_type,
                        "SKIPPED_TYPE_MISMATCH",
                        r.severity,
                        f"不适用于 {ctx.subtype}",
                    )
                )
                continue
            if self._hit_exception(r, ctx):
                skipped.append(
                    RuleExecution(
                        r.rule_id,
                        r.version,
                        r.rule_type,
                        "SKIPPED_EXCEPTION",
                        r.severity,
                        "命中例外条件",
                    )
                )
                continue
            enforceable.append(r)
        return enforceable, skipped

    @staticmethod
    def _hit_exception(rule: RuleSpec, ctx: ReviewContext) -> bool:
        """例外条件：声明式匹配（when_clause_contains / when_field_equals），不执行代码。"""
        for exc in rule.exceptions:
            kw = exc.get("when_clause_contains")
            if kw and kw in ctx.all_text():
                return True
            fk, fv = exc.get("when_field_equals", (None, None))
            if fk and ctx.fields.get(fk) == fv:
                return True
        return False

    def execute(self, rules: list[RuleSpec], ctx: ReviewContext) -> list[RuleExecution]:
        enforceable, skipped = self.select_enforceable(rules, ctx)
        results = list(skipped)
        for r in enforceable:
            fn = EXECUTORS.get(r.rule_type)
            if fn is None:
                results.append(
                    RuleExecution(
                        r.rule_id,
                        r.version,
                        r.rule_type,
                        "MANUAL_REQUIRED",
                        r.severity,
                        f"无该 rule_type 的执行器：{r.rule_type}",
                    )
                )
                continue
            results.append(fn(r, ctx))
        return results
