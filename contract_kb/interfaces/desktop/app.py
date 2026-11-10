# -*- coding: utf-8 -*-
"""Desktop GUI application bootstrap and entrypoint."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from .commands import PROJECT_ROOT, PYTEST_EXE, PYTHON_EXE
from .main_window import StartGUI
from .process_runner import ProcessRunner


def main() -> None:
    """Launch the Tkinter Desktop GUI Dashboard."""
    root = tk.Tk()
    app = StartGUI(root)

    def on_closing():
        if app.runner.is_running:
            if not messagebox.askyesno(
                "确认退出", "当前仍有正在运行的任务，确认强制终止并退出吗？"
            ):
                return
            app.runner.terminate()
        if app.web_process:
            try:
                app.web_process.terminate()
            except Exception:
                pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


__all__ = [
    "PROJECT_ROOT",
    "PYTEST_EXE",
    "PYTHON_EXE",
    "ProcessRunner",
    "StartGUI",
    "main",
]


if __name__ == "__main__":
    main()
