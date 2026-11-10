# -*- coding: utf-8 -*-
"""Reusable Tkinter card and link widgets for the Desktop GUI."""

from __future__ import annotations

import os
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
import webbrowser


def create_action_card(
    parent, text: str, desc: str, cmd, primary: bool = False
) -> ttk.Frame:
    """Create a labeled button card with subtitle description."""
    card = ttk.Frame(parent)
    card.pack(fill=tk.X, pady=4)

    style = "Primary.TButton" if primary else "Action.TButton"
    btn = ttk.Button(card, text=text, command=cmd, style=style)
    btn.pack(anchor=tk.W, fill=tk.X)

    lbl = ttk.Label(card, text=desc, style="Subtitle.TLabel", wraplength=480)
    lbl.pack(anchor=tk.W, padx=4, pady=(2, 4))
    return card


def create_quick_link(parent, r: int, c: int, text: str, path: Path) -> ttk.Button:
    """Create a grid button that opens a report file in the default browser."""

    def _open():
        if path.exists():
            webbrowser.open(str(path))
        else:
            messagebox.showinfo(
                "提示", f"该报告尚未生成，请先执行对应生成命令：\n{path.name}"
            )

    btn = ttk.Button(parent, text=text, command=_open)
    btn.grid(row=r, column=c, padx=6, pady=6, sticky="ew")
    parent.grid_columnconfigure(c, weight=1)
    return btn


def create_dir_link(parent, r: int, c: int, text: str, path: Path) -> ttk.Button:
    """Create a grid button that opens a directory in the OS file explorer."""

    def _open():
        if path.exists():
            if hasattr(os, "startfile"):
                os.startfile(str(path))
            else:
                webbrowser.open(path.as_uri())
        else:
            messagebox.showinfo("提示", f"目录尚不存在：\n{path}")

    btn = ttk.Button(parent, text=text, command=_open)
    btn.grid(row=r, column=c, padx=6, pady=6, sticky="ew")
    parent.grid_columnconfigure(c, weight=1)
    return btn
