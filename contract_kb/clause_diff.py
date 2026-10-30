# -*- coding: utf-8 -*-
"""条款差异定位器（Phase 4）：标准模板条款 vs 待审条款的具体变更识别。

变更类型（不依赖字符串相似度断言法律语义）：
- NUMBER_CHANGE：数字/金额/比率变化（30→300、3‰→1‰、1000→2000 元）
- DEADLINE_CHANGE：期限表达变化（三个工作日→三十个工作日）
- PARTY_CHANGE：责任主体表述变化（甲方→乙方）
- NEGATION_CHANGE：否定词出现/消失（不予退还→应退还、不得→可以）
- OBLIGATION_CHANGE：义务动词变化（应当→可以、必须→可）
- TEXT_DELTA：其余文本差异（仅陈述，不做法律等价判断）

原则：相似度仅用于聚类线索，不作为"法律等价"的断言依据；
所有结论附双方原文，法律效力判断留待人工/法务。
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, asdict

NUM_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
DAYS_RE = re.compile(r"(?:\d+|[一二三四五六七八九十]+)\s*个?\s*工作日")
NEG_WORDS = ["不", "不得", "不予", "不应", "禁止", "无须", "无需", "非因"]
OBLIG_STRONG = ["应当", "必须", "应"]
OBLIG_WEAK = ["可以", "可", "有权", "允许"]
PARTY_RE = re.compile(r"甲方|乙方|委托方|受托方")


@dataclass
class ClauseDelta:
    delta_type: str              # NUMBER_CHANGE | DEADLINE_CHANGE | PARTY_CHANGE |
                                 # NEGATION_CHANGE | OBLIGATION_CHANGE | TEXT_DELTA
    description: str
    standard_text: str
    reviewed_text: str
    standard_span: str = ""
    reviewed_span: str = ""
    affects_rights: bool = True
    legal_effect_note: str = "法律效力判断需人工/法务确认（不以相似度断言等价）"

    def to_dict(self):
        return asdict(self)


def _extract_spans(text: str) -> dict:
    return {
        "numbers": NUM_RE.findall(text),
        "deadlines": DAYS_RE.findall(text),
        "parties": sorted(set(PARTY_RE.findall(text))),
        "negations": [w for w in NEG_WORDS if w in text],
        "strong_oblig": [w for w in OBLIG_STRONG if w in text],
        "weak_oblig": [w for w in OBLIG_WEAK if w in text],
    }


def diff_clauses(standard_text: str, reviewed_text: str) -> list[ClauseDelta]:
    """逐维对比两段条款，输出结构化变更列表。"""
    deltas: list[ClauseDelta] = []
    s, r = _extract_spans(standard_text), _extract_spans(reviewed_text)

    # 数字/金额
    if s["numbers"] != r["numbers"]:
        deltas.append(ClauseDelta(
            "NUMBER_CHANGE",
            f"数值变化：{s['numbers']} → {r['numbers']}",
            standard_text, reviewed_text,
            standard_span=str(s["numbers"]), reviewed_span=str(r["numbers"])))
    # 期限
    if s["deadlines"] != r["deadlines"]:
        deltas.append(ClauseDelta(
            "DEADLINE_CHANGE",
            f"期限表达变化：{s['deadlines']} → {r['deadlines']}",
            standard_text, reviewed_text,
            standard_span=str(s["deadlines"]), reviewed_span=str(r["deadlines"])))
    # 责任主体
    if s["parties"] != r["parties"]:
        deltas.append(ClauseDelta(
            "PARTY_CHANGE",
            f"主体表述变化：{s['parties']} → {r['parties']}",
            standard_text, reviewed_text,
            standard_span=str(s["parties"]), reviewed_span=str(r["parties"])))
    # 否定词
    if s["negations"] != r["negations"]:
        deltas.append(ClauseDelta(
            "NEGATION_CHANGE",
            f"否定词变化：{s['negations']} → {r['negations']}（义务方向可能反转）",
            standard_text, reviewed_text,
            standard_span=str(s["negations"]), reviewed_span=str(r["negations"])))
    # 义务强度
    if (bool(s["strong_oblig"]) != bool(r["strong_oblig"])
            or bool(s["weak_oblig"]) != bool(r["weak_oblig"])):
        deltas.append(ClauseDelta(
            "OBLIGATION_CHANGE",
            f"义务强度变化：强义{s['strong_oblig']}/弱义{s['weak_oblig']} "
            f"→ 强义{r['strong_oblig']}/弱义{r['weak_oblig']}",
            standard_text, reviewed_text))
    # 其余文本差异（陈述性）
    if standard_text.strip() != reviewed_text.strip() and not deltas:
        ratio = difflib.SequenceMatcher(None, standard_text, reviewed_text).ratio()
        deltas.append(ClauseDelta(
            "TEXT_DELTA",
            f"文本差异（相似度 {ratio:.2f} 仅作聚类线索，不判断法律等价）",
            standard_text, reviewed_text))
    elif standard_text.strip() != reviewed_text.strip():
        # 已有结构化变更，附加整体文本差异说明
        deltas.append(ClauseDelta(
            "TEXT_DELTA", "除上述结构化变更外，文本另有差异",
            standard_text, reviewed_text, affects_rights=False))
    return deltas


def diff_clause_sets(standard_clauses: list[dict], reviewed_clauses: list[dict]) -> list[dict]:
    """按条款类别配对，输出逐对变更明细。"""
    std_by_cat: dict[str, list[dict]] = {}
    for c in standard_clauses:
        std_by_cat.setdefault(c.get("clause_category", "OTHER"), []).append(c)
    rev_by_cat: dict[str, list[dict]] = {}
    for c in reviewed_clauses:
        rev_by_cat.setdefault(c.get("clause_category", "OTHER"), []).append(c)

    report = []
    for cat in sorted(set(std_by_cat) | set(rev_by_cat)):
        stds, revs = std_by_cat.get(cat, []), rev_by_cat.get(cat, [])
        if not stds:
            report.append({"clause_category": cat, "status": "NEW_IN_REVIEWED",
                           "deltas": [], "note": "标准模板无此类条款（待审新增）"})
            continue
        if not revs:
            report.append({"clause_category": cat, "status": "MISSING_IN_REVIEWED",
                           "deltas": [], "note": "标准模板含此类条款（待审缺失）",
                           "standard_text": stds[0]["original_text"][:120]})
            continue
        # 逐对比较（首个标准条款 vs 首个待审条款；多条款按顺序对齐）
        pair_deltas: list[dict] = []
        for i in range(max(len(stds), len(revs))):
            s_t = stds[min(i, len(stds) - 1)]["original_text"]
            r_t = revs[min(i, len(revs) - 1)]["original_text"]
            for d in diff_clauses(s_t, r_t):
                dd = d.to_dict()
                dd["standard_node_ids"] = stds[min(i, len(stds) - 1)].get("source_node_ids", [])
                dd["reviewed_node_ids"] = revs[min(i, len(revs) - 1)].get("source_node_ids", [])
                pair_deltas.append(dd)
        report.append({"clause_category": cat,
                       "status": "DIFF" if pair_deltas else "MATCH",
                       "deltas": pair_deltas})
    return report
