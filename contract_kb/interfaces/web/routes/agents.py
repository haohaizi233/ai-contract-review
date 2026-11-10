# -*- coding: utf-8 -*-
"""法务 AI 智能体 SSE 事件流与队列控制路由（Agent Routes）。"""

from __future__ import annotations

import json
import queue
from typing import Any

from contract_kb.application.agents.review_sessions import get_agent_manager


def handle_get_agent_stream(handler: Any) -> None:
    session_id, tenant_id, user_id = handler._get_request_identity()
    mgr = get_agent_manager()
    try:
        sub_q = mgr.subscribe_events(
            session_id=session_id, tenant_id=tenant_id, user_id=user_id
        )
    except PermissionError as e:
        handler._send_json(403, {"error": str(e)})
        return

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache, no-transform")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("X-Accel-Buffering", "no")
    handler._send_cors_headers()
    handler.end_headers()

    try:
        while True:
            try:
                msg = sub_q.get(timeout=2.0)
                line = f"event: {msg['event']}\ndata: {json.dumps(msg['data'], ensure_ascii=False)}\n\n"
                handler.wfile.write(line.encode("utf-8"))
                handler.wfile.flush()
            except queue.Empty:
                handler.wfile.write(b": ping\n\n")
                handler.wfile.flush()
    except (
        BrokenPipeError,
        ConnectionResetError,
        ConnectionAbortedError,
        Exception,
    ):
        pass
    finally:
        handler.close_connection = True
        mgr.unsubscribe_events(
            sub_q, session_id=session_id, tenant_id=tenant_id, user_id=user_id
        )


def handle_get_agent_status(handler: Any) -> None:
    session_id, tenant_id, user_id = handler._get_request_identity()
    try:
        snap = get_agent_manager().get_current_snapshot(
            session_id=session_id, tenant_id=tenant_id, user_id=user_id
        )
        handler._send_json(200, snap)
    except PermissionError as e:
        handler._send_json(403, {"error": str(e)})
    except Exception as e:
        handler._send_json(500, {"error": str(e)})


def handle_post_prioritize_card(handler: Any) -> None:
    session_id, tenant_id, user_id = handler._get_request_identity()
    raw_body = handler.get_request_body()
    try:
        data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        ann_id = data.get("id") or ""
        ok = get_agent_manager().prioritize_card(
            ann_id, session_id=session_id, tenant_id=tenant_id, user_id=user_id
        )
        handler._send_json(200, {"success": ok})
    except PermissionError as e:
        handler._send_json(403, {"error": str(e)})
    except Exception as e:
        handler._send_json(500, {"error": str(e)})


def handle_post_toggle_pause(handler: Any) -> None:
    session_id, tenant_id, user_id = handler._get_request_identity()
    try:
        cur_status = get_agent_manager().toggle_pause(
            session_id=session_id, tenant_id=tenant_id, user_id=user_id
        )
        handler._send_json(200, {"status": cur_status})
    except PermissionError as e:
        handler._send_json(403, {"error": str(e)})
    except Exception as e:
        handler._send_json(500, {"error": str(e)})


def handle_post_retry_card(handler: Any) -> None:
    session_id, tenant_id, user_id = handler._get_request_identity()
    raw_body = handler.get_request_body()
    try:
        data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        ann_id = data.get("id") or ""
        ok = get_agent_manager().retry_card(
            ann_id, session_id=session_id, tenant_id=tenant_id, user_id=user_id
        )
        handler._send_json(200, {"success": ok})
    except PermissionError as e:
        handler._send_json(403, {"error": str(e)})
    except Exception as e:
        handler._send_json(500, {"error": str(e)})


def handle_post_skip_card(handler: Any) -> None:
    session_id, tenant_id, user_id = handler._get_request_identity()
    raw_body = handler.get_request_body()
    try:
        data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        ann_id = data.get("id") or ""
        ok = get_agent_manager().skip_card(
            ann_id, session_id=session_id, tenant_id=tenant_id, user_id=user_id
        )
        handler._send_json(200, {"success": ok})
    except PermissionError as e:
        handler._send_json(403, {"error": str(e)})
    except Exception as e:
        handler._send_json(500, {"error": str(e)})


def handle_post_reset_circuit(handler: Any) -> None:
    handler._get_request_identity()
    try:
        status = get_agent_manager().reset_circuit_breaker()
        handler._send_json(200, {"success": True, "circuit": status})
    except PermissionError as e:
        handler._send_json(403, {"error": str(e)})
    except Exception as e:
        handler._send_json(500, {"error": str(e)})


def handle_post_cancel(handler: Any) -> None:
    session_id, tenant_id, user_id = handler._get_request_identity()
    try:
        ok = get_agent_manager().cancel_task(
            session_id=session_id, tenant_id=tenant_id, user_id=user_id
        )
        handler._send_json(200, {"success": ok})
    except PermissionError as e:
        handler._send_json(403, {"error": str(e)})
    except Exception as e:
        handler._send_json(500, {"error": str(e)})
