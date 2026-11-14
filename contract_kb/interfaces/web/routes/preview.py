# -*- coding: utf-8 -*-
"""合同原件与基准母本高保真在线预览及本地打开路由（Document Preview Routes）。

功能：
1. 定位当前会话中送审修改稿或基准母本的物理原始文件 (.docx / .doc / .pdf)；
2. 提供 /api/diff/preview_file 接口：
   - info=1 返回文件元数据（大小、哈希、格式、标题）；
   - 默认直接以二进制流输出原始文件（具备 Content-Disposition: inline），供前端通过 docx-preview 在线秒级高保真渲染；
3. 提供 /api/diff/open_local 接口：本机 loopback 环境下一键调用系统默认 Office/WPS 打开。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
import urllib.parse

from contract_kb import config
from contract_kb.infrastructure.persistence.sqlite_storage import create_storage
from contract_kb.modules.document_processing.ingestion import import_template_file
from contract_kb.modules.templates.material_catalog import DEFAULT_REF_DIR

from ..services.diff_service import clean_template_title
from ..services.session_files import (
    CURRENT_DOC_NAME,
    CURRENT_FORCED_TEMPLATE_ID,
    CURRENT_SESSION_FILE,
    get_session_file,
    get_session_meta,
)

MIME_TYPES: dict[str, str] = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".pdf": "application/pdf",
    ".xml": "application/xml",
    ".txt": "text/plain; charset=utf-8",
}


def _calc_sha256(path: Path) -> str:
    """计算物理文件 SHA-256 哈希。"""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return ""


_TECHNICAL_NAME_RE = re.compile(
    r"^(contract\.\w+|current_active_review.*|mut_\d+_.*|verify_.*|golden_.*|WDCLI_[a-f0-9]+_.*|MB\w+?_V[\d.]+?_.*)$",
    re.IGNORECASE,
)


def _is_technical_name(name: str) -> bool:
    """判断是否为内部技术/测试占位文件名（不应直接面向用户展示）。"""
    return bool(_TECHNICAL_NAME_RE.match(Path(name).name))


def _friendly_file_name(title: str, ext: str) -> str:
    """由合同业务标题生成可读文件名，如 《xxx》送审修改稿 -> xxx-送审修改稿.docx。"""
    t = re.sub(r"[\\/:*?\"<>|]", "", re.sub(r"[《》]", " ", title or "")).strip()
    t = re.sub(r"\s+", "-", t) or "送审合同"
    return t if t.lower().endswith(ext) else f"{t}{ext}"


def resolve_preview_file(
    session_id: str = "default_session",
    target: str = "reviewed",
    tenant_id: str = "default",
) -> tuple[Path | None, dict[str, Any]]:
    """定位当前送审合同或基准母本的物理文件并提取展示元数据。"""
    target = (target or "reviewed").strip().lower()

    if target == "reviewed":
        s_file = get_session_file(session_id, tenant_id=tenant_id)
        if not s_file.exists():
            s_file = CURRENT_SESSION_FILE
        elif (
            session_id == "default_session"
            and CURRENT_SESSION_FILE.exists()
            and CURRENT_SESSION_FILE.stat().st_mtime > s_file.stat().st_mtime
        ):
            # 单机工作台模式下，全局活动合同较会话旧文件更新时执行物理同步
            try:
                import shutil

                shutil.copyfile(CURRENT_SESSION_FILE, s_file)
            except Exception:
                pass
            s_file = CURRENT_SESSION_FILE

        if not s_file.exists():
            s_file = config.GOLDEN_DIR / "mut_11_late_fee_change.docx"

        if not s_file.exists():
            return None, {"error": "送审合同文件不存在"}

        meta = get_session_meta(session_id, tenant_id=tenant_id)
        doc_name = meta.get("doc_name") or CURRENT_DOC_NAME
        ext = s_file.suffix.lower()

        # 智能解析面向用户的真实文件名，彻底杜绝暴露内部技术占位文件名 contract.docx
        orig_filename = meta.get("original_filename")
        if not orig_filename or _is_technical_name(orig_filename):
            orig_filename = _friendly_file_name(doc_name, ext)

        sha = _calc_sha256(s_file)
        size = s_file.stat().st_size

        return s_file, {
            "target": "reviewed",
            "file_name": orig_filename,
            "original_filename": orig_filename,
            "display_title": doc_name,
            "ext": ext,
            "size": size,
            "sha256": sha,
            "mime_type": MIME_TYPES.get(ext, "application/octet-stream"),
        }

    # target == "template" (基准母本)
    st = create_storage()
    matched_tpl = None
    try:
        meta = get_session_meta(session_id, tenant_id=tenant_id)
        forced_id = meta.get("forced_template_id") or CURRENT_FORCED_TEMPLATE_ID
        if forced_id:
            matched_tpl = st.get_document(forced_id)

        if not matched_tpl:
            # 自动探测当前送审稿的子类/大类进行匹配
            rev_file, _ = resolve_preview_file(
                session_id=session_id, target="reviewed", tenant_id=tenant_id
            )
            if rev_file and rev_file.exists():
                try:
                    rec, _ = import_template_file(rev_file)
                    if rec:
                        matched_tpl = st.find_active_template(
                            contract_subtype=rec.subtype, business_category=rec.category
                        )
                        if not matched_tpl and rec.category:
                            matched_tpl = st.find_active_template(
                                business_category=rec.category
                            )
                except Exception:
                    pass

        if not matched_tpl:
            matched_tpl = st.get_document("MB20251205013") or st.find_active_template()
    finally:
        st.close()

    tpl_path: Path | None = None
    src_file = matched_tpl.get("source_file") if matched_tpl else ""

    # 1. 直接依据 source_file 定位
    if src_file:
        candidates = [
            config.TEMPLATE_DIR / src_file,
            DEFAULT_REF_DIR / src_file,
            config.PROJECT_ROOT / "tests" / "fixtures" / "synth_corpus" / src_file,
        ]
        for c in candidates:
            if c.exists():
                tpl_path = c
                break

        if not tpl_path and config.TEMPLATE_DIR.exists():
            matches = list(config.TEMPLATE_DIR.rglob(src_file))
            if matches:
                tpl_path = matches[0]

        if not tpl_path and DEFAULT_REF_DIR.exists():
            matches = list(DEFAULT_REF_DIR.rglob(src_file))
            if matches:
                tpl_path = matches[0]

    # 2. 若依据微盘文件名未命中，通过剥离前缀/后缀纯净名称智能模糊匹配
    if not tpl_path:
        stem_raw = Path(src_file).stem if src_file else (matched_tpl.get("name") or "")
        clean_stem = re.sub(r"^(MB\w+?_V[\d\.]+?_|WDCLI_[a-f0-9]+_)", "", stem_raw)
        clean_base = re.sub(
            r"[-_](恒程|创新|代理|知识产权|事务所|公司|企业).*$", "", clean_stem
        ).strip()

        search_dirs = [config.TEMPLATE_DIR, DEFAULT_REF_DIR]
        for sdir in search_dirs:
            if not sdir.exists():
                continue
            for ext in ("*.docx", "*.doc"):
                for p in sdir.rglob(ext):
                    p_clean = re.sub(
                        r"[-_](恒程|创新|代理|知识产权|事务所|公司|企业).*$", "", p.stem
                    ).strip()
                    if clean_base and (
                        clean_base == p_clean
                        or clean_base in p.stem
                        or p_clean in clean_base
                    ):
                        if (
                            p.suffix.lower() == ".doc"
                            and p.with_suffix(".docx").exists()
                        ):
                            tpl_path = p.with_suffix(".docx")
                        else:
                            tpl_path = p
                        break
                if tpl_path:
                    break
            if tpl_path:
                break

    # 3. 兜底样本
    if not tpl_path:
        default_samples = [
            config.TEMPLATE_DIR / "专利" / "国内专利服务合同（年费缴纳）-事务所.docx",
            config.GOLDEN_DIR / "mut_11_late_fee_change.docx",
        ]
        for ds in default_samples:
            if ds.exists():
                tpl_path = ds
                break

    if not tpl_path or not tpl_path.exists():
        return None, {"error": "未能定位到基准母本原始文件"}

    # 若匹配到的是 .doc，且旁边存在转换后的 .docx，优先使用 .docx 实现高保真在线预览
    if tpl_path.suffix.lower() == ".doc" and tpl_path.with_suffix(".docx").exists():
        tpl_path = tpl_path.with_suffix(".docx")

    raw_title = clean_template_title(tpl_path.stem)
    sha = _calc_sha256(tpl_path)
    ext = tpl_path.suffix.lower()
    size = tpl_path.stat().st_size

    tpl_id = matched_tpl.get("document_id") if matched_tpl else ""
    display_title = (
        f"《{raw_title}》基准母本原件" if not raw_title.startswith("《") else raw_title
    )

    return tpl_path, {
        "target": "template",
        "template_id": tpl_id,
        "file_name": (
            _friendly_file_name(raw_title, ext)
            if _is_technical_name(tpl_path.name)
            else tpl_path.name
        ),
        "display_title": display_title,
        "ext": ext,
        "size": size,
        "sha256": sha,
        "mime_type": MIME_TYPES.get(ext, "application/octet-stream"),
    }


def handle_get_preview_file(handler: Any, parsed: urllib.parse.ParseResult) -> None:
    """输出文件元数据或直接以二进制流提供原格式文件在线预览。"""
    session_id, tenant_id, _ = handler._get_request_identity()
    qs = urllib.parse.parse_qs(parsed.query)
    target = qs.get("target", ["reviewed"])[0].lower()
    info_only = qs.get("info", ["0"])[0] in ("1", "true", "True")

    file_path, meta = resolve_preview_file(
        session_id=session_id, target=target, tenant_id=tenant_id
    )
    if not file_path or not file_path.exists():
        handler._send_json(404, {"error": f"未找到指定原文件 (target={target})"})
        return

    if info_only:
        handler._send_json(200, {"success": True, **meta})
        return

    try:
        data = file_path.read_bytes()
        ext = file_path.suffix.lower()
        content_type = MIME_TYPES.get(ext, "application/octet-stream")

        # 安全处置文件名防乱码，优先使用真实面向业务的文件名
        raw_name = meta.get("file_name") or file_path.name
        encoded_name = urllib.parse.quote(raw_name)
        ascii_name = re.sub(r"[^\x20-\x7E]", "_", raw_name)

        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header(
            "Content-Disposition",
            f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}",
        )
        handler.send_header("Cache-Control", "no-cache, must-revalidate")
        handler._send_cors_headers()
        handler.end_headers()
        handler.wfile.write(data)
    except Exception as e:
        handler._send_json(500, {"error": f"读取原文件失败: {e}"})


def handle_post_open_local(handler: Any) -> None:
    """在宿主桌面环境下使用系统关联软件 (Word/WPS) 直接打开原文件。"""
    session_id, tenant_id, _ = handler._get_request_identity()

    # 安全边界：仅允许来自本机 loopback 的桌面端请求调用本地打开
    client_ip = (
        handler.client_address[0] if getattr(handler, "client_address", None) else ""
    )
    if client_ip not in ("127.0.0.1", "::1", "localhost", "testserver"):
        handler._send_json(
            400,
            {
                "error": "remote_not_supported",
                "message": "远程环境不支持直接唤起宿主本地软件，请使用在线高保真预览或直接下载原件。",
            },
        )
        return

    raw_body = handler.get_request_body()
    try:
        data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        target = (data.get("target") or "reviewed").strip().lower()

        file_path, meta = resolve_preview_file(
            session_id=session_id, target=target, tenant_id=tenant_id
        )
        if not file_path or not file_path.exists():
            handler._send_json(
                404, {"error": f"本地未找到对应原文件 (target={target})"}
            )
            return

        if os.name == "nt" and hasattr(os, "startfile"):
            os.startfile(str(file_path.resolve()))  # type: ignore[attr-defined]
            handler._send_json(
                200,
                {
                    "success": True,
                    "message": f"已在系统默认程序中打开: {file_path.name}",
                    "file_name": file_path.name,
                    "resolved_path": str(file_path.resolve()),
                },
            )
        else:
            handler._send_json(
                200,
                {
                    "success": True,
                    "message": f"本地文件路径: {file_path.resolve()}",
                    "file_name": file_path.name,
                    "resolved_path": str(file_path.resolve()),
                },
            )
    except Exception as e:
        handler._send_json(500, {"error": f"调用本地打开失败: {e}"})
