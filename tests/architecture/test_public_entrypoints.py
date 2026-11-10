# -*- coding: utf-8 -*-
"""Public entrypoint and compatibility facade execution tests."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_core_public_imports_and_execution(tmp_path: Path) -> None:
    """Verify all mandatory public imports can be imported and execute real business paths."""
    import contract_kb
    import contract_kb.api
    import contract_kb.cli
    import contract_kb.full_diff
    import contract_kb.legal_agent
    import contract_kb.review_engine
    import contract_kb.storage
    import contract_kb.storage_pg
    import start_gui

    assert hasattr(contract_kb, "__version__")
    assert contract_kb.api.app is not None
    assert callable(contract_kb.cli.main)
    assert callable(start_gui.main)

    # 1. Storage facade execution
    db_file = tmp_path / "entrypoint_test.db"
    st = contract_kb.storage.Storage(db_file)
    try:
        assert st.list_documents() == []
        # 2. ReviewEngine facade execution
        engine = contract_kb.review_engine.ReviewEngine(st)
        status = engine.llm_status()
        assert "configured" in status
    finally:
        st.close()

    # 3. full_diff facade execution
    std_html, rev_html = contract_kb.full_diff.generate_word_diff_html(
        "第一条 甲方支付伍万元", "第一条 甲方支付拾万元"
    )
    assert "diff-del" in std_html and "diff-ins" in rev_html

    # 4. legal_agent facade execution
    agent = contract_kb.legal_agent.get_legal_agent()
    assert isinstance(agent, contract_kb.legal_agent.LegalReviewAgent)

    # 5. storage_pg facade exports
    assert contract_kb.storage_pg.StoragePG is contract_kb.storage_pg.PgVectorStorage


def test_cli_and_web_module_entrypoints_no_runpy_warning() -> None:
    """Verify `python -m contract_kb.cli` and `python -m contract_kb.interfaces.web.server --help` run cleanly."""
    res_cli = subprocess.run(
        [sys.executable, "-m", "contract_kb.cli", "help"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        errors="replace",
    )
    assert res_cli.returncode == 0
    assert "usage:" in res_cli.stdout
    assert "RuntimeWarning" not in res_cli.stderr

    res_web = subprocess.run(
        [sys.executable, "-m", "contract_kb.interfaces.web.server", "--help"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        errors="replace",
    )
    assert res_web.returncode == 0
    assert "--port" in res_web.stdout
    assert "RuntimeWarning" not in res_web.stderr
