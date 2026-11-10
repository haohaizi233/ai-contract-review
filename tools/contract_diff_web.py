# -*- coding: utf-8 -*-
"""向后兼容启动入口（Compatibility Entrypoint）：Web 审核工作台已模块化迁移至 `contract_kb.interfaces.web`。

用法：
  python tools/contract_diff_web.py [--port 8089]
  python tools/contract_diff_web.py --export-only
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from contract_kb.interfaces.web import (  # noqa: E402
    CATEGORY_LABELS,
    CURRENT_DOC_NAME,
    CURRENT_FORCED_TEMPLATE_ID,
    CURRENT_SESSION_FILE,
    DiffVisualizerHandler,
    HTML_TEMPLATE,
    SESSION_CACHE_DIR,
    clean_template_title,
    create_web_server,
    execute_current_diff,
    export_standalone_html,
    extract_template_version,
    generate_rendered_html,
    get_ranked_templates_catalog,
    get_session_dir,
    get_session_file,
    get_session_meta,
    hot_reload_template_library,
    is_hex_hash,
    load_html_template,
    main,
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
    "ROOT_DIR",
    "SESSION_CACHE_DIR",
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


if __name__ == "__main__":
    main()
