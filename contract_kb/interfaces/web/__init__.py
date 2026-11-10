# -*- coding: utf-8 -*-
"""Web 可视化工作台接口层（Web Interfaces）。"""

from __future__ import annotations

from typing import Any

from .app import (
    DiffVisualizerHandler,
    HTML_TEMPLATE,
    STATIC_DIR,
    TEMPLATES_DIR,
    WEB_DIR,
    export_standalone_html,
    generate_rendered_html,
    load_html_template,
)
from .services import (
    CATEGORY_LABELS,
    CURRENT_DOC_NAME,
    CURRENT_FORCED_TEMPLATE_ID,
    CURRENT_SESSION_FILE,
    SESSION_CACHE_DIR,
    clean_template_title,
    execute_current_diff,
    extract_template_version,
    get_ranked_templates_catalog,
    get_session_dir,
    get_session_file,
    get_session_meta,
    hot_reload_template_library,
    is_hex_hash,
    prepare_session_file,
    save_session_meta,
)

__all__ = [
    "CATEGORY_LABELS",
    "CURRENT_DOC_NAME",
    "CURRENT_FORCED_TEMPLATE_ID",
    "CURRENT_SESSION_FILE",
    "DiffVisualizerHandler",
    "HTML_TEMPLATE",
    "SESSION_CACHE_DIR",
    "STATIC_DIR",
    "TEMPLATES_DIR",
    "WEB_DIR",
    "clean_template_title",
    "create_web_server",
    "execute_current_diff",
    "export_standalone_html",
    "extract_template_version",
    "generate_rendered_html",
    "get_ranked_templates_catalog",
    "get_session_dir",
    "get_session_file",
    "get_session_meta",
    "hot_reload_template_library",
    "is_hex_hash",
    "load_html_template",
    "main",
    "prepare_session_file",
    "save_session_meta",
]


def __getattr__(name: str) -> Any:
    if name in ("create_web_server", "main"):
        from . import server

        return getattr(server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
