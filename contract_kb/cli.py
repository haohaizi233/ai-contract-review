# -*- coding: utf-8 -*-
"""Compatibility facade for `contract_kb.cli`.

Canonical implementation lives in `contract_kb.interfaces.cli.main`.
"""

from __future__ import annotations

from contract_kb.interfaces.cli.main import (
    _cmd_inventory,
    _cmd_refresh,
    _do_import,
    _load_or_import,
    main,
)

__all__ = ["_cmd_inventory", "_cmd_refresh", "_do_import", "_load_or_import", "main"]


if __name__ == "__main__":
    main()
