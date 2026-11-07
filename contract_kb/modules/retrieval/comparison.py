# -*- coding: utf-8 -*-
"""合同条款结构化差异比对引擎（Structured Clause Comparison Engine）。

核心原则：
1. 语义相关性不等于法律等价性（Cross-Encoder 或向量相似度只能作为候选排序，不可替代法律判断）；
2. 建立六层结构化校验流水线：
   Semantic Relevance -> Entity Consistency -> Numeric Consistency -> Polarity Consistency -> Condition Consistency -> Exception Consistency；
3. 数值比对严格使用 Decimal 精度，精确统一工作日与自然日、百分比与千分比；
4. 否定倒错（不得 vs 可以）与主体反转（甲方 vs 乙方）必须显式标记为硬差异。
"""

from __future__ import annotations

import decimal
import re
from decimal import Decimal
from typing import Any
from pydantic import BaseModel, Field

from .relations import LegalRelationExtractor, LegalRelationType

_CLEAN_RE = re.compile(r"[\s\u3000，。；：、“”‘’（）()《》\-—_/%‰]")


def _normalize_text(text: str) -> str:
    return _CLEAN_RE.sub("", text).lower()


class NumericDifference(BaseModel):
    constraint_type: str  # MONEY, WORKDAYS, CALENDAR_DAYS, RATE
    reviewed_value: str | float
    reference_value: str | float
    reviewed_unit: str
    reference_unit: str
    difference_magnitude: float | None = None
    is_mismatch: bool = False
    description: str = ""


class ClauseComparison(BaseModel):
    """强类型条款结构化比对结果契约。"""

    reviewed_clause_id: str
    reference_clause_ids: list[str] = Field(default_factory=list)
    similarity_score: float | None = None

    conclusion: str  # MATCH, NUMERIC_DIFFERENCE, POLARITY_INVERSION, SUBJECT_DIFFERENCE, CONDITION_DIFFERENCE, EXCEPTION_DIFFERENCE, STRUCTURAL_DIFFERENCE, INSUFFICIENT_EVIDENCE
    polarity_inverted: bool = False

    numeric_differences: list[NumericDifference] = Field(default_factory=list)
    subjects_changed: list[dict[str, Any]] = Field(default_factory=list)
    condition_differences: list[dict[str, Any]] = Field(default_factory=list)
    exception_differences: list[dict[str, Any]] = Field(default_factory=list)

    obligations_changed: list[dict[str, Any]] = Field(default_factory=list)
    rights_changed: list[dict[str, Any]] = Field(default_factory=list)
    evidence_references: list[dict[str, Any]] = Field(default_factory=list)

    requires_human_review: bool = False
    confidence: float = 1.0
    summary_findings: list[str] = Field(default_factory=list)


