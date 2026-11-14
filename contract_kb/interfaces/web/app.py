# -*- coding: utf-8 -*-
"""合同比对与条款级 Diff 前端可视化工作台核心应用（Web Workbench App）。"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import shutil
import sys
import time
from typing import Any
import urllib.parse

from contract_kb.interfaces.http.auth import (
    ROLE_ADMIN,
    ROLE_REVIEWER,
    ROLE_VIEWER,
    authenticate_web_request,
    Principal,
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
    handle_get_preview_file,
    handle_get_ref_templates_catalog,
    handle_get_sample,
    handle_get_templates_list,
    handle_post_approve_feedback,
    handle_post_cancel,
    handle_post_export_training_data,
    handle_post_feedback,
    handle_post_human_decision,
    handle_post_materials_load_contract,
    handle_post_open_local,
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
    CURRENT_DOC_NAME,
    CURRENT_SESSION_FILE,
    execute_current_diff,
    get_ranked_templates_catalog,
    get_session_file,
    save_session_meta,
)

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

# Console pipes can fill when the desktop launcher stops draining stderr.
# Access logging must never block HTTP responses on that console pipe.
_ACCESS_LOGGER = logging.getLogger("contract_kb.web.access")
_ACCESS_LOGGER.setLevel(logging.INFO)
_ACCESS_LOGGER.propagate = False
if not _ACCESS_LOGGER.handlers:
    _access_handler = RotatingFileHandler(
        config.DATA_DIR / "web_access.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
        delay=True,
    )
    _access_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _ACCESS_LOGGER.addHandler(_access_handler)


def load_html_template() -> str:
    """从包内 templates/ 与 static/ 目录加载并组装完整前端模板（不依赖当前工作目录）。"""
    html_skeleton = (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
    css_content = (STATIC_DIR / "css" / "app.css").read_text(encoding="utf-8")
    vendor_files = [
        STATIC_DIR / "js" / "vendor" / "jszip.min.js",
        STATIC_DIR / "js" / "vendor" / "docx-preview.min.js",
    ]
    core_files = [
        STATIC_DIR / "js" / "app.js",
        STATIC_DIR / "js" / "api_client.js",
        STATIC_DIR / "js" / "review_workspace.js",
        STATIC_DIR / "js" / "agent_stream.js",
    ]
    js_files = [p for p in vendor_files if p.exists()] + core_files
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
    is_enterprise: bool = False,
) -> str:
    """生成内嵌初始默认高危用例数据与按置信度排序的母本清单的独立完整 HTML。"""
    if initial_diff_data is not None:
        data_json = _safe_json_embed(initial_diff_data)
    elif is_enterprise:
        # 企业模式下严禁未认证直接泄露活动合同 Diff
        data_json = "null"
    else:
        try:
            sample_file = config.GOLDEN_DIR / "mut_11_late_fee_change.docx"
            if not CURRENT_SESSION_FILE.exists():
                shutil.copyfile(sample_file, CURRENT_SESSION_FILE)
            default_s_file = get_session_file("default_session", tenant_id="default")
            if (
                not default_s_file.exists()
                or CURRENT_SESSION_FILE.stat().st_mtime > default_s_file.stat().st_mtime
            ):
                shutil.copyfile(CURRENT_SESSION_FILE, default_s_file)
                save_session_meta(
                    "default_session",
                    {
                        "doc_name": CURRENT_DOC_NAME,
                        "original_filename": "mut_11_late_fee_change.docx",
                        "forced_template_id": None,
                    },
                    tenant_id="default",
                )
            initial_data = execute_current_diff(CURRENT_SESSION_FILE)
            initial_data["session_id"] = "default_session"
            initial_data["tenant_id"] = "default"
            data_json = _safe_json_embed(initial_data)
        except Exception:
            data_json = "null"

    if is_enterprise:
        # 企业模式下严禁向未认证用户泄露母本清单、参考母本库以及敏感真实案例元数据
        catalog_json = "[]"
        ref_json = "[]"
        material_json = "{}"
    else:
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

    base_template = (
        load_html_template()
        if not HTML_TEMPLATE or not os.environ.get("PYTEST_CURRENT_TEST")
        else HTML_TEMPLATE
    )
    html = base_template.replace("__INITIAL_DATA_PLACEHOLDER__", data_json)
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


WEB_ROUTE_ROLES: dict[str, str] = {
    "GET /api/templates/list": ROLE_VIEWER,
    "GET /api/diff/agent_stream": ROLE_VIEWER,
    "GET /api/diff/agent_status": ROLE_VIEWER,
    "GET /api/diff/preview_file": ROLE_VIEWER,
    "GET /api/ref_templates/catalog": ROLE_VIEWER,
    "GET /api/materials/stats": ROLE_VIEWER,
    "GET /api/materials/cases": ROLE_VIEWER,
    "GET /api/materials/case_detail": ROLE_VIEWER,
    "GET /api/diff/sample": ROLE_REVIEWER,
    "POST /api/templates/reload": ROLE_ADMIN,
    "POST /api/diff/switch-template": ROLE_REVIEWER,
    "POST /api/diff/open_local": ROLE_REVIEWER,
    "POST /api/diff/prioritize_card": ROLE_REVIEWER,
    "POST /api/diff/toggle_pause": ROLE_REVIEWER,
    "POST /api/diff/rerun_review": ROLE_REVIEWER,
    "POST /api/diff/retry_card": ROLE_REVIEWER,
    "POST /api/diff/skip_card": ROLE_REVIEWER,
    "POST /api/diff/reset_circuit": ROLE_ADMIN,
    "POST /api/diff/feedback": ROLE_REVIEWER,
    "POST /api/diff/export_training_data": ROLE_ADMIN,
    "POST /api/diff/cancel": ROLE_REVIEWER,
    "POST /api/diff/human_decision": ROLE_REVIEWER,
    "POST /api/diff/upload": ROLE_REVIEWER,
    "POST /api/materials/load_contract": ROLE_REVIEWER,
    "POST /api/ref_templates/load": ROLE_REVIEWER,
}


class DiffVisualizerHandler(BaseHTTPRequestHandler):
    """处理前端页面与比对 API 请求（具备多会话/多租户隔离与安全防护）。"""

    protocol_version = "HTTP/1.1"
    _cached_raw_body: bytes | None = None

    def handle_one_request(self) -> None:
        self._cached_raw_body = None
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    def log_message(self, format: str, *args: Any) -> None:
        if args and any("Connection" in str(a) or "10053" in str(a) for a in args):
            return
        table = getattr(self, "_control_char_table", None)
        raw_msg = format % args
        message = raw_msg.translate(table) if table is not None else raw_msg
        _ACCESS_LOGGER.info("%s %s", self.address_string(), message)

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

    def get_request_body(self) -> bytes:
        """安全获取已由认证层单次读取并完成哈希校验的请求正文原始字节。"""
        if getattr(self, "_cached_raw_body", None) is not None:
            return self._cached_raw_body or b""
        cl_str = self.headers.get("content-length", "").strip()
        if cl_str:
            try:
                cl = int(cl_str)
                if cl < 0 or cl > 50 * 1024 * 1024:
                    self._cached_raw_body = b""
                else:
                    self._cached_raw_body = self.rfile.read(cl)
            except Exception:
                self._cached_raw_body = b""
        else:
            self._cached_raw_body = b""
        return self._cached_raw_body

    _storage_provider: Any = None

    @classmethod
    def set_storage_provider(cls, provider: Any) -> None:
        """显式注入受信任 Storage 实例或 Storage Provider。"""
        cls._storage_provider = provider

    @classmethod
    def get_storage(cls) -> Any:
        """安全解析并获取当前绑定的受信任 Storage 实例。"""
        prov = cls._storage_provider
        if callable(prov):
            try:
                return prov()
            except Exception:
                return None
        return prov

    def get_request_principal(self) -> Principal | None:
        return getattr(self, "_principal", None)

    def _get_request_identity(self) -> tuple[str, str, str]:
        """从 Query 参数或 Header 安全提取并校验会话标识、租户标识与用户标识（支持企业模式强验签与 RBAC）。"""
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)

        session_id = (
            qs.get("session_id", [""])[0]
            or self.headers.get("X-Session-ID", "").strip()
        )
        if not session_id:
            session_id = "default_session"

        raw_body = self.get_request_body()

        st = self.get_storage()
        principal = authenticate_web_request(
            method=self.command,
            path=parsed.path,
            query=parsed.query,
            headers=dict(self.headers),
            raw_body=raw_body,
            storage=st,
        )
        self._principal = principal

        # 集中式 RBAC 路由权限核验
        route_key = f"{self.command.upper()} {parsed.path}"
        req_role = WEB_ROUTE_ROLES.get(
            route_key,
            ROLE_ADMIN if parsed.path.startswith("/api/") else ROLE_VIEWER,
        )
        if not principal.has_role(req_role):
            raise PermissionError(
                f"权限不足: 路由 {route_key} 需要 {req_role} 角色，当前用户角色为 {principal.roles}"
            )

        return session_id, principal.tenant_id, principal.user_id

    def do_GET(self) -> None:
        try:
            self._do_GET()
        except PermissionError as e:
            self._send_json(403, {"error": str(e)})

    def _do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path in ("/", "/index.html"):
            is_ent = os.environ.get(
                "DIFF_WEB_MODE", ""
            ).upper() == "ENTERPRISE" or bool(
                os.environ.get("CONTRACT_KB_AUTH_SHARED_KEY")
            )
            html = generate_rendered_html(is_enterprise=is_ent)
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
        elif parsed.path == "/api/diff/preview_file":
            handle_get_preview_file(self, parsed)
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
        elif parsed.path == "/api/diff/open_local":
            handle_post_open_local(self)
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
        elif parsed.path == "/api/diff/feedback/approve":
            handle_post_approve_feedback(self)
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


def create_web_server(
    host: str = "127.0.0.1",
    port: int = 8089,
    storage: Any = None,
) -> ThreadingHTTPServer:
    """创建多线程合同对比可视化 Web 服务实例（非本机回环必须启用企业认证模式并配置共享密钥）。"""
    is_loopback = host in ("127.0.0.1", "localhost", "::1")
    if not is_loopback:
        is_enterprise = os.environ.get(
            "DIFF_WEB_MODE", ""
        ).upper() == "ENTERPRISE" or bool(os.environ.get("CONTRACT_KB_AUTH_SHARED_KEY"))
        shared_key = os.environ.get("CONTRACT_KB_AUTH_SHARED_KEY", "").strip()
        if not is_enterprise or not shared_key:
            raise ValueError(
                f"安全阻断: 尝试在无认证/未配置共享密钥的单机模式下监听非本机地址 {host}。"
                "严禁将未受认证保护的服务暴露给局域网或公网，请启用 DIFF_WEB_MODE=ENTERPRISE 并配置 CONTRACT_KB_AUTH_SHARED_KEY。"
            )

    if storage is not None:
        DiffVisualizerHandler.set_storage_provider(storage)
        get_agent_manager().set_storage_provider(storage)

    return ThreadingHTTPServer((host, port), DiffVisualizerHandler)


def start_local_default_review() -> None:
    """预加载本地示例，使用与本地 HTTP 请求相同的认证身份。"""
    if os.environ.get("DIFF_WEB_MODE", "").upper() == "ENTERPRISE" or bool(
        os.environ.get("CONTRACT_KB_AUTH_SHARED_KEY")
    ):
        return

    principal = authenticate_web_request(
        method="GET", path="/", query="", headers={}, raw_body=b""
    )
    if CURRENT_SESSION_FILE.exists():
        default_file = get_session_file(
            "default_session", tenant_id=principal.tenant_id
        )
        shutil.copyfile(CURRENT_SESSION_FILE, default_file)
        save_session_meta(
            "default_session",
            {
                "doc_name": CURRENT_DOC_NAME,
                "original_filename": "mut_11_late_fee_change.docx",
                "forced_template_id": None,
            },
            tenant_id=principal.tenant_id,
        )
    initial_data = execute_current_diff(CURRENT_SESSION_FILE)
    initial_data["session_id"] = "default_session"
    initial_data["tenant_id"] = principal.tenant_id
    initial_data["user_id"] = principal.user_id
    get_agent_manager().start_document_review(
        initial_data,
        session_id="default_session",
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
    )


def main(argv: list[str] | None = None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

    # 引导期显式初始化受信任持久化存储
    try:
        from contract_kb.storage import create_storage

        st = create_storage()
        DiffVisualizerHandler.set_storage_provider(st)
        get_agent_manager().set_storage_provider(st)
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="合同整版原格式对照与右侧批注前端服务")
    parser.add_argument("--port", type=int, default=8089, help="Web 端口（默认 8089）")
    parser.add_argument(
        "--export-only", action="store_true", help="仅导出独立 HTML 页面"
    )
    args = parser.parse_args(argv)

    default_sample = config.GOLDEN_DIR / "mut_11_late_fee_change.docx"
    if default_sample.exists():
        need_reset = not CURRENT_SESSION_FILE.exists()
        if not need_reset:
            try:
                if (
                    CURRENT_SESSION_FILE.stat().st_size < 100
                    or CURRENT_SESSION_FILE.read_bytes()[:4] != b"PK\x03\x04"
                ):
                    need_reset = True
            except Exception:
                need_reset = True
        if need_reset:
            shutil.copyfile(default_sample, CURRENT_SESSION_FILE)

    if CURRENT_SESSION_FILE.exists():
        start_local_default_review()

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
