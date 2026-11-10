# -*- coding: utf-8 -*-
"""审核引擎门面（Facade）：委托至 14 阶段可组合流水线 `ReviewPipeline` 与策略注册表 `RuleRegistry`。

关键约束与向后兼容承诺：
- 保留 `ReviewEngine`、`CORE_RULE_IDS`、`SEVERITY_ORDER`、`SourceReference`、`Issue`、
  `_ParsedView`、`_NodeView`、`audit_format_from_rec`、`_dec`、`_cn_num_to_int` 等既有导出符号；
- 金额一律 `Decimal`，禁止 `float` 参与财务计算；
- 证据不足时返回 `INSUFFICIENT_EVIDENCE` / `NEEDS_MANUAL_REVIEW` / `NOT_CHECKED`，绝不输出确定性违规断言；
- `source_reference` 必须指向真实节点，不允许伪造位置；
- `CUSTOM` 模式不允许覆盖系统强制核心规则（`CORE_RULE_IDS`）；
- LLM 未配置时语义审核降级为确定性模式，如实标注 `NOT_CHECKED`。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from .application.pipeline import (
    ParsedDocumentView as _ParsedView,
)
from .application.pipeline import (
    ParsedNodeView as _NodeView,
)
from .application.pipeline import (
    ReviewPipeline,
    audit_format_from_record,
)
from .domain.models.review import ReviewResult
from .domain.value_objects.amounts import chinese_num_to_int, parse_decimal
from .modules.rules import CORE_RULE_IDS as _CORE_RULE_TUPLE
from .modules.rules import RuleRegistry

__all__ = [
    "CORE_RULE_IDS",
    "SEVERITY_ORDER",
    "SourceReference",
    "Issue",
    "ReviewEngine",
    "audit_format_from_rec",
    "_ParsedView",
    "_NodeView",
    "_dec",
    "_cn_num_to_int",
]

# 系统强制核心规则：CUSTOM 用户要求不得覆盖（保留 set 类型向后兼容）
CORE_RULE_IDS: set[str] = set(_CORE_RULE_TUPLE)

SEVERITY_ORDER: dict[str, int] = {
    "HIGH": 0,
    "MEDIUM": 1,
    "LOW": 2,
    "INFO": 3,
}


def audit_format_from_rec(rec: Any, standard_record: dict | None = None) -> dict:
    """向后兼容适配器：委托至 `application.pipeline.format_adapter.audit_format_from_record`。"""
    return audit_format_from_record(rec, standard_record=standard_record)


@dataclass
class SourceReference:
    """向后兼容原文定位数据结构（领域层对应 `domain.models.review.EvidenceReference`）。"""

    document_id: str
    node_ids: list = field(default_factory=list)
    clause_number: str | None = None
    original_text: str = ""


@dataclass
class Issue:
    """向后兼容审核问题数据结构（领域层对应 `domain.models.review.Finding`）。"""

    issue_id: str
    rule_id: str | None
    severity: str
    category: str
    title: str
    description: str
    expected_value: object = None
    actual_value: object = None
    source_reference: SourceReference | None = None
    knowledge_references: list = field(default_factory=list)
    suggestion: str = ""
    execution_mode: str = "DETERMINISTIC"
    requires_human_review: bool = False
    confidence: float | None = None
    check_status: str = "CHECKED"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _dec(s: str) -> Decimal | None:
    """字符串金额解析为精确 `Decimal`（委托至 `domain.value_objects.amounts.parse_decimal`）。"""
    return parse_decimal(s)


def _cn_num_to_int(s: str) -> int | None:
    """中文大写金额解析为整数（委托至 `domain.value_objects.amounts.chinese_num_to_int`）。"""
    return chinese_num_to_int(s)


class ReviewEngine:
    """合同智能审核引擎门面（内部由 14 阶段 `ReviewPipeline` 与 `RuleRegistry` 驱动）。"""

    def __init__(
        self,
        storage: Any,
        llm_provider: Any = None,
        llm_reviewer: Any = None,
        registry: RuleRegistry | None = None,
    ):
        self.storage = storage
        self._llm_provider = llm_provider
        self._llm_reviewer = llm_reviewer
        if llm_provider is None and llm_reviewer is None:
            # 启动时按授权配置初始化 LLM：配置了授权端点就绝不为 None；
            # 配置不完整时 fail-closed 抛错；未配置（none）时返回 None → 如实标 NOT_CHECKED。
            from .infrastructure.ai.llm_providers import get_llm_provider

            self._llm_provider = get_llm_provider()

        self.registry = registry or RuleRegistry(include_defaults=True)
        self.pipeline = ReviewPipeline(
            storage=self.storage,
            llm_provider=self._llm_provider,
            llm_reviewer=self._llm_reviewer,
            llm_status_fn=self.llm_status,
            registry=self.registry,
        )

    def llm_status(self) -> dict[str, Any]:
        """返回当前 LLM 接入状态（用于结果留痕，不伪造已审）。"""
        if self._llm_reviewer is not None:
            prov = getattr(self._llm_reviewer, "provider", None)
            return {
                "provider": getattr(prov, "name", "custom") if prov else "none",
                "configured": prov is not None,
            }
        prov = self._llm_provider
        return {
            "provider": getattr(prov, "name", "") if prov else "none",
            "configured": prov is not None,
        }

    def review_result(
        self,
        docx_path: str | Path,
        mode: str = "STANDARD",
        custom_requirements: list[str] | None = None,
        tenant_id: str | None = None,
        created_by: str = "system",
        *,
        task_id: str | None = None,
        idempotency_key: str | None = None,
        timeout_seconds: float = 120.0,
        cancel_requested: bool = False,
    ) -> ReviewResult:
        """执行完整 14 阶段审核流水线并返回强类型 `ReviewResult` 领域契约。"""
        return self.pipeline.run(
            docx_path=docx_path,
            mode=mode,
            custom_requirements=custom_requirements,
            tenant_id=tenant_id,
            created_by=created_by,
            task_id=task_id,
            idempotency_key=idempotency_key,
            timeout_seconds=timeout_seconds,
            cancel_requested=cancel_requested,
        )

    def review(
        self,
        docx_path: str | Path,
        mode: str = "STANDARD",
        custom_requirements: list[str] | None = None,
        tenant_id: str | None = None,
        created_by: str = "system",
        *,
        task_id: str | None = None,
        idempotency_key: str | None = None,
        timeout_seconds: float = 120.0,
        cancel_requested: bool = False,
    ) -> dict[str, Any]:
        """执行完整 14 阶段审核流水线并返回向后兼容字典结构。"""
        return self.review_result(
            docx_path=docx_path,
            mode=mode,
            custom_requirements=custom_requirements,
            tenant_id=tenant_id,
            created_by=created_by,
            task_id=task_id,
            idempotency_key=idempotency_key,
            timeout_seconds=timeout_seconds,
            cancel_requested=cancel_requested,
        ).to_dict()
