# -*- coding: utf-8 -*-
"""Phase 16-S-R1: 规则审批撤销 TOCTOU 动态阻断与多租户数据库集成测试。"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

from contract_kb.application.agents.legal_review_agent import LegalReviewAgent
from contract_kb.domain.models.review_identity import ReviewStatus
from contract_kb.infrastructure.persistence.sqlite_storage import create_storage


def test_real_db_toctou_revocation_detected(tmp_path):
    """真实 SQLite 数据库中，推理期间规则被撤销，TOCTOU 准确拦截并降级转人工复核。"""
    db_file = tmp_path / "toctou_int.db"
    real_storage = create_storage(db_path=db_file)
    tenant = "corp_toctou"
    rule_id = f"RULE_TOCTOU_{time.time_ns()}"

    # 1. 预先在真实数据库中写入已审批规则
    real_storage.save_rule(
        {
            "rule_id": rule_id,
            "version": 1,
            "name": "知识产权侵权兜底条款",
            "rule_type": "standard",
            "execution_mode": "SYNC",
            "severity": "CRITICAL",
            "priority": 100,
            "contract_types": ["*"],
            "parameters": {"clause_category": "IP_RIGHTS"},
            "source_doc": "IP风控手册2026",
            "source_clauses": [{"document_id": "ip_guide", "clause_id": "c_ip_1"}],
            "status": "APPROVED",
            "approved_by": "ip_director",
            "tenant_id": tenant,
        },
        tenant_id=tenant,
    )

    agent = LegalReviewAgent(base_url="https://api.openai.com", api_key="sk-test")
    cache_key = agent._cache_key(
        "IP_RIGHTS", "TEXT_DELTA", "std_ip", "rev_ip", "知识产权", tenant_id=tenant
    )
    with agent._cache_lock:
        if cache_key in agent._cache:
            del agent._cache[cache_key]

    # 2. 模拟 LLM 推理期间，外部管理后台异步将规则撤销 (UPDATE rules SET status='REVOKED')
    def revoke_rule_in_db(*args, **kwargs):
        with real_storage._lock:
            real_storage.conn.execute(
                "UPDATE rules SET status='REVOKED' WHERE rule_id=? AND tenant_id=?",
                (rule_id, tenant),
            )
            real_storage._commit()
        return {
            "text": json.dumps(
                {
                    "risk_level": "HIGH",
                    "risk_label": "知识产权风险",
                    "ai_analysis": "依据知识产权条款研判分析结论",
                    "action_advice": "建议保留侵权担保承诺",
                }
            ),
            "usage": {"prompt_tokens": 10, "completion_tokens": 10},
        }

    with (
        patch.object(agent, "is_external_allowed", return_value=True),
        patch(
            "contract_kb.application.agents.legal_review_agent.OpenAICompatProvider"
        ) as mock_prov_cls,
    ):
        mock_prov = MagicMock()
        mock_prov.complete.side_effect = revoke_rule_in_db
        mock_prov_cls.return_value = mock_prov

        res = agent.review_clause(
            annotation={
                "id": "ann-toctou-int-01",
                "clause_category": "IP_RIGHTS",
                "delta_type": "TEXT_DELTA",
                "standard_span": "std_ip",
                "reviewed_span": "rev_ip",
                "clause_title": "知识产权",
            },
            storage=real_storage,
            tenant_id=tenant,
            force_refresh=True,
        )

    # 3. 验证 TOCTOU 复核结果：状态必须为 NEEDS_HUMAN_REVIEW，证据被标记为 REVOKED，且绝未入缓存
    assert res.get("review_status") == ReviewStatus.NEEDS_HUMAN_REVIEW.value
    assert res.get("toctou_revocation_detected") is True
    assert "规则撤销失效警报" in res.get("ai_analysis", "") or "失效" in res.get(
        "ai_analysis", ""
    )
    assert cache_key not in agent._cache

    refs = res.get("evidence_references", [])
    if refs:
        assert refs[0].get("approval_status") == "REVOKED"
