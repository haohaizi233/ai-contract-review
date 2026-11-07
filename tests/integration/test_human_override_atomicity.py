# -*- coding: utf-8 -*-
"""集成测试：人工覆写的事务、并发与审计原子化（T02 / F02）。

严格验证：
1. 故障原子性：在 record_human_override、log_audit 或提交时注入失败，
   review_issues.human_state 必须完整回滚，不得遗留未审计的部分业务状态；
2. 乐观并发控制：当指定 expected_state 时，若状态已改变必须抛出冲突（409），
   并发覆写不得静默丢失前次修改；
3. 租户隔离：跨租户覆写必须被严格拒绝；
4. 成功留痕：单次成功覆写必须原子化写入 issue 状态变更、human_overrides 记录与 audit_log。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import pytest

from contract_kb.golden import generate_all
from contract_kb.modules.rules.governance import (
    HumanOverrideConflictError,
    HumanOverrideService,
)
from contract_kb.review_engine import ReviewEngine
from contract_kb.storage import Storage


@pytest.fixture
def test_setup(tmp_path: Path):
    samples_dir = tmp_path / "samples"
    manifest = generate_all(samples_dir)
    sample_file = samples_dir / manifest[0]["file"]

    db_path = tmp_path / "test_override.sqlite"
    st = Storage(db_path)
    engine = ReviewEngine(st)
    res = engine.review_result(sample_file, tenant_id="tenant_alpha", task_id="task_001")
    assert len(res.issues) > 0, "测试样本必须包含至少一个审核问题"
    target_issue = res.issues[0]

    service = HumanOverrideService()
    try:
        yield st, service, target_issue["issue_id"], "task_001"
    finally:
        st.close()


def test_override_rollback_when_history_write_fails(test_setup):
    """验证 F02 核心缺陷：当 record_human_override 抛出异常时，整个操作必须完全回滚。"""
    st, service, issue_id, task_id = test_setup
    initial_issue = st.get_issue(issue_id)
    initial_state = initial_issue.get("human_state") or "PENDING"

    # 注入覆盖历史写入失败
    def _exploding_record(**kwargs):
        raise RuntimeError("injected database error on record_human_override")

    with patch.object(st, "record_human_override", side_effect=_exploding_record):
        with pytest.raises(RuntimeError, match="injected database error"):
            service.apply_override(
                st,
                issue_id=issue_id,
                action="IGNORE",
                actor="legal_reviewer",
                reason="synthetic test",
                tenant_id="tenant_alpha",
            )

    # 必须完全回滚：human_state 仍为原状态，override 表无新增记录
    after_issue = st.get_issue(issue_id)
    assert after_issue["human_state"] == initial_state, (
        f"失败后状态必须保持原样，实际被篡改为: {after_issue['human_state']}"
    )
    overrides = st.list_human_overrides(task_id)
    assert len(overrides) == 0, "失败后不得残留 override 历史记录"


def test_override_rollback_when_audit_fails(test_setup):
    """验证审计日志写入失败时的完整回滚。"""
    st, service, issue_id, task_id = test_setup
    initial_issue = st.get_issue(issue_id)
    initial_state = initial_issue.get("human_state") or "PENDING"

    def _exploding_audit(*args, **kwargs):
        raise RuntimeError("injected audit log failure")

    with patch.object(st, "log_audit", side_effect=_exploding_audit):
        with pytest.raises(RuntimeError, match="injected audit log failure"):
            service.apply_override(
                st,
                issue_id=issue_id,
                action="ACCEPT",
                actor="auditor",
                reason="testing audit rollback",
                tenant_id="tenant_alpha",
            )

    after_issue = st.get_issue(issue_id)
    assert after_issue["human_state"] == initial_state
    assert len(st.list_human_overrides(task_id)) == 0


def test_override_optimistic_concurrency_conflict(test_setup):
    """验证带 expected_state 的乐观并发控制。"""
    st, service, issue_id, task_id = test_setup

    # 1. 第一次合法覆写（期望从 PENDING 变为 IGNORE）
    res1 = service.apply_override(
        st,
        issue_id=issue_id,
        action="IGNORE",
        actor="alice",
        reason="first override",
        tenant_id="tenant_alpha",
        expected_state="PENDING",
    )
    assert res1["human_state"] == "IGNORE"

    # 2. 第二次覆写仍指定 expected_state="PENDING" -> 必须报并发冲突
    with pytest.raises(HumanOverrideConflictError):
        service.apply_override(
            st,
            issue_id=issue_id,
            action="ACCEPT",
            actor="bob",
            reason="conflicting override",
            tenant_id="tenant_alpha",
            expected_state="PENDING",
        )

    # 状态仍保持第一次的 IGNORE
    current = st.get_issue(issue_id)
    assert current["human_state"] == "IGNORE"


def test_override_cross_tenant_rejected(test_setup):
    """跨租户覆写必须被拒绝。"""
    st, service, issue_id, _ = test_setup
    with pytest.raises(PermissionError, match="跨租户"):
        service.apply_override(
            st,
            issue_id=issue_id,
            action="IGNORE",
            actor="intruder",
            reason="cross tenant attempt",
            tenant_id="tenant_beta_foreign",
        )


def test_override_success_atomic_consistency(test_setup):
    """成功覆写的完整一致性与留痕断言。"""
    st, service, issue_id, task_id = test_setup
    res = service.apply_override(
        st,
        issue_id=issue_id,
        action="FALSE_POSITIVE",
        actor="senior_legal",
        reason="verified compliant with 2026 regulations",
        tenant_id="tenant_alpha",
        expected_state="PENDING",
    )
    assert res["human_state"] == "FALSE_POSITIVE"
    assert res["previous_state"] == "PENDING"

    issue = st.get_issue(issue_id)
    assert issue["human_state"] == "FALSE_POSITIVE"
    assert issue["finding_source"] == "HUMAN"

    overrides = st.list_human_overrides(task_id)
    assert len(overrides) == 1
    ovr = overrides[0]
    assert ovr["issue_id"] == issue_id
    assert ovr["action"] == "FALSE_POSITIVE"
    assert ovr["actor"] == "senior_legal"
    assert ovr["previous_state"] == "PENDING"
    assert ovr["new_state"] == "FALSE_POSITIVE"
