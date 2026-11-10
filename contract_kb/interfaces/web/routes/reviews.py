# -*- coding: utf-8 -*-
"""审查重跑与人工裁决留痕路由（Review Routes）。"""

from __future__ import annotations

import json
from typing import Any

from contract_kb.application.agents.review_sessions import get_agent_manager

from ..services.diff_service import execute_current_diff
from ..services.session_files import (
    CURRENT_SESSION_FILE,
    get_session_file,
    get_session_meta,
)


def handle_post_rerun_review(handler: Any) -> None:
    session_id, tenant_id, user_id = handler._get_request_identity()
    try:
        s_file = get_session_file(session_id)
        if not s_file.exists():
            s_file = CURRENT_SESSION_FILE
        if s_file.exists():
            meta = get_session_meta(session_id)
            res = execute_current_diff(
                s_file,
                forced_template_id=meta.get("forced_template_id"),
                doc_name=meta.get("doc_name"),
            )
            res["session_id"] = session_id
            get_agent_manager().start_document_review(
                res,
                force_restart=True,
                session_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
            )
        handler._send_json(200, {"success": True})
    except Exception as e:
        handler._send_json(500, {"error": str(e)})


def handle_post_human_decision(handler: Any) -> None:
    session_id, tenant_id, user_id = handler._get_request_identity()
    length = int(handler.headers.get("content-length", 0))
    raw_body = handler.rfile.read(length) if length > 0 else b"{}"
    try:
        data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        ann_id = str(data.get("id") or data.get("annotation_id") or "")
        decision = str(data.get("decision") or "ACCEPTED").upper()
        modified_advice = str(data.get("modified_advice") or "")

        sess = get_agent_manager().get_session(
            session_id, tenant_id=tenant_id, user_id=user_id
        )
        if not sess:
            handler._send_json(404, {"error": "session_not_found"})
            return

        with sess._lock:
            card = sess.annotations_map.get(ann_id)
            if card:
                if decision == "ACCEPTED":
                    card["human_review_status"] = "HUMAN_ACCEPTED"
                elif decision == "REJECTED":
                    card["human_review_status"] = "HUMAN_REJECTED"
                elif decision == "MODIFIED":
                    card["human_review_status"] = "HUMAN_MODIFIED"
                    card["human_modified_advice"] = modified_advice
                sess._save_checkpoint()
        handler._send_json(200, {"success": True, "card": card})
    except Exception as e:
        handler._send_json(500, {"error": str(e)})
