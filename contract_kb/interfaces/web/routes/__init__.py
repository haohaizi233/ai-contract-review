# -*- coding: utf-8 -*-
"""Web 工作台 HTTP 路由集合（Web Routes）。"""

from .agents import (
    handle_get_agent_status,
    handle_get_agent_stream,
    handle_post_cancel,
    handle_post_prioritize_card,
    handle_post_reset_circuit,
    handle_post_retry_card,
    handle_post_skip_card,
    handle_post_toggle_pause,
)
from .contracts import handle_get_sample, handle_post_upload
from .feedback import handle_post_export_training_data, handle_post_feedback
from .materials import (
    handle_get_materials_case_detail,
    handle_get_materials_cases,
    handle_get_materials_stats,
    handle_post_materials_load_contract,
)
from .reviews import handle_post_human_decision, handle_post_rerun_review
from .templates import (
    handle_get_ref_templates_catalog,
    handle_get_templates_list,
    handle_post_ref_templates_load,
    handle_post_switch_template,
    handle_post_templates_reload,
)

__all__ = [
    "handle_get_agent_status",
    "handle_get_agent_stream",
    "handle_get_materials_case_detail",
    "handle_get_materials_cases",
    "handle_get_materials_stats",
    "handle_get_ref_templates_catalog",
    "handle_get_sample",
    "handle_get_templates_list",
    "handle_post_cancel",
    "handle_post_export_training_data",
    "handle_post_feedback",
    "handle_post_human_decision",
    "handle_post_materials_load_contract",
    "handle_post_prioritize_card",
    "handle_post_ref_templates_load",
    "handle_post_rerun_review",
    "handle_post_reset_circuit",
    "handle_post_retry_card",
    "handle_post_skip_card",
    "handle_post_switch_template",
    "handle_post_templates_reload",
    "handle_post_toggle_pause",
    "handle_post_upload",
]
