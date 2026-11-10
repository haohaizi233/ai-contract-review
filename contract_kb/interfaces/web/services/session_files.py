# -*- coding: utf-8 -*-
"""Web 工作台多会话物理存储与会话元数据服务（Session Files Service）。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from contract_kb import config

SESSION_CACHE_DIR = config.DATA_DIR / "web_sessions"
SESSION_CACHE_DIR.mkdir(parents=True, exist_ok=True)
CURRENT_SESSION_FILE = SESSION_CACHE_DIR / "current_active_review.docx"

CURRENT_DOC_NAME: str = "《国内专利服务合同（年费缴纳）》送审修改稿"
CURRENT_FORCED_TEMPLATE_ID: str | None = None


def get_session_dir(session_id: str) -> Path:
    """获取或创建指定会话的物理存储隔离目录。"""
    clean_id = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id) or "default_session"
    s_dir = SESSION_CACHE_DIR / clean_id
    s_dir.mkdir(parents=True, exist_ok=True)
    return s_dir


def get_session_file(session_id: str, ext: str | None = None) -> Path:
    """获取会话当前的待审文档路径（支持 docx/doc/pdf 多格式）。"""
    s_dir = get_session_dir(session_id)
    if ext:
        clean_ext = ext if ext.startswith(".") else f".{ext}"
        return s_dir / f"contract{clean_ext}"
    for candidate in [".docx", ".doc", ".pdf"]:
        p = s_dir / f"contract{candidate}"
        if p.exists():
            return p
    return s_dir / "contract.docx"


def prepare_session_file(session_id: str, ext: str = ".docx") -> Path:
    """清理并准备全新待审文档物理文件路径，防止格式扩展名错配。"""
    s_dir = get_session_dir(session_id)
    for old in s_dir.glob("contract.*"):
        try:
            old.unlink(missing_ok=True)
        except Exception:
            pass
    clean_ext = ext if ext.startswith(".") else f".{ext}"
    return s_dir / f"contract{clean_ext}"


def get_session_meta(session_id: str) -> dict[str, Any]:
    meta_file = get_session_dir(session_id) / "meta.json"
    if meta_file.exists():
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "doc_name": CURRENT_DOC_NAME,
        "forced_template_id": None,
    }


def save_session_meta(session_id: str, meta: dict[str, Any]) -> None:
    meta_file = get_session_dir(session_id) / "meta.json"
    meta_file.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
