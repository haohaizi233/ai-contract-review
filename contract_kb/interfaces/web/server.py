# -*- coding: utf-8 -*-
"""Web 工作台 HTTP 服务启动器与 CLI 入口（Web Server）。"""

from __future__ import annotations

from .app import DiffVisualizerHandler, create_web_server, export_standalone_html, main

__all__ = [
    "DiffVisualizerHandler",
    "create_web_server",
    "export_standalone_html",
    "main",
]


if __name__ == "__main__":
    main()
