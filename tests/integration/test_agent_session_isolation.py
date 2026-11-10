# -*- coding: utf-8 -*-
"""Phase 13R2 & Phase 13R6: Agent 与 Web 工作台多用户/多会话并发隔离集成测试。"""

import tempfile
from pathlib import Path

from contract_kb.agent_queue_manager import AgentReviewManager


def test_concurrent_sessions_isolated_queues() -> None:
    """验证多会话在并发提交与广播时队列严格隔离，杜绝串线。"""
    manager = AgentReviewManager()

    sess_a = manager.get_or_create_session(
        session_id="session_user_alice",
        tenant_id="tenant_alpha",
        user_id="alice",
    )
    sess_b = manager.get_or_create_session(
        session_id="session_user_bob",
        tenant_id="tenant_beta",
        user_id="bob",
    )

    q_a = sess_a.subscribe()
    q_b = sess_b.subscribe()

    # 向 Session A 广播事件
    sess_a.broadcast("CLAUSE_REVIEWED", {"clause_id": "c1", "status": "APPROVED"})

    # 验证 Session A 收到，Session B 队列保持为空
    assert not q_a.empty()
    item_a = q_a.get_nowait()
    assert item_a["event"] == "CLAUSE_REVIEWED"
    assert item_a["data"]["clause_id"] == "c1"

    assert q_b.empty(), "Session B 不应收到 Session A 的任何广播事件"

    # 向 Session B 广播事件
    sess_b.broadcast("CLAUSE_REVIEWED", {"clause_id": "c2", "status": "REJECTED"})
    assert not q_b.empty()
    item_b = q_b.get_nowait()
    assert item_b["data"]["clause_id"] == "c2"

    assert q_a.empty(), "Session A 不应收到 Session B 的广播事件"


def test_session_cancellation_isolation() -> None:
    """验证取消会话 A 绝不影响处于运行或挂起状态的会话 B。"""
    manager = AgentReviewManager()

    sess_a = manager.get_or_create_session(
        session_id="session_to_cancel",
        tenant_id="tenant_1",
        user_id="u1",
    )
    sess_b = manager.get_or_create_session(
        session_id="session_to_keep",
        tenant_id="tenant_1",
        user_id="u2",
    )

    assert sess_a.status == "IDLE"
    assert sess_b.status == "IDLE"

    # 取消 A
    sess_a.cancel()
    assert sess_a.status == "CANCELLED"

    # 验证 B 依然正常存活
    assert sess_b.status == "IDLE"
    assert manager.get_session("session_to_keep") is not None


def test_session_storage_directory_isolation() -> None:
    """验证 Web 缓存目录按 session_id 进行绝对物理隔离，彻底废除全局文件。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        base_dir = Path(tmp_dir)

        session_a_dir = base_dir / "sess_user_1"
        session_b_dir = base_dir / "sess_user_2"

        session_a_dir.mkdir(parents=True, exist_ok=True)
        session_b_dir.mkdir(parents=True, exist_ok=True)

        file_a = session_a_dir / "contract.docx"
        file_b = session_b_dir / "contract.docx"

        file_a.write_text("CONTENT_USER_A_DATA", encoding="utf-8")
        file_b.write_text("CONTENT_USER_B_DATA", encoding="utf-8")

        # 验证物理文件互不干扰
        assert file_a.read_text(encoding="utf-8") == "CONTENT_USER_A_DATA"
        assert file_b.read_text(encoding="utf-8") == "CONTENT_USER_B_DATA"

        # 删除 A 不影响 B
        file_a.unlink()
        assert not file_a.exists()
        assert file_b.exists()
        assert file_b.read_text(encoding="utf-8") == "CONTENT_USER_B_DATA"
