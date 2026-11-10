# -*- coding: utf-8 -*-
"""Phase 16-S-R1: 8 项阻断与重要安全缺陷整改闭环验证测试集 (Flaws Remediation & Closure Verification).

严格覆盖:
1. P0-R1-01: Web 后台 Worker 已显式向法务 Agent 注入可信 Storage
2. P0-R1-02: 禁止仅 APPROVED 直接映射为 VERIFIED，必须经过 CitationVerifier 真实核验
3. P0-R1-03: Agent 缓存核验在数据库异常时严禁放行旧结果，强制 Fail-Closed
4. P0-R1-04: TOCTOU 复核异常时严禁持久化未核验缓存，强制标记人工复核
5. P1-R1-05: 服务层严禁通过 payload 字段自行批准训练样本，必须经授权审批人
6. P1-R1-06: 彻底清除虚假 doc_id 断言，基于真实 task_id / annotation_id 逐行核验已审批训练集
7. P1-R1-07: 独立 Web 的 Nonce Store 摆脱隐式模块依赖，支持显式注入与优雅安全降级
8. P1-R1-08: 未认证企业首页绝不输出敏感模板与案例元数据 (catalog/initial_cases)
"""

from __future__ import annotations

import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

from contract_kb.application.agents.knowledge_adapter import (
    FEEDBACK_BASE_DIR,
    record_user_feedback,
)
from contract_kb.application.agents.legal_review_agent import LegalReviewAgent
from contract_kb.application.agents.review_sessions import AgentReviewManager
from contract_kb.infrastructure.persistence.sqlite_storage import create_storage
from contract_kb.interfaces.http.auth import InMemoryAndDbNonceStore
from contract_kb.interfaces.web.app import generate_rendered_html


def test_reproduce_p0_r1_01_worker_lacks_storage_injection():
    """P0-R1-01 闭环验证: AgentReviewManager 后台 Worker 必须显式向 Agent 注入 Storage。"""
    mgr = AgentReviewManager()
    dummy_storage = create_storage(db_path=":memory:")
    mgr.set_storage_provider(dummy_storage)
    captured_calls = []

    with patch(
        "contract_kb.application.agents.review_sessions.get_legal_agent"
    ) as mock_getter:
        mock_agent = MagicMock()

        def fake_review_clause(**kwargs):
            captured_calls.append(kwargs)
            return {
                "id": kwargs.get("annotation", {}).get("id", "ann-p0-1"),
                "risk_level": "HIGH",
                "risk_label": "测试",
                "ai_analysis": "经过专业法务研判分析结论",
                "action_advice": "建议按标准条款执行",
                "review_status": "AI_REVIEW_COMPLETED",
                "source": "AI_AGENT",
            }

        mock_agent.review_clause.side_effect = fake_review_clause
        mock_getter.return_value = mock_agent

        mgr.start_document_review(
            diff_data={
                "annotations": [{"id": "ann-p0-1", "clause_category": "LIABILITY"}]
            },
            session_id=f"sess_p0_01_{time.time_ns()}",
            tenant_id="tenant_p0_01",
        )
        time.sleep(0.4)

    assert len(captured_calls) > 0, "Worker 应至少执行一条条款审查"
    call_kwargs = captured_calls[0]
    storage_passed = call_kwargs.get("storage")
    assert storage_passed is not None, (
        "Worker 必须显式向 Agent.review_clause 注入可信 Storage"
    )
    assert storage_passed is dummy_storage


