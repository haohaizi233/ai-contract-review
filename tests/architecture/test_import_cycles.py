# -*- coding: utf-8 -*-
"""Automated import cycle detection for contract_kb."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.audit_architecture import audit_repository  # noqa: E402


def test_zero_import_cycles_in_contract_kb() -> None:
    """Ensure contract_kb has zero strongly-connected circular import components."""
    report = audit_repository(PROJECT_ROOT)
    cycles = report["summary"]["contract_kb_cycles"]
    assert cycles == [], f"Circular import cycles detected in contract_kb: {cycles}"
