# -*- coding: utf-8 -*-
"""Package boundary, root directory convergence, and static asset packaging tests."""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_contract_kb_root_convergence() -> None:
    """Verify contract_kb/ root contains only approved entrypoints and compatibility facades."""
    allowed_root_files = {
        "__init__.py",
        "config.py",
        "api.py",
        "cli.py",
        "review_engine.py",
        "storage.py",
        "storage_pg.py",
        "legal_agent.py",
        "full_diff.py",
    }
    actual_root_files = {p.name for p in (PROJECT_ROOT / "contract_kb").glob("*.py")}
    assert actual_root_files == allowed_root_files, (
        f"Unexpected .py files in contract_kb/ root: {actual_root_files ^ allowed_root_files}"
    )


def test_tools_root_convergence_and_thin_wrappers() -> None:
    """Verify tools/ root contains only __init__.py and thin compatibility wrappers."""
    allowed_tools_root = {
        "__init__.py",
        "contract_diff_web.py",
        "cross_verify_and_merge.py",
        "diff_contract.py",
        "live_db_merge.py",
        "material_catalog.py",
        "sync_wecom_cli.py",
        "sync_wecom_wedrive.py",
        "tender_case_export.py",
    }
    actual_tools_root = {p.name for p in (PROJECT_ROOT / "tools").glob("*.py")}
    assert actual_tools_root == allowed_tools_root, (
        f"Unexpected .py files in tools/ root: {actual_tools_root ^ allowed_tools_root}"
    )

    for fname in allowed_tools_root:
        p = PROJECT_ROOT / "tools" / fname
        lines = len(p.read_text(encoding="utf-8").splitlines())
        assert lines <= 85, (
            f"tools/{fname} should be a thin compatibility wrapper (<= 85 lines), got {lines} lines"
        )


def test_repo_root_convergence() -> None:
    """Verify repository root has only start_gui.py as a thin compatibility entrypoint."""
    actual_repo_root = {p.name for p in PROJECT_ROOT.glob("*.py")}
    assert actual_repo_root == {"start_gui.py"}
    lines = len(
        (PROJECT_ROOT / "start_gui.py").read_text(encoding="utf-8").splitlines()
    )
    assert lines <= 50, f"start_gui.py should be a thin wrapper, got {lines} lines"


def test_single_canonical_class_definitions() -> None:
    """Ensure ClauseRecord and Storage are not defined in multiple conflicting places."""
    clause_record_defs: list[str] = []
    storage_defs: list[str] = []

    for py_file in sorted((PROJECT_ROOT / "contract_kb").rglob("*.py")):
        rel = py_file.relative_to(PROJECT_ROOT).as_posix()
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=rel)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                if node.name == "ClauseRecord":
                    clause_record_defs.append(rel)
                elif node.name == "Storage":
                    storage_defs.append(rel)

    assert clause_record_defs == [
        "contract_kb/modules/document_processing/clause_extractor.py"
    ]
    assert storage_defs == ["contract_kb/infrastructure/persistence/sqlite_storage.py"]


def test_web_static_assets_present() -> None:
    """Verify decomposed Web workbench HTML, CSS, and JS assets exist in contract_kb/interfaces/web."""
    web_dir = PROJECT_ROOT / "contract_kb" / "interfaces" / "web"
    expected_assets = [
        web_dir / "templates" / "index.html",
        web_dir / "static" / "css" / "app.css",
        web_dir / "static" / "js" / "app.js",
        web_dir / "static" / "js" / "api_client.js",
        web_dir / "static" / "js" / "review_workspace.js",
        web_dir / "static" / "js" / "agent_stream.js",
    ]
    for asset in expected_assets:
        assert asset.is_file() and asset.stat().st_size > 100, (
            f"Missing or empty web asset: {asset}"
        )