def test_reproduce_p0_r1_02_approved_directly_mapped_to_verified():
    """P0-R1-02 闭环验证: 仅 APPROVED 但缺少真实来源引文的规则绝不能被标为 VERIFIED。"""
    agent = LegalReviewAgent(base_url="https://api.openai.com", api_key="sk-test")
    mock_storage = MagicMock()
    mock_storage.get_rules.return_value = [
        {
            "rule_id": "RULE_WITHOUT_SOURCE_01",
            "clause_category": "LIABILITY",
            "status": "APPROVED",
            "name": "无来源审批规则",
            "parameters": {},
            "source_clauses": [],
        }
    ]

    with (
        patch.object(agent, "is_external_allowed", return_value=True),
        patch(
            "contract_kb.application.agents.legal_review_agent.OpenAICompatProvider"
        ) as mock_prov_cls,
    ):
        mock_prov = MagicMock()
        mock_prov.complete.return_value = {
            "text": json.dumps(
                {
                    "risk_level": "HIGH",
                    "risk_label": "违约金偏离",
                    "ai_analysis": "这是一个经过深入法律风险剖析的完整合规研判结论",
                    "action_advice": "【法务处置建议】坚决要求恢复母本条款约定",
                }
            ),
            "usage": {"prompt_tokens": 10, "completion_tokens": 10},
        }
        mock_prov_cls.return_value = mock_prov

        res = agent.review_clause(
            annotation={
                "id": "ann-p0-2",
                "clause_category": "LIABILITY",
                "delta_type": "TEXT_DELTA",
                "standard_span": "std",
                "reviewed_span": "rev",
                "clause_title": "title",
            },
            storage=mock_storage,
            force_refresh=True,
        )

    evidence_refs = res.get("evidence_references", [])
    assert len(evidence_refs) > 0
    ref = evidence_refs[0]
    # 闭环核验：缺少来源出处的规则必须标记为 INSUFFICIENT_PROVENANCE，绝不能为 VERIFIED
    assert ref.get("verification_status") != "VERIFIED"
    assert ref.get("verification_status") == "INSUFFICIENT_PROVENANCE"


def test_reproduce_p0_r1_03_cache_swallows_db_exception():
    """P0-R1-03 闭环验证: 缓存核验时数据库抛出异常时必须 Fail-Closed，禁止放行旧缓存。"""
    agent = LegalReviewAgent()
    cache_key = agent._cache_key("LIABILITY", "TEXT_DELTA", "std", "rev", "title")
    agent._cache[cache_key] = {
        "risk_level": "HIGH",
        "risk_label": "旧缓存结果",
        "evidence_references": [
            {"rule_id": "RULE_LIABILITY_01", "approval_status": "APPROVED"}
        ],
    }

    failing_storage = MagicMock()
    failing_storage.get_rules.side_effect = RuntimeError("Database connection down")

    res = agent.review_clause(
        annotation={
            "id": "ann-p0-3",
            "clause_category": "LIABILITY",
            "delta_type": "TEXT_DELTA",
            "standard_span": "std",
            "reviewed_span": "rev",
            "clause_title": "title",
        },
        storage=failing_storage,
        force_refresh=False,
    )
    # 闭环核验：数据库异常时 cache_valid 必须为 False，结果不得来自缓存 (from_cache != True)
    assert res.get("from_cache") is not True


def test_reproduce_p0_r1_04_toctou_swallows_db_exception():
    """P0-R1-04 闭环验证: TOCTOU 复核时数据库异常必须 Fail-Closed，拒绝缓存未验证结论。"""
    agent = LegalReviewAgent(base_url="https://api.openai.com", api_key="sk-test")
    cache_key = agent._cache_key(
        "LIABILITY", "TEXT_DELTA", "std_toctou", "rev_toctou", "title_toctou"
    )
    with agent._cache_lock:
        if cache_key in agent._cache:
            del agent._cache[cache_key]

    failing_storage = MagicMock()
    failing_storage.get_rules.side_effect = [
        [
            {
                "rule_id": "RULE_LIABILITY_01",
                "clause_category": "LIABILITY",
                "status": "APPROVED",
                "name": "违约金规则",
                "parameters": {},
            }
        ],
        [
            {
                "rule_id": "RULE_LIABILITY_01",
                "clause_category": "LIABILITY",
                "status": "APPROVED",
                "name": "违约金规则",
                "parameters": {},
            }
        ],
        RuntimeError("DB crashed during post-inference TOCTOU check"),
    ]

    with (
        patch.object(agent, "is_external_allowed", return_value=True),
        patch(
            "contract_kb.application.agents.legal_review_agent.OpenAICompatProvider"
        ) as mock_prov_cls,
    ):
        mock_prov = MagicMock()
        mock_prov.complete.return_value = {
            "text": json.dumps(
                {
                    "risk_level": "HIGH",
                    "risk_label": "偏离",
                    "ai_analysis": "依据规则合规研判分析结论",
                    "action_advice": "【法务处置建议】驳回修改",
                }
            ),
            "usage": {"prompt_tokens": 10, "completion_tokens": 10},
        }
        mock_prov_cls.return_value = mock_prov

        res = agent.review_clause(
            annotation={
                "id": "ann-p0-4",
                "clause_category": "LIABILITY",
                "delta_type": "TEXT_DELTA",
                "standard_span": "std_toctou",
                "reviewed_span": "rev_toctou",
                "clause_title": "title_toctou",
            },
            storage=failing_storage,
            force_refresh=True,
        )

    # 闭环核验：TOCTOU 异常时严禁持久化入缓存，且必须降级为人工复核 (NEEDS_HUMAN_REVIEW)
    assert cache_key not in agent._cache
    assert res.get("review_status") == "NEEDS_HUMAN_REVIEW"


