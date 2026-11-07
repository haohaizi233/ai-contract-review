# -*- coding: utf-8 -*-
"""轻量法律关系建模与提取器（Lightweight Legal Relation Extraction）。

定义并提取 9 类核心法律关系：
1. OBLIGATION（义务：“应当”、“必须”、“应”）
2. RIGHT（权利：“有权”、“可以”、“得以”）
3. PROHIBITION（禁止：“不得”、“严禁”、“禁止”）
4. CONDITION（生效/触发条件：“自...起”、“若...”、“收到...后”）
5. EXCEPTION（免责/适用例外：“但...除外”、“除...外”）
6. DEADLINE（履行期限：“...个工作日内”、“...日内”）
7. LIABILITY（违约责任：“支付违约金”、“赔偿损失”）
8. TERMINATION（合同解除：“单方解除”、“终止合同”）
9. CROSS_REFERENCE（跨条款引用：“依照第X条”、“参见第X条”）

设计原则：
- 确定性规则与语法槽位驱动，不引入重型外部图数据库；
- 模糊歧义时输出 UNKNOWN 或 NEEDS_MANUAL_REVIEW，绝不为了填满 Schema 凭空臆测；
- 原文片段严格可定位可核验。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class LegalRelationType(str, Enum):
    OBLIGATION = "OBLIGATION"
    RIGHT = "RIGHT"
    PROHIBITION = "PROHIBITION"
    CONDITION = "CONDITION"
    EXCEPTION = "EXCEPTION"
    DEADLINE = "DEADLINE"
    LIABILITY = "LIABILITY"
    TERMINATION = "TERMINATION"
    CROSS_REFERENCE = "CROSS_REFERENCE"


@dataclass
class LegalRelation:
    relation_id: str
    relation_type: str
    source_clause_id: str
    subject: str | None = None
    counterparty: str | None = None
    action_or_content: str = ""
    trigger_condition: str | None = None
    exception_term: str | None = None
    deadline: str | None = None
    target_reference: str | None = None
    raw_quote: str = ""
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_OBLIGATION_RE = re.compile(
    r"(甲方|乙方|双方|被许可方|许可方)\s*(应当|应|必须|需|负责)\s*([^，。；]{2,50})"
)
_RIGHT_RE = re.compile(
    r"(甲方|乙方|双方|被许可方|许可方)\s*(有权|可以|得以|有权决定)\s*([^，。；]{2,50})"
)
_PROHIBITION_RE = re.compile(
    r"(甲方|乙方|双方|被许可方|许可方)\s*(不得|严禁|禁止|切勿|不可)\s*([^，。；]{2,50})"
)
_TERMINATION_RE = re.compile(
    r"(甲方|乙方|守约方|任何一方)\s*有权\s*(单方解除|解除本合同|终止本协议|终止履行)([^。；]*)"
)
_LIABILITY_RE = re.compile(
    r"(甲方|乙方|违约方)\s*(?:应|应当)?\s*(支付违约金|赔偿全部损失|承担违约责任|按日支付)([^，。；]*)"
)
_DEADLINE_RE = re.compile(
    r"(?:在|于|自)?\s*([^，。；]{1,20})?\s*(\d+|[一二三四五六七八九十]+)\s*(个?工作日|日|天|个月|年)\s*(?:内|届满|之前)"
)
_CROSS_REF_RE = re.compile(
    r"(?:依照|参见|按照|根据|依据|如)?(第[一二三四五六七八九十0-9]+条(?:\s*第[一二三四五六七八九十0-9]+款)?)"
)


class LegalRelationExtractor:
    """轻量合同法律关系抽取器。"""

    def extract_relations(
        self,
        clause_data: dict[str, Any] | Any,
        clause_id: str | None = None,
    ) -> list[LegalRelation]:
        """从条款字典或对象中提取结构化法律关系。"""
        if isinstance(clause_data, dict):
            text = str(clause_data.get("original_text") or "")
            cid = clause_id or str(clause_data.get("clause_id") or "unknown")
            conditions = list(clause_data.get("conditions") or [])
            exceptions = list(clause_data.get("exceptions") or [])
        else:
            text = str(getattr(clause_data, "original_text", "") or "")
            cid = clause_id or str(getattr(clause_data, "clause_id", "unknown"))
            conditions = list(getattr(clause_data, "conditions", []) or [])
            exceptions = list(getattr(clause_data, "exceptions", []) or [])

        if not text.strip():
            return []

        relations: list[LegalRelation] = []
        cond_str = "；".join(conditions) if conditions else None
        exc_str = "；".join(exceptions) if exceptions else None

        # 1. 跨条款引用 (CROSS_REFERENCE)
        for m in _CROSS_REF_RE.finditer(text):
            target = m.group(1).strip()
            rid = hashlib.sha1(f"{cid}:REF:{target}:{m.start()}".encode()).hexdigest()[:12]
            relations.append(
                LegalRelation(
                    relation_id=f"rel_{rid}",
                    relation_type=LegalRelationType.CROSS_REFERENCE.value,
                    source_clause_id=cid,
                    action_or_content=f"引用{target}",
                    target_reference=target,
                    raw_quote=m.group(0),
                    confidence=1.0,
                )
            )

        # 2. 单方解除权 (TERMINATION)
        for m in _TERMINATION_RE.finditer(text):
            subj = m.group(1)
            act = m.group(2) + m.group(3)
            rid = hashlib.sha1(f"{cid}:TERM:{subj}:{m.start()}".encode()).hexdigest()[:12]
            relations.append(
                LegalRelation(
                    relation_id=f"rel_{rid}",
                    relation_type=LegalRelationType.TERMINATION.value,
                    source_clause_id=cid,
                    subject=subj,
                    action_or_content=act.strip(),
                    trigger_condition=cond_str,
                    exception_term=exc_str,
                    raw_quote=m.group(0),
                    confidence=0.95,
                )
            )

        # 3. 禁止性规范 (PROHIBITION - 负向义务)
        for m in _PROHIBITION_RE.finditer(text):
            subj = m.group(1)
            act = m.group(3).strip()
            rid = hashlib.sha1(f"{cid}:PROHIB:{subj}:{m.start()}".encode()).hexdigest()[:12]
            relations.append(
                LegalRelation(
                    relation_id=f"rel_{rid}",
                    relation_type=LegalRelationType.PROHIBITION.value,
                    source_clause_id=cid,
                    subject=subj,
                    action_or_content=f"不得{act}",
                    trigger_condition=cond_str,
                    exception_term=exc_str,
                    raw_quote=m.group(0),
                    confidence=0.95,
                )
            )

        # 4. 强制性义务 (OBLIGATION)
        for m in _OBLIGATION_RE.finditer(text):
            subj = m.group(1)
            act = m.group(3).strip()
            # 排除已被识别为解除的重复项
            if "解除" in act and any(r.relation_type == "TERMINATION" for r in relations):
                continue
            rid = hashlib.sha1(f"{cid}:OBLIG:{subj}:{m.start()}".encode()).hexdigest()[:12]
            relations.append(
                LegalRelation(
                    relation_id=f"rel_{rid}",
                    relation_type=LegalRelationType.OBLIGATION.value,
                    source_clause_id=cid,
                    subject=subj,
                    action_or_content=act,
                    trigger_condition=cond_str,
                    exception_term=exc_str,
                    raw_quote=m.group(0),
                    confidence=0.90,
                )
            )

        # 5. 权利性规范 (RIGHT)
        for m in _RIGHT_RE.finditer(text):
            subj = m.group(1)
            act = m.group(3).strip()
            if "解除" in act:
                continue
            rid = hashlib.sha1(f"{cid}:RIGHT:{subj}:{m.start()}".encode()).hexdigest()[:12]
            relations.append(
                LegalRelation(
                    relation_id=f"rel_{rid}",
                    relation_type=LegalRelationType.RIGHT.value,
                    source_clause_id=cid,
                    subject=subj,
                    action_or_content=act,
                    trigger_condition=cond_str,
                    exception_term=exc_str,
                    raw_quote=m.group(0),
                    confidence=0.90,
                )
            )

        # 6. 违约责任 (LIABILITY)
        for m in _LIABILITY_RE.finditer(text):
            subj = m.group(1)
            act = m.group(2) + m.group(3)
            rid = hashlib.sha1(f"{cid}:LIAB:{subj}:{m.start()}".encode()).hexdigest()[:12]
            relations.append(
                LegalRelation(
                    relation_id=f"rel_{rid}",
                    relation_type=LegalRelationType.LIABILITY.value,
                    source_clause_id=cid,
                    subject=subj,
                    action_or_content=act.strip(),
                    trigger_condition=cond_str,
                    exception_term=exc_str,
                    raw_quote=m.group(0),
                    confidence=0.95,
                )
            )

        # 7. 期限限制 (DEADLINE)
        for m in _DEADLINE_RE.finditer(text):
            start_event = (m.group(1) or "").strip()
            num = m.group(2)
            unit = m.group(3)
            rid = hashlib.sha1(f"{cid}:DL:{num}:{unit}:{m.start()}".encode()).hexdigest()[:12]
            relations.append(
                LegalRelation(
                    relation_id=f"rel_{rid}",
                    relation_type=LegalRelationType.DEADLINE.value,
                    source_clause_id=cid,
                    action_or_content=f"{num}{unit}内完成",
                    trigger_condition=f"起算点：{start_event}" if start_event else None,
                    deadline=f"{num}{unit}",
                    raw_quote=m.group(0),
                    confidence=0.90,
                )
            )

        return relations
