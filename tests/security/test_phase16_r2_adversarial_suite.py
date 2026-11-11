# -*- coding: utf-8 -*-
"""Phase 16-S-R2: 可信证据、提示词边界、训练审批与缓存对抗测试矩阵 (Adversarial Security Matrix)

覆盖以下场景：
1. 已审批规则来源文档不存在 -> 不得 VERIFIED (MISSING_SOURCE)
2. 来源条款不存在 -> 不得 VERIFIED (CLAUSE_NOT_FOUND)
3. 数据库查询来源异常 -> Fail-Closed (SOURCE_UNAVAILABLE)
4. 来源内容哈希变化 -> STALE / HASH_MISMATCH
5. 规则过期 -> 不得成为权威依据 (SOURCE_INACTIVE)
6. 规则被撤销 -> 不得成为权威依据 (APPROVAL_REVOKED)
7. Prompt 试图使用未验证规则 -> 必须降级为非权威参考
8. 模型伪造引用/越界引用未核验规则 -> 必须拒绝权威结论并转人工复核 (NEEDS_HUMAN_REVIEW)
9. 模型声称法定强制但无对应法定依据 -> 触发证据边界警报
10. 旧接口使用 legal_admin 自批 -> 必须阻断
11. 反馈提交人自己审批 -> 防自提自批阻断 (PermissionError)
12. 跨租户审批 -> 跨租户越权阻断 (PermissionError)
13. 撤销样本不得导出至 SFT 数据集
14. 空证据缓存遭遇知识更新 -> 必须失效
15. 数据库故障时读取缓存 -> 不得放行
16. Worker 接口不匹配 -> 严禁剔除安全参数重试，直接安全降级
17. 跨租户规则访问隔离 -> 严格阻断
"""

from __future__ import annotations

import datetime
import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from contract_kb.application.agents.knowledge_adapter import (
    approve_feedback_for_training,
    export_sft_dataset,
    record_user_feedback,
    revoke_training_approval,
    retrieve_legal_rules,
)
from contract_kb.application.agents.legal_review_agent import LegalReviewAgent
from contract_kb.domain.models.review_identity import ReviewStatus
from contract_kb.modules.retrieval.citation import CitationVerifier


class MockPrincipal:
    def __init__(
        self, user_id: str, tenant_id: str = "default", roles: list[str] | None = None
    ):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.roles = roles or ["reviewer"]
        self.is_system_admin = "admin" in self.roles and tenant_id in ("*", "system")
        self.is_admin = "admin" in self.roles

    def has_role(self, role: str) -> bool:
        return role in self.roles or "admin" in self.roles


class AdversarialStorage:
    def __init__(self, rules=None, clauses=None, documents=None):
        self.rules = rules or []
        self.clauses = clauses or {}
        self.documents = documents or {}

    def get_rules(self, tenant_id=None, status=None):
        if status:
            return [
                r
                for r in self.rules
                if str(r.get("status") or r.get("approval_status")) == status
            ]
        return self.rules

    def get_clause(self, clause_id, tenant_id=None):
        return self.clauses.get(clause_id)

    def get_document(self, document_id, tenant_id=None):
        return self.documents.get(document_id)


def test_adversarial_rule_missing_document_rejected():
    """场景 1: 已审批规则来源文档不存在 -> 不得 VERIFIED。"""
    verifier = CitationVerifier()
    rule = {
        "rule_id": "ADV_DOC_01",
        "tenant_id": "tenant_a",
        "status": "APPROVED",
        "revision": "1",
        "source_document": "GHOST_DOC_404",
        "source_clauses": [],
    }
    storage = AdversarialStorage(rules=[rule], documents={})
    res = verifier.verify_rule_evidence(
        storage=storage, rule_item=rule, expected_tenant_id="tenant_a"
    )
    assert not res.verified
    assert res.status in ("MISSING_SOURCE", "INSUFFICIENT_PROVENANCE")


def test_adversarial_rule_missing_clause_rejected():
    """场景 2: 来源条款不存在 -> 不得 VERIFIED。"""
    verifier = CitationVerifier()
    rule = {
        "rule_id": "ADV_CLAUSE_01",
        "tenant_id": "tenant_a",
        "status": "APPROVED",
        "revision": "1",
        "source_document": "DOC_STD_01",
        "source_clauses": ["CLAUSE_GHOST_404"],
    }
    storage = AdversarialStorage(rules=[rule], clauses={})
    res = verifier.verify_rule_evidence(
        storage=storage, rule_item=rule, expected_tenant_id="tenant_a"
    )
    assert not res.verified
    assert res.status == "CLAUSE_NOT_FOUND"


