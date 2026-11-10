# -*- coding: utf-8 -*-
"""Automated architecture tests enforcing Clean/DDD layer dependency directions."""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_KB_ROOT = PROJECT_ROOT / "contract_kb"


def _resolve_relative_import(
    rel_file: Path, module: str | None, level: int
) -> str | None:
    parts = list(rel_file.parts[:-1])
    if level > len(parts):
        return None
    base = parts[: len(parts) - level + 1]
    if module:
        base.extend(module.split("."))
    return ".".join(base)


def _extract_imports(py_file: Path) -> tuple[list[str], list[tuple[int, int]]]:
    """Return (imported_module_targets, [(lineno, relative_level), ...])."""
    rel_path = py_file.relative_to(PROJECT_ROOT)
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=rel_path.as_posix())
    targets: list[str] = []
    rel_levels: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                rel_levels.append((node.lineno, node.level))
                resolved = _resolve_relative_import(rel_path, node.module, node.level)
                if resolved:
                    targets.append(resolved)
                    for alias in node.names:
                        targets.append(f"{resolved}.{alias.name}")
            elif node.module:
                targets.append(node.module)
                for alias in node.names:
                    targets.append(f"{node.module}.{alias.name}")
    return targets, rel_levels


def test_domain_layer_has_no_outward_dependencies() -> None:
    """Domain layer must not depend on application, infrastructure, interfaces, evaluation, tools, tests, FastAPI, or psycopg2."""
    domain_dir = CONTRACT_KB_ROOT / "domain"
    forbidden_prefixes = (
        "contract_kb.application",
        "contract_kb.infrastructure",
        "contract_kb.interfaces",
        "contract_kb.evaluation",
        "contract_kb.modules",
        "tools",
        "tests",
        "fastapi",
        "psycopg2",
    )
    violations: list[str] = []
    for py_file in sorted(domain_dir.rglob("*.py")):
        imports, _ = _extract_imports(py_file)
        rel = py_file.relative_to(PROJECT_ROOT).as_posix()
        for imp in imports:
            if imp.startswith(forbidden_prefixes):
                violations.append(f"{rel} -> {imp}")
    assert not violations, "Domain layer dependency violations:\n" + "\n".join(
        violations
    )


def test_application_layer_does_not_depend_on_interfaces_or_tools() -> None:
    """Application layer must not depend on interfaces (HTTP/Web/Desktop/CLI), evaluation, tools, or tests."""
    app_dir = CONTRACT_KB_ROOT / "application"
    forbidden_prefixes = (
        "contract_kb.interfaces",
        "contract_kb.evaluation",
        "tools",
        "tests",
    )
    violations: list[str] = []
    for py_file in sorted(app_dir.rglob("*.py")):
        imports, _ = _extract_imports(py_file)
        rel = py_file.relative_to(PROJECT_ROOT).as_posix()
        for imp in imports:
            if imp.startswith(forbidden_prefixes):
                violations.append(f"{rel} -> {imp}")
    assert not violations, "Application layer dependency violations:\n" + "\n".join(
        violations
    )


def test_business_modules_do_not_depend_on_interfaces_or_tools() -> None:
    """Business modules (contract_kb/modules/*) must not depend on interfaces, evaluation, tools, or tests."""
    modules_dir = CONTRACT_KB_ROOT / "modules"
    forbidden_prefixes = (
        "contract_kb.interfaces",
        "contract_kb.evaluation",
        "tools",
        "tests",
    )
    violations: list[str] = []
    for py_file in sorted(modules_dir.rglob("*.py")):
        imports, _ = _extract_imports(py_file)
        rel = py_file.relative_to(PROJECT_ROOT).as_posix()
        for imp in imports:
            if imp.startswith(forbidden_prefixes):
                violations.append(f"{rel} -> {imp}")
    assert not violations, "Business module dependency violations:\n" + "\n".join(
        violations
    )


def test_production_modules_never_import_tests_tools_or_evaluation() -> None:
    """Production modules inside contract_kb (outside evaluation/) must never import tests, tools, or evaluation."""
    violations: list[str] = []
    for py_file in sorted(CONTRACT_KB_ROOT.rglob("*.py")):
        rel = py_file.relative_to(PROJECT_ROOT).as_posix()
        imports, _ = _extract_imports(py_file)
        for imp in imports:
            if imp == "tests" or imp.startswith("tests."):
                violations.append(f"{rel} -> {imp}")
            if imp == "tools" or imp.startswith("tools."):
                violations.append(f"{rel} -> {imp}")
            if not rel.startswith("contract_kb/evaluation/") and imp.startswith(
                "contract_kb.evaluation"
            ):
                violations.append(f"{rel} -> {imp}")
    assert not violations, (
        "Production code forbidden dependency violations:\n" + "\n".join(violations)
    )


def test_no_deep_relative_imports() -> None:
    """Forbid deep relative imports (level >= 4, i.e., `from ....`) inside contract_kb."""
    violations: list[str] = []
    for py_file in sorted(CONTRACT_KB_ROOT.rglob("*.py")):
        rel = py_file.relative_to(PROJECT_ROOT).as_posix()
        _, rel_levels = _extract_imports(py_file)
        for lineno, level in rel_levels:
            if level >= 4:
                violations.append(f"{rel}:{lineno} has relative import level={level}")
    assert not violations, "Deep relative imports found:\n" + "\n".join(violations)
