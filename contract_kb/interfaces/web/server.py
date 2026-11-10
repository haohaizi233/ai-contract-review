# -*- coding: utf-8 -*-
"""Web 工作台 HTTP 服务启动器与 CLI 入口（Web Server）。"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from http.server import ThreadingHTTPServer

from contract_kb import config
from contract_kb.application.agents.review_sessions import get_agent_manager

from .app import DiffVisualizerHandler, export_standalone_html
from .services import CURRENT_SESSION_FILE, execute_current_diff


def create_web_server(host: str = "127.0.0.1", port: int = 8089) -> ThreadingHTTPServer:
    """创建多线程合同对比可视化 Web 服务实例。"""
    return ThreadingHTTPServer((host, port), DiffVisualizerHandler)


def main(argv: list[str] | None = None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="合同整版原格式对照与右侧批注前端服务")
    parser.add_argument("--port", type=int, default=8089, help="Web 端口（默认 8089）")
    parser.add_argument(
        "--export-only", action="store_true", help="仅导出独立 HTML 页面"
    )
    args = parser.parse_args(argv)

    default_sample = config.GOLDEN_DIR / "mut_11_late_fee_change.docx"
    if default_sample.exists() and not CURRENT_SESSION_FILE.exists():
        shutil.copyfile(default_sample, CURRENT_SESSION_FILE)

    if CURRENT_SESSION_FILE.exists():
        initial_data = execute_current_diff(CURRENT_SESSION_FILE)
        get_agent_manager().start_document_review(initial_data)

    export_standalone_html()
    if args.export_only:
        return

    server = create_web_server("127.0.0.1", args.port)
    print(
        f"\n🚀 AI 合同审查 · 条款级 Diff 与整版原格式比对工作台已启动: http://127.0.0.1:{args.port}"
    )
    print(
        "👉 请在浏览器中打开上方网址，即可体验整版双栏原格式对照与右侧智能法务审阅批注！\n"
    )
    while True:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n服务已停止。")
            break
        except Exception as e:
            print(f"\n服务异常自动重连: {e}")
            time.sleep(0.5)


if __name__ == "__main__":
    main()
