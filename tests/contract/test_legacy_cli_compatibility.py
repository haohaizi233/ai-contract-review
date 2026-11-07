# -*- coding: utf-8 -*-
"""Contract tests for legacy CLI tools compatibility, argument parsing, and dry-run safety."""

from __future__ import annotations

import subprocess
import sys

from tools.cross_verify_and_merge import keys_of, norm_no


def test_contract_number_normalization_and_compound_keys():
    """Verify contract number normalization handles empty, compound, and Chinese patterns."""
    assert norm_no("") == ""
    assert norm_no(None) == ""
    assert norm_no("  ht-2024-001  \n") == "HT-2024-001"

    # Compound keys splitting
    compound = "HT2024-001 / HT2024-002, HT2024-003；HT2024-004"
    keys = keys_of(compound)
    assert keys == ["HT2024-001", "HT2024-002", "HT2024-003", "HT2024-004"]

    # Chinese contract numbers and paths
    cn_no = "深专合字[2024]第001号"
    assert norm_no(cn_no) == "深专合字[2024]第001号"
    assert keys_of(cn_no) == ["深专合字[2024]第001号"]


def test_cross_verify_and_merge_cli_dry_run_and_help():
    """Verify tools/cross_verify_and_merge.py supports --help and --dry-run cleanly."""
    res_help = subprocess.run(
        [sys.executable, "tools/cross_verify_and_merge.py", "--help"],
        capture_output=True,
        text=True,
        errors="replace",
    )
    assert res_help.returncode == 0
    assert (
        "cross_verify_and_merge" in res_help.stdout or "help" in res_help.stdout.lower()
    )

    res_dry = subprocess.run(
        [
            sys.executable,
            "tools/cross_verify_and_merge.py",
            "--dry-run",
            "--limit",
            "5",
        ],
        capture_output=True,
        text=True,
        errors="replace",
    )
    assert res_dry.returncode == 0, f"Dry-run failed with stderr: {res_dry.stderr}"
    assert (
        "DRY_RUN" in res_dry.stdout
        or "完成" in res_dry.stdout
        or "dry_run" in res_dry.stdout.lower()
    )


def test_live_db_merge_cli_dry_run_and_help():
    """Verify tools/live_db_merge.py supports --help and --dry-run cleanly."""
    res_help = subprocess.run(
        [sys.executable, "tools/live_db_merge.py", "--help"],
        capture_output=True,
        text=True,
        errors="replace",
    )
    assert res_help.returncode == 0

    res_dry = subprocess.run(
        [sys.executable, "tools/live_db_merge.py", "--dry-run", "--limit", "5"],
        capture_output=True,
        text=True,
        errors="replace",
    )
    assert res_dry.returncode == 0, f"Dry-run failed with stderr: {res_dry.stderr}"
    assert (
        "DRY_RUN" in res_dry.stdout
        or "完成" in res_dry.stdout
        or "dry_run" in res_dry.stdout.lower()
    )


def test_tender_case_export_cli_dry_run_and_help():
    """Verify tools/tender_case_export.py supports --help and --dry-run cleanly."""
    res_help = subprocess.run(
        [sys.executable, "tools/tender_case_export.py", "--help"],
        capture_output=True,
        text=True,
        errors="replace",
    )
    assert res_help.returncode == 0

    res_dry = subprocess.run(
        [
            sys.executable,
            "tools/tender_case_export.py",
            "--dry-run",
            "--limit",
            "5",
            "--no-web",
            "--no-fetch",
        ],
        capture_output=True,
        text=True,
        errors="replace",
    )
    assert res_dry.returncode == 0, f"Dry-run failed with stderr: {res_dry.stderr}"
