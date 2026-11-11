# -*- coding: utf-8 -*-
"""合同上传与黄金样本加载路由（Contract Routes）。"""

from __future__ import annotations

import hashlib
import re
import shutil
import time
import urllib.parse
from pathlib import Path
from typing import Any

from contract_kb import config
from contract_kb.application.agents.review_sessions import get_agent_manager

from ..services.diff_service import execute_current_diff
from ..services.session_files import (
    CURRENT_SESSION_FILE,
    get_session_file,
    prepare_session_file,
    save_session_meta,
)


def handle_get_sample(handler: Any, parsed: urllib.parse.ParseResult) -> None:
    session_id, tenant_id, user_id = handler._get_request_identity()
    qs = urllib.parse.parse_qs(parsed.query)
    key = qs.get("key", ["prescreen_tampered"])[0]

    try:
        if key == "prescreen_tampered":
            sample_file = config.GOLDEN_DIR / "mut_11_late_fee_change.docx"
            doc_name = "《国内专利服务合同（年费缴纳）》送审修改稿"
        elif key == "missing_confidential":
            sample_file = config.GOLDEN_DIR / "mut_06_missing_confidentiality.docx"
            doc_name = "《国内专利服务合同（年费缴纳）》送审修改稿（保密义务被删）"
        else:
            sample_files = list(config.TEMPLATE_DIR.glob("**/*驳回复审*.docx"))
            sample_file = (
                sample_files[0]
                if sample_files
                else config.GOLDEN_DIR / "mut_11_late_fee_change.docx"
            )
            doc_name = "《商标驳回复审代理协议》送审原稿"

        s_file = get_session_file(session_id, tenant_id=tenant_id)
        shutil.copyfile(sample_file, s_file)
        if session_id == "default_session" and tenant_id == "default":
            try:
                shutil.copyfile(sample_file, CURRENT_SESSION_FILE)
            except Exception:
                pass
        save_session_meta(
            session_id,
            {
                "doc_name": doc_name,
                "original_filename": sample_file.name,
                "forced_template_id": None,
            },
            tenant_id=tenant_id,
        )

        res = execute_current_diff(s_file, forced_template_id=None, doc_name=doc_name)
        res["session_id"] = session_id
        res["tenant_id"] = tenant_id
        res["user_id"] = user_id
        get_agent_manager().start_document_review(
            res, session_id=session_id, tenant_id=tenant_id, user_id=user_id
        )
        handler._send_json(200, res)
    except Exception as e:
        handler._send_json(500, {"error": str(e)})


def handle_post_upload(handler: Any) -> None:
    length = int(handler.headers.get("content-length", 0))
    if length > 25 * 1024 * 1024:
        handler._send_json(
            413,
            {
                "error": "payload_too_large",
                "message": "上传文件大小超出 25MB 限制",
            },
        )
        return

    session_id, tenant_id, user_id = handler._get_request_identity()
    if session_id == "default_session":
        session_id = (
            f"sess_{hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:16]}"
        )

    ctype = handler.headers.get("content-type", "")
    if "multipart/form-data" in ctype and "boundary=" in ctype:
        boundary = ctype.split("boundary=")[-1].strip().encode("utf-8")
        body = handler.get_request_body()
        parts = body.split(b"--" + boundary)
        file_bytes = None
        file_name = "待审合同.docx"
        for part in parts:
            if b'name="file"' in part:
                fn_m = re.search(rb'filename="([^"]+)"', part)
                if fn_m:
                    try:
                        file_name = fn_m.group(1).decode("utf-8")
                    except Exception:
                        file_name = fn_m.group(1).decode("latin-1")
                idx = part.find(b"\r\n\r\n")
                if idx != -1:
                    file_bytes = part[idx + 4 :]
                    if file_bytes.endswith(b"\r\n"):
                        file_bytes = file_bytes[:-2]
                    break

        if file_bytes:
            # 防路径穿越：仅提取基础文件名
            safe_name = Path(file_name).name
            ext = Path(safe_name).suffix.lower()
            if ext not in (".docx", ".doc", ".pdf"):
                handler._send_json(
                    400,
                    {
                        "error": "unsupported_file_type",
                        "message": "仅支持上传 docx, doc, pdf 格式合同",
                    },
                )
                return

            stem_clean = Path(safe_name).stem
            doc_name = (
                f"《{stem_clean}》送审稿"
                if not stem_clean.startswith("《")
                else stem_clean
            )
            s_file = prepare_session_file(session_id, ext=ext, tenant_id=tenant_id)
            s_file.write_bytes(file_bytes)
            if session_id == "default_session" and tenant_id == "default":
                try:
                    CURRENT_SESSION_FILE.write_bytes(file_bytes)
                except Exception:
                    pass
            save_session_meta(
                session_id,
                {
                    "doc_name": doc_name,
                    "original_filename": safe_name,
                    "forced_template_id": None,
                },
                tenant_id=tenant_id,
            )

            try:
                res = execute_current_diff(
                    s_file, forced_template_id=None, doc_name=doc_name
                )
                res["session_id"] = session_id
                res["tenant_id"] = tenant_id
                res["user_id"] = user_id
                get_agent_manager().start_document_review(
                    res,
                    session_id=session_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
                handler._send_json(200, res)
                return
            except PermissionError as e:
                handler._send_json(403, {"error": str(e)})
                return
            except Exception as e:
                handler._send_json(500, {"error": str(e)})
                return

    handler._send_json(400, {"error": "invalid_upload"})
