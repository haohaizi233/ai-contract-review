# -*- coding: utf-8 -*-
"""Web 工作台多会话物理存储与会话元数据服务（Session Files Service）。"""

from __future__ import annotations

import hashlib
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


def _safe_slug(s: str, default: str = "default") -> str:
    """清理标识符并计算哈希尾缀，避免字符折叠碰撞与路径穿越。"""
    if not s:
        return default
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", s).strip("_")
    if not cleaned:
        cleaned = default
    if re.search(r"[^a-zA-Z0-9_-]", s) or cleaned != s:
        h = hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]
        return f"{cleaned}_{h}"
    return cleaned


def get_session_dir(session_id: str, tenant_id: str = "default") -> Path:
    """获取或创建指定租户与会话的物理存储隔离目录，严防路径穿越与碰撞合流。"""
    clean_tenant = _safe_slug(tenant_id or "default", default="default")
    clean_id = _safe_slug(session_id or "default_session", default="default_session")
    s_dir = (SESSION_CACHE_DIR / clean_tenant / clean_id).resolve()
    if not s_dir.is_relative_to(SESSION_CACHE_DIR.resolve()):
        s_dir = SESSION_CACHE_DIR / clean_tenant / "fallback_session"
    s_dir.mkdir(parents=True, exist_ok=True)
    return s_dir


def get_session_file(
    session_id: str, ext: str | None = None, tenant_id: str = "default"
) -> Path:
    """获取指定租户与会话当前的待审文档路径（支持 docx/doc/pdf 多格式）。"""
    s_dir = get_session_dir(session_id, tenant_id=tenant_id)
    if ext:
        clean_ext = ext if ext.startswith(".") else f".{ext}"
        return s_dir / f"contract{clean_ext}"
    for candidate in [".docx", ".doc", ".pdf"]:
        p = s_dir / f"contract{candidate}"
        if p.exists():
            return p
    return s_dir / "contract.docx"


def prepare_session_file(
    session_id: str, ext: str = ".docx", tenant_id: str = "default"
) -> Path:
    """清理并准备全新待审文档物理文件路径，防止格式扩展名错配。"""
    s_dir = get_session_dir(session_id, tenant_id=tenant_id)
    for old in s_dir.glob("contract.*"):
        try:
            old.unlink(missing_ok=True)
        except Exception:
            pass
    clean_ext = ext if ext.startswith(".") else f".{ext}"
    return s_dir / f"contract{clean_ext}"


def get_session_meta(session_id: str, tenant_id: str = "default") -> dict[str, Any]:
    meta_file = get_session_dir(session_id, tenant_id=tenant_id) / "meta.json"
    if meta_file.exists():
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {
        "session_id": session_id,
        "tenant_id": tenant_id,
        "doc_name": CURRENT_DOC_NAME,
        "forced_template_id": None,
    }


def save_session_meta(
    session_id: str, meta: dict[str, Any], tenant_id: str = "default"
) -> None:
    meta_file = get_session_dir(session_id, tenant_id=tenant_id) / "meta.json"
    meta_dict = dict(meta)
    meta_dict.setdefault("session_id", session_id)
    meta_dict.setdefault("tenant_id", tenant_id)
    meta_file.write_text(
        json.dumps(meta_dict, ensure_ascii=False, indent=2), encoding="utf-8"
    )
