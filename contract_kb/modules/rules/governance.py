# -*- coding: utf-8 -*-
"""规则治理模块：注册表（Registry）、版本快照（Versioning）、策略评估（Policy）、
冲突消解（Conflict Resolution）与带审计人工覆盖（Human Override with Audit）。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from ...domain.models.review import (
    CheckStatus,
    Finding,
    FindingSource,
    deterministic_issue_id,
)
from .strategies import DEFAULT_STRATEGIES, RuleStrategy

# 系统级强制合规核心规则（用户自定义要求与低优规则不得豁免或覆盖）
CORE_RULE_IDS: frozenset[str] = frozenset(
    {
        "AMOUNT_CASE_CONSISTENCY",
        "CONTRACT_NUMBER_BACKFILL",
        "PARTY_ATTACHMENT_CONSISTENCY",
        "DISPUTE_JURISDICTION_STANDARD",
        "CLAUSE_REQUIRED_CONFIDENTIALITY",
        "CLAUSE_REQUIRED_LIABILITY",
    }
)

SEVERITY_ORDER: dict[str, int] = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}

# 用户自定义要求试图跳过核心规则的常见特征词映射
_BYPASS_KEYWORDS_MAP: tuple[tuple[tuple[str, ...], str], ...] = (
    (("忽略金额", "不检查金额", "跳过金额", "豁免金额"), "AMOUNT_CASE_CONSISTENCY"),
    (("忽略编号", "无需合同编号", "跳过合同编号"), "CONTRACT_NUMBER_BACKFILL"),
    (("忽略主体", "不核对甲方", "跳过主体"), "PARTY_ATTACHMENT_CONSISTENCY"),
    (("忽略管辖", "不限制管辖", "跳过争议管辖"), "DISPUTE_JURISDICTION_STANDARD"),
    (("无需保密", "忽略保密", "跳过保密"), "CLAUSE_REQUIRED_CONFIDENTIALITY"),
    (("无需违约责任", "忽略违约", "跳过违约"), "CLAUSE_REQUIRED_LIABILITY"),
)


class RuleRegistry:
    """确定性规则策略注册表。"""

    def __init__(self, include_defaults: bool = True):
        self._strategies: dict[str, RuleStrategy] = {}
        if include_defaults:
            for cls in DEFAULT_STRATEGIES:
                self.register(cls())

    def register(self, strategy: RuleStrategy) -> None:
        self._strategies[strategy.strategy_id] = strategy

    def get(self, strategy_id: str) -> RuleStrategy | None:
        return self._strategies.get(strategy_id)

    def list_strategies(self) -> list[RuleStrategy]:
        return list(self._strategies.values())

    def execute_all(
        self, rec: Any, clauses: list[dict[str, Any]], subtype: str
    ) -> list[Finding]:
        findings: list[Finding] = []
        for strategy in self._strategies.values():
            findings.extend(strategy.evaluate(rec, clauses, subtype))
        return findings

    def compute_rule_set_version(self, db_rules: list[dict[str, Any]]) -> str:
        """基于内置策略版本与当前生效（APPROVED）数据库规则计算确定性规则集指纹。"""
        builtin_manifest = [
            f"{s.strategy_id}:v{s.version}" for s in self._strategies.values()
        ]
        approved_manifest = sorted(
            f"{r.get('rule_id')}:v{r.get('version', 1)}:"
            f"{json.dumps(r.get('params', {}), sort_keys=True, ensure_ascii=False)}"
            for r in db_rules
            if r.get("status") == "APPROVED"
        )
        payload = "|".join(builtin_manifest + approved_manifest)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
        return f"rs-{digest}"


@dataclass(frozen=True)
class PolicyEvaluationOutcome:
    sanitized_custom_requirements: list[str]
    blocked_override_findings: list[Finding]


class PolicyEvaluator:
    """审核策略评估器：拦截并审计试图覆盖 CORE_RULE_IDS 的用户自定义指令。"""

    def evaluate_custom_requirements(
        self, custom_requirements: list[str]
    ) -> PolicyEvaluationOutcome:
        accepted: list[str] = []
        blocked: list[Finding] = []
        for req in custom_requirements:
            text = req.strip()
            if not text:
                continue
            violated_core = None
            for kws, core_id in _BYPASS_KEYWORDS_MAP:
                if any(kw in text for kw in kws):
                    violated_core = core_id
                    break
            if violated_core:
                blocked.append(
                    Finding(
                        issue_id="",
                        rule_id="CORE_POLICY_NON_OVERRIDABLE",
                        severity="MEDIUM",
                        category="POLICY_GUARD",
                        title=f"拒绝豁免系统强制合规规则：{violated_core}",
                        description=(
                            f"自定义要求『{text[:60]}』试图豁免系统核心强制规则 "
                            f"{violated_core}。依据企业合同合规治理红线，核心规则不可被覆盖，已继续强制执行。"
                        ),
                        expected_value=f"强制执行 {violated_core}",
                        actual_value=text[:80],
                        execution_mode="DETERMINISTIC",
                        finding_source=FindingSource.DETERMINISTIC.value,
                        requires_human_review=True,
                        check_status=CheckStatus.CHECKED.value,
                    )
                )
            else:
                accepted.append(text)
        return PolicyEvaluationOutcome(
            sanitized_custom_requirements=accepted,
            blocked_override_findings=blocked,
        )


class ConflictResolver:
    """多源发现去重、冲突消解与确定性排序器。

    规则：
    1. 每个 Finding 先执行认知边界守卫（低置信度/缺证据禁止输出确定性定论）；
    2. 同键冲突时：DETERMINISTIC 核心规则优先于非核心规则，DETERMINISTIC 优先于 LLM_SEMANTIC，
       更高严重度（HIGH > MEDIUM > LOW > INFO）优先保留；
    3. 基于 reproducibility_seed 生成可复现的确定性 issue_id。
    """

    def resolve(
        self, findings: list[Finding], reproducibility_seed: str = "default"
    ) -> list[Finding]:
        by_key: dict[tuple[Any, ...], Finding] = {}
        source_rank = {
            FindingSource.HUMAN.value: 0,
            FindingSource.DETERMINISTIC.value: 1,
            FindingSource.LLM_SEMANTIC.value: 2,
        }

        for f in findings:
            f.enforce_epistemic_guard()
            if (
                f.rule_id
                and f.category != "RULE_PENDING_APPROVAL"
                and f.source_reference
                and f.source_reference.node_ids
            ):
                key: tuple[Any, ...] = (
                    "RULE_NODE",
                    f.rule_id,
                    tuple(sorted(f.source_reference.node_ids)),
                )
            else:
                key = (f.rule_id, f.category, f.title, str(f.actual_value)[:40])
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = f
                continue
            merged_refs = list(existing.knowledge_references) + [
                r
                for r in f.knowledge_references
                if r not in existing.knowledge_references
            ]
            # 比较优先级：核心规则 > 来源等级 > 严重度等级
            cand_tuple = (
                0 if (f.rule_id in CORE_RULE_IDS) else 1,
                source_rank.get(f.finding_source, 9),
                SEVERITY_ORDER.get(f.severity, 9),
            )
            exist_tuple = (
                0 if (existing.rule_id in CORE_RULE_IDS) else 1,
                source_rank.get(existing.finding_source, 9),
                SEVERITY_ORDER.get(existing.severity, 9),
            )
            if cand_tuple < exist_tuple:
                f.knowledge_references = merged_refs
                by_key[key] = f
            else:
                existing.knowledge_references = merged_refs

        resolved = list(by_key.values())
        resolved.sort(
            key=lambda x: (
                SEVERITY_ORDER.get(x.severity, 9),
                x.category,
                x.rule_id or "",
                x.title,
            )
        )
        for idx, item in enumerate(resolved):
            if not item.issue_id:
                item.issue_id = deterministic_issue_id(
                    reproducibility_seed, item.rule_id, item.category, idx
                )
        return resolved


class HumanOverrideService:
    """带不可篡改审计追踪的人工复核覆盖服务（Human Override with Audit）。

    仅变更当次审核任务的发现状态（human_state），并写入 `human_overrides` 与 `audit_log`，
    严禁静默修改企业正式规则表。
    """

    VALID_ACTIONS = frozenset(
        {"ACCEPT", "IGNORE", "EDIT", "NOTE", "FALSE_POSITIVE", "FALSE_NEGATIVE"}
    )

    def apply_override(
        self,
        storage: Any,
        issue_id: str,
        action: str,
        actor: str,
        reason: str = "",
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        if action not in self.VALID_ACTIONS:
            raise ValueError(f"不支持的人工复核动作：{action!r}")
        issue = storage.get_issue(issue_id)
        if not issue:
            raise KeyError(f"未找到审核问题：{issue_id}")
        if tenant_id is not None and issue.get("task_tenant_id") != tenant_id:
            raise PermissionError("跨租户人工复核覆盖被拒绝")

        prev_state = issue.get("human_state") or "PENDING"
        ok = storage.feedback_issue(issue_id, action, actor, reason)
        if not ok:
            raise RuntimeError(f"更新审核问题人工状态失败：{issue_id}")

        override_id = f"ovr_{uuid.uuid4().hex[:12]}"
        record_fn = getattr(storage, "record_human_override", None)
        if record_fn is not None:
            record_fn(
                override_id=override_id,
                task_id=issue["task_id"],
                issue_id=issue_id,
                tenant_id=issue.get("task_tenant_id") or tenant_id or "default",
                actor=actor,
                action=action,
                reason=reason,
                previous_state=prev_state,
                new_state=action,
                created_at=time.time(),
            )

        return {
            "override_id": override_id,
            "task_id": issue["task_id"],
            "issue_id": issue_id,
            "actor": actor,
            "previous_state": prev_state,
            "human_state": action,
            "finding_source": FindingSource.HUMAN.value,
            "reason": reason,
        }
