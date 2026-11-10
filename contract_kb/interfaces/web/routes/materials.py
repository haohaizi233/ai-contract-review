# -*- coding: utf-8 -*-
"""历史素材库检索、案卷详情与附件加载路由（Materials Routes）。"""

from __future__ import annotations

import json
import shutil
import urllib.parse
from typing import Any

from contract_kb.application.agents.review_sessions import get_agent_manager
from contract_kb.modules.templates.material_catalog import get_material_manager

from ..services.diff_service import execute_current_diff
from ..services.session_files import prepare_session_file, save_session_meta


def handle_get_materials_stats(handler: Any) -> None:
    try:
        mgr = get_material_manager()
        stats = mgr.get_stats()
        handler._send_json(200, stats)
    except Exception as e:
        handler._send_json(500, {"error": str(e)})


def handle_get_materials_cases(handler: Any, parsed: urllib.parse.ParseResult) -> None:
    qs = urllib.parse.parse_qs(parsed.query)
    category = qs.get("category", ["ALL"])[0]
    search = qs.get("search", [""])[0]
    has_word = qs.get("has_word", ["1"])[0] in ("1", "true", "True")
    try:
        page = max(1, int(qs.get("page", ["1"])[0]))
    except ValueError:
        page = 1
    try:
        page_size = min(100, max(1, int(qs.get("page_size", ["20"])[0])))
    except ValueError:
        page_size = 20
    try:
        mgr = get_material_manager()
        res = mgr.search_cases(
            category=category,
            search=search,
            has_word_only=has_word,
            page=page,
            page_size=page_size,
        )
        handler._send_json(200, res)
    except Exception as e:
        handler._send_json(500, {"error": str(e)})


def handle_get_materials_case_detail(
    handler: Any, parsed: urllib.parse.ParseResult
) -> None:
    qs = urllib.parse.parse_qs(parsed.query)
    contract_no = qs.get("contract_no", [""])[0].strip()
    if not contract_no:
        handler._send_json(400, {"error": "缺少 contract_no 参数"})
        return
    try:
        mgr = get_material_manager()
        detail = mgr.get_case_detail(contract_no)
        if not detail:
            handler._send_json(404, {"error": f"未找到案卷 [{contract_no}]"})
        else:
            handler._send_json(200, detail)
    except Exception as e:
        handler._send_json(500, {"error": str(e)})


def handle_post_materials_load_contract(handler: Any) -> None:
    session_id, tenant_id, user_id = handler._get_request_identity()
    length = int(handler.headers.get("content-length", 0))
    raw_body = handler.rfile.read(length) if length > 0 else b"{}"
    try:
        data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        c_no = (data.get("contract_no") or "").strip()
        att_path = (data.get("attachment_path") or "").strip() or None
        if not c_no:
            handler._send_json(400, {"error": "缺少 contract_no 参数"})
            return
        mgr = get_material_manager()
        file_path, _att_meta = mgr.get_contract_attachment_file(c_no, att_path)
        if not file_path or not file_path.exists():
            handler._send_json(
                404, {"error": f"未找到案卷 [{c_no}] 的合同附件物理文件"}
            )
            return

        s_file = prepare_session_file(
            session_id, ext=file_path.suffix, tenant_id=tenant_id
        )
        shutil.copyfile(file_path, s_file)

        c_info = mgr.cases_by_no.get(c_no, {})
        c_name = c_info.get("name") or c_no
        doc_name = (
            f"《{c_name}》履约合同原件" if not c_name.startswith("《") else c_name
        )
        save_session_meta(
            session_id,
            {"doc_name": doc_name, "forced_template_id": None},
            tenant_id=tenant_id,
        )

        res = execute_current_diff(s_file, forced_template_id=None, doc_name=doc_name)
        res["session_id"] = session_id
        res["tenant_id"] = tenant_id
        res["user_id"] = user_id
        res["contract_no"] = c_no
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