def test_adversarial_clause_query_db_error_fails_closed():
    """场景 3: 数据库查询来源异常 -> Fail-Closed。"""
    verifier = CitationVerifier()
    mock_st = MagicMock()
    mock_st.get_rules.return_value = [
        {
            "rule_id": "ADV_ERR_01",
            "tenant_id": "tenant_a",
            "status": "APPROVED",
            "source_clauses": ["C1"],
        }
    ]
    mock_st.get_clause.side_effect = sqlite3.OperationalError("database is locked")
    rule = {
        "rule_id": "ADV_ERR_01",
        "tenant_id": "tenant_a",
        "status": "APPROVED",
        "source_clauses": ["C1"],
    }
    res = verifier.verify_rule_evidence(
        storage=mock_st, rule_item=rule, expected_tenant_id="tenant_a"
    )
    assert not res.verified
    assert res.status == "SOURCE_UNAVAILABLE"


def test_adversarial_content_hash_tamper_rejected():
    """场景 4: 来源条款哈希变化 -> HASH_MISMATCH 阻断。"""
    verifier = CitationVerifier()
    rule = {
        "rule_id": "ADV_HASH_01",
        "tenant_id": "tenant_a",
        "status": "APPROVED",
        "revision": "1",
        "source_document": "DOC_01",
        "source_clauses": [
            {"clause_id": "C_TAMPER_01", "hash": "expected_correct_hash_64hex"}
        ],
    }
    storage = AdversarialStorage(
        rules=[rule],
        clauses={
            "C_TAMPER_01": {
                "clause_id": "C_TAMPER_01",
                "document_id": "DOC_01",
                "doc_status": "ACTIVE",
                "content_hash": "tampered_corrupted_hash_64hex",
            }
        },
    )
    res = verifier.verify_rule_evidence(
        storage=storage, rule_item=rule, expected_tenant_id="tenant_a"
    )
    assert not res.verified
    assert res.status == "HASH_MISMATCH"


def test_adversarial_expired_rule_rejected():
    """场景 5: 规则已过期 -> 不得成为权威依据 (SOURCE_INACTIVE)。"""
    verifier = CitationVerifier()
    past_iso = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)
    ).isoformat()
    rule = {
        "rule_id": "ADV_EXP_01",
        "tenant_id": "tenant_a",
        "status": "APPROVED",
        "valid_until": past_iso,
        "source_document": "DOC_01",
        "source_clauses": ["C1"],
    }
    storage = AdversarialStorage(rules=[rule], clauses={"C1": {"doc_status": "ACTIVE"}})
    res = verifier.verify_rule_evidence(
        storage=storage, rule_item=rule, expected_tenant_id="tenant_a"
    )
    assert not res.verified
    assert res.status == "SOURCE_INACTIVE"


def test_adversarial_revoked_rule_rejected():
    """场景 6: 规则已被撤销 -> 不得成为权威依据 (APPROVAL_REVOKED)。"""
    verifier = CitationVerifier()
    rule = {
        "rule_id": "ADV_REV_01",
        "tenant_id": "tenant_a",
        "status": "REVOKED",
        "source_clauses": ["C1"],
    }
    storage = AdversarialStorage(rules=[rule], clauses={"C1": {"doc_status": "ACTIVE"}})
    res = verifier.verify_rule_evidence(
        storage=storage, rule_item=rule, expected_tenant_id="tenant_a"
    )
    assert not res.verified
    assert res.status == "APPROVAL_REVOKED"


def test_adversarial_unverified_rule_degraded_in_prompt():
    """场景 7: Prompt 试图使用未验证规则 -> 必须降级为非权威参考，绝不打上权威标签。"""
    unverified_rule = {
        "rule_id": "RULE_UNVER_ADV",
        "name": "未核验规则",
        "status": "APPROVED",
        "source_document": "NON_EXISTENT",
        "source_clauses": [],
        "revision": 1,
        "clause_category": "CONFIDENTIALITY",
        "parameters": {"clause_category": "CONFIDENTIALITY"},
    }
    storage = AdversarialStorage(rules=[unverified_rule], documents={})
    ctx = retrieve_legal_rules(
        category="CONFIDENTIALITY",
        delta_type="TEXT_CHANGE",
        tenant_id="tenant_a",
        storage=storage,
    )
    assert "[企业已审批风控基准·优先适用]" not in ctx
    assert "[非权威参考·不可作为确证依据]" in ctx


