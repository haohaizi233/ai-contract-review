# -*- coding: utf-8 -*-
"""模板清单、置信度切换、参考模板加载与热重载路由（Template Routes）。"""

from __future__ import annotations

import hashlib
import json
import shutil
from typing import Any

from contract_kb import config
from contract_kb.application.agents.review_sessions import get_agent_manager
from contract_kb.infrastructure.persistence.sqlite_storage import create_storage
from contract_kb.modules.templates.material_catalog import (
    DEFAULT_REF_DIR,
    clean_template_title as catalog_clean_title,
    get_ref_templates_catalog,
)

from ..services.diff_service import (
    execute_current_diff,
    get_ranked_templates_catalog,
    hot_reload_template_library,
)
from ..services.session_files import (
    CURRENT_SESSION_FILE,
    get_session_file,
    get_session_meta,
    prepare_session_file,
    save_session_meta,
)


def handle_get_templates_list(handler: Any) -> None:
    session_id, tenant_id, _ = handler._get_request_identity()
    s_file = get_session_file(session_id, tenant_id=tenant_id)
    if not s_file.exists():
        s_file = CURRENT_SESSION_FILE
    meta = get_session_meta(session_id, tenant_id=tenant_id)
    try:
        catalog = get_ranked_templates_catalog(s_file, meta.get("forced_template_id"))
        handler._send_json(200, {"success": True, "templates": catalog})
    except Exception as e:
        handler._send_json(500, {"error": str(e)})


def handle_get_ref_templates_catalog(handler: Any) -> None:
    try:
        catalog = get_ref_templates_catalog()
        handler._send_json(200, {"success": True, "templates": catalog})
    except Exception as e:
        handler._send_json(500, {"error": str(e)})


def handle_post_templates_reload(handler: Any) -> None:
    try:
        res = hot_reload_template_library()
        handler._send_json(200, res)
    except Exception as e:
        handler._send_json(500, {"error": str(e)})


def handle_post_switch_template(handler: Any) -> None:
    session_id, tenant_id, user_id = handler._get_request_identity()
    length = int(handler.headers.get("content-length", 0))
    raw_body = handler.rfile.read(length) if length > 0 else b"{}"
    try:
        data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        target_tpl_id = (data.get("template_id") or "").strip()
        meta = get_session_meta(session_id, tenant_id=tenant_id)
        if not target_tpl_id or target_tpl_id == "auto":
            meta["forced_template_id"] = None
        else:
            meta["forced_template_id"] = target_tpl_id
        save_session_meta(session_id, meta, tenant_id=tenant_id)

        s_file = get_session_file(session_id, tenant_id=tenant_id)
        if not s_file.exists():
            default_sample = config.GOLDEN_DIR / "mut_11_late_fee_change.docx"
            shutil.copyfile(default_sample, s_file)

        res = execute_current_diff(
            s_file,
            forced_template_id=meta["forced_template_id"],
            doc_name=meta.get("doc_name"),
        )
        res["session_id"] = session_id
        res["tenant_id"] = tenant_id
        res["user_id"] = user_id
        get_agent_manager().start_document_review(
            res,
            force_restart=True,
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        handler._send_json(200, res)
    except Exception as e:
        handler._send_json(500, {"error": str(e)})


def handle_post_ref_templates_load(handler: Any) -> None:
    session_id, tenant_id, user_id = handler._get_request_identity()
    length = int(handler.headers.get("content-length", 0))
    raw_body = handler.rfile.read(length) if length > 0 else b"{}"
    try:
        data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        rel_path = (data.get("rel_path") or "").strip()
        mode = (data.get("mode") or "reviewed").strip()

        if not rel_path:
            handler._send_json(400, {"error": "缺少 rel_path 参数"})
            return

        target_file = (DEFAULT_REF_DIR / rel_path).resolve()
        if (
            not target_file.is_relative_to(DEFAULT_REF_DIR.resolve())
            or not target_file.exists()
        ):
            handler._send_json(404, {"error": f"模板文件不存在: {rel_path}"})
            return

        if target_file.suffix.lower() == ".doc":
            docx_alt = target_file.with_suffix(".docx")
            if docx_alt.exists():
                target_file = docx_alt

        clean_name = catalog_clean_title(target_file.stem)

        st = create_storage()
        tpl_id = None
        try:
            target_sha = hashlib.sha256(target_file.read_bytes()).hexdigest()
            doc_rec = st.find_document_by_hash(target_sha)
            if not doc_rec:
                for d in st.list_documents():
                    if (
                        d.get("source_file") == target_file.name
                        or d.get("file_name") == target_file.name
                    ):
                        doc_rec = d
                        break
            if doc_rec:
                tpl_id = doc_rec.get("document_id")
        finally:
            st.close()

        if mode == "standard":
            if not tpl_id:
                handler._send_json(
                    400,
                    {"error": f"模板 [{target_file.name}] 未在知识库中注册有效 ID"},
                )
                return
            s_file = get_session_file(session_id, tenant_id=tenant_id)
            if not s_file.exists():
                default_sample = config.GOLDEN_DIR / "mut_11_late_fee_change.docx"
                shutil.copyfile(default_sample, s_file)
            meta = get_session_meta(session_id, tenant_id=tenant_id)
            meta["forced_template_id"] = tpl_id
            save_session_meta(session_id, meta, tenant_id=tenant_id)

            res = execute_current_diff(
                s_file,
                forced_template_id=tpl_id,
                doc_name=meta.get("doc_name"),
            )
        elif mode == "self_diff":
            s_file = prepare_session_file(
                session_id, ext=target_file.suffix, tenant_id=tenant_id
            )
            shutil.copyfile(target_file, s_file)
            doc_name = f"《{clean_name}》标准模板自测核验"
            save_session_meta(
                session_id,
                {"doc_name": doc_name, "forced_template_id": tpl_id},
                tenant_id=tenant_id,
            )

            res = execute_current_diff(
                s_file,
                forced_template_id=tpl_id,
                doc_name=doc_name,
            )
        else:
            s_file = prepare_session_file(
                session_id, ext=target_file.suffix, tenant_id=tenant_id
            )
            shutil.copyfile(target_file, s_file)
            doc_name = f"《{clean_name}》送审原稿"
            save_session_meta(
                session_id,
                {"doc_name": doc_name, "forced_template_id": None},
                tenant_id=tenant_id,
            )

            res = execute_current_diff(
                s_file,
                forced_template_id=None,
                doc_name=doc_name,
            )

        res["session_id"] = session_id

        res["tenant_id"] = tenant_id

        res["user_id"] = user_id
        get_agent_manager().start_document_review(
            res,
            force_restart=True,
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        handler._send_json(200, res)
    except Exception as e:
        handler._send_json(500, {"error": str(e)})
