# -*- coding: utf-8 -*-
"""Phase 13R2 (6.4 & 6.5): Agent 线程超时、Zombie Worker 防串扰、异常恢复与重试测试集。"""

import threading
import time
from unittest.mock import patch

from contract_kb.application.agents.review_sessions import AgentReviewManager


def test_late_worker_result_discarded_on_generation_change() -> None:
    """验证任务取消或切换新文档后，旧代际 Zombie Worker 的迟到返回不会污染当前状态。"""
    manager = AgentReviewManager()
    sess = manager.get_or_create_session("sess_zombie_test", "tenant_1", "user_1")

    diff_data_1 = {
        "uploaded_file_name": "doc_old.docx",
        "sha256": "hash_old_111",
        "matched_template_id": "tpl_1",
        "annotations": [
            {
                "id": "ann-1",
                "index": 1,
                "title": "旧条款1",
                "clause_category": "LIABILITY",
                "delta_type": "NUMBER_CHANGE",
                "quote_standard": "3‰",
                "quote_reviewed": "1‰",
            }
        ],
    }

    entered_review = threading.Event()
    allow_return = threading.Event()

    def slow_review(annotation, doc_context=None):
        entered_review.set()
        allow_return.wait(timeout=3.0)
        return {
            "id": annotation.get("id"),
            "risk_level": "LOW",
            "risk_label": "迟到错误覆盖",
            "ai_analysis": "ZOMBIE_RESULT_SHOULD_BE_DISCARDED",
            "action_advice": "ZOMBIE_ADVICE",
            "review_status": "AI_REVIEW_COMPLETED",
            "source": "ZOMBIE_WORKER",
        }

    with patch(
        "contract_kb.legal_agent.LegalReviewAgent.review_clause",
        side_effect=slow_review,
    ):
        sess.start_review(diff_data_1, force_restart=True)
        assert entered_review.wait(timeout=3.0), (
            "Worker 未在预期时间内进入 review_clause"
        )
        sess.cancel()  # 递增 _worker_generation 并置为 CANCELLED
        allow_return.set()
        if sess._worker_thread:
            sess._worker_thread.join(timeout=3.0)

        snap = sess.get_snapshot()
        assert snap["status"] == "CANCELLED"
        ann = snap["annotations"][0]
        assert ann.get("ai_analysis") != "ZOMBIE_RESULT_SHOULD_BE_DISCARDED"
        assert not ann.get("_aiDone")


def test_watchdog_or_skip_prevents_duplicate_worker_submission() -> None:
    """验证当某卡片已被跳过或 Watchdog 兜底标记 _aiDone 后，原慢线程返回不会重复提交或二次广播。"""
    manager = AgentReviewManager()
    sess = manager.get_or_create_session("sess_dup_guard", "tenant_1", "user_1")
    q = sess.subscribe()

    diff_data = {
        "uploaded_file_name": "doc_dup.docx",
        "sha256": "hash_dup_222",
        "matched_template_id": "tpl_1",
        "annotations": [
            {
                "id": "ann-dup-1",
                "index": 1,
                "title": "超时条款",
                "clause_category": "LIABILITY",
                "delta_type": "NUMBER_CHANGE",
            }
        ],
    }

    entered_review = threading.Event()
    allow_return = threading.Event()

    def slow_review(annotation, doc_context=None):
        entered_review.set()
        allow_return.wait(timeout=3.0)
        return {
            "id": annotation.get("id"),
            "risk_level": "LOW",
            "risk_label": "慢线程结果",
            "ai_analysis": "LATE_WORKER_OVERWRITE",
            "action_advice": "LATE_ADVICE",
            "review_status": "AI_REVIEW_COMPLETED",
            "source": "LATE_WORKER",
        }

    with patch(
        "contract_kb.legal_agent.LegalReviewAgent.review_clause",
        side_effect=slow_review,
    ):
        sess.start_review(diff_data, force_restart=True)
        assert entered_review.wait(timeout=3.0), (
            "Worker 未在预期时间内进入 review_clause"
        )
        # 在慢线程尚未返回时，模拟人工跳过/看门狗提前完成该卡片
        sess.skip_card("ann-dup-1")
        allow_return.set()
        if sess._worker_thread:
            sess._worker_thread.join(timeout=3.0)

        snap = sess.get_snapshot()
        ann = snap["annotations"][0]
        assert ann["_aiDone"] is True
        assert ann["_source"] == "MANUALLY_SKIPPED"
        assert ann.get("ai_analysis") != "LATE_WORKER_OVERWRITE"

        # 检查事件队列中绝无慢线程触发的 card_complete
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        complete_events = [e for e in events if e["event"] == "card_complete"]
        assert len(complete_events) == 0


def test_worker_exception_graceful_fallback_and_single_card_retry() -> None:
    """验证 Worker 异常崩溃时自动降级为 RULE_REVIEW_COMPLETED，且支持单卡重试与只广播一次完成。"""
    manager = AgentReviewManager()
    sess = manager.get_or_create_session("sess_recover", "tenant_1", "user_1")
    q = sess.subscribe()

    diff_data = {
        "uploaded_file_name": "doc_recover.docx",
        "sha256": "hash_rec_333",
        "matched_template_id": "tpl_1",
        "annotations": [
            {
                "id": "ann-rec-1",
                "index": 1,
                "title": "异常恢复测试条款",
                "clause_category": "CONFIDENTIALITY",
                "delta_type": "MISSING_CLAUSE",
                "quote_standard": "第五条 保密条款",
                "quote_reviewed": "",
            }
        ],
    }

    call_count = {"n": 0}

    def flaky_review(annotation, doc_context=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("模拟底层网络套接字意外断开")
        return {
            "id": annotation.get("id"),
            "risk_level": "HIGH",
            "risk_label": "重试成功",
            "ai_analysis": "重试后成功分析",
            "action_advice": "恢复保密条款",
            "review_status": "RULE_REVIEW_COMPLETED",
            "source": "RETRY_SUCCESS",
        }

    with patch(
        "contract_kb.legal_agent.LegalReviewAgent.review_clause",
        side_effect=flaky_review,
    ):
        sess.start_review(diff_data, force_restart=True)
        if sess._worker_thread:
            sess._worker_thread.join(timeout=5.0)
        for _ in range(50):
            if sess.status == "COMPLETED":
                break
            time.sleep(0.02)

        assert sess.status == "COMPLETED"
        snap1 = sess.get_snapshot()
        ann1 = snap1["annotations"][0]
        assert ann1["_aiDone"] is True
        assert ann1["review_status"] == "RULE_REVIEW_COMPLETED"
        assert ann1["_source"] == "WORKER_EXCEPTION_FALLBACK"

        # 清空旧队列，触发单卡重试
        while not q.empty():
            q.get_nowait()

        ok = sess.retry_card("ann-rec-1")
        assert ok is True

        if sess._worker_thread:
            sess._worker_thread.join(timeout=5.0)
        for _ in range(50):
            if sess.status == "COMPLETED":
                break
            time.sleep(0.02)

        assert sess.status == "COMPLETED"
        snap2 = sess.get_snapshot()
        ann2 = snap2["annotations"][0]
        assert ann2["ai_analysis"] == "重试后成功分析"
        assert ann2["_source"] == "RETRY_SUCCESS"

        # 确认本轮重试仅触发一次 all_complete
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        all_complete_events = [e for e in events if e["event"] == "all_complete"]
        assert len(all_complete_events) == 1