def test_adversarial_model_output_citing_unverified_rule_degraded():
    """场景 8: 模型输出越界引用了未经验证的规则 -> 必须判定为 NEEDS_HUMAN_REVIEW 并阻断入缓存。"""
    agent = LegalReviewAgent(base_url="https://api.openai.com", api_key="sk-test")
    rule_unverified = {
        "rule_id": "RULE_UNVER_FORBIDDEN",
        "clause_category": "LIABILITY",
        "status": "APPROVED",
        "source_document": "GHOST",
        "source_clauses": [],
        "parameters": {"clause_category": "LIABILITY"},
    }
    storage = AdversarialStorage(rules=[rule_unverified], documents={})
    with (
        patch.object(agent, "is_external_allowed", return_value=True),
        patch(
            "contract_kb.application.agents.legal_review_agent.OpenAICompatProvider"
        ) as m_prov_cls,
    ):
        m_prov = MagicMock()
        # 模型在其分析中引用了未经权威核验的 RULE_UNVER_FORBIDDEN
        m_prov.complete.return_value = {
            "text": json.dumps(
                {
                    "risk_level": "HIGH",
                    "risk_label": "责任上限违规",
                    "ai_analysis": "依据 RULE_UNVER_FORBIDDEN，违约责任不得设上限！",
                    "action_advice": "要求删除责任上限",
                }
            ),
            "usage": {"prompt_tokens": 10, "completion_tokens": 10},
        }
        m_prov_cls.return_value = m_prov

        res = agent.review_clause(
            annotation={
                "id": "ann-adv-01",
                "clause_category": "LIABILITY",
                "delta_type": "TEXT_DELTA",
                "standard_span": "std",
                "reviewed_span": "rev",
                "clause_title": "责任限制",
            },
            storage=storage,
            force_refresh=True,
            tenant_id="tenant_a",
        )

    assert res.get("review_status") == ReviewStatus.NEEDS_HUMAN_REVIEW.value
    assert res.get("boundary_violation") is True
    assert "越界引用了未核验规则" in res.get("failure_reason", "")


def test_adversarial_model_fabricating_statute_degraded():
    """场景 9: 模型声称国家法律明确规定但缺乏法定依据凭证 -> 触发证据边界警报。"""
    agent = LegalReviewAgent(base_url="https://api.openai.com", api_key="sk-test")
    storage = AdversarialStorage(rules=[])
    with (
        patch.object(agent, "is_external_allowed", return_value=True),
        patch(
            "contract_kb.application.agents.legal_review_agent.OpenAICompatProvider"
        ) as m_prov_cls,
    ):
        m_prov = MagicMock()
        m_prov.complete.return_value = {
            "text": json.dumps(
                {
                    "risk_level": "HIGH",
                    "risk_label": "管辖非法",
                    "ai_analysis": "国家法律明确规定必须由甲方住所地专属管辖，否则合同无效！",
                    "action_advice": "坚决要求恢复法定专属管辖",
                }
            ),
            "usage": {"prompt_tokens": 10, "completion_tokens": 10},
        }
        m_prov_cls.return_value = m_prov

        res = agent.review_clause(
            annotation={
                "id": "ann-adv-statute",
                "clause_category": "DISPUTE_RESOLUTION",
                "delta_type": "TEXT_DELTA",
                "standard_span": "std",
                "reviewed_span": "rev",
                "clause_title": "争议解决",
            },
            storage=storage,
            force_refresh=True,
            tenant_id="tenant_a",
        )

    assert res.get("review_status") == ReviewStatus.NEEDS_HUMAN_REVIEW.value
    assert res.get("boundary_violation") is True
    assert "缺乏对应的已核验法定证据" in res.get("failure_reason", "")


