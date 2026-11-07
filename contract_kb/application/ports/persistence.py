# -*- coding: utf-8 -*-
"""应用层持久化与工作单元端口契约（Ports）。"""

from __future__ import annotations

from typing import Any, ContextManager, Protocol, runtime_checkable


@runtime_checkable
class UnitOfWorkPort(Protocol):
    """事务性工作单元协议（支持显式 commit / rollback 与上下文自动回滚）。"""

    def __enter__(self) -> "UnitOfWorkPort": ...

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


@runtime_checkable
class KnowledgeStoragePort(Protocol):
    """知识库、规则治理、合同审核任务与统一合同仓储端口。"""

    def unit_of_work(self) -> ContextManager[UnitOfWorkPort]: ...

    def upsert_document(
        self, doc: dict[str, Any], tenant: str | None = None
    ) -> tuple[str, bool]: ...

    def get_document(
        self, document_id: str, tenant_id: str | None = None
    ) -> dict[str, Any] | None: ...

    def list_documents(self, tenant_id: str | None = None) -> list[dict[str, Any]]: ...

    def find_active_template(
        self,
        contract_subtype: str | None = None,
        business_category: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any] | None: ...

    def get_document_clauses(
        self,
        document_id: str,
        clause_category: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def search(
        self,
        query: str,
        top_k: int = 8,
        business_category: str | None = None,
        contract_subtype: str | None = None,
        clause_number: str | None = None,
        keyword: str | None = None,
        knowledge_type: str | None = None,
        approved_only: bool = False,
        rerank: bool = True,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def get_rules(
        self, status: str | None = None, tenant_id: str | None = None
    ) -> list[dict[str, Any]]: ...

    def save_rule(self, rule: dict[str, Any], tenant_id: str | None = None) -> int: ...

    def approve_rule(
        self,
        rule_id: str,
        approver: str,
        version: int | None = 1,
        tenant_id: str | None = None,
    ) -> bool: ...

    def record_rule_execution(
        self,
        exec_id: str,
        rule_id: str,
        rule_version: int,
        rule_type: str,
        result: str,
        severity: str,
        message: str,
        evidence: dict[str, Any] | list[Any] | str,
        executed_at: float | None = None,
    ) -> None: ...

    def save_task(
        self,
        task_id: str,
        document_id: str,
        mode: str,
        status: str,
        meta: dict[str, Any],
        tenant_id: str = "default",
        created_by: str = "system",
        idempotency_key: str | None = None,
        rule_set_version: str | None = None,
        model_version: str | None = None,
        reproducibility_hash: str | None = None,
    ) -> None: ...

    def get_task(self, task_id: str) -> dict[str, Any] | None: ...

    def save_issues(self, task_id: str, issues: list[dict[str, Any]]) -> None: ...

    def get_issues(self, task_id: str) -> list[dict[str, Any]]: ...

    def get_issue(self, issue_id: str) -> dict[str, Any] | None: ...

    def feedback_issue(
        self,
        issue_id: str,
        action: str,
        actor: str,
        note: str = "",
        expected_state: str | None = None,
    ) -> bool: ...

    def log_audit(
        self, actor: str, action: str, target: str, detail: str = ""
    ) -> None: ...

    def reserve_idempotency_key(self, identity: Any, resource_id: str) -> Any: ...

    def finalize_idempotency_key(
        self,
        identity: Any = None,
        resource_id: str | None = None,
        status: str = "COMPLETED",
        *,
        tenant_id: str | None = None,
        actor_id: str | None = None,
        operation: str | None = None,
        idempotency_key: str | None = None,
    ) -> None: ...
