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
    session_id, tenant_id, user_id = handler._get_request_identity()
    raw_body = handler.get_request_body()
    try:
        data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        # 校验并拦截客户端伪造租户与审核人身份
        client_tenant = data.get("tenant_id")
        if client_tenant and client_tenant != tenant_id:
            handler._send_json(
                403, {"error": "forbidden", "message": "禁止伪造或跨租户提交反馈"}
            )
            return
        client_reviewer = data.get("reviewer_id")
        if client_reviewer and client_reviewer != user_id:
            handler._send_json(
                403, {"error": "forbidden", "message": "禁止伪造审核人身份"}
            )
            return

        # 服务端受信任主体强制赋值覆盖，杜绝客户端自报
        data["session_id"] = session_id
        data["tenant_id"] = tenant_id
        data["reviewer_id"] = user_id

        # 杜绝客户端自行批准入库：普通反馈请求不能直接批准为训练样本
        data["is_approved"] = False
        data["is_approved_for_training"] = False

        saved_path = record_user_feedback(data)  # type: ignore[call-arg, arg-type]
        handler._send_json(200, {"success": True, "saved_to": str(saved_path)})
    except PermissionError as e:
        handler._send_json(403, {"error": str(e)})
    except Exception as e:
        handler._send_json(500, {"error": str(e)})


def handle_post_export_training_data(handler: Any) -> None:
    session_id, tenant_id, user_id = handler._get_request_identity()
    principal = handler.get_request_principal()
    if principal is None or not principal.has_role("admin"):
        handler._send_json(
            403,
            {
                "error": "forbidden",
                "message": "仅管理员与授权数据审批人具备导出训练集权限",
            },
        )
        return
    try:
        res = export_sft_dataset(tenant_id=tenant_id, require_approval=True)
        handler._send_json(200, res)
    except PermissionError as e:
        handler._send_json(403, {"error": str(e)})
    except Exception as e:
        handler._send_json(500, {"error": str(e)})
