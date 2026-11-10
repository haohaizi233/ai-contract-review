# -*- coding: utf-8 -*-
"""法务反馈收集与 SFT 微调样本集导出路由（Feedback Routes）。"""

from __future__ import annotations

import json
from typing import Any

from contract_kb.application.agents.knowledge_adapter import (
    export_sft_dataset,
    record_user_feedback,
)


def handle_post_feedback(handler: Any) -> None:
    length = int(handler.headers.get("content-length", 0))
    raw_body = handler.rfile.read(length) if length > 0 else b"{}"
    try:
        data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        saved_path = record_user_feedback(data)  # type: ignore[call-arg, arg-type]
        handler._send_json(200, {"success": True, "saved_to": str(saved_path)})
    except Exception as e:
        handler._send_json(500, {"error": str(e)})


def handle_post_export_training_data(handler: Any) -> None:
    try:
        res = export_sft_dataset()
        handler._send_json(200, res)
    except Exception as e:
        handler._send_json(500, {"error": str(e)})