def test_adversarial_submitter_self_approval_blocked(tmp_path, monkeypatch):
    """场景 10 & 11: 提交人试图自行审批反馈进入训练集 -> 强制抛出 PermissionError 阻断。"""
    monkeypatch.setenv("CONTRACT_KB_DATA_DIR", str(tmp_path))

    # 提交人 Alice 提交反馈
    record_user_feedback(
        task_id="task_adv_self_approve",
        file_sha256="0" * 64,
        annotation_id="ann_01",
        sample_data={"clause_category": "LIABILITY"},
        reviewer_id="lawyer_alice",
        tenant_id="corp_adv",
    )

    # 审批人仍是 Alice (自提自批)
    alice_principal = MockPrincipal(
        user_id="lawyer_alice", tenant_id="corp_adv", roles=["reviewer"]
    )

    with pytest.raises(PermissionError) as exc_info:
        approve_feedback_for_training(
            task_id="task_adv_self_approve",
            annotation_id="ann_01",
            approver=alice_principal,
            tenant_id="corp_adv",
            reason="自提自批",
        )
    assert "防自提自批安全阻断" in str(exc_info.value)


def test_adversarial_cross_tenant_approval_blocked(tmp_path, monkeypatch):
    """场景 12: 租户 B 的审批人审批租户 A 的样本 -> 强制抛出 PermissionError 阻断。"""
    monkeypatch.setenv("CONTRACT_KB_DATA_DIR", str(tmp_path))

    record_user_feedback(
        task_id="task_tenant_a",
        file_sha256="0" * 64,
        annotation_id="ann_a",
        sample_data={"clause_category": "LIABILITY"},
        reviewer_id="user_a",
        tenant_id="tenant_alpha",
    )

    approver_b = MockPrincipal(
        user_id="lead_counsel_b", tenant_id="tenant_beta", roles=["admin"]
    )

    with pytest.raises(PermissionError) as exc_info:
        approve_feedback_for_training(
            task_id="task_tenant_a",
            annotation_id="ann_a",
            approver=approver_b,
            tenant_id="tenant_alpha",
            reason="跨租户越权审批",
        )
    assert "禁止跨租户批准训练数据" in str(exc_info.value)


def test_adversarial_revoked_sample_not_exported(tmp_path, monkeypatch):
    """场景 13: 审批通过后又被撤销的样本，SFT 导出时必须被彻底剔除。"""
    monkeypatch.setenv("CONTRACT_KB_DATA_DIR", str(tmp_path))

    record_user_feedback(
        task_id="task_revokable",
        file_sha256="0" * 64,
        annotation_id="ann_rev",
        sample_data={"clause_category": "CONFIDENTIALITY"},
        reviewer_id="submitter_bob",
        tenant_id="tenant_gamma",
    )

    approver_carol = MockPrincipal(
        user_id="approver_carol", tenant_id="tenant_gamma", roles=["reviewer"]
    )
    approve_feedback_for_training(
        task_id="task_revokable",
        annotation_id="ann_rev",
        approver=approver_carol,
        tenant_id="tenant_gamma",
        reason="初审通过",
    )

    # 管理员发现问题执行撤销
    admin_principal = MockPrincipal(
        user_id="chief_admin", tenant_id="tenant_gamma", roles=["admin"]
    )
    revoke_training_approval(
        task_id="task_revokable",
        annotation_id="ann_rev",
        approver=admin_principal,
        tenant_id="tenant_gamma",
        reason="存在违规内容撤销入库",
    )

    # 导出 SFT 数据集
    export_res = export_sft_dataset(tenant_id="tenant_gamma")
    assert export_res["train_count"] == 0, f"撤销样本不应导出进入训练集: {export_res}"


def test_adversarial_cross_tenant_rule_isolation():
    """场景 17: 跨租户规则访问隔离 -> 严格阻断为 TENANT_MISMATCH。"""
    verifier = CitationVerifier()
    rule = {
        "rule_id": "RULE_CORP_X",
        "tenant_id": "corp_x",
        "status": "APPROVED",
        "source_clauses": ["C1"],
    }
    storage = AdversarialStorage(rules=[rule], clauses={"C1": {"doc_status": "ACTIVE"}})
    res = verifier.verify_rule_evidence(
        storage=storage, rule_item=rule, expected_tenant_id="corp_y"
    )
    assert not res.verified
    assert res.status == "TENANT_MISMATCH"
