# -*- coding: utf-8 -*-
"""Compatibility entrypoint for the Desktop GUI Dashboard.

Canonical implementation lives in `contract_kb.interfaces.desktop`.
"""

from __future__ import annotations

from contract_kb.interfaces.desktop.app import (
    PROJECT_ROOT,
    PYTEST_EXE,
    PYTHON_EXE,
    ProcessRunner,
    StartGUI,
    main,
)

__all__ = [
    "PROJECT_ROOT",
    "PYTEST_EXE",
    "PYTHON_EXE",
    "ProcessRunner",
    "StartGUI",
    "main",
]


if __name__ == "__main__":
    main()
