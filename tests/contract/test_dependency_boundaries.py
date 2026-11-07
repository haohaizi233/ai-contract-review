# -*- coding: utf-8 -*-
"""Architectural boundary tests: verifying Clean Architecture dependency rules and repository contract parity."""

from __future__ import annotations

import ast
from pathlib import Path

from contract_kb.storage import Storage
from contract_kb.storage_pg import StoragePG


def get_imports_from_file(file_path: Path) -> set[str]:
    """Parse a python file into an AST and extract all top-level imported module names."""
    try:
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(file_path))
    except Exception:
        return set()

    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                imports.add(n.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


def test_domain_layer_does_not_import_outer_layers():
    """Domain layer models and services must not depend on application, infrastructure, interfaces, or web frameworks."""
    domain_root = Path("contract_kb/domain")
    assert domain_root.exists()

    forbidden_prefixes = (
        "contract_kb.application",
        "contract_kb.infrastructure",
        "contract_kb.interfaces",
        "fastapi",
        "starlette",
        "flask",
    )

    violations = []
    for py_file in domain_root.rglob("*.py"):
        imported_modules = get_imports_from_file(py_file)
        for mod in imported_modules:
            for forbidden in forbidden_prefixes:
                if mod == forbidden or mod.startswith(forbidden + "."):
                    violations.append(f"{py_file} -> {mod}")

    assert not violations, (
        "Domain layer violates clean dependency boundary:\n" + "\n".join(violations)
    )


def test_storage_repository_contract_parity():
    """Verify both SQLite and PostgreSQL storage adapters implement identical repository contract interfaces."""
    required_methods = [
        "unit_of_work",
        "claim_job",
        "heartbeat_job",
        "finish_job",
        "save_canonical_version",
        "get_canonical_versions",
        "find_canonical_by_source",
        "reserve_idempotency_key",
        "finalize_idempotency_key",
        "record_and_check_nonce",
        "record_human_override",
        "list_human_overrides",
        "upsert_document",
        "get_clause",
        "get_task",
        "save_task",
    ]

    for method_name in required_methods:
        assert hasattr(Storage, method_name), (
            f"Storage (SQLite) missing required contract method: {method_name}"
        )
        assert hasattr(StoragePG, method_name), (
            f"StoragePG (PostgreSQL) missing required contract method: {method_name}"
        )
        assert callable(getattr(Storage, method_name))
        assert callable(getattr(StoragePG, method_name))
