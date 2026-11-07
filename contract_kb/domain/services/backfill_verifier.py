# -*- coding: utf-8 -*-
"""合同编号与文件内容回填核验领域服务（BackfillVerificationService）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..models.canonical_contract import ContractNumber


@dataclass(frozen=True)
class BackfillVerificationReport:
    has_label: bool
    has_filled_number: bool
    extracted_number: str | None
    entity_prefix: str
    prefix_matches_expected: bool | None
    header_matches_body: bool | None
    issues: list[dict[str, Any]]


class BackfillVerificationService:
    """校验合同编号在正文与页眉中的回填完整性及签约主体前缀一致性。"""

    def verify(
        self,
        clauses: list[dict[str, Any]],
        headers: list[dict[str, Any]] | None = None,
        expected_entity_prefix: str | None = None,
    ) -> BackfillVerificationReport:
        label_clause = None
        for c in clauses:
            if "合同编号" in c.get("original_text", ""):
                label_clause = c
                break

        if label_clause is None:
            return BackfillVerificationReport(
                has_label=False,
                has_filled_number=False,
                extracted_number=None,
                entity_prefix="",
                prefix_matches_expected=None,
                header_matches_body=None,
                issues=[
                    {
                        "code": "MISSING_CONTRACT_NUMBER_LABEL",
                        "message": "待审文档未找到『合同编号』标识行",
                    }
                ],
            )

        text = label_clause.get("original_text", "")
        m = re.search(r"合同编号[:：]\s*(\S+)", text)
        raw_val = m.group(1).strip() if m else ""
        parsed = ContractNumber.parse(raw_val)
        if parsed is None:
            return BackfillVerificationReport(
                has_label=True,
                has_filled_number=False,
                extracted_number=None,
                entity_prefix="",
                prefix_matches_expected=None,
                header_matches_body=None,
                issues=[
                    {
                        "code": "EMPTY_CONTRACT_NUMBER",
                        "message": "『合同编号』存在但未回填有效编号值",
                        "clause": label_clause,
                    }
                ],
            )

        issues: list[dict[str, Any]] = []
        prefix_ok: bool | None = None
        if expected_entity_prefix:
            prefix_ok = parsed.entity_prefix == expected_entity_prefix.strip().upper()
            if not prefix_ok:
                issues.append(
                    {
                        "code": "ENTITY_PREFIX_MISMATCH",
                        "message": (
                            f"合同编号前缀 {parsed.entity_prefix!r} 与签约主体预期前缀 "
                            f"{expected_entity_prefix!r} 不一致"
                        ),
                    }
                )

        header_ok: bool | None = None
        if headers:
            header_nums = []
            for h in headers:
                htext = h.get("text", "") if isinstance(h, dict) else str(h)
                hm = re.search(r"合同编号[:：]\s*(\S+)", htext)
                if hm and hm.group(1).strip():
                    header_nums.append(hm.group(1).strip())
            if header_nums:
                header_ok = all(hn == parsed.raw for hn in header_nums)
                if not header_ok:
                    issues.append(
                        {
                            "code": "HEADER_BODY_NUMBER_MISMATCH",
                            "message": f"页眉合同编号 {header_nums} 与正文编号 {parsed.raw} 不一致",
                        }
                    )

        return BackfillVerificationReport(
            has_label=True,
            has_filled_number=True,
            extracted_number=parsed.raw,
            entity_prefix=parsed.entity_prefix,
            prefix_matches_expected=prefix_ok,
            header_matches_body=header_ok,
            issues=issues,
        )
