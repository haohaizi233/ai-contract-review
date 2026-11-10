# -*- coding: utf-8 -*-
"""Desktop GUI interface package."""

from __future__ import annotations

from typing import Any

__all__ = [
    "PROJECT_ROOT",
    "PYTEST_EXE",
    "PYTHON_EXE",
    "ProcessRunner",
    "StartGUI",
    "main",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from . import app

        return getattr(app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
