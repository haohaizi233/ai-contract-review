# -*- coding: utf-8 -*-
"""Asynchronous subprocess runner for the Desktop GUI dashboard."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from tkinter import messagebox

from .commands import PROJECT_ROOT


class ProcessRunner:
    """子进程异步执行器，保证 UI 界面在耗时命令运行期间完全流畅无卡顿。"""

    def __init__(self, log_callback, status_callback, finish_callback) -> None:
        self.log_callback = log_callback
        self.status_callback = status_callback
        self.finish_callback = finish_callback
        self.current_process: subprocess.Popen | None = None
        self._is_running = False
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._is_running

    def run_commands_sequential(self, cmd_list: list[tuple[str, list[str]]]):
        """顺序执行一组命令"""
        if self._is_running:
            messagebox.showwarning(
                "提示", "当前已有命令正在执行中，请等待其完成或先点击【终止】。"
            )
            return

        self._is_running = True
        self._thread = threading.Thread(
            target=self._worker_sequential, args=(cmd_list,), daemon=True
        )
        self._thread.start()

    def run_command(self, title: str, cmd: list[str], cwd: Path | None = None):
        """执行单条命令"""
        self.run_commands_sequential([(title, cmd)])

    def _worker_sequential(self, cmd_list: list[tuple[str, list[str]]]):
        total = len(cmd_list)
        all_success = True

        for idx, (title, cmd) in enumerate(cmd_list, 1):
            if not self._is_running:
                break

            self.status_callback(f"[{idx}/{total}] 正在执行: {title} ...", is_busy=True)
            display_cmd = " ".join(
                "<inline-python-script>" if "\n" in str(c) else str(c) for c in cmd
            )
            self.log_callback(
                f"\n{'=' * 70}\n>>> [{idx}/{total}] 任务开始: {title}\n>>> 执行指令: {display_cmd}\n{'=' * 70}\n",
                tag="header",
            )

            t0 = time.time()
            try:
                # Windows 下隐藏窗口调用
                creationflags = 0
                if sys.platform == "win32":
                    creationflags = subprocess.CREATE_NO_WINDOW

                env = os.environ.copy()
                env["PYTHONUNBUFFERED"] = "1"

                self.current_process = subprocess.Popen(
                    cmd,
                    cwd=str(PROJECT_ROOT),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=creationflags,
                )

                assert self.current_process.stdout is not None
                # 实时捕获标准输出流
                for line in iter(self.current_process.stdout.readline, ""):
                    if not line:
                        break
                    self.log_callback(line, tag=self._categorize_line(line))

                self.current_process.stdout.close()
                ret_code = self.current_process.wait()
                cost = time.time() - t0

                if ret_code == 0:
                    self.log_callback(
                        f"\n>>> [OK] {title} 执行成功 (耗时: {cost:.2f}s, 退出码: 0)\n",
                        tag="success",
                    )
                else:
                    self.log_callback(
                        f"\n>>> [FAIL] {title} 异常退出 (耗时: {cost:.2f}s, 退出码: {ret_code})\n",
                        tag="error",
                    )
                    all_success = False
                    # 多步执行遇错中断
                    if total > 1:
                        self.log_callback(
                            ">>> [ABORT] 后续任务已取消执行。\n", tag="warning"
                        )
                        break

            except Exception as e:
                self.log_callback(
                    f"\n>>> [ERROR] 进程启动或执行发生异常: {e}\n", tag="error"
                )
                all_success = False
                break
            finally:
                self.current_process = None

        self._is_running = False
        self.finish_callback(all_success)

    def terminate(self):
        """强制终止正在执行的子进程"""
        if self.current_process and self.current_process.poll() is None:
            try:
                self.status_callback("正在强制终止进程...", is_busy=True)
                self.current_process.terminate()
                # Windows taskkill 彻底终止进程树
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self.current_process.pid)],
                        capture_output=True,
                    )
                self.log_callback(
                    "\n>>> [TERMINATED] 用户已手动终止当前任务。\n", tag="warning"
                )
            except Exception as e:
                self.log_callback(f"\n>>> 终止失败: {e}\n", tag="error")
        self._is_running = False
        self.finish_callback(False)

    @staticmethod
    def _categorize_line(line: str) -> str:
        lower = line.lower()
        cleaned = lower.replace("failures=0", "").replace("failed=0", "")
        if any(
            w in cleaned for w in ["fail", "error", "traceback", "exception", "failed"]
        ):
            return "error"
        if any(
            w in lower
            for w in ["[ok]", "pass", "success", "imported", "100%", "completed"]
        ):
            return "success"
        if any(w in lower for w in ["warn", "skipped", "pending"]):
            return "warning"
        return "normal"
