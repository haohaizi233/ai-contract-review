# -*- coding: utf-8 -*-
"""后端静默执行的法务 AI 智能体审查任务管理器（Agent Review Task Manager）- 企业级全链路容灾加固版。

借鉴爱马仕（Hermes Agent）、Claude Code (CC) 与小龙虾（OpenClaw）核心高可用与容灾机制：
1. 【代际令牌与僵尸线程彻底消除】(Generation Token / Anti-Zombie Isolation - CC)：
   每次启动或重置审查任务时分配递增 generation 序号，老线程在每次循环前后检测代际，
   若已过时则立即自我终止，绝无多个线程竞态抢占队列或并发压垮大模型端点。
2. 【原子检查点与断点续跑自愈】(Atomic Checkpointing & Crash Recovery - Hermes / OpenClaw)：
   研判进度与每张完成卡片实时落盘至独立持久化检查点（.tmp + replace 原子替换）。
   无论浏览器刷新、断线重连还是服务重启，瞬时从最近检查点无缝恢复已研判成果，杜绝重复扣减算力。
3. 【任务级心跳看门狗与卡死抢救】(Queue Watchdog & Stagnation Supervisor - OpenClaw)：
   独立看门狗守护循环定时巡检单张卡片耗时。若因极端异常导致卡片处理超限（默认45s），
   看门狗自动介入抢救并施加高质量企业红线兜底，将卡片流转至下一条，坚决杜绝全流程无限期卡死。
4. 【交互式单卡重试与优先级插队】(Interactive Card Control & Priority Scheduling)：
   支持对疑难或降级卡片一键重审（清除缓存后插队至队列最前端并激活调度），支持卡片插队与手动跳过。
5. 【自愈式 SSE 事件广播】(Self-Healing Event Stream)：
   新连入客户端即时分发包含熔断状态、看门狗指标与已完成卡片的完整快照，支持无损断线重连。
"""

from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
from pathlib import Path
from typing import Any

from contract_kb import config
from contract_kb.legal_agent import get_legal_agent

CHECKPOINT_DIR = config.DATA_DIR / "agent_checkpoints"