def test_reproduce_p1_r1_05_service_layer_allows_self_approval():
    """P1-R1-05 闭环验证: 服务层 record_user_feedback 杜绝客户端 self-approval 入库。"""
    t_id = f"task_p1_05_{time.time_ns()}"
    tenant = "corp_p1_05"
    sample = {
        "task_id": t_id,
        "annotation_id": "ann-1",
        "is_approved": True,  # 攻击者尝试自提自批
        "is_approved_for_training": True,
        "tenant_id": tenant,
    }
    saved_path = record_user_feedback(sample, is_approved=True, approver_principal=None)
    assert saved_path is not None
    assert "raw_feedback" in str(saved_path)

    rev_file = (
        FEEDBACK_BASE_DIR / tenant / "reviewed_feedback" / "reviewed_events.jsonl"
    )
    if rev_file.exists():
        for line in rev_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            assert rec.get("task_id") != t_id, (
                "未经审批人授权的记录绝不能进入 reviewed_events.jsonl"
            )


def test_reproduce_p1_r1_06_false_positive_self_approval_test():
    """P1-R1-06 闭环验证: 基于真实 task_id / annotation_id 逐行解析，确认自审批样本绝不入库。"""
    real_task = f"task_p1_06_{time.time_ns()}"
    tenant = "corp_p1_06"
    sample = {
        "task_id": real_task,
        "doc_id": "doc_ghost",
        "annotation_id": "ann-real-1",
        "is_approved": True,
        "tenant_id": tenant,
    }
    record_user_feedback(sample, is_approved=True, approver_principal=None)
    rev_file = (
        FEEDBACK_BASE_DIR / tenant / "reviewed_feedback" / "reviewed_events.jsonl"
    )
    if rev_file.exists():
        for line in rev_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            assert rec.get("task_id") != real_task


def test_reproduce_p1_r1_07_nonce_store_implicit_sys_modules():
    """P1-R1-07 闭环验证: 独立 Web 生产模式下 Nonce Store 显式注入存储，不再依赖 sys.modules。"""
    old_env = os.environ.get("CONTRACT_KB_ENV")
    os.environ["CONTRACT_KB_ENV"] = "production"
    try:
        dummy_st = create_storage(db_path=":memory:")
        store = InMemoryAndDbNonceStore(storage=dummy_st)
        old_mod = sys.modules.pop("contract_kb.api", None)
        try:
            ok = store.record_and_check(
                nonce="1234567890123456",
                tenant="default",
                user="alice",
                expires_at=time.time() + 60,
                storage=dummy_st,
            )
            assert ok is True
        finally:
            if old_mod is not None:
                sys.modules["contract_kb.api"] = old_mod
    finally:
        if old_env is not None:
            os.environ["CONTRACT_KB_ENV"] = old_env
        else:
            os.environ.pop("CONTRACT_KB_ENV", None)


def test_reproduce_p1_r1_08_enterprise_homepage_metadata_leak():
    """P1-R1-08 闭环验证: 未认证企业首页绝不包含内部案例与模板元数据。"""
    html = generate_rendered_html(is_enterprise=True)
    # 检查内嵌数据已被严格置空，绝不包含真实案例与母本清单数据
    assert "let allTemplates = [] || [];" in html
    assert "window.MATERIAL_DATA = {} || {};" in html
    assert "let refTemplatesCache = [] || [];" in html
    assert "window.INITIAL_DATA = null;" in html
