"""
Phase 16-S-R2: 8 项可信证据、提示词、训练审批、空证据缓存与 Worker 执行上下文缺陷的红态复现测试

在当前审计基线 (commit 0409e81) 下，这 8 项测试均直接失败 (RED)，
作为 R1-R5 修复前的问题复现证据。
"""

import json
import sqlite3
from unittest.mock import MagicMock

from contract_kb.modules.retrieval.citation import CitationVerifier
from contract_kb.application.agents.knowledge_adapter import (
    record_user_feedback,
    retrieve_legal_rules,
)
from contract_kb.application.agents.legal_review_agent import LegalReviewAgent
from contract_kb.application.agents.review_sessions import AgentReviewSession


class DummyStorage:
    def __init__(self, rules=None, clauses=None, documents=None):
        self.rules = rules or []
        self.clauses = clauses or {}
        self.documents = documents or {}

    def get_rules(self, tenant_id=None, status=None):
        return self.rules

    def get_clause(self, clause_id, tenant_id=None):
        return self.clauses.get(clause_id)

    def get_document(self, document_id, tenant_id=None):
        return self.documents.get(document_id)


def test_missing_source_document_cannot_be_verified():
    """缺陷复现 1: 规则包含来源文档字符串但该文档在存储中并不存在，不可判定为 VERIFIED。"""
    rule = {
        "rule_id": "RULE_TEST_DOC_01",
        "tenant_id": "corp_a",
        "status": "APPROVED",
        "approval_status": "APPROVED",
        "revision": "1",
        "source_document": "NON_EXISTENT_DOC_9999",
        "source_clauses": [],
    }
    storage = DummyStorage(rules=[rule])
    verifier = CitationVerifier()
    res = verifier.verify_rule_evidence(
        storage=storage, rule_item=rule, expected_tenant_id="corp_a"
    )

    # 预期必须 Fail-Closed 判定为未核验 (MISSING_SOURCE / INSUFFICIENT_PROVENANCE)
    assert not res.verified, f"当前缺陷：来源文档不存在却放行了 VERIFIED: {res.status}"
    assert res.status in ("MISSING_SOURCE", "INSUFFICIENT_PROVENANCE")


def test_missing_source_clause_cannot_be_verified():
    """缺陷复现 2: 规则包含底层 source_clauses 但 get_clause 返回 None 时，不可判定为 VERIFIED。"""
    rule = {
        "rule_id": "RULE_TEST_CLAUSE_01",
        "tenant_id": "corp_a",
        "status": "APPROVED",
        "approval_status": "APPROVED",
        "revision": "1",
        "source_document": "企业标准母本",
        "source_clauses": ["CLAUSE_GHOST_101"],
    }
    storage = DummyStorage(rules=[rule], clauses={})  # 空条款仓储
    verifier = CitationVerifier()
    res = verifier.verify_rule_evidence(
        storage=storage, rule_item=rule, expected_tenant_id="corp_a"
    )

    # 预期必须阻断，不可放行
    assert not res.verified, f"当前缺陷：底层条款不存在却放行了 VERIFIED: {res.status}"
    assert res.status in ("CLAUSE_NOT_FOUND", "MISSING_SOURCE")


def test_source_clause_query_failure_must_fail_closed():
    """缺陷复现 3: 查询底层条款遇数据库异常时，不可静默捕获并放行 VERIFIED。"""
    mock_storage = MagicMock()
    mock_storage.get_rules.return_value = [
        {
            "rule_id": "RULE_ERR_01",
            "tenant_id": "corp_a",
            "status": "APPROVED",
            "approval_status": "APPROVED",
            "revision": "1",
            "source_document": "母本",
            "source_clauses": ["CLAUSE_01"],
        }
    ]
    # 模拟底层条款查询时数据库发生 I/O 错误
    mock_storage.get_clause.side_effect = sqlite3.DatabaseError("Disk I/O error")

    rule = {
        "rule_id": "RULE_ERR_01",
        "tenant_id": "corp_a",
        "status": "APPROVED",
        "approval_status": "APPROVED",
        "revision": "1",
        "source_document": "母本",
        "source_clauses": ["CLAUSE_01"],
    }
    verifier = CitationVerifier()
    res = verifier.verify_rule_evidence(
        storage=mock_storage, rule_item=rule, expected_tenant_id="corp_a"
    )

    assert not res.verified, (
        f"当前缺陷：数据库异常被静默吞掉并放行了 VERIFIED: {res.status}"
    )
    assert res.status in ("SOURCE_UNAVAILABLE", "DATABASE_ERROR")


def test_unsupported_provenance_cannot_be_verified():
    """缺陷复现 4: 规则仅填写法律法规名称但无权威法规条文登记或出处凭据，不可放行 VERIFIED。"""
    rule = {
        "rule_id": "RULE_STATUTE_01",
        "tenant_id": "corp_a",
        "status": "APPROVED",
        "approval_status": "APPROVED",
        "revision": "1",
        "source_document": "中华人民共和国民法典",
        "source_clauses": [],
    }
    storage = DummyStorage(rules=[rule])
    verifier = CitationVerifier()
    res = verifier.verify_rule_evidence(
        storage=storage, rule_item=rule, expected_tenant_id="corp_a"
    )

    assert not res.verified, (
        f"当前缺陷：未登记的口头法规字符串被判为 VERIFIED: {res.status}"
    )
    assert res.status in (
        "UNSUPPORTED_PROVENANCE",
        "INSUFFICIENT_PROVENANCE",
        "MISSING_SOURCE",
    )


