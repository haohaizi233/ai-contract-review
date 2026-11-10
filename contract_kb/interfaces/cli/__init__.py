# -*- coding: utf-8 -*-
"""CLI interface package."""

from __future__ import annotations

from typing import Any

__all__ = ["_cmd_inventory", "_cmd_refresh", "_do_import", "_load_or_import", "main"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from . import main as cli_main

        return getattr(cli_main, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
