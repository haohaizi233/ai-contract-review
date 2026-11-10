# -*- coding: utf-8 -*-
"""向后兼容门面（Compatibility Facade）：全量差异比对引擎已迁移至 `contract_kb.modules.contract_comparison.full_diff`。"""

from __future__ import annotations

from .modules.contract_comparison.full_diff import *  # noqa: F401, F403
from .modules.contract_comparison.full_diff import (  # noqa: F401
    _enhance_diff_tables,
    build_full_contract_diff,
    escape_html,
    generate_word_diff_html,
)
