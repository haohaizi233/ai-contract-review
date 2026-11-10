# -*- coding: utf-8 -*-
"""合同比对与条款级 Diff 前端可视化工作台核心应用（Web Workbench App）。"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import os
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any
import urllib.parse

from contract_kb.interfaces.http.auth import (
    compute_auth_signature_v2,
    EMPTY_BODY_SHA256,
)
from contract_kb import config
from contract_kb.application.agents.review_sessions import get_agent_manager
from contract_kb.modules.templates.material_catalog import (
    DEFAULT_REF_DIR,
    get_material_manager,
    get_ref_templates_catalog,
)

from .routes import (
    handle_get_agent_status,
    handle_get_agent_stream,
    handle_get_materials_case_detail,
    handle_get_materials_cases,
    handle_get_materials_stats,
    handle_get_ref_templates_catalog,
    handle_get_sample,
    handle_get_templates_list,
    handle_post_cancel,
    handle_post_export_training_data,
    handle_post_feedback,
    handle_post_human_decision,
    handle_post_materials_load_contract,
    handle_post_prioritize_card,
    handle_post_ref_templates_load,
    handle_post_rerun_review,
    handle_post_reset_circuit,
    handle_post_retry_card,
    handle_post_skip_card,
    handle_post_switch_template,
    handle_post_templates_reload,
    handle_post_toggle_pause,
    handle_post_upload,
)
from .services import (
    CURRENT_SESSION_FILE,
    execute_current_diff,
    get_ranked_templates_catalog,
)

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


def load_html_template() -> str:
    """从包内 templates/ 与 static/ 目录加载并组装完整前端模板（不依赖当前工作目录）。"""
    html_skeleton = (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
    css_content = (STATIC_DIR / "css" / "app.css").read_text(encoding="utf-8")
    js_files = [
        STATIC_DIR / "js" / "app.js",
        STATIC_DIR / "js" / "api_client.js",
        STATIC_DIR / "js" / "review_workspace.js",
        STATIC_DIR / "js" / "agent_stream.js",
    ]
    js_content = "\n\n".join(p.read_text(encoding="utf-8") for p in js_files)
    html = html_skeleton.replace("/* __APP_CSS_PLACEHOLDER__ */", css_content)
    html = html.replace("/* __APP_JS_PLACEHOLDER__ */", js_content)
    return html


HTML_TEMPLATE: str = load_html_template()


def _safe_json_embed(obj: Any) -> str:
    """安全将 Python 对象序列化为可安全内嵌在 <script> 标签中的 JSON，杜绝闭合标签注入 XSS。"""
    raw = json.dumps(obj, ensure_ascii=False)
    return (
        raw.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def generate_rendered_html(
    initial_diff_data: dict[str, Any] | None = None,
) -> str:
    """生成内嵌初始默认高危用例数据与按置信度排序的母本清单的独立完整 HTML。"""
    if initial_diff_data is not None:
        data_json = _safe_json_embed(initial_diff_data)
    else:
        try:
            sample_file = config.GOLDEN_DIR / "mut_11_late_fee_change.docx"
            if not CURRENT_SESSION_FILE.exists():
                shutil.copyfile(sample_file, CURRENT_SESSION_FILE)
            initial_data = execute_current_diff(CURRENT_SESSION_FILE)
            data_json = _safe_json_embed(initial_data)
        except Exception:
            data_json = "null"

    try:
        catalog = get_ranked_templates_catalog(CURRENT_SESSION_FILE)
        catalog_json = _safe_json_embed(catalog)
    except Exception:
        catalog_json = "[]"

    try:
        ref_catalog = get_ref_templates_catalog()
        ref_json = _safe_json_embed(ref_catalog)
    except Exception:
        ref_json = "[]"

    try:
        mgr = get_material_manager()
        stats = mgr.get_stats()
        initial_cases = mgr.search_cases(
            category="ALL", has_word_only=True, page=1, page_size=20
        )
        material_data = {
            "stats": stats,
            "initial_cases": initial_cases,
        }
        material_json = _safe_json_embed(material_data)
    except Exception:
        material_json = "{}"

    html = HTML_TEMPLATE.replace("__INITIAL_DATA_PLACEHOLDER__", data_json)
    html = html.replace("__ALL_TEMPLATES_PLACEHOLDER__", catalog_json)
    html = html.replace("__REF_TEMPLATES_PLACEHOLDER__", ref_json)
    html = html.replace("__MATERIAL_DATA_PLACEHOLDER__", material_json)
    return html


def export_standalone_html() -> None:
    """将整版对比与批注工作台导出为独立 HTML 页面存放在 data/contract_merged 与 参考/ 供直接双击使用。"""
    html_content = generate_rendered_html()

    out_path = (
        config.DATA_DIR.parent / "contract_merged" / "合同模板条款比对与Diff可视化.html"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_content, encoding="utf-8")
    print(f"[已生成素材库独立整版原格式对照与右侧批注网页] {out_path}")

    ref_out = DEFAULT_REF_DIR / "合同模板条款比对与Diff可视化.html"
    if DEFAULT_REF_DIR.exists():
        try:
            ref_out.write_text(html_content, encoding="utf-8")
            print(f"[已同步生成模板库独立对比网页] {ref_out}")
        except Exception:
            pass


class DiffVisualizerHandler(BaseHTTPRequestHandler):
    """处理前端页面与比对 API 请求（具备多会话/多租户隔离与安全防护）。"""

    protocol_version = "HTTP/1.1"

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    def log_message(self, format: str, *args: Any) -> None:
        if args and any("Connection" in str(a) or "10053" in str(a) for a in args):
            return
        super().log_message(format, *args)

    def _send_cors_headers(self):
        origin = self.headers.get("Origin", "")
        if origin:
            parsed = urllib.parse.urlparse(origin)
            h = (parsed.hostname or "").lower()
            if h in ("127.0.0.1", "localhost", "::1", "testserver"):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Credentials", "true")
                self.send_header("Vary", "Origin")
                return

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-Session-ID, X-Tenant-ID, X-User-ID, X-User, X-Roles, X-Tenant, X-Auth-Signature, X-Auth-Timestamp, X-Auth-Nonce, X-Content-SHA256",
        )
        self.end_headers()

    def _send_json(self, status_code: int, data: Any):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _get_request_identity(self) -> tuple[str, str, str]:
        """从 Query 参数或 Header 安全提取并校验会话标识、租户标识与用户标识。"""
        # 1. 跨站写保护（CSRF / Origin 防护）
        if self.command in ("POST", "PUT", "DELETE", "PATCH"):
            origin = self.headers.get("Origin", "")
            if origin:
                parsed_orig = urllib.parse.urlparse(origin)
                h = (parsed_orig.hostname or "").lower()
                allowed_hosts = {"127.0.0.1", "localhost", "::1", "testserver"}
                custom_allowed = os.environ.get("CONTRACT_WEB_ALLOWED_HOSTS", "").split(
                    ","
                )
                for ca in custom_allowed:
                    if ca.strip():
                        allowed_hosts.add(ca.strip().lower())
                if h not in allowed_hosts:
                    raise PermissionError(f"跨站请求伪造被拦截: 非受信 Origin {origin}")

        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)

        session_id = (
            qs.get("session_id", [""])[0]
            or self.headers.get("X-Session-ID")
            or "default_session"
        )

        is_enterprise = os.environ.get(
            "DIFF_WEB_MODE", ""
        ).upper() == "ENTERPRISE" or bool(os.environ.get("CONTRACT_KB_AUTH_SHARED_KEY"))

        if is_enterprise:
            user = self.headers.get("X-User", "").strip()
            roles_raw = self.headers.get("X-Roles", "").strip()
            tenant = self.headers.get("X-Tenant", "").strip() or "default"
            sig = self.headers.get("X-Auth-Signature", "").strip()
            ts = self.headers.get("X-Auth-Timestamp", "").strip()
            nonce = self.headers.get("X-Auth-Nonce", "").strip()
            body_sha = (
                self.headers.get("X-Content-SHA256", "").strip() or EMPTY_BODY_SHA256
            )

            if not user or not roles_raw or not sig or not ts or not nonce:
                raise PermissionError(
                    "企业模式下缺少受信任认证签名 Header (X-User, X-Roles, X-Auth-Signature, X-Auth-Timestamp, X-Auth-Nonce)"
                )

            key = os.environ.get("CONTRACT_KB_AUTH_SHARED_KEY", "")
            if key:
                expected_sig = compute_auth_signature_v2(
                    key=key,
                    method=self.command,
                    path=parsed.path,
                    query=parsed.query,
                    user=user,
                    roles=roles_raw,
                    tenant=tenant,
                    timestamp=ts,
                    nonce=nonce,
                    body_sha256=body_sha,
                )
                if not hmac.compare_digest(sig, expected_sig):
                    raise PermissionError("认证签名校验失败")

            return session_id, tenant, user

        # 单机本地开发模式 (LOCAL_DEV)
        # 禁止客户端自报未经签名的非 default 外部租户
        declared_tenant = (
            qs.get("tenant_id", [""])[0] or self.headers.get("X-Tenant-ID", "").strip()
        )
        if declared_tenant and declared_tenant != "default":
            raise PermissionError(
                f"单机本地模式禁止未经认证访问或伪造非 default 租户身份: {declared_tenant}"
            )

        user_id = self.headers.get("X-User-ID", "").strip() or "local_dev_user"
        return session_id, "default", user_id

    def do_GET(self) -> None:
        try:
            self._do_GET()
        except PermissionError as e:
            self._send_json(403, {"error": str(e)})

    def _do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path in ("/", "/index.html"):
            html = generate_rendered_html()
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path.startswith("/static/"):
            rel = parsed.path.removeprefix("/static/").lstrip("/")
            asset = (STATIC_DIR / rel).resolve()
            if asset.is_relative_to(STATIC_DIR.resolve()) and asset.is_file():
                ctype = (
                    "text/css; charset=utf-8"
                    if asset.suffix == ".css"
                    else "application/javascript; charset=utf-8"
                )
                body = asset.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()
        elif parsed.path == "/api/diff/agent_stream":
            handle_get_agent_stream(self)
        elif parsed.path == "/api/templates/list":
            handle_get_templates_list(self)
        elif parsed.path == "/api/diff/sample":
            handle_get_sample(self, parsed)
        elif parsed.path == "/api/diff/agent_status":
            handle_get_agent_status(self)
        elif parsed.path == "/api/ref_templates/catalog":
            handle_get_ref_templates_catalog(self)
        elif parsed.path == "/api/materials/stats":
            handle_get_materials_stats(self)
        elif parsed.path == "/api/materials/cases":
            handle_get_materials_cases(self, parsed)
        elif parsed.path == "/api/materials/case_detail":
            handle_get_materials_case_detail(self, parsed)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        try:
            self._do_POST()
        except PermissionError as e:
            self._send_json(403, {"error": str(e)})

    def _do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/api/templates/reload":
            handle_post_templates_reload(self)
        elif parsed.path == "/api/diff/switch-template":
            handle_post_switch_template(self)
        elif parsed.path == "/api/diff/prioritize_card":
            handle_post_prioritize_card(self)
        elif parsed.path == "/api/diff/toggle_pause":
            handle_post_toggle_pause(self)
        elif parsed.path == "/api/diff/rerun_review":
            handle_post_rerun_review(self)
        elif parsed.path == "/api/diff/retry_card":
            handle_post_retry_card(self)
        elif parsed.path == "/api/diff/skip_card":
            handle_post_skip_card(self)
        elif parsed.path == "/api/diff/reset_circuit":
            handle_post_reset_circuit(self)
        elif parsed.path == "/api/diff/feedback":
            handle_post_feedback(self)
        elif parsed.path == "/api/diff/export_training_data":
            handle_post_export_training_data(self)
        elif parsed.path == "/api/diff/cancel":
            handle_post_cancel(self)
        elif parsed.path == "/api/diff/human_decision":
            handle_post_human_decision(self)
        elif parsed.path == "/api/diff/upload":
            handle_post_upload(self)
        elif parsed.path == "/api/materials/load_contract":
            handle_post_materials_load_contract(self)
        elif parsed.path == "/api/ref_templates/load":
            handle_post_ref_templates_load(self)
        else:
            self.send_response(404)
            self.end_headers()


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