class ClauseComparisonEngine:
    """结构化条款差异对比引擎。"""

    def __init__(self, relation_extractor: LegalRelationExtractor | None = None):
        self.extractor = relation_extractor or LegalRelationExtractor()

    def _parse_decimal_value(self, val_str: str | float) -> Decimal | None:
        try:
            return Decimal(str(val_str).replace(",", "").strip())
        except (decimal.InvalidOperation, ValueError):
            return None

    def compare_numerics(
        self,
        reviewed_text: str,
        reference_text: str,
    ) -> list[NumericDifference]:
        """使用 Decimal 进行金额、比率与期限的高精度比对。"""
        diffs: list[NumericDifference] = []

        # 0. 提取具体金额
        rev_money = re.findall(
            r"(?:人民币|RMB|¥)?\s*([\d,]+(?:\.\d+)?)\s*(?:元|圆整)", reviewed_text
        )
        ref_money = re.findall(
            r"(?:人民币|RMB|¥)?\s*([\d,]+(?:\.\d+)?)\s*(?:元|圆整)", reference_text
        )
        if rev_money and ref_money:
            m_rev = self._parse_decimal_value(rev_money[0])
            m_ref = self._parse_decimal_value(ref_money[0])
            if m_rev is not None and m_ref is not None and m_rev != m_ref:
                diffs.append(
                    NumericDifference(
                        constraint_type="MONEY",
                        reviewed_value=float(m_rev),
                        reference_value=float(m_ref),
                        reviewed_unit="YUAN",
                        reference_unit="YUAN",
                        difference_magnitude=float(abs(m_rev - m_ref)),
                        is_mismatch=True,
                        description=f"金额差异：待审 {m_rev} 元 vs 标准 {m_ref} 元",
                    )
                )

        # 1. 提取费率/违约金率（统一折算为无量纲小数比对）
        rev_rate_m = re.search(r"(\d+(?:\.\d+)?)\s*(‰|%)", reviewed_text)
        ref_rate_m = re.search(r"(\d+(?:\.\d+)?)\s*(‰|%)", reference_text)
        if rev_rate_m and ref_rate_m:
            v_rev = Decimal(rev_rate_m.group(1)) * (
                Decimal("0.001") if rev_rate_m.group(2) == "‰" else Decimal("0.01")
            )
            v_ref = Decimal(ref_rate_m.group(1)) * (
                Decimal("0.001") if ref_rate_m.group(2) == "‰" else Decimal("0.01")
            )
            if v_rev != v_ref:
                diffs.append(
                    NumericDifference(
                        constraint_type="RATE",
                        reviewed_value=rev_rate_m.group(0),
                        reference_value=ref_rate_m.group(0),
                        reviewed_unit=rev_rate_m.group(2),
                        reference_unit=ref_rate_m.group(2),
                        difference_magnitude=float(abs(v_rev - v_ref)),
                        is_mismatch=True,
                        description=f"违约金率差异：待审 {rev_rate_m.group(0)} vs 标准 {ref_rate_m.group(0)}",
                    )
                )

        # 2. 提取期限天数（严格区分工作日与自然日）
        rev_work_m = re.search(
            r"(\d+|[一二三四五六七八九十]+)\s*个?工作日", reviewed_text
        )
        ref_work_m = re.search(
            r"(\d+|[一二三四五六七八九十]+)\s*个?工作日", reference_text
        )
        rev_cal_m = re.search(
            r"(\d+|[一二三四五六七八九十]+)\s*(?:日|天)(?!工作日)", reviewed_text
        )
        ref_cal_m = re.search(
            r"(\d+|[一二三四五六七八九十]+)\s*(?:日|天)(?!工作日)", reference_text
        )

        # 检查单位错位（工作日 vs 自然日）
        if (rev_work_m and ref_cal_m) or (rev_cal_m and ref_work_m):
            diffs.append(
                NumericDifference(
                    constraint_type="DAY_UNIT_MISMATCH",
                    reviewed_value=(rev_work_m or rev_cal_m).group(0),  # type: ignore
                    reference_value=(ref_work_m or ref_cal_m).group(0),  # type: ignore
                    reviewed_unit="WORKDAYS" if rev_work_m else "CALENDAR_DAYS",
                    reference_unit="WORKDAYS" if ref_work_m else "CALENDAR_DAYS",
                    is_mismatch=True,
                    description="履行期限计算口径差异：一方为工作日，另一方为自然日",
                )
            )
        elif rev_work_m and ref_work_m:
            num_rev = self._parse_decimal_value(rev_work_m.group(1))
            num_ref = self._parse_decimal_value(ref_work_m.group(1))
            if num_rev is not None and num_ref is not None and num_rev != num_ref:
                diffs.append(
                    NumericDifference(
                        constraint_type="WORKDAYS",
                        reviewed_value=float(num_rev),
                        reference_value=float(num_ref),
                        reviewed_unit="WORKDAY",
                        reference_unit="WORKDAY",
                        difference_magnitude=float(abs(num_rev - num_ref)),
                        is_mismatch=True,
                        description=f"工期天数差异：待审 {num_rev} 个工作日 vs 标准 {num_ref} 个工作日",
                    )
                )

        return diffs

    def check_polarity_inversion(
        self,
        reviewed_text: str,
        reference_text: str,
    ) -> bool:
        """检测否定倒错（不得/严禁/禁止 vs 可以/应当/有权）。"""
        prohibit_words = ("不得", "严禁", "禁止", "不可", "切勿", "无权")
        allow_words = ("可以", "有权", "应当", "必须", "应", "得以", "准予")

        rev_has_prohibit = any(w in reviewed_text for w in prohibit_words)
        ref_has_prohibit = any(w in reference_text for w in prohibit_words)

        rev_has_allow = any(w in reviewed_text for w in allow_words)
        ref_has_allow = any(w in reference_text for w in allow_words)

        if rev_has_prohibit and not ref_has_prohibit and ref_has_allow:
            return True
        if ref_has_prohibit and not rev_has_prohibit and rev_has_allow:
            return True
        return False

    def check_asymmetric_party_reversal(
        self,
        reviewed_text: str,
        reference_text: str,
    ) -> tuple[bool, str]:
        """检测非对称保密/权属等法律义务主体倒错（如甲方披露/乙方保密 vs 乙方披露/甲方保密）。"""
        patterns = [
            (
                r"甲方.*?(?:披露|提供).*?乙方.*?(?:保密|承担保密义务)",
                r"乙方.*?(?:披露|提供).*?甲方.*?(?:保密|承担保密义务)",
            ),
            (
                r"乙方.*?(?:披露|提供).*?甲方.*?(?:保密|承担保密义务)",
                r"甲方.*?(?:披露|提供).*?乙方.*?(?:保密|承担保密义务)",
            ),
            (r"知识产权.*?(?:归|归属于).*?甲方", r"知识产权.*?(?:归|归属于).*?乙方"),
            (r"知识产权.*?(?:归|归属于).*?乙方", r"知识产权.*?(?:归|归属于).*?甲方"),
        ]
        for p_rev, p_ref in patterns:
            if re.search(p_rev, reviewed_text) and re.search(p_ref, reference_text):
                return True, "非对称法律义务主体倒错"
        return False, ""

    def compare_clauses(
        self,
        reviewed_clause: dict[str, Any] | Any,
        reference_clause: dict[str, Any] | Any,
        *,
        similarity_score: float | None = None,
    ) -> ClauseComparison:
        """执行全套结构化差异比对。"""
        rev_dict = (
            reviewed_clause
            if isinstance(reviewed_clause, dict)
            else reviewed_clause.to_dict()
        )
        ref_dict = (
            reference_clause
            if isinstance(reference_clause, dict)
            else reference_clause.to_dict()
        )

        rev_id = str(rev_dict.get("clause_id") or "reviewed")
        ref_id = str(ref_dict.get("clause_id") or "reference")
        rev_text = str(rev_dict.get("original_text") or rev_dict.get("text") or "")
        ref_text = str(ref_dict.get("original_text") or ref_dict.get("text") or "")

        findings: list[str] = []
        conclusions: list[str] = []

        # 1. 否定倒错检查 (Polarity Inversion - 最高破坏性差异)
        polarity_inv = self.check_polarity_inversion(rev_text, ref_text)
        if polarity_inv:
            conclusions.append("POLARITY_INVERSION")
            findings.append(
                "【法律极性反转】待审条款与企业标准条款存在正负向禁止/许可倒错！"
            )

        # 1.1 非对称主体倒错检查
        is_asym, asym_desc = self.check_asymmetric_party_reversal(rev_text, ref_text)
        if is_asym:
            conclusions.append("SUBJECT_DIFFERENCE")
            findings.append(f"【主体倒错】{asym_desc}")

        # 1.2 例外但书不对称检查（如“除非经甲方书面同意” vs “不得”）
        rev_has_exception = bool(re.search(r"除非|除.*?外|但书|若经.*?同意", rev_text))
        ref_has_exception = bool(re.search(r"除非|除.*?外|但书|若经.*?同意", ref_text))
        if rev_has_exception != ref_has_exception:
            conclusions.append("EXCEPTION_DIFFERENCE")
            findings.append(
                "【例外条件不对称】待审条款与企业标准条款关于免责例外条件的约定不一致！"
            )

        # 2. 数值精度检查 (Numeric Differences)
        num_diffs = self.compare_numerics(rev_text, ref_text)
        if any(d.is_mismatch for d in num_diffs):
            conclusions.append("NUMERIC_DIFFERENCE")
            for nd in num_diffs:
                findings.append(f"【数值差异】{nd.description}")

        # 3. 法律关系与主体方向比对
        rev_rels = self.extractor.extract_relations(rev_dict, clause_id=rev_id)
        ref_rels = self.extractor.extract_relations(ref_dict, clause_id=ref_id)

        subjects_changed = []
        rev_obligations = [
            r for r in rev_rels if r.relation_type == LegalRelationType.OBLIGATION.value
        ]
        ref_obligations = [
            r for r in ref_rels if r.relation_type == LegalRelationType.OBLIGATION.value
        ]
        if rev_obligations and ref_obligations:
            s_rev = rev_obligations[0].subject
            s_ref = ref_obligations[0].subject
            if s_rev and s_ref and s_rev != s_ref:
                subjects_changed.append(
                    {
                        "reviewed_subject": s_rev,
                        "reference_subject": s_ref,
                        "action": rev_obligations[0].action_or_content,
                    }
                )
                conclusions.append("SUBJECT_DIFFERENCE")
                findings.append(
                    f"【义务主体错位】待审合同由【{s_rev}】履行，企业标准模板由【{s_ref}】履行！"
                )

        # 4. 生效条件与例外比对
        cond_diffs = []
        rev_conds = list(rev_dict.get("conditions") or [])
        ref_conds = list(ref_dict.get("conditions") or [])
        if rev_conds != ref_conds and (rev_conds or ref_conds):
            cond_diffs.append(
                {
                    "reviewed_conditions": rev_conds,
                    "reference_conditions": ref_conds,
                }
            )
            conclusions.append("CONDITION_DIFFERENCE")
            findings.append(
                f"【生效前提差异】待审条件: {rev_conds or '无'} vs 标准条件: {ref_conds or '无'}"
            )

        exc_diffs = []
        rev_excs = list(rev_dict.get("exceptions") or [])
        ref_excs = list(ref_dict.get("exceptions") or [])
        if rev_excs != ref_excs and (rev_excs or ref_excs):
            exc_diffs.append(
                {
                    "reviewed_exceptions": rev_excs,
                    "reference_exceptions": ref_excs,
                }
            )
            conclusions.append("EXCEPTION_DIFFERENCE")
            findings.append(
                f"【免责范围差异】待审例外: {rev_excs or '无'} vs 标准例外: {ref_excs or '无'}"
            )

        # 确定主结论
        if "POLARITY_INVERSION" in conclusions:
            final_conclusion = "POLARITY_INVERSION"
        elif "NUMERIC_DIFFERENCE" in conclusions:
            final_conclusion = "NUMERIC_DIFFERENCE"
        elif "SUBJECT_DIFFERENCE" in conclusions:
            final_conclusion = "SUBJECT_DIFFERENCE"
        elif "CONDITION_DIFFERENCE" in conclusions:
            final_conclusion = "CONDITION_DIFFERENCE"
        elif "EXCEPTION_DIFFERENCE" in conclusions:
            final_conclusion = "EXCEPTION_DIFFERENCE"
        else:
            final_conclusion = (
                "MATCH"
                if _normalize_text(rev_text) == _normalize_text(ref_text)
                else "STRUCTURAL_DIFFERENCE"
            )

        requires_review = final_conclusion != "MATCH"

        return ClauseComparison(
            reviewed_clause_id=rev_id,
            reference_clause_ids=[ref_id],
            similarity_score=similarity_score,
            conclusion=final_conclusion,
            polarity_inverted=polarity_inv,
            numeric_differences=num_diffs,
            subjects_changed=subjects_changed,
            condition_differences=cond_diffs,
            exception_differences=exc_diffs,
            obligations_changed=[r.to_dict() for r in rev_obligations],
            rights_changed=[
                r.to_dict()
                for r in rev_rels
                if r.relation_type == LegalRelationType.RIGHT.value
            ],
            evidence_references=[{"clause_id": ref_id, "text": ref_text[:80]}],
            requires_human_review=requires_review,
            confidence=1.0 if (num_diffs or polarity_inv or subjects_changed) else 0.85,
            summary_findings=findings,
        )
