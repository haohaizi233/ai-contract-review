# -*- coding: utf-8 -*-
"""Compatibility wrapper for tools/sync_wecom_wedrive.py."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.integrations.wecom.sync_wecom_wedrive import *  # noqa: F401, F403, E402
from tools.integrations.wecom.sync_wecom_wedrive import main  # noqa: E402

if __name__ == "__main__":
    main()
