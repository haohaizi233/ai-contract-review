# -*- coding: utf-8 -*-
"""Phase 16-S: SSE 订阅与 Agent 会话跨用户隔离安全测试。

覆盖：
- Case 04: 同租户跨用户 SSE 订阅越权阻断 (User B 尝试订阅 User A 的 Session 事件)
- Case 05: 跨租户 / 跨用户任务控制 (暂停、重试、跳过、取消) 权限阻断
- Case 15: 并发重复操作安全 (重复暂停/取消/重连无状态错乱与死锁)
"""

import threading
import time

import pytest

from contract_kb.application.agents.review_sessions import AgentReviewManager


def test_cross_user_sse_subscription_blocked():
    """Case 04: 用户 B 试图订阅同租户用户 A 的审核会话 SSE 事件，必须被严格拒绝。"""
    manager = AgentReviewManager()
    session_id = f"sess_sse_iso_{time.time_ns()}"
    tenant_id = "tenant_alpha"

    # User A 创建会话
    sess_a = manager.get_or_create_session(
        session_id=session_id,
        tenant_id=tenant_id,
        user_id="user_alice",
    )
    assert sess_a.user_id == "user_alice"

    # User B 试图调用 subscribe_events 窃取事件流
    with pytest.raises(PermissionError) as exc_info:
        manager.subscribe_events(
            session_id=session_id,
            tenant_id=tenant_id,
            user_id="user_bob",  # 攻击者窃听
        )
    assert "跨用户越权阻断" in str(exc_info.value)

    # 验证 User A 正常订阅成功
    q_alice = manager.subscribe_events(
        session_id=session_id,
        tenant_id=tenant_id,
        user_id="user_alice",
    )
    assert q_alice is not None
    manager.unsubscribe_events(
        q_alice, session_id=session_id, tenant_id=tenant_id, user_id="user_alice"
    )


def test_cross_tenant_and_cross_user_task_control_blocked():
    """Case 05: 非所有者用户无法暂停、跳过、重试或取消其他用户的合同审核任务。"""
    manager = AgentReviewManager()
    session_id = f"sess_ctrl_{time.time_ns()}"

    # Owner: alice@tenant_alpha
    sess = manager.get_or_create_session(
        session_id=session_id,
        tenant_id="tenant_alpha",
        user_id="user_alice",
    )
    assert sess is not None

    # 1. 同租户不同用户 bob 尝试操作，必须被 PermissionError 阻断
    with pytest.raises(PermissionError):
        manager.toggle_pause(
            session_id=session_id, tenant_id="tenant_alpha", user_id="user_bob"
        )

    with pytest.raises(PermissionError):
        manager.prioritize_card(
            "ann-1", session_id=session_id, tenant_id="tenant_alpha", user_id="user_bob"
        )

    with pytest.raises(PermissionError):
        manager.skip_card(
            "ann-1", session_id=session_id, tenant_id="tenant_alpha", user_id="user_bob"
        )

    with pytest.raises(PermissionError):
        manager.cancel_task(
            session_id=session_id, tenant_id="tenant_alpha", user_id="user_bob"
        )

    # 2. 跨租户 attacker 尝试操作，必须被 PermissionError 阻断
    with pytest.raises(PermissionError):
        manager.get_session(
            session_id=session_id, tenant_id="tenant_beta", user_id="attacker"
        )

    with pytest.raises(PermissionError):
        manager.cancel_task(
            session_id=session_id, tenant_id="tenant_beta", user_id="attacker"
        )

    # 3. 正常所有者 alice 可以执行控制
    cur_st = manager.toggle_pause(
        session_id=session_id, tenant_id="tenant_alpha", user_id="user_alice"
    )
    assert cur_st in ("PAUSED", "RUNNING")
    cancelled = manager.cancel_task(
        session_id=session_id, tenant_id="tenant_alpha", user_id="user_alice"
    )
    assert cancelled is True


def test_concurrent_repeat_actions_stability():
    """Case 15: 并发重复暂停、取消与订阅/退订，系统不发生死锁或状态错乱。"""
    manager = AgentReviewManager()
    session_id = f"sess_concurrent_{time.time_ns()}"
    tenant_id = "tenant_gamma"
    user_id = "user_charlie"

    sess = manager.get_or_create_session(
        session_id=session_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )

    errors = []

    def worker_toggle_pause():
        try:
            for _ in range(20):
                manager.toggle_pause(
                    session_id=session_id, tenant_id=tenant_id, user_id=user_id
                )
                time.sleep(0.005)
        except Exception as e:
            errors.append(e)

    def worker_subscribe_unsubscribe():
        try:
            for _ in range(20):
                q = manager.subscribe_events(
                    session_id=session_id, tenant_id=tenant_id, user_id=user_id
                )
                sess.broadcast("ping", {"msg": "test"})
                manager.unsubscribe_events(
                    q, session_id=session_id, tenant_id=tenant_id, user_id=user_id
                )
                time.sleep(0.005)
        except Exception as e:
            errors.append(e)

    threads = [
        threading.Thread(target=worker_toggle_pause),
        threading.Thread(target=worker_toggle_pause),
        threading.Thread(target=worker_subscribe_unsubscribe),
        threading.Thread(target=worker_subscribe_unsubscribe),
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"并发操作出现异常: {errors}"
