# -*- coding: utf-8 -*-
"""Compatibility wrapper for tools/tender_case_export.py."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.integrations.tender.tender_case_export import *  # noqa: F401, F403, E402
from tools.integrations.tender.tender_case_export import main  # noqa: E402

if __name__ == "__main__":
    main()
