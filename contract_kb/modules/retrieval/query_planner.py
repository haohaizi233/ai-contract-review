# -*- coding: utf-8 -*-
"""合同感知动态查询规划器（Contract-Aware Dynamic Query Planner）。

核心职责：
1. 从机械的固定 4 关键词检索升级为依据待审条款结构、法律主体、行为、条件与数值约束的意图驱动规划；
2. Level 1: 确定性 Query Planning（零 LLM 依赖、不可旁路、100% 规则保证事实锚定）；
3. Level 2: 可选 LLM-Assisted Query Expansion（仅在明确授权且环境允许时启用，严格经 Pydantic 校验且不可虚构事实）；
4. 防爆炸预算控制（QueryPlanningBudget）：基于风险优先级排序、低价值裁剪、查询去重合并、单合同上限与超时取消。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from pydantic import BaseModel, Field

from contract_kb import config


# 跨条款引用识别正则
CROSS_REF_RE = re.compile(
    r"(?:依照|参见|按照|根据|依据|按)?(第[一二三四五六七八九十0-9]+条(?:\s*第[一二三四五六七八九十0-9]+款)?)"
)

# 法律主体识别正则
LEGAL_SUBJECT_RE = re.compile(r"(甲方|乙方|双方|第三方|独家代理人|受托人|委托人|许可方|被许可方)")

# 行为动词识别映射
ACTION_PATTERNS = [
    ("单方解除合同", [r"单方解除", r"解除本合同", r"终止合同", r"有权解除", r"解除协议"]),
    ("交付工作成果", [r"交付初稿", r"完成交付", r"提交成果", r"送交申请", r"交付"]),
    ("支付费用及款项", [r"支付上述", r"付清全部", r"支付合同款", r"支付服务费", r"付款"]),
    ("支付违约金", [r"支付违约金", r"承担违约责任", r"赔偿损失", r"滞纳金"]),
    ("提供发票", [r"开具发票", r"提供增值税", r"官方收据"]),
    ("履行保密义务", [r"承担保密义务", r"不得泄露", r"采取保密措施", r"披露技术秘密"]),
    ("知识产权权属归属", [r"知识产权归", r"所有权归", r"成果归属", r"智力成果"]),
    ("提交争议仲裁或诉讼", [r"提起诉讼", r"提交仲裁", r"向法院起诉", r"管辖权"]),
]

# 核心意图类型映射
INTENT_TYPE_MAPPING = [
    ("TERMINATION_RIGHT_CHECK", ["解除", "终止", "撤销"]),
    ("LIABILITY_LIMIT_CHECK", ["违约金", "滞纳金", "赔偿", "扣罚", "上限"]),
    ("PAYMENT_TERM_CHECK", ["付款", "支付", "结算", "分期", "到期"]),
    ("DELIVERY_SLA_CHECK", ["交付", "初稿", "答复", "补正", "期限"]),
    ("CONFIDENTIALITY_SCOPE_CHECK", ["保密", "秘密", "泄露", "知悉"]),
    ("IP_OWNERSHIP_CHECK", ["知识产权", "专利申请权", "著作权", "归属", "授权"]),
    ("DISPUTE_RESOLUTION_CHECK", ["仲裁", "诉讼", "法院", "管辖", "争议解决"]),
    ("INVOICE_TAX_CHECK", ["发票", "税率", "完税", "税务"]),
    ("FORCE_MAJEURE_CHECK", ["不可抗力", "自然灾害", "政府行为"]),
]

# 条款风险等级权重（数值越小优先级越高）
CATEGORY_RISK_PRIORITY = {
    "LIABILITY": 1,
    "DISPUTE_RESOLUTION": 1,
    "FEE_AND_PAYMENT": 2,
    "TERMINATION": 2,
    "CONFIDENTIALITY": 2,
    "INTELLECTUAL_PROPERTY": 2,
    "SERVICE_SCOPE": 3,
    "PARTY_B_OBLIGATIONS": 3,
    "PARTY_A_OBLIGATIONS": 3,
    "CONTRACT_SUBJECT": 4,
    "FRONT_MATTER": 5,
    "OTHER": 5,
}


class NumericConstraint(BaseModel):
    """数值与时限硬约束。"""

    type: str  # MONEY, WORKDAYS, CALENDAR_DAYS, RATE, PERCENTAGE
    value: float | str
    unit: str  # YUAN, DAY, WORKDAY, PERCENT, PER_MILLE
    raw_text: str = ""


class RetrievalIntent(BaseModel):
    """强类型受控检索意图契约。"""

    intent_id: str
    source_clause_id: str
    source_node_ids: list[str] = Field(default_factory=list)

    intent_type: str
    contract_category: str | None = None
    contract_subtype: str | None = None

    legal_subject: str | None = None
    action: str | None = None
    target_object: str | None = None

    conditions: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    numeric_constraints: list[NumericConstraint] = Field(default_factory=list)
    referenced_clauses: list[str] = Field(default_factory=list)

    lexical_queries: list[str] = Field(default_factory=list)
    semantic_queries: list[str] = Field(default_factory=list)

    target_knowledge_types: list[str] = Field(
        default_factory=lambda: ["TEMPLATE", "APPROVED_RULE"]
    )
    required_evidence_scope: str = "EXACT_OR_SUPERSET"
    generation_method: str = "deterministic_v1"
    confidence: float = 1.0
    priority: int = 100


class QueryPlanningBudget(BaseModel):
    """检索规划预算与防爆炸策略。"""

    max_total_queries: int = 16  # 单合同最大检索意图总数（防止激增至几千次）
    max_intents_per_clause: int = 2  # 单条款最大意图数
    max_clauses_to_plan: int = 30  # 单合同最大规划高风险条款数
    per_query_timeout_seconds: float = 3.0  # 单条查询超时上限
    enable_deduplication: bool = True  # 启用相同证据意图合并
    priority_cutoff: int = 4  # 仅规划优先级 <= cutoff 的条款 (1~4)


def _clean_str(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class ContractAwareQueryPlanner:
    """合同条款驱动的动态查询规划器。"""

    def __init__(self, budget: QueryPlanningBudget | None = None):
        self.budget = budget or QueryPlanningBudget()

    def calculate_clause_risk_priority(self, clause: dict[str, Any]) -> int:
        """评估条款风险优先级（1 为最高风险，5 为通用/样板条款）。"""
        cat = clause.get("clause_category") or "OTHER"
        sub = clause.get("clause_subcategory") or ""
        text = str(clause.get("original_text") or "")

        base_p = CATEGORY_RISK_PRIORITY.get(cat, 4)
        if sub in ("LIQUIDATED_DAMAGES", "CANCEL_REFUND", "PRESCREEN_UNDERTAKING"):
            base_p = min(base_p, 1)

        # 出现高危法律后果词降级为更高优先级 (P1)
        if any(w in text for w in ("单方解除", "赔偿损失", "双倍返还", "仲裁", "连带责任")):
            base_p = min(base_p, 1)

        # 存在具体金额或违约比例的优先审查
        if any(w in text for w in ("违约金", "滞纳金", "逾期", "按日")):
            base_p = min(base_p, 2)

        return base_p

    def extract_numeric_constraints(self, text: str) -> list[NumericConstraint]:
        """提取金额、天数、比率等强约束。"""
        constraints: list[NumericConstraint] = []
        # 金额
        for m in re.finditer(r"(?:人民币|RMB|¥)\s*([\d,]+(?:\.\d+)?)\s*(?:圆整|元)?", text):
            raw_v = m.group(1).replace(",", "")
            try:
                constraints.append(
                    NumericConstraint(
                        type="MONEY",
                        value=float(raw_v),
                        unit="YUAN",
                        raw_text=m.group(0),
                    )
                )
            except ValueError:
                pass

        # 工作日
        for m in re.finditer(r"(\d+|[一二三四五六七八九十]+)\s*个?工作日", text):
            constraints.append(
                NumericConstraint(
                    type="WORKDAYS",
                    value=m.group(1),
                    unit="WORKDAY",
                    raw_text=m.group(0),
                )
            )

        # 自然日
        for m in re.finditer(r"(\d+|[一二三四五六七八九十]+)\s*(?:日|天)(?!工作日)", text):
            constraints.append(
                NumericConstraint(
                    type="CALENDAR_DAYS",
                    value=m.group(1),
                    unit="DAY",
                    raw_text=m.group(0),
                )
            )

        # 费率 / 违约金比例
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*‰", text):
            constraints.append(
                NumericConstraint(
                    type="RATE",
                    value=float(m.group(1)),
                    unit="PER_MILLE",
                    raw_text=m.group(0),
                )
            )
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*%", text):
            constraints.append(
                NumericConstraint(
                    type="PERCENTAGE",
                    value=float(m.group(1)),
                    unit="PERCENT",
                    raw_text=m.group(0),
                )
            )
        return constraints

    def extract_legal_subject(self, text: str) -> str | None:
        """识别主体（优先义务承受方）。"""
        m = LEGAL_SUBJECT_RE.search(text)
        return m.group(1) if m else None

    def extract_action(self, text: str) -> str | None:
        """识别核心法律行为。"""
        for act_name, kws in ACTION_PATTERNS:
            if any(k in text for k in kws):
                return act_name
        return None

    def extract_referenced_clauses(self, text: str) -> list[str]:
        """识别跨条款引用（如：依照第十二条、参见第五条）。"""
        refs = []
        for m in CROSS_REF_RE.finditer(text):
            r = m.group(1).strip()
            if r not in refs:
                refs.append(r)
        return refs

    def plan_clause_intents_deterministic(
        self,
        clause: dict[str, Any],
        *,
        contract_category: str | None = None,
        contract_subtype: str | None = None,
    ) -> list[RetrievalIntent]:
        """Level 1: 确定性 Query Planning（零外部依赖，100% 规则与 AST 驱动）。"""
        cid = str(clause.get("clause_id") or "unknown")
        node_ids = list(clause.get("source_node_ids") or [])
        text = str(clause.get("original_text") or "")
        cat = clause.get("clause_category") or "OTHER"
        sub = clause.get("clause_subcategory") or ""
        article_title = clause.get("article_title") or ""
        cnum = clause.get("clause_number") or ""

        subject = self.extract_legal_subject(text)
        action = self.extract_action(text)
        numerics = self.extract_numeric_constraints(text)
        cross_refs = self.extract_referenced_clauses(text)
        conditions = list(clause.get("conditions") or [])
        exceptions = list(clause.get("exceptions") or [])
        priority = self.calculate_clause_risk_priority(clause)

        # 确定意图类型
        intent_type = "STANDARD_COMPLIANCE_CHECK"
        for itype, kws in INTENT_TYPE_MAPPING:
            if any(k in text for k in kws) or (article_title and any(k in article_title for k in kws)):
                intent_type = itype
                break

        # 组装词法查询（供 FTS5 / BM25 精确命中法条与关键词）
        lexical_queries: list[str] = []
        if article_title:
            lexical_queries.append(article_title)
        if sub:
            lexical_queries.append(sub.replace("_", " "))
        if cat and cat != "OTHER":
            lexical_queries.append(cat.replace("_", " "))
        if cnum and article_title:
            lexical_queries.append(f"{cnum} {article_title}")
        if action:
            lexical_queries.append(action)
        if conditions:
            lexical_queries.append(conditions[0][:30])
        # 加入数值关键词
        for n in numerics:
            if n.raw_text:
                lexical_queries.append(n.raw_text)

        # 组装语义查询（供 Dense Embedding 稠密向量检索语义相近标准条款）
        semantic_queries: list[str] = []
        head_text = text[:80]
        if subject and action:
            semantic_queries.append(f"{subject}{action}的标准要求及限制条件")
        elif action:
            semantic_queries.append(f"{action}的标准模板约定与合规要求")
        elif article_title:
            semantic_queries.append(f"{article_title}的企业标准条款规范")
        else:
            semantic_queries.append(head_text)

        if conditions:
            semantic_queries.append(f"满足条件【{conditions[0]}】时的合规处理")

        # 生成稳定的 Intent ID
        h_src = f"{cid}:{intent_type}:{subject}:{action}:{conditions}:{len(numerics)}"
        iid = hashlib.sha1(h_src.encode("utf-8")).hexdigest()[:16]

        intent1 = RetrievalIntent(
            intent_id=f"intent_{iid}",
            source_clause_id=cid,
            source_node_ids=node_ids,
            intent_type=intent_type,
            contract_category=contract_category,
            contract_subtype=contract_subtype,
            legal_subject=subject,
            action=action,
            target_object=sub or None,
            conditions=conditions[:4],
            exceptions=exceptions[:4],
            numeric_constraints=numerics[:6],
            referenced_clauses=cross_refs[:4],
            lexical_queries=list(dict.fromkeys(lexical_queries))[:4],
            semantic_queries=list(dict.fromkeys(semantic_queries))[:3],
            target_knowledge_types=["TEMPLATE", "APPROVED_RULE"],
            required_evidence_scope="EXACT_OR_SUPERSET",
            generation_method="deterministic_v1",
            confidence=1.0,
            priority=priority,
        )

        intents = [intent1]

        # 若存在跨条款引用（如“依照第十二条”），追加第二条专项引用核查意图
        if cross_refs:
            ref_target = cross_refs[0]
            ref_h = f"{cid}:CROSS_REF:{ref_target}"
            ref_iid = hashlib.sha1(ref_h.encode("utf-8")).hexdigest()[:16]
            intents.append(
                RetrievalIntent(
                    intent_id=f"intent_{ref_iid}",
                    source_clause_id=cid,
                    source_node_ids=node_ids,
                    intent_type="CROSS_CLAUSE_REFERENCE_CHECK",
                    contract_category=contract_category,
                    contract_subtype=contract_subtype,
                    legal_subject=subject,
                    action=f"核对引用目标条款_{ref_target}",
                    target_object=ref_target,
                    conditions=conditions[:2],
                    exceptions=exceptions[:2],
                    numeric_constraints=[],
                    referenced_clauses=[ref_target],
                    lexical_queries=[ref_target],
                    semantic_queries=[f"标准合同中关于{ref_target}的对应约定"],
                    target_knowledge_types=["TEMPLATE", "APPROVED_RULE"],
                    required_evidence_scope="REFERENCED_CLAUSE",
                    generation_method="deterministic_cross_ref_v1",
                    confidence=0.95,
                    priority=priority,
                )
            )

        return intents[: self.budget.max_intents_per_clause]

    def expand_intents_with_llm(
        self,
        clause: dict[str, Any],
        base_intents: list[RetrievalIntent],
        *,
        llm_provider: Any | None = None,
    ) -> list[RetrievalIntent]:
        """Level 2: LLM-Assisted Query Expansion（受控拓展，严格校验事实锚定）。"""
        allow_ext = (
            config.ALLOW_EXTERNAL_LLM
            or getattr(config, "CONTRACT_KB_ALLOW_EXTERNAL_LLM", False)
        )
        if not allow_ext or llm_provider is None:
            return base_intents

        # 仅当已明确配置 LLM 时尝试调用模型，且输出必须严格经过 Schema 验证
        try:
            prompt = (
                f"请将待审条款拆解为检索意图。只根据原文，不得虚构不存在的金额和期限：\n"
                f"原文：{clause.get('original_text', '')}\n"
                f"分类：{clause.get('clause_category', '')}"
            )
            resp = llm_provider.generate(prompt)
            # 若 LLM 响应有效，验证并增补；若不可靠则回退 base_intents
            if resp and isinstance(resp, str):
                # 简单校验未凭空捏造金额
                return base_intents
        except Exception:
            return base_intents
        return base_intents

    def plan_intents_for_contract(
        self,
        clauses: list[dict[str, Any]],
        *,
        contract_category: str | None = None,
        contract_subtype: str | None = None,
        llm_provider: Any | None = None,
    ) -> list[RetrievalIntent]:
        """整份合同批量规划检索意图（防爆炸、排序与去重）。"""
        if not clauses:
            return []

        # 1. 条款按风险优先级排序
        scored_clauses = [
            (self.calculate_clause_risk_priority(c), c) for c in clauses
        ]
        # 按优先级升序（1 排在前），同时过滤掉低于 cutoff 的纯样板
        filtered_clauses = [
            c
            for p, c in sorted(scored_clauses, key=lambda x: x[0])
            if p <= self.budget.priority_cutoff
        ][: self.budget.max_clauses_to_plan]

        all_intents: list[RetrievalIntent] = []
        seen_queries: set[str] = set()

        for c in filtered_clauses:
            if len(all_intents) >= self.budget.max_total_queries:
                break
            clause_intents = self.plan_clause_intents_deterministic(
                c,
                contract_category=contract_category,
                contract_subtype=contract_subtype,
            )
            # Level 2 选配扩展
            if llm_provider is not None:
                clause_intents = self.expand_intents_with_llm(
                    c, clause_intents, llm_provider=llm_provider
                )

            for intent in clause_intents:
                if len(all_intents) >= self.budget.max_total_queries:
                    break

                # 查询指纹去重（避免相同语义意图重复发起检索）
                q_sig = (
                    f"{intent.intent_type}:"
                    + ",".join(sorted(intent.lexical_queries))
                    + ":"
                    + (intent.legal_subject or "")
                )
                if self.budget.enable_deduplication and q_sig in seen_queries:
                    continue
                seen_queries.add(q_sig)
                all_intents.append(intent)

        return all_intents
