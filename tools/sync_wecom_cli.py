# -*- coding: utf-8 -*-
"""Compatibility wrapper for tools/sync_wecom_cli.py."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.integrations.wecom.sync_wecom_cli import *  # noqa: F401, F403, E402
from tools.integrations.wecom.sync_wecom_cli import main  # noqa: E402

if __name__ == "__main__":
    main()
