# -*- coding: utf-8 -*-
"""向后兼容导出层：模板五级路由与排他规则字典已迁移至领域模块 `contract_kb.modules.templates.catalog_data`。"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT_DIR = Path(__file__).resolve().parents[2]
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

from contract_kb.modules.templates.catalog_data import (  # noqa: E402
    DISAMBIGUATION_GROUPS,
    TEMPLATE_5TH_LAYER_RULES,
    TEMPLATE_HIERARCHY_MAP,
)

__all__ = [
    "DISAMBIGUATION_GROUPS",
    "TEMPLATE_5TH_LAYER_RULES",
    "TEMPLATE_HIERARCHY_MAP",
]
