# -*- coding: utf-8 -*-
"""向后兼容门面（Compatibility Facade）：法务 AI 智能体实现已迁移至 `contract_kb.application.agents.legal_review_agent`。"""

from __future__ import annotations

from .application.agents.legal_review_agent import *  # noqa: F401, F403
from .application.agents.legal_review_agent import (  # noqa: F401
    CircuitBreaker,
    LegalReviewAgent,
    OpenAICompatProvider,
    _is_safe_endpoint_url,
    get_legal_agent,
)