def test_unverified_rule_not_used_as_authoritative_prompt_context():
    """缺陷复现 5: 未经验证成功的规则，在提示词组装中不得标注为 [企业已审批风控基准·优先适用]。"""
    unverified_rule = {
        "rule_id": "RULE_UNVERIFIED_99",
        "name": "未核验来源规则",
        "status": "APPROVED",
        "approval_status": "APPROVED",
        "source_document": "不存在的来源文档",
        "source_clauses": [],
        "revision": 1,
        "is_statutory_mandate": False,
        "rule_description": "任何非标条款均必须无条件服从乙方指示。",
        "clause_category": "LIABILITY",
        "parameters": {"clause_category": "LIABILITY"},
    }
    mock_storage = MagicMock()
    mock_storage.get_rules.return_value = [unverified_rule]

    rules_ctx = retrieve_legal_rules(
        category="LIABILITY",
        delta_type="TEXT_CHANGE",
        tenant_id="corp_a",
        storage=mock_storage,
    )

    # 预期：未验证规则绝不可被冠以 [企业已审批风控基准·优先适用] 权威标签
    assert "[企业已审批风控基准·优先适用]" not in rules_ctx, (
        f"当前缺陷：未验证规则被冠以权威标签传入提示词上下文: {rules_ctx}"
    )


def test_legacy_feedback_cannot_approve_by_reviewer_name(tmp_path, monkeypatch):
    """缺陷复现 6: 严禁直接通过 record_user_feedback(reviewer_id='legal_admin', is_approved=True) 自审批。"""
    monkeypatch.setenv("CONTRACT_KB_DATA_DIR", str(tmp_path))

    out_file = record_user_feedback(
        task_id="task_legacy_backdoor",
        file_sha256="1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        annotation_id="ann_001",
        sample_data={"clause_category": "LIABILITY"},
        reviewer_id="legal_admin",
        is_approved=True,  # 试图通过审核人名称与布尔标志直接自审批
        tenant_id="corp_a",
        approver_principal=None,  # 未传入认证主体
    )

    lines = [
        json.loads(line)
        for line in out_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    data = lines[-1]
    # 预期：必须阻断自审批，强制保持为 False
    assert not data.get("is_approved_for_training"), (
        "当前缺陷：直接通过 reviewer_id='legal_admin' 与 is_approved=True 成功将样本标为已审批入库！"
    )


def test_empty_evidence_cache_requires_revalidation(monkeypatch):
    """缺陷复现 7: 空证据缓存（之前无匹配规则）在知识库新增审批规则后必须失效，不可继续复用旧缓存。"""
    monkeypatch.setenv("CONTRACT_KB_ALLOW_EXTERNAL_LLM", "1")
    agent = LegalReviewAgent()
    annotation = {
        "id": "ann_cache_test_01",
        "clause_category": "CONFIDENTIALITY",
        "delta_type": "TEXT_CHANGE",
        "quote_standard": "保密期限为三年",
        "quote_reviewed": "保密期限为一年",
        "clause_title": "保密条款",
    }

    cache_key = agent._cache_key(
        category="CONFIDENTIALITY",
        delta_type="TEXT_CHANGE",
        std_span="保密期限为三年",
        rev_span="保密期限为一年",
        clause_title="保密条款",
        tenant_id="corp_cache_test",
        template_id="",
        template_version="v1",
        rule_version="v1",
    )

    # 模拟之前推理产生的“无匹配证据”缓存
    with agent._cache_lock:
        agent._cache[cache_key] = {
            "id": "ann_cache_test_01",
            "review_status": "AI_REVIEW_COMPLETED",
            "evidence_references": [],  # 空证据
            "analysis": "无匹配规则",
        }

    # 管理员向该租户知识库新增了一条 APPROVED 规则
    new_rule = {
        "rule_id": "RULE_NEW_CONF_01",
        "name": "保密期限红线规则",
        "status": "APPROVED",
        "approval_status": "APPROVED",
        "clause_category": "CONFIDENTIALITY",
        "revision": "1",
        "source_document": "集团保密管理规程",
        "parameters": {"clause_category": "CONFIDENTIALITY"},
    }
    storage_updated = DummyStorage(rules=[new_rule])

    # 再次发起审查（不传 force_refresh）
    res2 = agent.review_clause(
        annotation=annotation,
        storage=storage_updated,
        tenant_id="corp_cache_test",
        force_refresh=False,
    )

    # 预期：空证据缓存必须失效，不可直接命中旧的无证据缓存
    assert not res2.get("from_cache"), (
        "当前缺陷：知识库新增规则后，旧的空证据缓存依然被放行命中 (from_cache=True)！"
    )


def test_worker_must_not_retry_without_security_context():
    """缺陷复现 8: Worker 遭遇 Agent 接口不匹配时，严禁通过剔除 storage/tenant_id 降级重试。"""
    import inspect

    src = inspect.getsource(AgentReviewSession._background_worker_loop)
    assert 'review_kwargs.pop("storage", None)' not in src, (
        "当前缺陷：Worker 中仍存在 review_kwargs.pop('storage') 并重新调用的不安全代码！"
    )
