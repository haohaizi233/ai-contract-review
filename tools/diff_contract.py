# -*- coding: utf-8 -*-
"""Compatibility wrapper for tools/diff_contract.py."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.maintenance.diff_contract import (  # noqa: E402
    main,
    print_diff_report,
    run_diff,
)

__all__ = ["ROOT_DIR", "main", "print_diff_report", "run_diff"]

if __name__ == "__main__":
    main()
