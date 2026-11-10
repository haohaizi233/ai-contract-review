# -*- coding: utf-8 -*-
"""模板库与素材库资产目录工具门面（实现位于 `contract_kb.modules.templates.material_catalog`）。"""

from __future__ import annotations

from contract_kb.modules.templates.material_catalog import (
    DEFAULT_MERGED_DIR,
    DEFAULT_REF_DIR,
    PROJECT_ROOT,
    REF_FOLDER_ORDER,
    MaterialLibraryManager,
    clean_template_title,
    get_material_manager,
    get_ref_templates_catalog,
)

__all__ = [
    "DEFAULT_MERGED_DIR",
    "DEFAULT_REF_DIR",
    "MaterialLibraryManager",
    "PROJECT_ROOT",
    "REF_FOLDER_ORDER",
    "clean_template_title",
    "get_material_manager",
    "get_ref_templates_catalog",
]