class AgentReviewManager:
    """后端常驻的高可用合同审查智能体任务调度器与 SSE 广播中心。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._subscribers: set[queue.Queue] = set()

        # 当前文档研判会话状态
        self.doc_id: str = ""
        self.doc_context: dict[str, Any] = {}
        self.annotations: list[dict[str, Any]] = []
        self.annotations_map: dict[str, dict[str, Any]] = {}
        self.status: str = "IDLE"  # IDLE | RUNNING | PAUSED | COMPLETED
        self.current_ann_id: str | None = None
        self.completed_count: int = 0
        self.total_count: int = 0

        # 看门狗与执行代际（Anti-Zombie）
        self._worker_generation: int = 0
        self._card_start_time: float = 0.0
        self.card_timeout_seconds: float = 45.0

        # 内部工作线程与任务队列
        self._work_queue: list[str] = []
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # 默认不暂停

        # 启动常驻看门狗巡检线程
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_supervisor_loop,
            daemon=True,
            name="LegalAgentQueueWatchdog",
        )
        self._watchdog_thread.start()

    # ==================== 检查点持久化与恢复 (Hermes 3-Layer Memory) ====================

    def _get_checkpoint_file(self, doc_id: str) -> Path:
        CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        safe_hash = hashlib.sha256(doc_id.encode("utf-8")).hexdigest()[:20]
        return CHECKPOINT_DIR / f"ckpt_{safe_hash}.json"

    def _save_checkpoint(self) -> None:
        """原子落盘保存当前文档的研判检查点。"""
        if not self.doc_id:
            return
        try:
            ckpt_file = self._get_checkpoint_file(self.doc_id)
            completed_cards = [
                {
                    "id": a["id"],
                    "index": a.get("index"),
                    "risk_level": a.get("risk_level"),
                    "risk_label": a.get("risk_label"),
                    "ai_analysis": a.get("ai_analysis"),
                    "action_advice": a.get("action_advice"),
                    "reasoning": a.get("_reasoning", ""),
                    "source": a.get("_source", "AI_AGENT"),
                    "elapsed_sec": a.get("_elapsedSec", 0),
                    "from_cache": a.get("_fromCache", False),
                }
                for a in self.annotations
                if a.get("_aiDone")
            ]
            data = {
                "doc_id": self.doc_id,
                "doc_context": self.doc_context,
                "total_count": self.total_count,
                "completed_count": len(completed_cards),
                "completed_cards": completed_cards,
                "updated_at": time.time(),
            }
            tmp = ckpt_file.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(ckpt_file)
        except Exception:
            pass

    def _load_checkpoint(self, doc_id: str) -> dict[str, Any] | None:
        """从持久化磁盘加载检查点。"""
        try:
            ckpt_file = self._get_checkpoint_file(doc_id)
            if ckpt_file.exists():
                return json.loads(ckpt_file.read_text(encoding="utf-8"))
        except Exception:
            pass
        return None

    def _clear_checkpoint(self, doc_id: str) -> None:
        try:
            ckpt_file = self._get_checkpoint_file(doc_id)
            if ckpt_file.exists():
                ckpt_file.unlink()
        except Exception:
            pass

    # ==================== 任务调度与代际隔离 ====================

    def start_document_review(
        self,
        diff_data: dict[str, Any],
        force_restart: bool = False,
    ) -> None:
        """为当前文档初始化或启动后端静默审查任务（具备代际隔离与断点续跑）。"""
        with self._lock:
            doc_name = diff_data.get("uploaded_file", "unknown.docx")
            matched_tpl = diff_data.get("matched_template_name", "")
            raw_anns = diff_data.get("annotations") or []

            new_doc_id = f"{doc_name}:{matched_tpl}:{len(raw_anns)}"

            # 同文档且正在进行中，无需重置
            if (
                not force_restart
                and self.doc_id == new_doc_id
                and self.status in ("RUNNING", "COMPLETED")
            ):
                return

            # 递增代际令牌：彻底令前序所有活跃工作线程在下一循环安全退出（杜绝僵尸线程）
            self._worker_generation += 1
            current_gen = self._worker_generation

            self._stop_event.set()
            self._pause_event.set()

            self.doc_id = new_doc_id
            self.doc_context = {
                "uploaded_file": doc_name,
                "matched_template_name": matched_tpl,
            }

            if force_restart:
                self._clear_checkpoint(new_doc_id)

            # 建立深拷贝与索引
            self.annotations = [dict(a) for a in raw_anns]
            self.annotations_map = {a["id"]: a for a in self.annotations}
            self.total_count = len(self.annotations)
            self.current_ann_id = None

            # 尝试从持久化检查点恢复（秒级断点自愈）
            restored_count = 0
            if not force_restart:
                ckpt = self._load_checkpoint(new_doc_id)
                if ckpt and ckpt.get("completed_cards"):
                    for card in ckpt["completed_cards"]:
                        cid = card.get("id")
                        if cid and cid in self.annotations_map:
                            item = self.annotations_map[cid]
                            item["risk_level"] = card.get(
                                "risk_level", item.get("risk_level")
                            )
                            item["risk_label"] = card.get(
                                "risk_label", item.get("risk_label")
                            )
                            item["ai_analysis"] = card.get(
                                "ai_analysis", item.get("ai_analysis")
                            )
                            item["action_advice"] = card.get(
                                "action_advice", item.get("action_advice")
                            )
                            item["_reasoning"] = card.get("reasoning", "")
                            item["_source"] = card.get("source", "CHECKPOINT_RESUMED")
                            item["_elapsedSec"] = card.get("elapsed_sec", 0)
                            item["_fromCache"] = card.get("from_cache", True)
                            item["_aiDone"] = True
                            restored_count += 1

            self.completed_count = sum(1 for a in self.annotations if a.get("_aiDone"))

            # 待研判条款排队：高危(100) > 缺失(90) > 中危(50) > 其他(10)
            pending = [a for a in self.annotations if not a.get("_aiDone")]

            if not pending and self.total_count > 0:
                self.status = "COMPLETED"
                # 全量恢复完毕，直接广播并返回
                self.broadcast(
                    "all_complete",
                    {
                        "completed": self.completed_count,
                        "total": self.total_count,
                        "message": f"文档条款已从持久化检查点全部秒级自愈恢复（共 {restored_count} 项）！",
                    },
                )
                return

            def _priority(a: dict[str, Any]) -> int:
                if a.get("risk_level") == "HIGH":
                    return 100
                if a.get("delta_type") == "MISSING_CLAUSE":
                    return 90
                if a.get("risk_level") == "MEDIUM":
                    return 50
                return 10

            pending.sort(key=_priority, reverse=True)
            self._work_queue = [a["id"] for a in pending]
            self.status = "RUNNING"

            # 启动绑定该代际的全新工作线程
            self._stop_event.clear()
            self._worker_thread = threading.Thread(
                target=self._background_worker_loop,
                args=(current_gen,),
                daemon=True,
                name=f"LegalAgentWorker-Gen{current_gen}",
            )
            self._worker_thread.start()

        # 广播新任务启动事件
        self.broadcast(
            "init",
            {
                "doc_id": self.doc_id,
                "status": "RUNNING",
                "completed": self.completed_count,
                "total": self.total_count,
                "restored_count": restored_count,
                "doc_context": self.doc_context,
            },
        )

    def prioritize_card(self, ann_id: str) -> bool:
        """支持特定卡片插队至队列最前端。"""
        with self._lock:
            if ann_id in self._work_queue:
                self._work_queue.remove(ann_id)
                self._work_queue.insert(0, ann_id)
                self.broadcast("card_prioritized", {"id": ann_id})
                return True
        return False

    def retry_card(self, ann_id: str) -> bool:
        """对指定卡片进行单卡重审（清除缓存并插队前置重新研判）。"""
        with self._lock:
            ann_item = self.annotations_map.get(ann_id)
            if not ann_item:
                return False

            agent = get_legal_agent()
            cache_key = agent._cache_key(
                category=ann_item.get("clause_category", "OTHER"),
                delta_type=ann_item.get("delta_type", "TEXT_DELTA"),
                std_span=ann_item.get("standard_span")
                or ann_item.get("quote_standard")
                or "",
                rev_span=ann_item.get("reviewed_span")
                or ann_item.get("quote_reviewed")
                or "",
                clause_title=ann_item.get("clause_title", ""),
            )
            agent.clear_cache_for_key(cache_key)

            # 重置卡片状态并插队
            if ann_item.get("_aiDone"):
                ann_item["_aiDone"] = False
                self.completed_count = max(0, self.completed_count - 1)

            if ann_id in self._work_queue:
                self._work_queue.remove(ann_id)
            self._work_queue.insert(0, ann_id)

            # 若当前工作线程已结束，重新唤醒
            if (
                self.status in ("IDLE", "COMPLETED")
                or not self._worker_thread
                or not self._worker_thread.is_alive()
            ):
                self._worker_generation += 1
                cur_gen = self._worker_generation
                self.status = "RUNNING"
                self._stop_event.clear()
                self._pause_event.set()
                self._worker_thread = threading.Thread(
                    target=self._background_worker_loop,
                    args=(cur_gen,),
                    daemon=True,
                    name=f"LegalAgentWorker-Gen{cur_gen}",
                )
                self._worker_thread.start()

        self.broadcast("card_retry", {"id": ann_id})
        return True

    def skip_card(self, ann_id: str) -> bool:
        """手动跳过特定卡片研判。"""
        with self._lock:
            if ann_id in self._work_queue:
                self._work_queue.remove(ann_id)
            if self.current_ann_id == ann_id:
                # 触发看门狗抢救逻辑即可平滑流转
                self._card_start_time = 0.0

            ann_item = self.annotations_map.get(ann_id)
            if ann_item and not ann_item.get("_aiDone"):
                ann_item["_aiDone"] = True
                ann_item["_source"] = "MANUALLY_SKIPPED"
                self.completed_count += 1
                self._save_checkpoint()

        self.broadcast("card_skip", {"id": ann_id})
        return True

    def toggle_pause(self) -> str:
        """切换暂停/继续状态。"""
        with self._lock:
            if self._pause_event.is_set():
                self._pause_event.clear()
                self.status = "PAUSED"
            else:
                self._pause_event.set()
                self.status = "RUNNING"
            cur_status = self.status

        self.broadcast("status_change", {"status": cur_status})
        return cur_status

    def reset_circuit_breaker(self) -> dict[str, Any]:
        """手动重置大模型熔断器。"""
        agent = get_legal_agent()
        agent.circuit_breaker.reset()
        status = agent.circuit_breaker.get_status()
        self.broadcast("circuit_state_change", status)
        return status

    # ==================== 工作循环与看门狗抢救 ====================

    def _background_worker_loop(self, my_generation: int) -> None:
        """常驻后台执行循环（严格绑定 generation 代际，杜绝僵尸线程竞态）。"""
        agent = get_legal_agent()

        while not self._stop_event.is_set():
            # 严格代际检测：若有新任务启动，本老线程立刻无害退出
            if self._worker_generation != my_generation:
                break

            self._pause_event.wait()
            if self._worker_generation != my_generation or self._stop_event.is_set():
                break

            ann_id = None
            with self._lock:
                if not self._work_queue:
                    self.status = "COMPLETED"
                    self.current_ann_id = None
                    self._card_start_time = 0.0
                    break
                ann_id = self._work_queue.pop(0)
                self.current_ann_id = ann_id
                self._card_start_time = time.time()
                ann_item = self.annotations_map.get(ann_id)

            if not ann_item:
                continue

            # 1. 广播卡片研判启动事件
            self.broadcast(
                "card_start",
                {
                    "id": ann_id,
                    "index": ann_item.get("index"),
                    "clause_title": ann_item.get("clause_title"),
                    "status": "RUNNING",
                },
            )

            # 2. 静默调用法务智能体研判（底层内嵌看门狗硬超时与自适应熔断）
            try:
                res = agent.review_clause(
                    annotation=ann_item,
                    doc_context=self.doc_context,
                    force_refresh=False,
                )
            except Exception as e:
                res = agent.build_high_precision_fallback(
                    annotation=ann_item,
                    error_msg=f"线程级捕获异常: {str(e)}",
                    source="FALLBACK_RULE",
                )

            # 再次检测代际：如果当前线程在执行网络请求期间代际已过时，则不污染新数据
            if self._worker_generation != my_generation:
                break

            # 3. 更新内存中卡片真实属性
            with self._lock:
                ann_item["risk_level"] = res.get(
                    "risk_level", ann_item.get("risk_level")
                )
                ann_item["risk_label"] = res.get(
                    "risk_label", ann_item.get("risk_label")
                )
                ann_item["ai_analysis"] = res.get(
                    "ai_analysis", ann_item.get("ai_analysis")
                )
                ann_item["action_advice"] = res.get(
                    "action_advice", ann_item.get("action_advice")
                )
                ann_item["_reasoning"] = res.get("reasoning", "")
                ann_item["_source"] = res.get("source", "AI_AGENT")
                ann_item["_elapsedSec"] = res.get("elapsed_sec", 0)
                ann_item["_fromCache"] = res.get("from_cache", False)
                ann_item["_aiDone"] = True
                self.completed_count += 1
                cur_completed = self.completed_count
                cur_total = self.total_count
                self.current_ann_id = None
                self._card_start_time = 0.0

            # 4. 原子检查点落盘（保障即使断线重启也能秒级自愈）
            self._save_checkpoint()

            # 5. 广播单张卡片研判完成事件
            card_payload = {
                "id": ann_id,
                "index": ann_item.get("index"),
                "risk_level": ann_item["risk_level"],
                "risk_label": ann_item["risk_label"],
                "ai_analysis": ann_item["ai_analysis"],
                "action_advice": ann_item["action_advice"],
                "reasoning": ann_item["_reasoning"],
                "source": ann_item["_source"],
                "elapsed_sec": ann_item["_elapsedSec"],
                "from_cache": ann_item["_fromCache"],
                "completed": cur_completed,
                "total": cur_total,
            }
            self.broadcast("card_complete", card_payload)

            # 6. 广播总体进度
            pct = round((cur_completed / cur_total) * 100) if cur_total > 0 else 100
            self.broadcast(
                "progress",
                {
                    "completed": cur_completed,
                    "total": cur_total,
                    "percent": pct,
                },
            )

            # 稍微休眠 80ms 保持平稳调度
            time.sleep(0.08)

        # 队列排空完成
        with self._lock:
            if (
                self._worker_generation == my_generation
                and not self._stop_event.is_set()
            ):
                self.status = "COMPLETED"
                self.current_ann_id = None
                self._card_start_time = 0.0
                cur_completed = self.completed_count
                cur_total = self.total_count

                self.broadcast(
                    "all_complete",
                    {
                        "completed": cur_completed,
                        "total": cur_total,
                        "message": "全篇合同条款已全部静默研判完毕！",
                    },
                )

    def _watchdog_supervisor_loop(self) -> None:
        """任务级看门狗守护循环（借鉴 OpenClaw 滞留检测与抢救机制）。"""
        while True:
            time.sleep(2.0)
            try:
                stalled_id = None
                with self._lock:
                    if (
                        self.status == "RUNNING"
                        and self.current_ann_id
                        and self._card_start_time > 0
                    ):
                        elapsed = time.time() - self._card_start_time
                        if elapsed > self.card_timeout_seconds:
                            stalled_id = self.current_ann_id
                            stalled_item = self.annotations_map.get(stalled_id)
                            if stalled_item and not stalled_item.get("_aiDone"):
                                # 看门狗强制施救
                                agent = get_legal_agent()
                                res = agent.build_high_precision_fallback(
                                    annotation=stalled_item,
                                    error_msg=f"研判执行超过看门狗时限({round(elapsed)}s)，已被守护进程自动抢救流转",
                                    source="WATCHDOG_TIMEOUT_FALLBACK",
                                    elapsed_sec=elapsed,
                                )
                                stalled_item["risk_level"] = res["risk_level"]
                                stalled_item["risk_label"] = res["risk_label"]
                                stalled_item["ai_analysis"] = res["ai_analysis"]
                                stalled_item["action_advice"] = res["action_advice"]
                                stalled_item["_reasoning"] = res["reasoning"]
                                stalled_item["_source"] = res["source"]
                                stalled_item["_elapsedSec"] = res["elapsed_sec"]
                                stalled_item["_fromCache"] = False
                                stalled_item["_aiDone"] = True
                                self.completed_count += 1
                                self.current_ann_id = None
                                self._card_start_time = 0.0

                if stalled_id:
                    self._save_checkpoint()
                    self.broadcast(
                        "watchdog_alert",
                        {
                            "id": stalled_id,
                            "message": f"卡片 #{stalled_id} 耗时超限，看门狗已自动抢救并启用企业红线兜底。",
                        },
                    )
            except Exception:
                pass

    # ==================== SSE 订阅与事件分发 ====================

    def subscribe(self) -> queue.Queue:
        """为新的前端连接创建专有事件队列，并立即发送当前完整快照。"""
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            completed_cards = [
                {
                    "id": a["id"],
                    "index": a.get("index"),
                    "risk_level": a.get("risk_level"),
                    "risk_label": a.get("risk_label"),
                    "ai_analysis": a.get("ai_analysis"),
                    "action_advice": a.get("action_advice"),
                    "reasoning": a.get("_reasoning", ""),
                    "source": a.get("_source", "AI_AGENT"),
                    "elapsed_sec": a.get("_elapsedSec", 0),
                    "from_cache": a.get("_fromCache", False),
                }
                for a in self.annotations
                if a.get("_aiDone")
            ]
            agent = get_legal_agent()
            snapshot = {
                "doc_id": self.doc_id,
                "status": self.status,
                "current_ann_id": self.current_ann_id,
                "completed_count": self.completed_count,
                "total_count": self.total_count,
                "completed_cards": completed_cards,
                "circuit_breaker": agent.circuit_breaker.get_status(),
                "generation": self._worker_generation,
            }
            q.put({"name": "snapshot", "data": snapshot})
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        """前端连接关闭时移除队列，防止内存泄漏。"""
        with self._lock:
            self._subscribers.discard(q)

    def broadcast(self, event_name: str, data: dict[str, Any]) -> None:
        """向所有活跃的前端 SSE 订阅者分发事件。"""
        event = {"name": event_name, "data": data}
        with self._lock:
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    dead.append(q)
            for d in dead:
                self._subscribers.discard(d)

    def get_current_snapshot(self) -> dict[str, Any]:
        """获取当前最新会话状态。"""
        with self._lock:
            agent = get_legal_agent()
            return {
                "doc_id": self.doc_id,
                "status": self.status,
                "current_ann_id": self.current_ann_id,
                "completed_count": self.completed_count,
                "total_count": self.total_count,
                "circuit_breaker": agent.circuit_breaker.get_status(),
                "generation": self._worker_generation,
                "annotations": self.annotations,
            }


# 全局单例管理器
_global_manager: AgentReviewManager | None = None
_manager_lock = threading.Lock()


def get_agent_manager() -> AgentReviewManager:
    global _global_manager
    with _manager_lock:
        if _global_manager is None:
            _global_manager = AgentReviewManager()
        return _global_manager
