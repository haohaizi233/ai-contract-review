# -*- coding: utf-8 -*-
"""审查重跑与人工裁决留痕路由（Review Routes）。"""

from __future__ import annotations

import os
import time
import json
from contract_kb.domain.models.review_identity import HumanReviewStatus
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
        s_file = get_session_file(session_id, tenant_id=tenant_id)
        if not s_file.exists():
            is_enterprise = os.environ.get(
                "DIFF_WEB_MODE", ""
            ).upper() == "ENTERPRISE" or bool(
                os.environ.get("CONTRACT_KB_AUTH_SHARED_KEY")
            )
            if is_enterprise:
                handler._send_json(
                    404,
                    {
                        "error": "session_file_not_found",
                        "message": "当前会话无待审合同文件，严禁回退他租户文档",
                    },
                )
                return
            s_file = CURRENT_SESSION_FILE
        if s_file.exists():
            meta = get_session_meta(session_id, tenant_id=tenant_id)
            res = execute_current_diff(
                s_file,
                forced_template_id=meta.get("forced_template_id"),
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
        handler._send_json(200, {"success": True})
    except PermissionError as e:
        handler._send_json(403, {"error": str(e)})
    except Exception as e:
        handler._send_json(500, {"error": str(e)})


def handle_post_human_decision(handler: Any) -> None:
    session_id, tenant_id, user_id = handler._get_request_identity()
    raw_body = handler.get_request_body()
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
            if not card:
                handler._send_json(404, {"error": f"未找到对应批注卡片: {ann_id}"})
                return

            curr_status_str = card.get("human_review_status") or "PENDING"
            try:
                curr_status = HumanReviewStatus(curr_status_str)
            except ValueError:
                curr_status = HumanReviewStatus.PENDING

            target_status_map = {
                "ACCEPTED": HumanReviewStatus.HUMAN_ACCEPTED,
                "REJECTED": HumanReviewStatus.HUMAN_REJECTED,
                "MODIFIED": HumanReviewStatus.HUMAN_MODIFIED,
            }
            target_status = target_status_map.get(decision)
            if not target_status:
                handler._send_json(400, {"error": f"非法的人工研判结论: {decision}"})
                return

            if not curr_status.can_transition_to(target_status):
                handler._send_json(
                    400,
                    {
                        "error": f"非法状态流转: 不能从 {curr_status.value} 直接跳变至 {target_status.value}"
                    },
                )
                return

            card["human_review_status"] = target_status.value
            card["reviewed_by"] = user_id
            card["reviewed_at"] = time.time()
            if target_status == HumanReviewStatus.HUMAN_MODIFIED:
                card["human_modified_advice"] = modified_advice

            # 审计留痕
            if "human_audit_trail" not in card:
                card["human_audit_trail"] = []
            card["human_audit_trail"].append(
                {
                    "from_status": curr_status.value,
                    "to_status": target_status.value,
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "timestamp": time.time(),
                    "modified_advice": modified_advice
                    if target_status == HumanReviewStatus.HUMAN_MODIFIED
                    else None,
                }
            )
            sess._save_checkpoint()
        handler._send_json(200, {"success": True, "card": card})
    except Exception as e:
        handler._send_json(500, {"error": str(e)})
