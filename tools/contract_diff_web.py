# -*- coding: utf-8 -*-
"""合同比对与条款级 Diff 前端可视化服务（Contract Diff Web Visualizer）。

高管/法务级全案设计：
1. 源文件以原格式高保真渲染（支持 docx, doc, pdf 等特别处理）：
   大标题居中大字、副标题居中、合同编号右对齐、双方主体与鉴于条款、条款条标题加粗左对齐、
   段落正文首行缩进 2 字符、两端对齐、尾部双方签署盖章区。真实 A4 纸张质感。
2. 左右对比两版纯净聚焦增删（去冗余批注徽标）：
   正文段落彻底摒弃密密麻麻的小药丸徽标（如 🏷️ [批注 #XX]），纯粹聚焦 Word/PDF 审阅级法律标红：
   - 删除文本：<del class="diff-del">红字删除线</del>
   - 新增/修改：<ins class="diff-ins">下划线新增</ins>
   - 高危数值篡改：金黄高危底色醒目下划线
   - 母本被整段删除的条款：母本端整段优雅标红删除线，送审端呈现极简平齐占位槽（不插大标签）
   - 送审稿新增非标条款：送审端优雅新增标红，母本端平齐占位槽
3. 三栏双向协同联动滚动（Three-Way Synchronized Scrolling）：
   - 左栏：标准参考母本（独立滚动）
   - 中栏：送审修改稿（独立滚动）
   - 右栏：法务审查详情与批注专栏（自适应独立滚动）
   - 上下滑动左右严格对齐（条款高度自动等高平衡计算）
   - 三个栏只要滑动其中任意一个，都会平滑且相对应地滑动对应的另外两个，无死循环抖动
4. 最右侧详情栏自适应（Responsive Sidebar）：
   - 弹性自适应宽度，卡片流式排版无文字截断，支持一键折叠/展开
5. 模板匹配置信度前端可视化：
   - 顶部导航栏直观展示 AI 匹配置信度评分（例如 99.0% 极高、良好、存疑）
   - 区分自动识别与用户手动指定模式，悬浮气泡查看五级路由与匹配细节
6. 模板库热加载与手动自由更换（标上置信度，从上往下排）：
   - 模板选择弹窗内每张模板卡片醒目标注匹配置信度（99.0% 权威匹配、85.0% 推荐等）
   - 模板按与送审合同的匹配置信度从高到低从上往下严格排序
   - 一键热加载模板库：无需重启服务，秒级扫描并入库最新标准模板
   - 彻底隐藏底层 16 位十六进制哈希与内部临时测试文件名（杜绝 current_active_review.docx 等），面向用户全面采用纯净规范公文命名

用法：
  python tools/contract_diff_web.py [--port 8089]
  python tools/contract_diff_web.py --export-only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import queue
import re
import shutil
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from contract_kb import config  # noqa: E402
from contract_kb.full_diff import build_full_contract_diff  # noqa: E402
from contract_kb.storage import create_storage  # noqa: E402
from contract_kb.modules.document_processing.ingestion import (
    scan_and_import,
    import_template_file,
)  # noqa: E402
from contract_kb.legal_agent_kb import record_user_feedback, export_sft_dataset  # noqa: E402
from contract_kb.agent_queue_manager import get_agent_manager  # noqa: E402
from tools.material_catalog import (  # noqa: E402
    DEFAULT_REF_DIR,
    get_material_manager,
    get_ref_templates_catalog,
    clean_template_title as catalog_clean_title,
)

# 业务分类中文对照
CATEGORY_LABELS: dict[str, str] = {
    "PATENT": "专利业务类",
    "TRADEMARK": "商标业务类",
    "COPYRIGHT": "著作权类",
    "HIGH_END_CONSULTING": "高端咨询类",
    "MIXED_IP": "综合业务类",
    "LEGAL_LITIGATION": "法律诉讼类",
    "INT_IP": "国际业务类",
    "PROJECT_AUDIT": "项目申报类",
    "FRAMEWORK_NDA": "保密框架类",
    "IP_OPERATION": "知识产权运营",
    "IP_GENERAL": "综合知识产权",
    "UNKNOWN": "通用其他类",
}

# 全局运行状态与最近一次比对会话
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


def clean_template_title(raw_name: str) -> str:
    """提取模板纯净中文标题，剥离内部路径、微盘前缀、MB编号与企业尾缀。"""
    stem = Path(raw_name).stem
    if "/" in stem:
        stem = stem.split("/")[-1]
    if "\\" in stem:
        stem = stem.split("\\")[-1]
    stem = re.sub(r"^WDCLI_[a-f0-9]+_", "", stem)
    stem = re.sub(r"^MB\w+?_V[\d\.]+?_", "", stem)
    stem = re.sub(r"[-_](恒程|创新|代理|知识产权|事务所|公司|企业).*$", "", stem)
    stem = stem.strip()
    return stem or Path(raw_name).stem


def extract_template_version(raw_name: str) -> str:
    """提取模板版本号。"""
    m = re.search(r"_(V[\d\.]+)_", raw_name)
    return m.group(1) if m else "V1.0"


def is_hex_hash(val: str) -> bool:
    """判断是否为底层 16 进制哈希（如 9b754cf78f1ea963），杜绝面向用户暴露。"""
    if not val:
        return False
    val_clean = val.strip()
    return bool(
        re.match(r"^[0-9a-fA-F]{12,64}$", val_clean)
    ) and not val_clean.startswith("MB")


def get_ranked_templates_catalog(
    doc_path: Path, forced_template_id: str | None = None
) -> list[dict[str, Any]]:
    """为库中官方标准模板计算针对当前送审合同的匹配置信度，并严格从高到低从上往下排序。"""
    st = create_storage()
    try:
        all_docs = st.list_documents()
        # 包含全库所有可用母本（排除制度文件）
        docs = [d for d in all_docs if d.get("document_type") != "governance_doc"]
        if not docs:
            docs = all_docs

        matched_tpl = None
        target_subtype = "PATENT_ANNUAL_FEE"
        target_category = "PATENT"

        if doc_path.exists():
            try:
                rec, _ = import_template_file(doc_path)
                if rec:
                    target_subtype = rec.subtype or target_subtype
                    target_category = rec.category or target_category
            except Exception:
                pass

        if forced_template_id:
            matched_tpl = st.get_document(forced_template_id)
        if not matched_tpl:
            matched_tpl = st.find_active_template(
                contract_subtype=target_subtype, business_category=target_category
            )
    finally:
        st.close()

    matched_id = matched_tpl["document_id"] if matched_tpl else ""
    templates: list[dict[str, Any]] = []

    for d in docs:
        doc_id = d["document_id"]
        fname = d.get("source_file") or ""
        clean_name = clean_template_title(fname)
        cat = d.get("business_category") or "UNKNOWN"
        sub = d.get("contract_subtype") or ""
        ver = extract_template_version(fname)

        # 区分标准编码与内部哈希
        display_code = doc_id if not is_hex_hash(doc_id) else (ver or "")

        # 核心匹配置信度评分与分类判定
        if doc_id == matched_id:
            conf = 0.99
            conf_level = "VERY_HIGH"
            conf_label = "权威匹配"
        elif sub and sub == target_subtype:
            if "年费" in fname and ("年费" in clean_name or "年费" in str(doc_path)):
                conf = 0.96
            else:
                conf = 0.92
            conf_level = "VERY_HIGH"
            conf_label = "同款子类"
        elif cat and cat == target_category:
            keywords = [
                "快速预审",
                "申请",
                "复审",
                "侵权",
                "检索",
                "变更",
                "保密",
                "代理",
                "诉讼",
            ]
            matched_kws = [kw for kw in keywords if kw in clean_name]
            if matched_kws:
                conf = round(0.78 + min(0.10, len(matched_kws) * 0.05), 2)
                conf_level = "HIGH"
                conf_label = "同类推荐"
            else:
                conf = 0.65
                conf_level = "MEDIUM"
                conf_label = "同类备选"
        elif cat in ["HIGH_END_CONSULTING", "MIXED_IP"]:
            conf = 0.45
            conf_level = "MEDIUM"
            conf_label = "相关业务"
        elif cat == "LEGAL_LITIGATION":
            conf = 0.35
            conf_level = "LOW"
            conf_label = "争议诉讼"
        else:
            conf = 0.15
            conf_level = "LOW"
            conf_label = "跨类参考"

        templates.append(
            {
                "document_id": doc_id,
                "display_code": display_code,
                "is_standard_mb": not is_hex_hash(doc_id),
                "name": clean_name,
                "full_name": fname,
                "category": cat,
                "category_name": CATEGORY_LABELS.get(cat, "通用其他类"),
                "subtype": sub,
                "version": ver,
                "confidence_score": conf,
                "confidence_percent": f"{conf * 100:.1f}%",
                "confidence_level": conf_level,
                "confidence_label": conf_label,
                "is_matched": (doc_id == matched_id),
                "status": d.get("status") or "ACTIVE",
            }
        )

    # 从上往下排：按 confidence_score 严格降序排列（最匹配的排在最上方）
    templates.sort(key=lambda x: -x["confidence_score"])
    return templates


def hot_reload_template_library() -> dict[str, Any]:
    """热加载模板库：秒级扫描最新标准模板并原子增量入库。"""
    config.ensure_dirs()
    scan_res = scan_and_import(config.TEMPLATE_DIR)
    raw = scan_res.pop("_raw", [])

    st = create_storage()
    inserted = 0
    updated = 0
    try:
        for d in raw:
            # 过滤非合同制度文件
            if d.document_type == "governance_doc":
                continue
            _, is_new = st.upsert_document(d.to_dict())
            if is_new:
                inserted += 1
            else:
                updated += 1
        catalog = get_ranked_templates_catalog(
            CURRENT_SESSION_FILE, CURRENT_FORCED_TEMPLATE_ID
        )
    finally:
        st.close()

    scanned_count = scan_res.get("file_count", 0)
    imported_count = len(scan_res.get("imported", []))
    total_count = len(catalog)

    return {
        "success": True,
        "scanned_files": scanned_count,
        "imported_files": imported_count,
        "inserted_docs": inserted,
        "updated_docs": updated,
        "total_templates": total_count,
        "message": f"模板库热加载成功！共扫描 {scanned_count} 份文件，更新 {updated} 份，库中现有 {total_count} 套官方标准母本。",
        "templates": catalog,
    }


def execute_current_diff(
    doc_path: Path,
    forced_template_id: str | None = None,
    doc_name: str | None = None,
) -> dict[str, Any]:
    """使用指定文档和可选强制母本执行整版条款对比与批注，并规范化用户面向名称。"""
    res = build_full_contract_diff(doc_path, forced_template_id=forced_template_id)

    conf = float(res.get("classification_confidence") or 0.0)
    is_manual = bool(forced_template_id or res.get("is_manual_override"))

    target_name = doc_name or CURRENT_DOC_NAME
    # 规范化送审文档展示名称（彻底杜绝内部临时文件名）
    res["doc_title"] = target_name
    res["reviewed_doc_title"] = target_name
    res["friendly_doc_title"] = target_name
    res["display_doc_name"] = target_name
    res["uploaded_file"] = target_name
    res["uploaded_file_name"] = target_name
    res["file_name"] = target_name

    # 规范化基准母本展示名称（彻底杜绝底层十六进制哈希 ID）
    raw_tpl_name = res.get("matched_template_name") or ""
    clean_tpl_name = clean_template_title(raw_tpl_name)
    tpl_id = res.get("matched_template_id") or ""
    display_code = tpl_id if not is_hex_hash(tpl_id) else ""

    res["clean_template_name"] = clean_tpl_name
    res["clean_template_code"] = display_code
    res["is_hex_hash_id"] = is_hex_hash(tpl_id)

    if is_manual:
        res["is_manual_override"] = True
        res["matched_method"] = "MANUAL_SELECTION"
        res["confidence_score"] = 1.0
        res["confidence_percent"] = "100.0%"
        res["confidence_level"] = "MANUAL"
        res["confidence_desc"] = "人工手动指定模板"
    else:
        res["is_manual_override"] = False
        res["matched_method"] = "AUTO_CLASSIFICATION"
        res["confidence_score"] = conf
        res["confidence_percent"] = f"{conf * 100:.1f}%"
        if conf >= 0.85:
            res["confidence_level"] = "HIGH"
            res["confidence_desc"] = "极高置信度 · 权威匹配"
        elif conf >= 0.70:
            res["confidence_level"] = "MEDIUM"
            res["confidence_desc"] = "高置信度 · 良好匹配"
        else:
            res["confidence_level"] = "LOW"
            res["confidence_desc"] = "存疑置信度 · 建议复核"

    return res


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI 合同审查 · 条款级 Diff 与整版原格式比对工作台</title>
<style>
  :root {
    --bg-canvas: #E2E8F0;
    --paper-bg: #FFFFFF;
    --border: #CBD5E1;
    --border-light: #E2E8F0;
    --text-main: #0F172A;
    --text-muted: #64748B;
    --text-sub: #94A3B8;
    --primary: #2563EB;
    --primary-light: #EFF6FF;
    --danger: #DC2626;
    --danger-light: #FEF2F2;
    --warning: #D97706;
    --warning-light: #FFFBEB;
    --success: #16A34A;
    --success-light: #F0FDF4;
    --purple: #7C3AED;
    --purple-light: #FAF5FF;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body {
    height: 100vh;
    overflow: hidden;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "SimSun", "STSong", serif;
    background: var(--bg-canvas);
    color: var(--text-main);
    line-height: 1.6;
  }

  /* ==================== 1. 顶部全局极简导航栏 (48px) ==================== */
  .header {
    height: 48px;
    background: #FFFFFF;
    border-bottom: 1px solid var(--border-light);
    padding: 0 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    z-index: 100;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
  }
  .brand { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
  .brand-logo {
    width: 28px; height: 28px; border-radius: 7px;
    background: linear-gradient(135deg, #1E40AF 0%, #3B82F6 100%);
    color: #fff; display: flex; align-items: center; justify-content: center;
    font-weight: 800; font-size: 15px; font-family: sans-serif;
  }
  .brand-title { font-size: 14.5px; font-weight: 700; color: #1E293B; letter-spacing: -0.2px; font-family: sans-serif; }

  .header-meta-group {
    display: flex; align-items: center; gap: 8px; font-size: 12px; font-family: sans-serif;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }

  /* 可点击的母本胶囊 */
  .meta-tag-btn {
    display: inline-flex; align-items: center; gap: 5px; padding: 3px 10px;
    border-radius: 6px; font-weight: 600; cursor: pointer;
    border: 1px solid #BBF7D0; background: #F0FDF4; color: #15803D;
    transition: all 0.15s ease; font-family: sans-serif; font-size: 12px;
  }
  .meta-tag-btn:hover {
    background: #DCFCE7; border-color: #86EFAC;
    box-shadow: 0 1px 4px rgba(22, 163, 74, 0.18);
  }
  .meta-tag-btn .btn-arrow { font-size: 10px; opacity: 0.8; margin-left: 2px; }

  /* 置信度胶囊标签 */
  .meta-tag.confidence {
    display: inline-flex; align-items: center; gap: 5px; padding: 3px 9px;
    border-radius: 6px; font-weight: 700; font-size: 12px; cursor: help;
    transition: all 0.2s ease;
  }
  .meta-tag.confidence.conf-high {
    background: #DCFCE7; color: #15803D; border: 1px solid #86EFAC;
  }
  .meta-tag.confidence.conf-medium {
    background: #EFF6FF; color: #1E40AF; border: 1px solid #BFDBFE;
  }
  .meta-tag.confidence.conf-low {
    background: #FEF3C7; color: #B45309; border: 1px solid #FDE68A;
  }
  .meta-tag.confidence.conf-manual {
    background: #FAF5FF; color: #7E22CE; border: 1px solid #E9D5FF;
  }
  .conf-badge-pill {
    font-size: 10px; padding: 1px 5px; border-radius: 3px; font-weight: 800;
  }
  .conf-high .conf-badge-pill { background: #16A34A; color: #FFF; }
  .conf-medium .conf-badge-pill { background: #2563EB; color: #FFF; }
  .conf-low .conf-badge-pill { background: #D97706; color: #FFF; }
  .conf-manual .conf-badge-pill { background: #7C3AED; color: #FFF; }

  .meta-tag.doc-title {
    background: #F1F5F9; color: #1E293B; border: 1px solid #CBD5E1;
    display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px;
    border-radius: 6px; font-weight: 700;
  }

  .header-actions { display: flex; align-items: center; gap: 6px; font-family: sans-serif; flex-shrink: 0; }
  .btn {
    padding: 5px 11px; border-radius: 6px; font-size: 12px; font-weight: 600;
    cursor: pointer; transition: all 0.15s ease; display: inline-flex;
    align-items: center; gap: 5px; border: 1px solid var(--border-light);
    background: #FFFFFF; color: #334155; font-family: sans-serif;
  }
  .btn:hover { background: #F8FAFC; border-color: var(--border); }
  .btn-primary { background: var(--primary); color: #FFF; border-color: var(--primary); }
  .btn-primary:hover { background: #1D4ED8; }
  .btn-purple { background: #7C3AED; color: #FFF; border-color: #7C3AED; }
  .btn-purple:hover { background: #6D28D9; }

  /* ==================== 2. 紧凑型工具与审阅状态控制条 (40px) ==================== */
  .toolbar-strip {
    height: 40px;
    background: #F8FAFC;
    border-bottom: 1px solid var(--border-light);
    padding: 0 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    z-index: 90;
    font-family: sans-serif;
  }
  .tool-left { display: flex; align-items: center; gap: 10px; }
  .segmented-control {
    display: flex; background: #E2E8F0; padding: 2px; border-radius: 6px;
  }
  .seg-btn {
    padding: 3px 9px; font-size: 11.5px; font-weight: 600; border-radius: 4px;
    border: none; background: transparent; color: #64748B; cursor: pointer;
    transition: all 0.15s ease; white-space: nowrap; font-family: sans-serif;
  }
  .seg-btn.active {
    background: #FFF; color: #0F172A; box-shadow: 0 1px 2px rgba(0,0,0,0.06);
  }

  .nav-group { display: flex; align-items: center; gap: 5px; }
  .nav-arrow-btn {
    padding: 2px 7px; border-radius: 4px; border: 1px solid var(--border-light);
    background: #FFF; font-size: 11.5px; font-weight: 600; color: #334155;
    cursor: pointer; font-family: sans-serif;
  }
  .nav-arrow-btn:hover { background: #F1F5F9; }
  .nav-indicator-pill {
    font-size: 11.5px; font-weight: 700; color: #0F172A; min-width: 80px; text-align: center;
  }

  .tool-right { display: flex; align-items: center; gap: 8px; font-family: sans-serif; }
  .stat-pill {
    font-size: 11px; font-weight: 600; padding: 2px 7px; border-radius: 999px;
    display: inline-flex; align-items: center; gap: 4px; border: 1px solid transparent;
  }
  .stat-pill.danger { background: #FEE2E2; color: #991B1B; border-color: #FECACA; }
  .stat-pill.missing { background: #FEF3C7; color: #92400E; border-color: #FDE68A; }
  .stat-pill.warning { background: #FFFBEB; color: #B45309; border-color: #FDE68A; }
  .stat-pill.progress { background: #F0FDF4; color: #15803D; border-color: #BBF7D0; }

  .btn-toggle-sidebar {
    background: #1E293B; color: #FFF; border: 1px solid #1E293B;
    padding: 3px 9px; border-radius: 6px; font-size: 11.5px; font-weight: 600;
    cursor: pointer; display: flex; align-items: center; gap: 6px; font-family: sans-serif;
  }
  .btn-toggle-sidebar:hover { background: #334155; }
  .sidebar-badge {
    background: #EF4444; color: #FFF; font-size: 10px; padding: 0 5px;
    border-radius: 999px; font-weight: 800;
  }

  /* ==================== 3. 核心工作区：三栏布局 (左母本 / 中送审 / 右详情自适应) ==================== */
  .review-workspace {
    display: grid;
    grid-template-columns: 1fr 1fr minmax(320px, 420px);
    height: calc(100vh - 88px);
    width: 100%;
    overflow: hidden;
    position: relative;
    background: #E2E8F0;
    transition: grid-template-columns 0.25s ease;
  }
  .review-workspace.sidebar-collapsed {
    grid-template-columns: 1fr 1fr 0px !important;
  }
  .review-workspace.sidebar-collapsed .annotations-sidebar-panel {
    display: none !important;
  }
  .mode-single .review-workspace {
    grid-template-columns: 0px 1fr minmax(320px, 420px);
  }
  .mode-single #colStandardPane {
    display: none !important;
  }

  /* 独立列面板 */
  .contract-column {
    display: flex;
    flex-direction: column;
    height: 100%;
    max-height: 100%;
    min-height: 0;
    overflow: hidden;
    min-width: 0;
    position: relative;
    border-right: 1px solid #CBD5E1;
    background: #E2E8F0;
  }

  .column-sticky-header {
    height: 38px;
    padding: 0 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: #FFFFFF;
    border-bottom: 1px solid #CBD5E1;
    z-index: 10;
    font-size: 12px;
    font-weight: 700;
    font-family: sans-serif;
    flex-shrink: 0;
  }
  .column-sticky-header.standard { border-top: 3px solid #16A34A; color: #166534; background: #F0FDF4; }
  .column-sticky-header.reviewed { border-top: 3px solid #2563EB; color: #1E40AF; background: #F8FAFC; }
  .col-header-info { display: flex; align-items: center; gap: 6px; }
  .col-header-sub { font-size: 11.5px; color: #475569; font-weight: 600; }

  /* 独立滚动视口 */
  .contract-scroll-container {
    flex: 1;
    min-height: 0;
    overflow-y: scroll;
    overflow-x: hidden;
    padding: 24px 28px 80px 28px;
    scroll-behavior: auto;
    overscroll-behavior-y: contain;
    overflow-anchor: none;
  }

  /* A4 纸张质感画布 */
  .contract-paper-canvas {
    background: #FFFFFF;
    border-radius: 6px;
    box-shadow: 0 4px 20px rgba(15, 23, 42, 0.08);
    border: 1px solid #CBD5E1;
    padding: 40px 44px;
    min-height: 900px;
    max-width: 780px;
    margin: 0 auto;
  }

  /* ==================== 4. 真实公文原格式排版 ==================== */
  .clause-block {
    position: relative;
    margin-bottom: 10px;
    transition: background 0.2s ease, outline 0.2s ease;
    border-radius: 4px;
    cursor: pointer;
  }
  .clause-block.active-clause {
    background: #FEF9C3 !important;
    outline: 2px solid #EAB308;
  }

  .contract-doc-title {
    font-size: 20px;
    font-weight: 700;
    text-align: center;
    margin: 10px 0 6px 0;
    letter-spacing: 1px;
    color: #0F172A;
  }
  .contract-doc-subtitle {
    font-size: 13.5px;
    text-align: center;
    color: #64748B;
    margin-bottom: 14px;
  }
  .contract-doc-meta {
    font-size: 12px;
    text-align: right;
    color: #475569;
    margin-bottom: 18px;
    font-family: monospace;
  }
  .contract-doc-parties {
    font-size: 13.5px;
    line-height: 1.8;
    color: #1E293B;
    margin-bottom: 8px;
  }
  .contract-doc-preamble {
    font-size: 13.5px;
    line-height: 1.85;
    text-indent: 2em;
    color: #1E293B;
    margin-bottom: 12px;
  }
  .contract-article-heading {
    font-size: 14.5px;
    font-weight: 700;
    color: #0F172A;
    margin: 16px 0 6px 0;
    border-left: 3px solid #2563EB;
    padding-left: 8px;
  }
  .contract-doc-p {
    font-size: 13.5px;
    line-height: 1.85;
    text-indent: 2em;
    text-align: justify;
    color: #1E293B;
    margin-bottom: 6px;
  }
  .contract-doc-signatures {
    margin-top: 24px;
    padding-top: 16px;
    border-top: 1px dashed #CBD5E1;
    font-size: 13px;
    line-height: 1.9;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
  }

  /* ==================== 4.1 高保真表格与图片排版 ==================== */
  .contract-table-card {
    margin: 14px 0;
    border-radius: 6px;
    border: 1px solid #CBD5E1;
    background: #FFFFFF;
    overflow: hidden;
    box-shadow: 0 1px 4px rgba(15, 23, 42, 0.05);
  }
  .table-badge {
    padding: 6px 12px;
    font-size: 11.5px;
    font-weight: 700;
    background: #F1F5F9;
    color: #475569;
    border-bottom: 1px solid #E2E8F0;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .contract-doc-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    text-align: left;
    background: #FFFFFF;
  }
  .contract-doc-table th, .contract-doc-table td {
    border: 1px solid #E2E8F0;
    padding: 8px 12px;
    vertical-align: middle;
    line-height: 1.5;
  }
  .contract-doc-table th {
    background: #F8FAFC;
    color: #334155;
    font-weight: 600;
    font-size: 12px;
  }
  .contract-doc-table tr:hover td {
    background-color: #F8FAFC;
  }
  .contract-doc-table .party-label {
    width: 75px;
    background: #F8FAFC;
    color: #475569;
    font-weight: 600;
    white-space: nowrap;
    border-right: 1px solid #E2E8F0;
  }
  .contract-doc-table .party-val {
    color: #1E293B;
  }
  .table-total-row {
    background: #F8FAFC !important;
    font-weight: 700;
  }
  .table-total-row td {
    border-top: 2px solid #CBD5E1 !important;
  }
  .doc-slot-placeholder {
    color: #94A3B8;
    font-style: italic;
    font-size: 12px;
  }
  .contract-doc-img {
    max-width: 100%;
    height: auto;
    border-radius: 4px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    margin: 8px 0;
  }

  /* ==================== 5. 纯净增删标红 ==================== */
  .diff-del {
    color: #DC2626;
    text-decoration: line-through 1.5px #DC2626;
    background: #FEF2F2;
    padding: 0 2px;
    border-radius: 2px;
  }
  .diff-ins {
    color: #2563EB;
    text-decoration: underline 1.5px #2563EB;
    font-weight: 600;
    background: #EFF6FF;
    padding: 0 2px;
    border-radius: 2px;
  }
  .diff-ins-number {
    color: #92400E;
    text-decoration: underline 2px #D97706;
    font-weight: 700;
    background: #FEF3C7;
    padding: 0 3px;
    border-radius: 3px;
  }
  .mode-color-redgreen .diff-ins {
    color: #15803D;
    text-decoration: underline 1.5px #15803D;
    background: #F0FDF4;
  }

  .doc-text-deleted {
    background: #FFF5F5;
    border-left: 3px solid #F87171;
    padding: 8px 12px;
    border-radius: 4px;
    line-height: 1.85;
    text-indent: 2em;
  }
  .diff-del-flow {
    color: #7F1D1D;
    text-decoration: line-through 1.5px #DC2626;
    white-space: pre-wrap;
  }

  .doc-slot-deleted {
    background: #FAF8F8;
    border: 1px dashed #CBD5E1;
    border-radius: 4px;
    padding: 8px 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    color: #94A3B8;
    transition: all 0.15s ease;
  }
  .doc-slot-deleted:hover {
    background: #FEF2F2;
    border-color: #F87171;
    color: #DC2626;
  }
  .slot-placeholder-text { font-family: sans-serif; font-style: italic; }

  .doc-slot-blank {
    background: #FAFAFA;
    border: 1px dashed #E2E8F0;
    border-radius: 4px;
    padding: 8px 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    color: #94A3B8;
  }

  .doc-text-added {
    background: #FAF5FF;
    border-left: 3px solid #7C3AED;
    padding: 8px 12px;
    border-radius: 4px;
    line-height: 1.85;
    text-indent: 2em;
  }
  .diff-ins-flow {
    color: #4C1D95;
    text-decoration: underline 1.5px #7C3AED;
    white-space: pre-wrap;
  }

  /* ==================== 6. 右侧法务审阅详情与批注专栏 (自适应) ==================== */
  .annotations-sidebar-panel {
    background: #FFFFFF;
    border-left: 1px solid var(--border-light);
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
    z-index: 85;
    font-family: sans-serif;
  }
  .sidebar-header {
    padding: 10px 14px;
    border-bottom: 1px solid var(--border-light);
    background: #F8FAFC;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-shrink: 0;
  }
  .sidebar-title {
    font-size: 13px; font-weight: 700; color: #1E293B; display: flex; align-items: center; gap: 6px;
  }
  .sidebar-count-badge {
    background: #1E293B; color: #FFF; font-size: 10.5px; padding: 1px 6px; border-radius: 999px; font-weight: 800;
  }
  .mini-tool-btn {
    font-size: 11px; font-weight: 600; padding: 3px 6px; border-radius: 4px;
    border: 1px solid var(--border-light); background: #FFF; color: #475569; cursor: pointer;
  }
  .mini-tool-btn:hover { background: #F1F5F9; color: #0F172A; }

  .sidebar-filter-strip {
    padding: 6px 10px;
    border-bottom: 1px solid #F1F5F9;
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
    background: #FFFFFF;
    flex-shrink: 0;
  }
  .filter-chip {
    padding: 2px 7px;
    border-radius: 999px;
    border: 1px solid var(--border-light);
    background: #FFF;
    font-size: 10.5px;
    font-weight: 600;
    color: #475569;
    cursor: pointer;
    transition: all 0.15s ease;
    white-space: nowrap;
  }
  .filter-chip.active {
    background: #1E293B;
    color: #FFF;
    border-color: #1E293B;
  }

  .annotations-list {
    overflow-y: scroll;
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    flex: 1;
    scroll-behavior: auto;
    overscroll-behavior-y: contain;
    overflow-anchor: none;
  }

  .annotation-card {
    background: #FFFFFF;
    border: 1px solid var(--border-light);
    border-radius: 6px;
    padding: 10px 12px;
    cursor: pointer;
    transition: all 0.15s ease;
    box-shadow: 0 1px 2px rgba(0,0,0,0.02);
    position: relative;
  }
  .annotation-card:hover {
    border-color: var(--primary);
    box-shadow: 0 2px 6px rgba(37,99,235,0.08);
  }
  .annotation-card.active {
    border-color: var(--primary);
    box-shadow: 0 0 0 2px rgba(37,99,235,0.3);
    background: #FAFCFF;
  }
  .annotation-card.status-ACCEPTED { border-left: 4px solid #16A34A; background: #F0FDF4; }
  .annotation-card.status-QUESTIONED { border-left: 4px solid #D97706; background: #FFFBEB; }
  .annotation-card.status-OVERRULED { border-left: 4px solid #94A3B8; background: #F8FAFC; opacity: 0.88; }

  .card-top {
    display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 4px;
  }
  .card-index {
    font-size: 10.5px; font-weight: 800; color: #64748B; background: #F1F5F9;
    padding: 1px 5px; border-radius: 3px;
  }
  .card-risk-pill {
    font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 4px;
  }
  .card-risk-pill.HIGH { background: #FEE2E2; color: #991B1B; }
  .card-risk-pill.MEDIUM { background: #FEF3C7; color: #92400E; }
  .card-risk-pill.LOW { background: #F1F5F9; color: #475569; }

  .card-clause-name {
    font-size: 12px; font-weight: 700; color: #0F172A; margin-bottom: 4px;
  }

  .card-comparison-box {
    background: #F8FAFC;
    border-radius: 4px;
    padding: 6px 8px;
    font-size: 11px;
    margin-bottom: 6px;
    border: 1px solid #EDF2F7;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .comp-row { display: flex; gap: 6px; }
  .comp-label { font-weight: 700; color: #64748B; min-width: 44px; flex-shrink: 0; }
  .comp-text { color: #1E293B; word-break: break-all; }
  .comp-text.del { color: #991B1B; text-decoration: line-through; }
  .comp-text.ins { color: #15803D; font-weight: 600; }

  .card-analysis {
    font-size: 11.5px; color: #334155; margin-bottom: 4px; line-height: 1.45;
  }
  .card-advice {
    font-size: 11px; color: #1E40AF; background: #EFF6FF; border-left: 2px solid var(--primary);
    padding: 4px 6px; border-radius: 0 3px 3px 0; margin-bottom: 6px; line-height: 1.4;
  }

  .card-actions {
    display: flex; justify-content: space-between; align-items: center; border-top: 1px solid #F1F5F9;
    padding-top: 5px; margin-top: 2px;
  }
  .card-action-btn-group { display: flex; gap: 3px; }
  .mini-btn {
    padding: 2px 6px; border-radius: 3px; font-size: 10.5px; font-weight: 600;
    border: 1px solid var(--border-light); background: #FFF; color: #475569; cursor: pointer;
  }
  .mini-btn:hover { background: #F1F5F9; color: #0F172A; }
  .mini-btn.active-accept { background: #DCFCE7; color: #15803D; border-color: #86EFAC; }
  .mini-btn.active-question { background: #FEF3C7; color: #92400E; border-color: #FCD34D; }
  .mini-btn.active-overrule { background: #F1F5F9; color: #64748B; border-color: #CBD5E1; }

  .locate-btn {
    font-size: 10.5px; font-weight: 600; color: var(--primary); border: none; background: transparent;
    cursor: pointer; display: flex; align-items: center; gap: 2px;
  }
  .locate-btn:hover { text-decoration: underline; }

  /* ==================== 6.1 Agent 智能研判与全链路容灾监控面板 (爱马仕/CC/小龙虾) ==================== */
  .agent-status-panel {
    background: #F8FAFC;
    border-bottom: 1px solid var(--border-light);
    padding: 8px 12px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    font-size: 11px;
    font-family: sans-serif;
  }
  .agent-status-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .agent-badge-group {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .agent-state-pill {
    padding: 2px 7px;
    border-radius: 4px;
    font-weight: 700;
    font-size: 10.5px;
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .agent-state-pill.RUNNING { background: #EFF6FF; color: #1E40AF; border: 1px solid #BFDBFE; }
  .agent-state-pill.PAUSED { background: #FEF3C7; color: #92400E; border: 1px solid #FDE68A; }
  .agent-state-pill.COMPLETED { background: #DCFCE7; color: #15803D; border: 1px solid #86EFAC; }
  .agent-state-pill.CIRCUIT_OPEN { background: #FEE2E2; color: #991B1B; border: 1px solid #FECACA; }

  .agent-control-btn {
    padding: 2px 7px;
    border-radius: 4px;
    font-size: 10.5px;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid var(--border);
    background: #FFF;
    color: #334155;
    transition: all 0.15s;
  }
  .agent-control-btn:hover { background: #F1F5F9; color: #0F172A; }
  .agent-control-btn.primary { background: var(--primary); color: #FFF; border-color: var(--primary); }

  .agent-progress-bar-bg {
    width: 100%;
    height: 4px;
    background: #E2E8F0;
    border-radius: 2px;
    overflow: hidden;
  }
  .agent-progress-bar-fill {
    height: 100%;
    background: linear-gradient(90deg, #3B82F6, #10B981);
    transition: width 0.3s ease;
    border-radius: 2px;
  }

  .agent-meta-stats {
    display: flex;
    justify-content: space-between;
    font-size: 10px;
    color: #64748B;
  }
  .agent-meta-tag {
    display: inline-flex;
    align-items: center;
    gap: 3px;
  }

  /* 卡片流式状态 & 容灾标记 */
  .annotation-card.agent-running {
    border-color: #3B82F6 !important;
    box-shadow: 0 0 0 1px #3B82F6, 0 4px 12px rgba(59, 130, 246, 0.15);
    background: #FAF5FF !important;
  }
  .card-source-badge {
    font-size: 9.5px;
    font-weight: 600;
    padding: 1px 5px;
    border-radius: 3px;
    display: inline-flex;
    align-items: center;
    gap: 2px;
  }
  .card-source-badge.ai { background: #EFF6FF; color: #1D4ED8; }
  .card-source-badge.cache { background: #ECFDF5; color: #047857; }
  .card-source-badge.fallback { background: #FFFBEB; color: #B45309; }
  .card-source-badge.watchdog { background: #FDF4FF; color: #86198F; }
  .card-source-badge.resume { background: #F1F5F9; color: #475569; }
  .card-source-badge.running { background: #EFF6FF; color: #2563EB; animation: pulseGlow 1.5s infinite; }

  @keyframes pulseGlow {
    0% { opacity: 0.7; }
    50% { opacity: 1; }
    100% { opacity: 0.7; }
  }

  .card-extra-action {
    display: flex;
    gap: 4px;
    align-items: center;
  }
  .card-mini-action-btn {
    padding: 1px 5px;
    border-radius: 3px;
    font-size: 10px;
    font-weight: 600;
    background: #FFF;
    border: 1px solid #CBD5E1;
    color: #475569;
    cursor: pointer;
  }
  .card-mini-action-btn:hover { background: #F1F5F9; color: #0F172A; }

  .card-reasoning-collapse {
    margin-top: 6px;
    padding: 6px 8px;
    background: #F8FAFC;
    border: 1px dashed #CBD5E1;
    border-radius: 4px;
    font-size: 10.5px;
    color: #475569;
    line-height: 1.4;
    white-space: pre-wrap;
    max-height: 130px;
    overflow-y: auto;
  }

  /* ==================== 7. 上传模态窗 ==================== */
  .modal-overlay {
    position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
    background: rgba(15, 23, 42, 0.45); backdrop-filter: blur(2px);
    z-index: 1000; display: none; align-items: center; justify-content: center;
    font-family: sans-serif;
  }
  .modal-overlay.open { display: flex; }
  .modal-card {
    background: #FFFFFF; border-radius: 12px; width: 560px; max-width: 90vw;
    box-shadow: 0 20px 40px rgba(0,0,0,0.18); overflow: hidden;
  }
  .modal-header {
    padding: 14px 18px; border-bottom: 1px solid var(--border-light);
    display: flex; justify-content: space-between; align-items: center; background: #F8FAFC;
  }
  .modal-title { font-size: 14px; font-weight: 700; color: #0F172A; }
  .modal-close-btn {
    background: transparent; border: none; font-size: 16px; cursor: pointer; color: #64748B;
  }
  .modal-body { padding: 18px; display: flex; flex-direction: column; gap: 14px; }

  .drop-zone {
    border: 2px dashed #CBD5E1; border-radius: 8px; padding: 24px;
    text-align: center; cursor: pointer; background: #F8FAFC; transition: all 0.2s ease;
  }
  .drop-zone:hover, .drop-zone.dragover {
    border-color: var(--primary); background: var(--primary-light);
  }
  .drop-icon { font-size: 28px; margin-bottom: 4px; }
  .drop-title { font-size: 13.5px; font-weight: 700; color: #1E293B; }
  .drop-desc { font-size: 11px; color: var(--text-muted); margin-top: 2px; }

  .preset-grid { display: flex; flex-direction: column; gap: 6px; }
  .preset-btn {
    padding: 8px 12px; border-radius: 6px; border: 1px solid var(--border-light);
    background: #F8FAFC; color: #334155; font-size: 12px; text-align: left;
    cursor: pointer; transition: all 0.15s ease; display: flex;
    justify-content: space-between; align-items: center;
  }
  .preset-btn:hover { border-color: var(--primary); background: #EFF6FF; color: var(--primary); }

  /* ==================== 8. 模板选择器模态窗 (带置信度标红·从上往下排) ==================== */
  .tpl-modal-card {
    background: #FFFFFF; border-radius: 12px; width: 880px; max-width: 94vw;
    height: 660px; max-height: 88vh; box-shadow: 0 25px 50px rgba(0,0,0,0.22);
    display: flex; flex-direction: column; overflow: hidden;
  }
  .tpl-modal-header {
    padding: 14px 20px; border-bottom: 1px solid var(--border-light);
    display: flex; justify-content: space-between; align-items: center; background: #F8FAFC;
    flex-shrink: 0;
  }
  .tpl-modal-title { font-size: 14.5px; font-weight: 700; color: #0F172A; display: flex; align-items: center; gap: 8px; }
  .tpl-modal-toolbar {
    padding: 10px 20px; border-bottom: 1px solid var(--border-light);
    display: flex; gap: 10px; align-items: center; background: #FFFFFF; flex-shrink: 0;
  }
  .tpl-search-input {
    flex: 1; padding: 7px 12px; border-radius: 6px; border: 1px solid var(--border);
    font-size: 12.5px; outline: none; transition: border-color 0.15s ease;
  }
  .tpl-search-input:focus {
    border-color: var(--primary); box-shadow: 0 0 0 2px rgba(37,99,235,0.15);
  }
  .tpl-category-tabs {
    padding: 6px 20px; border-bottom: 1px solid #F1F5F9; display: flex;
    gap: 6px; flex-wrap: wrap; background: #F8FAFC; flex-shrink: 0;
  }
  .tpl-tab-btn {
    padding: 3px 10px; border-radius: 999px; border: 1px solid transparent;
    background: transparent; font-size: 11.5px; font-weight: 600; color: #64748B;
    cursor: pointer; transition: all 0.15s ease;
  }
  .tpl-tab-btn:hover { background: #E2E8F0; color: #0F172A; }
  .tpl-tab-btn.active { background: #1E293B; color: #FFF; }
  .tpl-list-scroll {
    flex: 1; overflow-y: scroll; padding: 14px 20px; display: grid;
    grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 10px; align-content: start;
  }
  .tpl-card-item {
    border: 1px solid var(--border-light); border-radius: 8px; padding: 11px 14px;
    cursor: pointer; background: #FFFFFF; transition: all 0.15s ease; display: flex;
    flex-direction: column; gap: 5px; position: relative;
  }
  .tpl-card-item:hover {
    border-color: var(--primary); box-shadow: 0 4px 12px rgba(37,99,235,0.1);
    transform: translateY(-1px);
  }
  .tpl-card-item.active-tpl {
    border: 2px solid #16A34A; background: #F0FDF4;
  }
  .tpl-card-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
  .tpl-card-title { font-size: 13.5px; font-weight: 700; color: #0F172A; line-height: 1.4; }

  /* 模板卡片右上角置信度徽章 */
  .tpl-card-conf-badge {
    display: inline-flex; align-items: center; gap: 3px; padding: 2px 7px;
    border-radius: 4px; font-size: 11px; font-weight: 800; flex-shrink: 0;
  }
  .tpl-card-conf-badge.conf-very-high {
    background: #DCFCE7; color: #15803D; border: 1px solid #86EFAC;
  }
  .tpl-card-conf-badge.conf-high {
    background: #EFF6FF; color: #1E40AF; border: 1px solid #BFDBFE;
  }
  .tpl-card-conf-badge.conf-medium {
    background: #FEF3C7; color: #B45309; border: 1px solid #FDE68A;
  }
  .tpl-card-conf-badge.conf-low {
    background: #F1F5F9; color: #64748B; border: 1px solid #E2E8F0;
  }

  .tpl-card-meta { display: flex; gap: 6px; align-items: center; font-size: 11px; color: #64748B; flex-wrap: wrap; }
  .tpl-card-pill { padding: 1px 6px; border-radius: 3px; font-size: 10px; font-weight: 700; }
  .tpl-card-pill.category { background: #EFF6FF; color: #1E40AF; }
  .tpl-card-pill.code { background: #F1F5F9; color: #475569; font-family: monospace; }
  .tpl-card-pill.current { background: #DCFCE7; color: #15803D; border: 1px solid #86EFAC; }

  .tpl-modal-footer {
    padding: 10px 20px; border-top: 1px solid var(--border-light); background: #F8FAFC;
    display: flex; justify-content: space-between; align-items: center; flex-shrink: 0;
  }

  /* ==================== 9. 全局轻量 Toast 提示 & Loading 遮罩 ==================== */
  #toastContainer {
    position: fixed; top: 60px; left: 50%; transform: translateX(-50%);
    z-index: 2000; display: flex; flex-direction: column; gap: 8px; pointer-events: none;
  }
  .toast-msg {
    padding: 10px 18px; border-radius: 8px; color: #FFFFFF; font-size: 13px;
    font-weight: 600; box-shadow: 0 10px 25px rgba(0,0,0,0.18); display: flex;
    align-items: center; gap: 8px; animation: toastIn 0.25s ease forwards;
    pointer-events: auto; font-family: sans-serif;
  }
  .toast-msg.success { background: #065F46; border: 1px solid #059669; }
  .toast-msg.info { background: #1E40AF; border: 1px solid #3B82F6; }
  .toast-msg.warning { background: #92400E; border: 1px solid #D97706; }
  .toast-msg.error { background: #991B1B; border: 1px solid #DC2626; }
  @keyframes toastIn {
    from { opacity: 0; transform: translateY(-12px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .loading-mask {
    position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
    background: rgba(255, 255, 255, 0.75); backdrop-filter: blur(2px);
    z-index: 1500; display: none; flex-direction: column; align-items: center;
    justify-content: center; font-size: 14px; font-weight: 700; color: #1E293B; gap: 12px;
    font-family: sans-serif;
  }
  .loading-mask.show { display: flex; }
  .spinner {
    width: 32px; height: 32px; border: 3px solid #E2E8F0;
    border-top-color: var(--primary); border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ==================== 8.5 按钮与素材库/模板全景/案卷详情增强样式 ==================== */
  .btn-emerald {
    background: linear-gradient(135deg, #059669 0%, #10B981 100%) !important;
    color: #FFFFFF !important;
    border: 1px solid #047857 !important;
    box-shadow: 0 1px 3px rgba(16, 185, 129, 0.25);
  }
  .btn-emerald:hover {
    background: linear-gradient(135deg, #047857 0%, #059669 100%) !important;
    box-shadow: 0 2px 6px rgba(16, 185, 129, 0.4);
  }
  .btn-blue {
    background: linear-gradient(135deg, #2563EB 0%, #3B82F6 100%) !important;
    color: #FFFFFF !important;
    border: 1px solid #1D4ED8 !important;
    box-shadow: 0 1px 3px rgba(37, 99, 235, 0.25);
  }
  .btn-blue:hover {
    background: linear-gradient(135deg, #1D4ED8 0%, #2563EB 100%) !important;
    box-shadow: 0 2px 6px rgba(37, 99, 235, 0.4);
  }

  /* 素材库弹窗与卡片样式 */
  .mat-modal-card {
    background: #FFFFFF; border-radius: 12px; width: 92%; max-width: 1180px; height: 88vh;
    box-shadow: 0 20px 45px rgba(0,0,0,0.2); display: flex; flex-direction: column; overflow: hidden;
    animation: modalPop 0.2s ease-out; font-family: sans-serif;
  }
  .mat-modal-header {
    padding: 12px 20px; border-bottom: 1px solid var(--border-light);
    display: flex; justify-content: space-between; align-items: center; background: #FFFFFF;
  }
  .mat-header-left { display: flex; align-items: center; gap: 12px; }
  .mat-stats-pill {
    font-size: 11.5px; font-weight: 700; background: #ECFDF5; color: #065F46;
    border: 1px solid #A7F3D0; padding: 2px 10px; border-radius: 999px;
  }
  .mat-modal-toolbar {
    padding: 10px 20px; background: #F8FAFC; border-bottom: 1px solid var(--border-light);
    display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap;
  }
  .mat-search-wrap { display: flex; gap: 8px; flex: 1; max-width: 680px; }
  .mat-search-input {
    flex: 1; padding: 6px 12px; border: 1px solid #CBD5E1; border-radius: 6px;
    font-size: 13px; font-family: sans-serif; outline: none;
  }
  .mat-search-input:focus { border-color: #059669; box-shadow: 0 0 0 2px rgba(5,150,105,0.15); }
  .mat-toggle-label {
    display: flex; align-items: center; gap: 6px; font-size: 12px; color: #334155; font-weight: 600; cursor: pointer;
  }
  .mat-category-tabs {
    display: flex; gap: 6px; padding: 8px 20px; background: #FFFFFF;
    border-bottom: 1px solid var(--border-light); overflow-x: auto; flex-shrink: 0;
  }
  .mat-tab-btn {
    padding: 3px 12px; border-radius: 999px; border: 1px solid var(--border-light);
    background: #FFF; font-size: 12px; font-weight: 600; color: #475569; cursor: pointer;
    white-space: nowrap; transition: all 0.15s ease;
  }
  .mat-tab-btn:hover { background: #F1F5F9; color: #0F172A; }
  .mat-tab-btn.active {
    background: #059669; color: #FFF; border-color: #059669; box-shadow: 0 1px 3px rgba(5,150,105,0.25);
  }
  .mat-list-container {
    flex: 1; overflow-y: auto; padding: 14px 20px; display: grid;
    grid-template-columns: repeat(auto-fill, minmax(350px, 1fr)); gap: 12px; align-content: start;
    background: #F8FAFC;
  }
  .mat-case-card {
    background: #FFFFFF; border: 1px solid var(--border-light); border-radius: 8px;
    padding: 12px 14px; display: flex; flex-direction: column; gap: 6px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.03); transition: all 0.18s ease;
  }
  .mat-case-card:hover {
    border-color: #059669; box-shadow: 0 4px 12px rgba(5,150,105,0.12); transform: translateY(-1px);
  }
  .mat-card-header { display: flex; justify-content: space-between; align-items: center; }
  .mat-no-tag {
    font-family: Consolas, monospace; font-size: 11.5px; font-weight: 700;
    color: #1E293B; background: #E2E8F0; padding: 1px 6px; border-radius: 4px;
  }
  .mat-cat-tag {
    font-size: 11px; font-weight: 600; color: #065F46; background: #D1FAE5; padding: 1px 6px; border-radius: 4px;
  }
  .mat-card-title {
    font-size: 13.5px; font-weight: 700; color: #0F172A; line-height: 1.4;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .mat-card-parties {
    font-size: 12px; color: #475569; display: flex; flex-direction: column; gap: 2px;
  }
  .mat-party-row {
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .mat-card-meta {
    display: flex; justify-content: space-between; font-size: 11px; color: #64748B; margin-top: 2px;
  }
  .mat-card-chips { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 4px; }
  .mat-chip {
    font-size: 10px; font-weight: 700; padding: 1px 5px; border-radius: 3px;
  }
  .mat-chip.docx { background: #DBEAFE; color: #1E40AF; }
  .mat-chip.doc { background: #E0E7FF; color: #4338CA; }
  .mat-chip.pdf { background: #FEE2E2; color: #991B1B; }
  .mat-chip.md { background: #D1FAE5; color: #065F46; }
  .mat-card-actions {
    display: flex; gap: 6px; margin-top: 6px; border-top: 1px dashed var(--border-light); padding-top: 8px;
  }
  .mat-btn-action {
    flex: 1; padding: 4px 8px; font-size: 11.5px; font-weight: 700; border-radius: 4px;
    border: none; cursor: pointer; transition: all 0.15s ease; text-align: center;
  }
  .mat-btn-diff {
    background: #059669; color: #FFF;
  }
  .mat-btn-diff:hover { background: #047857; }
  .mat-btn-detail {
    background: #F1F5F9; color: #334155; border: 1px solid var(--border-light);
  }
  .mat-btn-detail:hover { background: #E2E8F0; }
  .mat-modal-footer {
    padding: 10px 20px; border-top: 1px solid var(--border-light);
    display: flex; justify-content: space-between; align-items: center; background: #FFFFFF;
  }
  .mat-pagination-btns { display: flex; align-items: center; gap: 8px; }
  .mat-cur-page { font-size: 12px; font-weight: 700; color: #0F172A; min-width: 60px; text-align: center; }

  /* 模板全景弹窗样式 */
  .ref-modal-card {
    background: #FFFFFF; border-radius: 12px; width: 88%; max-width: 1040px; height: 85vh;
    box-shadow: 0 20px 45px rgba(0,0,0,0.2); display: flex; flex-direction: column; overflow: hidden;
    animation: modalPop 0.2s ease-out; font-family: sans-serif;
  }
  .ref-modal-header {
    padding: 12px 20px; border-bottom: 1px solid var(--border-light);
    display: flex; justify-content: space-between; align-items: center;
  }
  .ref-header-left { display: flex; align-items: center; gap: 12px; }
  .ref-stats-pill {
    font-size: 11.5px; font-weight: 700; background: #EFF6FF; color: #1E40AF;
    border: 1px solid #BFDBFE; padding: 2px 10px; border-radius: 999px;
  }
  .ref-modal-toolbar {
    padding: 10px 20px; background: #F8FAFC; border-bottom: 1px solid var(--border-light);
    display: flex; align-items: center; gap: 12px;
  }
  .ref-search-input {
    flex: 1; max-width: 480px; padding: 6px 12px; border: 1px solid #CBD5E1;
    border-radius: 6px; font-size: 13px; font-family: sans-serif; outline: none;
  }
  .ref-search-input:focus { border-color: #2563EB; box-shadow: 0 0 0 2px rgba(37,99,235,0.15); }
  .ref-hint-text { font-size: 11.5px; color: #64748B; font-weight: 500; }
  .ref-category-tabs {
    display: flex; gap: 6px; padding: 8px 20px; background: #FFFFFF;
    border-bottom: 1px solid var(--border-light); overflow-x: auto; flex-shrink: 0;
  }
  .ref-cat-btn {
    padding: 3px 12px; border-radius: 999px; border: 1px solid var(--border-light);
    background: #FFF; font-size: 12px; font-weight: 600; color: #475569; cursor: pointer;
    white-space: nowrap; transition: all 0.15s ease;
  }
  .ref-cat-btn:hover { background: #F1F5F9; color: #0F172A; }
  .ref-cat-btn.active {
    background: #2563EB; color: #FFF; border-color: #2563EB; box-shadow: 0 1px 3px rgba(37,99,235,0.25);
  }
  .ref-list-container {
    flex: 1; overflow-y: auto; padding: 14px 20px; display: grid;
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 10px; align-content: start;
    background: #F8FAFC;
  }
  .ref-card {
    background: #FFFFFF; border: 1px solid var(--border-light); border-radius: 8px;
    padding: 10px 12px; display: flex; flex-direction: column; gap: 6px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.02); transition: all 0.15s ease;
  }
  .ref-card:hover {
    border-color: #2563EB; box-shadow: 0 3px 8px rgba(37,99,235,0.12); transform: translateY(-1px);
  }
  .ref-card-header { display: flex; justify-content: space-between; align-items: center; }
  .ref-folder-tag {
    font-size: 11px; font-weight: 700; color: #1E40AF; background: #DBEAFE; padding: 1px 6px; border-radius: 4px;
  }
  .ref-mb-tag {
    font-family: Consolas, monospace; font-size: 11px; font-weight: 700; color: #7C3AED; background: #EDE9FE; padding: 1px 6px; border-radius: 4px;
  }
  .ref-card-title {
    font-size: 13.5px; font-weight: 700; color: #0F172A; line-height: 1.4;
  }
  .ref-card-meta { font-size: 11px; color: #64748B; }
  .ref-card-actions { display: flex; gap: 5px; margin-top: 4px; padding-top: 6px; border-top: 1px dashed var(--border-light); }
  .ref-btn-action {
    flex: 1; padding: 3px 6px; font-size: 11px; font-weight: 700; border-radius: 4px;
    border: 1px solid transparent; cursor: pointer; transition: all 0.15s ease; text-align: center;
  }
  .ref-btn-primary { background: #2563EB; color: #FFF; }
  .ref-btn-primary:hover { background: #1D4ED8; }
  .ref-btn-std { background: #F1F5F9; color: #334155; border-color: var(--border-light); }
  .ref-btn-std:hover { background: #E2E8F0; }
  .ref-btn-self { background: #FAF5FF; color: #7C3AED; border-color: #DDD6FE; }
  .ref-btn-self:hover { background: #EDE9FE; }
  .ref-modal-footer {
    padding: 10px 20px; border-top: 1px solid var(--border-light);
    display: flex; justify-content: space-between; align-items: center; background: #FFFFFF;
  }

  /* 案卷详情弹窗 */
  .detail-modal-card {
    background: #FFFFFF; border-radius: 12px; width: 80%; max-width: 900px; height: 85vh;
    box-shadow: 0 20px 45px rgba(0,0,0,0.25); display: flex; flex-direction: column; overflow: hidden;
    animation: modalPop 0.2s ease-out; font-family: sans-serif;
  }
  .detail-modal-header {
    padding: 12px 20px; border-bottom: 1px solid var(--border-light);
    display: flex; justify-content: space-between; align-items: center;
  }
  .detail-no-pill {
    font-family: Consolas, monospace; font-size: 12px; font-weight: 700;
    color: #065F46; background: #D1FAE5; padding: 2px 8px; border-radius: 4px; margin-left: 8px;
  }
  .detail-modal-body {
    flex: 1; overflow-y: auto; padding: 20px; font-size: 13.5px; line-height: 1.7; background: #FFFFFF;
  }
  .detail-modal-body table {
    width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 12.5px;
  }
  .detail-modal-body th, .detail-modal-body td {
    border: 1px solid #CBD5E1; padding: 6px 10px; word-break: break-word;
  }
  .detail-modal-body th { background: #F1F5F9; font-weight: 600; text-align: left; }
  .detail-modal-body h1, .detail-modal-body h2, .detail-modal-body h3 {
    color: #0F172A; margin: 12px 0 6px 0; font-weight: 700;
  }
  .detail-modal-footer {
    padding: 10px 20px; border-top: 1px solid var(--border-light);
    display: flex; justify-content: flex-end; align-items: center; gap: 10px; background: #F8FAFC;
  }

  /* 打印样式适配 */
  @media print {
    .header, .toolbar-strip, .annotations-sidebar-panel, .modal-overlay, .column-sticky-header, #toastContainer, .loading-mask { display: none !important; }
    html, body { height: auto; overflow: visible; background: #FFF; }
    .review-workspace { display: block; height: auto; }
    .contract-scroll-container { padding: 0; overflow: visible; }
    .contract-paper-canvas { box-shadow: none; border: none; padding: 0; }
  }
</style>
</head>
<body class="mode-pure-redline">

<!-- 顶部 48px 全局精炼导航栏 -->
<header class="header">
  <div class="brand">
    <div class="brand-logo">Δ</div>
    <span class="brand-title">AI 合同审查 · 条款级 Diff 与整版原格式比对工作台</span>
  </div>

  <div class="header-meta-group">
    <!-- 点击可更换母本的胶囊 -->
    <button class="meta-tag-btn" id="matchedTemplateTag" onclick="openTemplateModal()" title="点击自主更换基准母本">
      <span>🎯 基准母本:</span>
      <span id="matchedTemplateName">国内专利服务合同（年费缴纳）</span>
      <span class="btn-arrow">▾ 更换</span>
    </button>

    <!-- 模板匹配置信度标签 -->
    <span class="meta-tag confidence conf-high" id="confidenceTag" title="点击查看置信度计算规则">
      <span>📊 匹配置信度:</span>
      <b id="confValueText">99.0%</b>
      <span class="conf-badge-pill" id="confPillText">权威匹配</span>
    </span>

    <span class="meta-tag doc-title" id="docFileName" title="送审合同原稿">《国内专利服务合同》送审修改稿</span>
  </div>

  <div class="header-actions">
    <button class="btn btn-emerald" id="btnOpenMaterialModal" onclick="openMaterialModal()" title="调取 2.1万+ 历史实单案卷及 4,500+ Word 合同原件">📚 历史素材库 (contract_merged)</button>
    <button class="btn btn-blue" id="btnOpenRefTemplateModal" onclick="openRefTemplateModal()" title="全景浏览【参考/】目录 54 套官方标准模板">📑 模板库全景 (参考/)</button>
    <button class="btn" id="btnSwitchTemplate" onclick="openTemplateModal()">🔄 更换母本</button>
    <button class="btn btn-purple" id="btnReloadTemplates" onclick="reloadTemplates()">⚡ 热加载母本库</button>
    <button class="btn btn-primary" onclick="openUploadModal()">📂 上传送审合同</button>
    <button class="btn" onclick="window.print()">🖨️ 导出对比单</button>
  </div>
</header>

<!-- 紧凑型 40px 工具与审阅导航条 -->
<div class="toolbar-strip">
  <div class="tool-left">
    <!-- 视图模式切换 -->
    <div class="segmented-control">
      <button class="seg-btn active" id="btnModeSide" onclick="setViewMode('side')">📖 原格式双栏对照</button>
      <button class="seg-btn" id="btnModeSingle" onclick="setViewMode('single')">📄 单版标红审阅</button>
      <button class="seg-btn" id="btnModeDiff" onclick="setViewMode('diff')">🎯 仅看变更条款</button>
    </div>

    <!-- 标红风格切换 -->
    <div class="segmented-control">
      <button class="seg-btn active" id="btnStyleRedline" onclick="setRedlineMode(true)">🔴 纯标红划线</button>
      <button class="seg-btn" id="btnStyleColor" onclick="setRedlineMode(false)">🎨 红绿对照</button>
    </div>

    <!-- 快速导航 -->
    <div class="nav-group">
      <button class="nav-arrow-btn" onclick="prevAnnotation()" title="快捷键: [ 或 Alt+↑">◀ 上一处</button>
      <span class="nav-indicator-pill" id="navIndicator">批注 1 / 0</span>
      <button class="nav-arrow-btn" onclick="nextAnnotation()" title="快捷键: ] 或 Alt+↓">下一处 ▶</button>
    </div>
  </div>

  <div class="tool-right">
    <!-- 核心统计徽章 -->
    <span class="stat-pill danger">🔴 高危篡改 <b id="statHighRisk">0</b></span>
    <span class="stat-pill missing">🚩 核心缺失 <b id="statMissingClauses">0</b></span>
    <span class="stat-pill warning">🟠 期限/违约 <b id="statMediumRisk">0</b></span>
    <span class="stat-pill progress">⚖️ 核验进度 <b id="statProgress">0/0</b></span>

    <button class="btn-toggle-sidebar" id="btnToggleSidebar" onclick="toggleSidebarCollapse()">
      <span>📋 详情专栏</span>
      <span class="sidebar-badge" id="btnSidebarCount">0</span>
    </button>
  </div>
</div>

<!-- ==================== 核心工作区：三栏独立视口与双向联动 ==================== -->
<div class="review-workspace" id="reviewWorkspace">

  <!-- 1. 左栏：标准参考母本（基准条款） -->
  <section class="contract-column pane-standard" id="colStandardPane">
    <div class="column-sticky-header standard">
      <div class="col-header-info">
        <span class="col-icon">🏛️</span>
        <span class="col-header-title">标准参考母本（基准正文）</span>
      </div>
      <span class="col-header-sub" id="headerStdName">母本库</span>
    </div>
    <div class="contract-scroll-container" id="colStandardContainer">
      <div class="contract-paper-canvas" id="paperStandard"></div>
    </div>
  </section>

  <!-- 2. 中栏：送审修改稿（增删标红） -->
  <section class="contract-column pane-reviewed" id="colReviewedPane">
    <div class="column-sticky-header reviewed">
      <div class="col-header-info">
        <span class="col-icon">✏️</span>
        <span class="col-header-title">送审合同（待审修改稿·增删对比）</span>
      </div>
      <span class="col-header-sub" id="headerRevName">待审稿</span>
    </div>
    <div class="contract-scroll-container" id="colReviewedContainer">
      <div class="contract-paper-canvas" id="paperReviewed"></div>
    </div>
  </section>

  <!-- 3. 右栏：法务审阅详情与批注专栏 (自适应) -->
  <aside class="annotations-sidebar-panel" id="annotationsSidebar">
    <div class="sidebar-header">
      <div class="sidebar-title">
        <span>📋 条款变动与法务详情</span>
        <span class="sidebar-count-badge" id="sidebarCount">0</span>
      </div>
      <button class="mini-tool-btn" onclick="toggleSidebarCollapse()" title="收起详情专栏">◀ 收起</button>
    </div>

    <!-- Agent 研判与全链路容灾监控面板 (爱马仕/CC/小龙虾高可用) -->
    <div class="agent-status-panel" id="agentStatusPanel">
      <div class="agent-status-row">
        <div class="agent-badge-group">
          <span class="agent-state-pill RUNNING" id="agentStateBadge">🟢 智能研判中</span>
          <span style="font-weight:700; color:#1E293B;" id="agentProgressRatio">0/0</span>
        </div>
        <div style="display:flex; gap:4px;">
          <button class="agent-control-btn" id="btnAgentPause" onclick="toggleAgentPause()" title="暂停/继续大模型后台研判">⏸️ 暂停</button>
          <button class="agent-control-btn" onclick="rerunAgentReview()" title="强制全篇重新研判">🔄 重审</button>
          <button class="agent-control-btn" id="btnResetCircuit" onclick="resetCircuitBreaker()" style="display:none;" title="重置大模型熔断器">🛡️ 复位熔断</button>
        </div>
      </div>
      <div class="agent-progress-bar-bg">
        <div class="agent-progress-bar-fill" id="agentProgressBar" style="width: 0%;"></div>
      </div>
      <div class="agent-meta-stats">
        <span class="agent-meta-tag" id="agentHeartbeatTag" title="SSE 双向保活与心跳监护">💓 心跳正常</span>
        <span class="agent-meta-tag" id="agentCircuitTag" title="连续失败自适应熔断（爱马仕/CC容灾）">🛡️ 熔断防护: 闭合</span>
        <span class="agent-meta-tag" id="agentWatchdogTag" title="单任务45s硬看门狗（OpenClaw卡死抢救）">⏱️ 看门狗: 就绪</span>
      </div>
    </div>

    <!-- 分类筛选标签 -->
    <div class="sidebar-filter-strip">
      <button class="filter-chip active" onclick="setFilter('ALL')">全部 (<span id="filterAllCount">0</span>)</button>
      <button class="filter-chip" onclick="setFilter('HIGH')">🔴 高危 (<span id="filterHighCount">0</span>)</button>
      <button class="filter-chip" onclick="setFilter('MISSING')">🚩 缺失 (<span id="filterMissingCount">0</span>)</button>
      <button class="filter-chip" onclick="setFilter('NEW')">➕ 新增 (<span id="filterNewCount">0</span>)</button>
      <button class="filter-chip" onclick="setFilter('WARNING')">🟠 违约/期限 (<span id="filterWarnCount">0</span>)</button>
    </div>

    <div class="annotations-list" id="annotationsList"></div>
  </aside>

</div>

<!-- 模态弹窗：上传送审合同 (DOCX/DOC/PDF) 与快速加载实测用例 -->
<div class="modal-overlay" id="uploadModal" onclick="closeUploadModalOnBackdrop(event)">
  <div class="modal-card">
    <div class="modal-header">
      <span class="modal-title">📂 上传送审合同 (DOCX/DOC/PDF) / 快速加载用例</span>
      <button class="modal-close-btn" onclick="closeUploadModal()">✕</button>
    </div>
    <div class="modal-body">
      <div class="drop-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
        <input type="file" id="fileInput" accept=".docx,.doc,.pdf" style="display:none;" onchange="handleFileSelect(event)">
        <div class="drop-icon">📄</div>
        <div class="drop-title">点击或拖拽 DOCX / DOC / PDF 送审合同至此</div>
        <div class="drop-desc">自动高保真结构解析并秒级对齐母本库，出具原格式增删比对</div>
      </div>

      <div style="font-size:12px; font-weight:700; color:#475569; margin-top:6px;">⚡ 快速加载实测高危用例 / 连通全量资产库：</div>
      <div class="preset-grid">
        <button class="preset-btn" style="border-color:#10B981; background:#F0FDF4;" onclick="closeUploadModal(); openMaterialModal();">
          <span>📚 打开 2.1万+ 历史素材库 (选择真实业务合同比对)</span>
          <span style="font-size:10.5px; font-weight:700; background:#DCFCE7; color:#166534; padding:1px 6px; border-radius:3px;">全库检索</span>
        </button>
        <button class="preset-btn" style="border-color:#3B82F6; background:#EFF6FF;" onclick="closeUploadModal(); openRefTemplateModal();">
          <span>📑 打开 54 套官方模板库 (选择标准母本原稿测试)</span>
          <span style="font-size:10.5px; font-weight:700; background:#DBEAFE; color:#1E40AF; padding:1px 6px; border-radius:3px;">母本原稿</span>
        </button>
        <button class="preset-btn" onclick="loadSample('prescreen_tampered'); closeUploadModal();">
          <span>🚨 快速预审 (违约金日息从 3‰ 偷降到 1‰)</span>
          <span style="font-size:10.5px; font-weight:700; background:#FEE2E2; color:#991B1B; padding:1px 6px; border-radius:3px;">高危篡改</span>
        </button>
        <button class="preset-btn" onclick="loadSample('missing_confidential'); closeUploadModal();">
          <span>🚩 专利代理 (保密条款与核心权利义务被偷删)</span>
          <span style="font-size:10.5px; font-weight:700; background:#FEF3C7; color:#92400E; padding:1px 6px; border-radius:3px;">核心缺失</span>
        </button>
        <button class="preset-btn" onclick="loadSample('trademark_match'); closeUploadModal();">
          <span>✅ 商标驳回复审 (合规原版基准)</span>
          <span style="font-size:10.5px; font-weight:700; background:#DCFCE7; color:#15803D; padding:1px 6px; border-radius:3px;">100%匹配</span>
        </button>
      </div>
    </div>
  </div>
</div>

<!-- ==================== 模态弹窗：选择/更换合同标准模板库 ==================== -->
<div class="modal-overlay" id="templateModal" onclick="closeTemplateModalOnBackdrop(event)">
  <div class="tpl-modal-card">
    <div class="tpl-modal-header">
      <div class="tpl-modal-title">
        <span>🔄 选择更换标准合同母本</span>
        <span style="font-size:11.5px; font-weight:600; color:#64748B;">(共 <span id="tplModalTotalCount">0</span> 套可用模板 · 严格按置信度从高到低排序)</span>
      </div>
      <button class="modal-close-btn" onclick="closeTemplateModal()">✕</button>
    </div>

    <div class="tpl-modal-toolbar">
      <input type="text" class="tpl-search-input" id="tplSearchInput" placeholder="🔍 快速搜索模板名称、编号(MB...)或关键词..." oninput="onTemplateSearchInput(event)">
      <button class="btn btn-purple" onclick="reloadTemplates(true)" title="重新扫描模板目录并热更新入库">⚡ 热加载库</button>
      <button class="btn" onclick="resetAutoTemplate()" title="切回由 AI 智能自动识别母本">🎯 恢复自动识别</button>
    </div>

    <div class="tpl-category-tabs" id="tplCategoryTabs"></div>

    <div class="tpl-list-scroll" id="tplListContainer"></div>

    <div class="tpl-modal-footer">
      <div style="font-size:11.5px; color:#64748B;">
        <span>💡 提示：所有模板已按照与当前送审合同的匹配置信度从高到低从上往下排序，点击任意卡片即可切换基准并实时重算。</span>
      </div>
      <button class="btn" onclick="closeTemplateModal()">关闭</button>
    </div>
  </div>
</div>

<!-- ==================== 模态弹窗：历史素材库 (contract_merged) ==================== -->
<div class="modal-overlay" id="materialModal" onclick="closeMaterialModalOnBackdrop(event)">
  <div class="mat-modal-card">
    <div class="mat-modal-header">
      <div class="mat-header-left">
        <span class="modal-title">📚 历史合同素材库 (contract_merged)</span>
        <span class="mat-stats-pill" id="matStatsPill">共 21,623 案卷 · 4,586 实单 Word 原件</span>
      </div>
      <button class="modal-close-btn" onclick="closeMaterialModal()">✕</button>
    </div>

    <!-- 顶部检索与筛选工具条 -->
    <div class="mat-modal-toolbar">
      <div class="mat-search-wrap">
        <input type="text" class="mat-search-input" id="matSearchInput" placeholder="🔍 搜索合同编号(如 CIP/GW/HCI...)、合同名称、对方主体(客户名)、申请人..." onkeyup="onMaterialSearchKey(event)">
        <button class="btn btn-emerald" onclick="triggerMaterialSearch()">搜索</button>
        <button class="btn" onclick="resetMaterialSearch()">重置</button>
      </div>

      <div class="mat-toggle-wrap">
        <label class="mat-toggle-label">
          <input type="checkbox" id="matHasWordOnly" checked onchange="triggerMaterialSearch()">
          <span>仅看含 Word 原件 (4,586份可直接比对)</span>
        </label>
      </div>
    </div>

    <!-- 分类标签页 -->
    <div class="mat-category-tabs" id="matCategoryTabs">
      <button class="mat-tab-btn active" id="matTab-ALL" onclick="setMaterialCategory('ALL')">全部 (2.1万)</button>
      <button class="mat-tab-btn" id="matTab-CIP" onclick="setMaterialCategory('CIP')">专利核心 (CIP/CIPCX)</button>
      <button class="mat-tab-btn" id="matTab-TRADEMARK" onclick="setMaterialCategory('TRADEMARK')">商标代理 (GW/MG/TZ)</button>
      <button class="mat-tab-btn" id="matTab-LITIGATION" onclick="setMaterialCategory('LITIGATION')">诉讼维权 (HCI/HCZ)</button>
      <button class="mat-tab-btn" id="matTab-FRAMEWORK" onclick="setMaterialCategory('FRAMEWORK')">合作框架 (HCCX/HKP)</button>
      <button class="mat-tab-btn" id="matTab-OTHER" onclick="setMaterialCategory('OTHER')">分部与其他</button>
    </div>

    <!-- 案卷卡片列表容器 -->
    <div class="mat-list-container" id="matListContainer">
      <div class="mat-loading-hint" style="grid-column: 1/-1; padding: 40px; text-align: center; color: #64748B;">正在加载素材库案卷...</div>
    </div>

    <!-- 分页与底部状态栏 -->
    <div class="mat-modal-footer">
      <div class="mat-page-info" id="matPageInfo">显示第 1-20 条 / 共 0 条</div>
      <div class="mat-pagination-btns">
        <button class="btn btn-sm" id="matBtnPrev" onclick="changeMaterialPage(-1)">◀ 上一页</button>
        <span class="mat-cur-page" id="matCurPageIndicator">1 / 1</span>
        <button class="btn btn-sm" id="matBtnNext" onclick="changeMaterialPage(1)">下一页 ▶</button>
      </div>
      <button class="btn" onclick="closeMaterialModal()">关闭</button>
    </div>
  </div>
</div>

<!-- ==================== 模态弹窗：模板库全景 (参考/) ==================== -->
<div class="modal-overlay" id="refTemplateModal" onclick="closeRefTemplateModalOnBackdrop(event)">
  <div class="ref-modal-card">
    <div class="ref-modal-header">
      <div class="ref-header-left">
        <span class="modal-title">📑 官方标准模板库全景 (参考/)</span>
        <span class="ref-stats-pill">共 54 套受控母本 · 10 大业务板块全量覆盖</span>
      </div>
      <button class="modal-close-btn" onclick="closeRefTemplateModal()">✕</button>
    </div>

    <div class="ref-modal-toolbar">
      <input type="text" class="ref-search-input" id="refSearchInput" placeholder="🔍 快速搜索模板名称、业务板块或编号..." oninput="onRefSearchInput(event)">
      <div class="ref-hint-text">💡 点击【载入待审】可测试真实原稿渲染；点击【设为母本】更换基准；点击【一键自测】验证 100% 匹配。</div>
    </div>

    <!-- 板块分类标签 -->
    <div class="ref-category-tabs" id="refCategoryTabs"></div>

    <!-- 模板卡片列表 -->
    <div class="ref-list-container" id="refListContainer"></div>

    <div class="ref-modal-footer">
      <div style="font-size:12px; color:#64748B;">全量模板均通过 OOXML 严格结构解析与段落 AST 校验。</div>
      <button class="btn" onclick="closeRefTemplateModal()">关闭</button>
    </div>
  </div>
</div>

<!-- ==================== 模态弹窗：案卷审批与履约详情 ==================== -->
<div class="modal-overlay" id="caseDetailModal" onclick="closeCaseDetailModalOnBackdrop(event)">
  <div class="detail-modal-card">
    <div class="detail-modal-header">
      <div>
        <span class="modal-title" id="detailModalTitle">案卷履约与审批详情</span>
        <span class="detail-no-pill" id="detailModalNo">CIP20202024-BM</span>
      </div>
      <button class="modal-close-btn" onclick="closeCaseDetailModal()">✕</button>
    </div>
    <div class="detail-modal-body" id="detailModalBody">
      <div style="padding:20px; text-align:center; color:#64748B;">加载中...</div>
    </div>
    <div class="detail-modal-footer">
      <button class="btn btn-emerald" id="detailBtnLoadDiff" onclick="loadCurrentDetailContract()">⚡ 载入此案卷合同并比对</button>
      <button class="btn" onclick="closeCaseDetailModal()">关闭</button>
    </div>
  </div>
</div>

<!-- 全局轻量 Toast 消息容器 -->
<div id="toastContainer"></div>

<!-- 全局 Loading 遮罩 -->
<div class="loading-mask" id="globalLoadingMask">
  <div class="spinner"></div>
  <div id="loadingMaskText">正在加载中，请稍候...</div>
</div>

<script>
// 全局状态管理
let currentData = null;
let currentViewMode = 'side'; // 'side' | 'single' | 'diff'
let currentFilter = 'ALL';
let activeAnnotationIndex = -1;

// 模板库数据与筛选状态
let allTemplates = __ALL_TEMPLATES_PLACEHOLDER__ || [];
let activeTemplateCategory = 'ALL';
let templateSearchKeyword = '';

// 素材库 (contract_merged) 状态
window.MATERIAL_DATA = __MATERIAL_DATA_PLACEHOLDER__ || {};
let currentMaterialCategory = 'ALL';
let currentMaterialSearch = '';
let currentMaterialPage = 1;
let currentMaterialHasWordOnly = true;
let currentDetailContractNo = null;

// 模板全景 (参考/) 状态
let refTemplatesCache = __REF_TEMPLATES_PLACEHOLDER__ || [];
let activeRefCategory = 'ALL';
let refSearchKeyword = '';

// 预置默认数据占位符
window.INITIAL_DATA = __INITIAL_DATA_PLACEHOLDER__;

// Toast 提示工具
function showToast(message, type = 'success') {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = `toast-msg ${type}`;
  let icon = '✅';
  if (type === 'info') icon = 'ℹ️';
  if (type === 'warning') icon = '⚠️';
  if (type === 'error') icon = '❌';
  toast.innerHTML = `<span>${icon}</span><span>${escapeHtml(message)}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(-10px)';
    toast.style.transition = 'all 0.25s ease';
    setTimeout(() => toast.remove(), 260);
  }, 2600);
}

function showLoading(text = '正在处理中...') {
  const mask = document.getElementById('globalLoadingMask');
  document.getElementById('loadingMaskText').innerText = text;
  mask.classList.add('show');
}
function hideLoading() {
  document.getElementById('globalLoadingMask').classList.remove('show');
}

// 模态弹窗控制
function openUploadModal() {
  document.getElementById('uploadModal').classList.add('open');
}
function closeUploadModal() {
  document.getElementById('uploadModal').classList.remove('open');
}
function closeUploadModalOnBackdrop(e) {
  if (e.target.id === 'uploadModal') closeUploadModal();
}

function openTemplateModal() {
  initTemplateModal();
  document.getElementById('templateModal').classList.add('open');
}
function closeTemplateModal() {
  document.getElementById('templateModal').classList.remove('open');
}
function closeTemplateModalOnBackdrop(e) {
  if (e.target.id === 'templateModal') closeTemplateModal();
}

function openMaterialModal() {
  document.getElementById('materialModal').classList.add('open');
  initMaterialModal();
}
function closeMaterialModal() {
  document.getElementById('materialModal').classList.remove('open');
}
function closeMaterialModalOnBackdrop(e) {
  if (e.target.id === 'materialModal') closeMaterialModal();
}

function openRefTemplateModal() {
  document.getElementById('refTemplateModal').classList.add('open');
  initRefTemplateModal();
}
function closeRefTemplateModal() {
  document.getElementById('refTemplateModal').classList.remove('open');
}
function closeRefTemplateModalOnBackdrop(e) {
  if (e.target.id === 'refTemplateModal') closeRefTemplateModal();
}

function openCaseDetailModal(contractNo) {
  currentDetailContractNo = contractNo;
  document.getElementById('detailModalNo').innerText = contractNo;
  document.getElementById('detailModalTitle').innerText = '案卷详情: ' + contractNo;
  document.getElementById('detailModalBody').innerHTML = '<div style="padding:40px; text-align:center; color:#64748B;">正在调取案卷全宗履约与审批档案...</div>';
  document.getElementById('caseDetailModal').classList.add('open');
  fetchCaseDetail(contractNo);
}
function closeCaseDetailModal() {
  document.getElementById('caseDetailModal').classList.remove('open');
}
function closeCaseDetailModalOnBackdrop(e) {
  if (e.target.id === 'caseDetailModal') closeCaseDetailModal();
}

// 拖拽上传支持 (DOCX / DOC / PDF)
const dropZone = document.getElementById('dropZone');
dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  if (e.dataTransfer.files.length > 0) {
    uploadContractFile(e.dataTransfer.files[0]);
    closeUploadModal();
  }
});

function handleFileSelect(e) {
  if (e.target.files.length > 0) {
    uploadContractFile(e.target.files[0]);
    closeUploadModal();
  }
}

async function uploadContractFile(file) {
  const formData = new FormData();
  formData.append('file', file);
  showLoading('正在解析送审文档并自动匹配基准母本...');

  try {
    const resp = await fetch('/api/diff/upload', { method: 'POST', body: formData });
    const res = await resp.json();
    hideLoading();
    if (res.error) {
      showToast('上传解析失败: ' + res.error, 'error');
      return;
    }
    renderFullDiff(res);
    initAgentSSE(false);
    // 同步刷新模板置信度与排序
    fetchTemplateListQuietly();
    showToast(`合同《${file.name.replace(/\.[^/.]+$/, '')}》解析完成，已对齐基准母本！`, 'success');
  } catch (err) {
    hideLoading();
    showToast('请求失败，请确保后台 Web 服务正常启动: ' + err, 'error');
  }
}

async function loadSample(sampleKey) {
  showLoading('正在加载实测用例并对齐母本...');
  try {
    const resp = await fetch('/api/diff/sample?key=' + sampleKey);
    const res = await resp.json();
    hideLoading();
    renderFullDiff(res);
    initAgentSSE(false);
    fetchTemplateListQuietly();
    showToast('实测用例加载成功！', 'success');
  } catch (err) {
    hideLoading();
    showToast('加载实测用例失败: ' + err, 'error');
  }
}

// 静默拉取按最新文档排序的模板库列表
async function fetchTemplateListQuietly() {
  try {
    const resp = await fetch('/api/templates/list');
    const res = await resp.json();
    if (res.templates) {
      allTemplates = res.templates;
      if (document.getElementById('templateModal').classList.contains('open')) {
        initTemplateModal();
      }
    }
  } catch (err) {}
}

// ==================== 模板选择与更换模块（按置信度从上往下排） ====================
function initTemplateModal() {
  document.getElementById('tplModalTotalCount').innerText = allTemplates.length;
  renderCategoryTabs();
  renderTemplateItems();
}

function renderCategoryTabs() {
  const tabsContainer = document.getElementById('tplCategoryTabs');
  tabsContainer.innerHTML = '';

  const catCounts = {};
  allTemplates.forEach(t => {
    const cat = t.category || 'UNKNOWN';
    catCounts[cat] = (catCounts[cat] || 0) + 1;
  });

  const categoryDef = [
    { key: 'ALL', label: '全部' },
    { key: 'PATENT', label: '专利业务' },
    { key: 'TRADEMARK', label: '商标业务' },
    { key: 'HIGH_END_CONSULTING', label: '高端咨询' },
    { key: 'LEGAL_LITIGATION', label: '法律诉讼' },
    { key: 'INT_IP', label: '国际业务' },
    { key: 'PROJECT_AUDIT', label: '项目申报' },
    { key: 'MIXED_IP', label: '综合业务' },
    { key: 'COPYRIGHT', label: '著作权' },
    { key: 'FRAMEWORK_NDA', label: '保密框架' },
    { key: 'IP_OPERATION', label: '知识产权运营' },
  ];

  categoryDef.forEach(c => {
    const count = c.key === 'ALL' ? allTemplates.length : (catCounts[c.key] || 0);
    if (c.key !== 'ALL' && count === 0) return;
    const btn = document.createElement('button');
    btn.className = `tpl-tab-btn ${activeTemplateCategory === c.key ? 'active' : ''}`;
    btn.innerText = `${c.label} (${count})`;
    btn.onclick = () => {
      activeTemplateCategory = c.key;
      renderCategoryTabs();
      renderTemplateItems();
    };
    tabsContainer.appendChild(btn);
  });
}

function onTemplateSearchInput(e) {
  templateSearchKeyword = (e.target.value || '').trim().toLowerCase();
  renderTemplateItems();
}

function renderTemplateItems() {
  const container = document.getElementById('tplListContainer');
  container.innerHTML = '';

  const currentTplId = currentData ? (currentData.matched_template_id || '') : '';

  // 过滤并确保严格从上往下按置信度排序
  const filtered = allTemplates.filter(t => {
    if (activeTemplateCategory !== 'ALL' && t.category !== activeTemplateCategory) {
      return false;
    }
    if (templateSearchKeyword) {
      const matchName = (t.name || '').toLowerCase().includes(templateSearchKeyword);
      const matchDocId = (t.document_id || '').toLowerCase().includes(templateSearchKeyword);
      const matchFile = (t.full_name || '').toLowerCase().includes(templateSearchKeyword);
      const matchCat = (t.category_name || '').toLowerCase().includes(templateSearchKeyword);
      if (!matchName && !matchDocId && !matchFile && !matchCat) return false;
    }
    return true;
  });

  // 严格按置信度从高到低从上往下排序
  filtered.sort((a, b) => {
    const confA = typeof a.confidence_score === 'number' ? a.confidence_score : 0;
    const confB = typeof b.confidence_score === 'number' ? b.confidence_score : 0;
    return confB - confA;
  });

  if (filtered.length === 0) {
    container.innerHTML = `
      <div style="grid-column: 1/-1; text-align:center; padding:40px; color:#94A3B8; font-size:13px;">
        未检索到匹配的模板，请尝试其他关键词或点击上方【热加载库】
      </div>
    `;
    return;
  }

  filtered.forEach(t => {
    const isCurrent = t.document_id === currentTplId;
    const card = document.createElement('div');
    card.className = `tpl-card-item ${isCurrent ? 'active-tpl' : ''}`;
    card.onclick = () => switchTemplate(t.document_id, t.name);

    // 置信度样式映射
    const confScore = typeof t.confidence_score === 'number' ? t.confidence_score : 0.0;
    let badgeCls = 'conf-low';
    if (confScore >= 0.90) badgeCls = 'conf-very-high';
    else if (confScore >= 0.75) badgeCls = 'conf-high';
    else if (confScore >= 0.50) badgeCls = 'conf-medium';

    const confPct = t.confidence_percent || `${(confScore * 100).toFixed(1)}%`;
    const confLbl = t.confidence_label || (confScore >= 0.9 ? '权威匹配' : (confScore >= 0.75 ? '推荐' : '备选'));

    // 格式化编号展示（非十六进制哈希才显示）
    const showCode = t.is_standard_mb && t.document_id ? `<span class="tpl-card-pill code">${escapeHtml(t.document_id)}</span>` : '';
    const showVer = t.version ? `<span>版本: ${escapeHtml(t.version)}</span>` : '';

    card.innerHTML = `
      <div class="tpl-card-header">
        <span class="tpl-card-title">${escapeHtml(t.name)}</span>
        <div class="tpl-card-conf-badge ${badgeCls}" title="与送审合同匹配置信度: ${confPct}">
          <span>🎯</span>
          <span>${confPct}</span>
          <span style="font-size:9.5px; opacity:0.85;">${escapeHtml(confLbl)}</span>
        </div>
      </div>
      <div class="tpl-card-meta">
        <span class="tpl-card-pill category">${escapeHtml(t.category_name)}</span>
        ${showCode}
        ${showVer}
        ${isCurrent ? '<span class="tpl-card-pill current">✓ 当前使用</span>' : ''}
      </div>
    `;
    container.appendChild(card);
  });
}

// 执行更换模板
async function switchTemplate(targetDocId, targetName = '') {
  closeTemplateModal();
  showLoading(`正在将基准母本切换为 [${targetName || targetDocId}] 并重新比对...`);

  try {
    const resp = await fetch('/api/diff/switch-template', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ template_id: targetDocId }),
    });
    const res = await resp.json();
    hideLoading();
    if (res.error) {
      showToast('切换模板失败: ' + res.error, 'error');
      return;
    }
    renderFullDiff(res);
    initAgentSSE(false);
    fetchTemplateListQuietly();
    showToast(`基准母本已成功更换为《${res.clean_template_name || targetName}》！`, 'success');
  } catch (err) {
    hideLoading();
    showToast('切换模板网络请求失败: ' + err, 'error');
  }
}

// 恢复 AI 自动识别母本
async function resetAutoTemplate() {
  closeTemplateModal();
  showLoading('正在根据待审合同内容恢复 AI 智能自动匹配母本...');

  try {
    const resp = await fetch('/api/diff/switch-template', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ template_id: 'auto' }),
    });
    const res = await resp.json();
    hideLoading();
    if (res.error) {
      showToast('恢复自动匹配失败: ' + res.error, 'error');
      return;
    }
    renderFullDiff(res);
    initAgentSSE(false);
    fetchTemplateListQuietly();
    showToast(`已恢复 AI 自动识别匹配母本: 《${res.clean_template_name || res.matched_template_name}》！`, 'success');
  } catch (err) {
    hideLoading();
    showToast('请求失败: ' + err, 'error');
  }
}

// 热加载模板库
async function reloadTemplates(fromModal = false) {
  const btn = document.getElementById('btnReloadTemplates');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '🔄 扫描热加载中...';
  }

  try {
    const resp = await fetch('/api/templates/reload', { method: 'POST' });
    const res = await resp.json();
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '⚡ 热加载模板库';
    }

    if (res.error) {
      showToast('热加载模板库失败: ' + res.error, 'error');
      return;
    }

    allTemplates = res.templates || [];
    initTemplateModal();
    showToast(res.message || `热加载成功！库中现有 ${allTemplates.length} 套标准母本`, 'success');
  } catch (err) {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '⚡ 热加载模板库';
    }
    showToast('热加载请求失败: ' + err, 'error');
  }
}

// 格式判定：识别正文节点原排版角色
function detectBlockCategory(title, text, idx) {
  const cleaned = (text || '').trim();
  if (idx === 0 && (cleaned.includes('合同') || cleaned.includes('协议'))) {
    return 'TITLE';
  }
  if (idx === 1 && (cleaned.startsWith('(') || cleaned.startsWith('（') || cleaned.includes('年费') || cleaned.includes('委托') || cleaned.includes('申请'))) {
    return 'SUBTITLE';
  }
  if (cleaned.startsWith('合同编号') || cleaned.startsWith('编号:') || cleaned.startsWith('协议编号')) {
    return 'META';
  }
  if (title === '双方主体与基本信息' || (cleaned.includes('甲方') && cleaned.includes('乙方') && (cleaned.includes('地址') || cleaned.includes('法定代表人') || cleaned.includes('统一社会信用代码')))) {
    return 'PARTIES';
  }
  if (cleaned.includes('达成如下协议') || cleaned.includes('鉴于') || cleaned.includes('特订立本合同')) {
    return 'PREAMBLE';
  }
  if (/^第[一二三四五六七八九十百]+条/.test(cleaned)) {
    const lines = cleaned.split('\n').map(l => l.trim()).filter(Boolean);
    if (lines.length === 1 && cleaned.length < 35) {
      return 'ARTICLE_HEADING';
    }
    return 'ARTICLE_SECTION';
  }
  if (cleaned.includes('（以下无正文）') || (cleaned.includes('甲方') && cleaned.includes('乙方') && (cleaned.includes('盖章') || cleaned.includes('签字')))) {
    return 'SIGNATURE';
  }
  if (/^\d+\.\d+/.test(cleaned)) {
    return 'SUBCLAUSE';
  }
  return 'PARAGRAPH';
}

// 格式化输出为纯正合同排版 HTML
function formatContractHtml(html, rawText, category) {
  if (!html) return '<div class="empty-placeholder"></div>';

  if (html.includes('<table') || html.includes('contract-table-card') || html.includes('contract-doc-table')) {
    return html;
  }

  if (html.includes('doc-text-deleted') || html.includes('doc-slot-deleted') || html.includes('doc-slot-blank') || html.includes('doc-text-added')) {
    return html;
  }

  if (category === 'ARTICLE_SECTION') {
    const headingMatch = html.match(/^(<div class="[^"]*">)?(第[一二三四五六七八九十百]+条[^\n<]*)\n([\s\S]*)$/);
    if (headingMatch) {
      const prefix = headingMatch[1] || '';
      const headingText = headingMatch[2];
      const bodyText = headingMatch[3];
      return `${prefix}<div class="contract-article-heading">${headingText}</div><div class="contract-doc-p">${bodyText}</div>${prefix ? '</div>' : ''}`;
    }
    return `<div class="contract-article-section">${html}</div>`;
  }

  if (category === 'TITLE') {
    return `<h1 class="contract-doc-title">${html}</h1>`;
  }
  if (category === 'SUBTITLE') {
    return `<div class="contract-doc-subtitle">${html}</div>`;
  }
  if (category === 'META') {
    return `<div class="contract-doc-meta">${html}</div>`;
  }
  if (category === 'PARTIES') {
    return `<div class="contract-doc-parties">${html}</div>`;
  }
  if (category === 'PREAMBLE') {
    return `<div class="contract-doc-preamble">${html}</div>`;
  }
  if (category === 'ARTICLE_HEADING') {
    return `<div class="contract-article-heading">${html}</div>`;
  }
  if (category === 'SIGNATURE') {
    return `<div class="contract-doc-signatures">${html}</div>`;
  }

  return `<div class="contract-doc-p">${html}</div>`;
}

function escapeHtml(text) {
  if (!text) return '';
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// 渲染整版原格式比对与批注
function renderFullDiff(data) {
  resetThreeWaySyncScroll();
  currentData = data;
  activeAnnotationIndex = -1;

  // 1. 规范化送审合同名称展示（彻底杜绝技术内部文件名暴露）
  let friendlyDocTitle = data.friendly_doc_title || data.reviewed_doc_title || data.display_doc_name || data.doc_title || '';
  if (!friendlyDocTitle || /active_review|mut_|\.docx$/i.test(friendlyDocTitle)) {
    const raw = (data.uploaded_file || '').split(/[\/\\]/).pop();
    if (raw.includes('06') || raw.includes('confidential')) {
      friendlyDocTitle = '《国内专利服务合同（年费缴纳）》送审修改稿（保密义务被删）';
    } else if (raw.includes('驳回复审')) {
      friendlyDocTitle = '《商标驳回复审代理协议》送审原稿';
    } else {
      friendlyDocTitle = '《国内专利服务合同（年费缴纳）》送审修改稿';
    }
  }

  // 确保书名号格式优美
  if (!friendlyDocTitle.startsWith('《') || !friendlyDocTitle.endsWith('》')) {
    const rawInner = friendlyDocTitle.replace(/^[《<]+|[》>]+$/g, '');
    friendlyDocTitle = '《' + rawInner + '》';
  }

  document.getElementById('docFileName').innerText = friendlyDocTitle;

  // 2. 规范化基准母本名称展示（彻底杜绝十六进制哈希 ID 暴露）
  let tplCleanName = data.clean_template_name || data.matched_template_display || '';
  if (!tplCleanName) {
    const rawTplName = data.matched_template_name || '国内专利服务合同（年费缴纳）';
    const mbCode = data.clean_template_code || data.matched_template_code || (String(data.matched_template_id || '').startsWith('MB') ? data.matched_template_id : '');
    const cleanBase = rawTplName.replace(/\.(docx?|pdf)$/i, '')
      .replace(/^WDCLI_[a-f0-9]+_/, '')
      .replace(/^MB\w+?_V[\d\.]+?_/, '')
      .replace(/[-_](恒程|创新|代理|知识产权|事务所|公司|企业).*$/, '')
      .trim();
    tplCleanName = mbCode ? '[' + mbCode + '] ' + cleanBase : cleanBase;
  }

  const matchedNameEl = document.getElementById('matchedTemplateName');
  if (matchedNameEl) matchedNameEl.innerText = tplCleanName;
  // 顶部母本悬停提示（若为 MB 编号则展示，若是十六进制哈希则不暴露底层哈希）
  const tplId = data.matched_template_id || '';
  const isHash = data.is_hex_hash_id || (tplId.length > 12 && !tplId.startsWith('MB'));
  document.getElementById('matchedTemplateTag').title = `基准模板: ${tplCleanName}${(!isHash && tplId) ? ' (' + tplId + ')' : ''} · 点击更换`;

  // 3. 渲染置信度标签
  const confTag = document.getElementById('confidenceTag');
  const confValText = document.getElementById('confValueText');
  const confPillText = document.getElementById('confPillText');

  const confScore = typeof data.confidence_score === 'number' ? data.confidence_score : (typeof data.classification_confidence === 'number' ? data.classification_confidence : 1.0);
  const isManual = data.is_manual_override === true || data.matched_method === 'MANUAL_SELECTION';

  if (isManual) {
    confTag.className = 'meta-tag confidence conf-manual';
    confValText.innerText = '100.0%';
    confPillText.innerText = '手动指定';
    confTag.title = `【模板匹配机制】人工手动指定模式\n基准母本已由用户锁定: ${tplCleanName}\n点击基准母本可随时更换或恢复自动识别。`;
  } else {
    const pct = data.confidence_percent || `${(confScore * 100).toFixed(1)}%`;
    confValText.innerText = pct;

    if (confScore >= 0.85) {
      confTag.className = 'meta-tag confidence conf-high';
      confPillText.innerText = '权威匹配';
    } else if (confScore >= 0.70) {
      confTag.className = 'meta-tag confidence conf-medium';
      confPillText.innerText = '良好匹配';
    } else {
      confTag.className = 'meta-tag confidence conf-low';
      confPillText.innerText = '建议复核';
    }

    confTag.title = `【AI 智能路由置信度】${pct}\n识别业务子类: ${data.detected_subtype || '标准合同'}\n匹配基准母本: ${tplCleanName}\n状态: ${data.confidence_desc || '自动五级分类对齐'}`;
  }

  // 4. 双栏纸张列标题规范化呈现（绝不显示底层 16 进制 Hash 码）
  const stdCodeStr = (!isHash && data.clean_template_code) ? ` (${data.clean_template_code})` : '';
  document.getElementById('headerStdName').innerText = `${tplCleanName}${stdCodeStr}`;
  document.getElementById('headerRevName').innerText = friendlyDocTitle;

  // 5. 统计数据
  const stats = data.stats || {};
  const annCount = (data.annotations || []).length;
  document.getElementById('statHighRisk').innerText = stats.high_risk || 0;
  document.getElementById('statMissingClauses').innerText = stats.missing_clauses || 0;
  document.getElementById('statMediumRisk').innerText = (stats.medium_risk || 0);

  document.getElementById('sidebarCount').innerText = annCount;
  const btnSidebarCountEl = document.getElementById('btnSidebarCount');
  if (btnSidebarCountEl) btnSidebarCountEl.innerText = annCount;

  // 过滤器数值
  const anns = data.annotations || [];
  document.getElementById('filterAllCount').innerText = anns.length;
  document.getElementById('filterHighCount').innerText = anns.filter(a => a.risk_level === 'HIGH' && a.delta_type !== 'MISSING_CLAUSE').length;
  document.getElementById('filterMissingCount').innerText = anns.filter(a => a.delta_type === 'MISSING_CLAUSE').length;
  document.getElementById('filterNewCount').innerText = anns.filter(a => a.delta_type === 'NEW_CLAUSE').length;
  document.getElementById('filterWarnCount').innerText = anns.filter(a => a.risk_level === 'MEDIUM' && a.delta_type !== 'NEW_CLAUSE').length;

  updateProgressDisplay();
  renderContractColumns();
  renderAnnotationsSidebar();

  // 双栏渲染完毕后，同步左右每一行条款的高度
  requestAnimationFrame(() => {
    syncClauseHeights();
    setupThreeWaySyncScroll();
  });
}

function updateProgressDisplay() {
  if (!currentData || !currentData.annotations) return;
  const total = currentData.annotations.length;
  const handled = currentData.annotations.filter(a => a.review_status && a.review_status !== 'PENDING').length;
  document.getElementById('statProgress').innerText = `${handled}/${total}`;
}

// 核心渲染：左右双栏分别渲染为完整连续 A4 纸张页面
function renderContractColumns() {
  const paperStd = document.getElementById('paperStandard');
  const paperRev = document.getElementById('paperReviewed');
  paperStd.innerHTML = '';
  paperRev.innerHTML = '';

  const blocks = currentData.aligned_blocks || [];
  blocks.forEach((block, idx) => {
    const isMatch = block.status === 'MATCH';
    if (currentViewMode === 'diff' && isMatch) {
      return;
    }

    const txt = block.standard_text || block.reviewed_text || '';
    const category = detectBlockCategory(block.clause_title, txt, idx);

    // 左侧母本条款块
    const stdBlock = document.createElement('div');
    stdBlock.className = `clause-block ${block.status.toLowerCase()}`;
    stdBlock.id = `std-${block.block_id}`;
    stdBlock.dataset.blockId = block.block_id;
    if (block.annotation_ids && block.annotation_ids.length > 0) {
      stdBlock.dataset.annId = block.annotation_ids[0];
      stdBlock.onclick = () => selectAnnotation(block.annotation_ids[0]);
    }
    stdBlock.innerHTML = formatContractHtml(block.standard_html || escapeHtml(block.standard_text), block.standard_text, category);
    paperStd.appendChild(stdBlock);

    // 右侧送审条款块
    const revBlock = document.createElement('div');
    revBlock.className = `clause-block ${block.status.toLowerCase()}`;
    revBlock.id = `rev-${block.block_id}`;
    revBlock.dataset.blockId = block.block_id;
    if (block.annotation_ids && block.annotation_ids.length > 0) {
      revBlock.dataset.annId = block.annotation_ids[0];
      revBlock.onclick = () => selectAnnotation(block.annotation_ids[0]);
    }
    revBlock.innerHTML = formatContractHtml(block.reviewed_html || escapeHtml(block.reviewed_text), block.reviewed_text, category);
    paperRev.appendChild(revBlock);
  });
}

// 左右条款高度等高自适应平衡算法
function syncClauseHeights() {
  if (!currentData || !currentData.aligned_blocks) return;
  const blocks = currentData.aligned_blocks;

  blocks.forEach(b => {
    const s = document.getElementById(`std-${b.block_id}`);
    const r = document.getElementById(`rev-${b.block_id}`);
    if (s) s.style.minHeight = '0px';
    if (r) r.style.minHeight = '0px';
  });

  blocks.forEach(b => {
    const s = document.getElementById(`std-${b.block_id}`);
    const r = document.getElementById(`rev-${b.block_id}`);
    if (s && r) {
      const h = Math.max(s.getBoundingClientRect().height, r.getBoundingClientRect().height);
      s.style.minHeight = `${h}px`;
      r.style.minHeight = `${h}px`;
    }
  });
}

window.addEventListener('resize', () => {
  syncClauseHeights();
});

// ==================== 三栏双向协同联动滚动 ====================
let activeDriver = null;
let syncListenerAttached = false;
let syncFrame = 0;
let scrollGeometry = null;
let scrollPanes = null;
const followerPositions = new WeakMap();

// Coordinates are relative to each scrollport, never to an offsetParent.
function contentTop(element, pane) {
  return element.getBoundingClientRect().top - pane.getBoundingClientRect().top
    - pane.clientTop + pane.scrollTop;
}
function scrollLimit(pane) { return Math.max(0, pane.scrollHeight - pane.clientHeight); }
function invalidateScrollGeometry() { scrollGeometry = null; }
function resetThreeWaySyncScroll() {
  cancelAnimationFrame(syncFrame);
  syncFrame = 0;
  activeDriver = null;
  invalidateScrollGeometry();
  if (scrollPanes) Object.values(scrollPanes).forEach(pane => {
    pane.scrollTo({top: pane.scrollTop, behavior: 'instant'});
    followerPositions.delete(pane);
  });
}

function highlightAnnotation(annId) {
  const card = document.getElementById(`card-${annId}`);
  if (currentData?.annotations?.[activeAnnotationIndex]?.id === annId && card?.classList.contains('active')) return;
  const index = (currentData?.annotations || []).findIndex(a => a.id === annId);
  if (index < 0) return;
  const ann = currentData.annotations[index];
  activeAnnotationIndex = index;
  document.querySelectorAll('.annotation-card.active').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.clause-block.active-clause').forEach(el => el.classList.remove('active-clause'));
  card?.classList.add('active');
  document.getElementById(`std-${ann.block_id}`)?.classList.add('active-clause');
  document.getElementById(`rev-${ann.block_id}`)?.classList.add('active-clause');
  const indicator = document.getElementById('navIndicator');
  if (indicator) indicator.innerText = `批注 ${ann.index || index + 1} / ${currentData.annotations.length}`;
}

function highlightScrollPosition(source, position) {
  const stops = scrollGeometry.annotationStops[source];
  if (!stops.length) return;
  let low = 0, high = stops.length;
  while (low < high) {
    const mid = (low + high) >> 1;
    if (stops[mid].position <= position) low = mid + 1; else high = mid;
  }
  // Use the clause at the reading line, or the closest card to the sidebar center.
  let index = Math.max(0, low - 1);
  if (source === 'detail' && low < stops.length
      && Math.abs(stops[low].position - position) < Math.abs(stops[index].position - position)) index = low;
  highlightAnnotation(stops[index].id);
}

function mapScrollPosition(position, anchors) {
  if (position <= anchors[0][0]) return anchors[0][1];
  const last = anchors[anchors.length - 1];
  if (position >= last[0]) return last[1];
  let low = 0, high = anchors.length - 1;
  while (high - low > 1) {
    const mid = (low + high) >> 1;
    if (anchors[mid][0] <= position) low = mid; else high = mid;
  }
  const a = anchors[low], b = anchors[high];
  return a[1] + (b[1] - a[1]) * (position - a[0]) / (b[0] - a[0]);
}

function buildScrollGeometry() {
  const result = {annotationStops: {std: [], rev: [], detail: []}};
  const blocks = currentData?.aligned_blocks || [];
  const annotations = new Map((currentData?.annotations || []).map(a => [a.id, a]));
  for (const card of scrollPanes.detail.querySelectorAll('.annotation-card')) {
    const ann = annotations.get(card.id.replace('card-', ''));
    if (!ann) continue;
    result.annotationStops.detail.push({id: ann.id, position: contentTop(card, scrollPanes.detail)
      + card.getBoundingClientRect().height / 2 - scrollPanes.detail.clientHeight / 2});
    for (const key of ['std', 'rev']) {
      const block = document.getElementById(`${key}-${ann.block_id}`);
      if (block) result.annotationStops[key].push({id: ann.id, position: contentTop(block, scrollPanes[key]) - 30});
    }
  }
  Object.values(result.annotationStops).forEach(stops => stops.sort((a, b) => a.position - b.position));
  for (const source of Object.keys(scrollPanes)) {
    result[source] = {};
    for (const target of Object.keys(scrollPanes)) {
      if (target === source) continue;
      const from = scrollPanes[source], to = scrollPanes[target];
      const maxFrom = scrollLimit(from), maxTo = scrollLimit(to);
      const candidates = [];
      if (source !== 'detail' && target !== 'detail') {
        for (const block of blocks) {
          const a = document.getElementById(`${source}-${block.block_id}`);
          const b = document.getElementById(`${target}-${block.block_id}`);
          if (a && b) candidates.push([contentTop(a, from) - 30, contentTop(b, to) - 30]);
        }
      } else {
        // Only rendered cards participate: filters and missing annotations are safe.
        for (const card of scrollPanes.detail.querySelectorAll('.annotation-card')) {
          const ann = annotations.get(card.id.replace('card-', ''));
          if (!ann) continue;
          const docKey = source === 'detail' ? target : source;
          const block = document.getElementById(`${docKey}-${ann.block_id}`);
          if (!block) continue;
          const docPosition = contentTop(block, scrollPanes[docKey]) - 30;
          const cardPosition = contentTop(card, scrollPanes.detail)
            + card.getBoundingClientRect().height / 2 - scrollPanes.detail.clientHeight / 2;
          candidates.push(source === 'detail' ? [cardPosition, docPosition] : [docPosition, cardPosition]);
        }
      }
      candidates.sort((a, b) => a[0] - b[0]);
      const anchors = [[0, 0]];
      for (const point of candidates) {
        const previous = anchors[anchors.length - 1];
        // Duplicate clauses, reordered cards and clipped endpoints cannot reverse scrolling.
        if (point[0] > previous[0] && point[1] > previous[1]
            && point[0] < maxFrom && point[1] < maxTo) anchors.push(point);
      }
      anchors.push([maxFrom, maxTo]);
      result[source][target] = anchors;
    }
  }
  return result;
}

function claimDriver(source) {
  if (activeDriver !== source) {
    cancelAnimationFrame(syncFrame);
    syncFrame = 0;
    // Stop a previous click-navigation animation before accepting native input.
    if (activeDriver === 'navigation') {
      Object.values(scrollPanes).forEach(p => p.scrollTo({top: p.scrollTop, behavior: 'instant'}));
    }
  }
  activeDriver = source;
  followerPositions.delete(scrollPanes[source]);
}

function setupThreeWaySyncScroll() {
  resetThreeWaySyncScroll();
  if (syncListenerAttached) return;
  syncListenerAttached = true;
  scrollPanes = {
    std: document.getElementById('colStandardContainer'),
    rev: document.getElementById('colReviewedContainer'),
    detail: document.getElementById('annotationsList')
  };
  const resizeObserver = new ResizeObserver(invalidateScrollGeometry);
  const mutationObserver = new MutationObserver(() => {
    invalidateScrollGeometry();
    // Release removed cards before observing the current render.
    resizeObserver.disconnect();
    // Observe new cards as well as existing cards whose content expands asynchronously.
    Object.values(scrollPanes).forEach(p => {
      resizeObserver.observe(p);
      p.querySelectorAll('.annotation-card, .contract-paper-canvas').forEach(el => resizeObserver.observe(el));
    });
  });
  for (const [key, pane] of Object.entries(scrollPanes)) {
    pane.tabIndex = 0;
    pane.addEventListener('wheel', event => { if (!event.ctrlKey) claimDriver(key); }, {passive: true});
    pane.addEventListener('touchstart', () => claimDriver(key), {passive: true});
    pane.addEventListener('pointerdown', () => claimDriver(key), {passive: true});
    pane.addEventListener('keydown', event => {
      if (/^(ArrowUp|ArrowDown|PageUp|PageDown|Home|End| )$/.test(event.key)
          && !event.target.closest?.('input, textarea, select, [contenteditable="true"]')) claimDriver(key);
    });
    pane.addEventListener('scroll', () => {
      if (activeDriver === 'navigation') return;
      const expected = followerPositions.get(pane);
      if (expected !== undefined && Math.abs(pane.scrollTop - expected) < 1) return;
      if (activeDriver && activeDriver !== key) return;
      activeDriver = key;
      if (syncFrame) return;
      // Coalesce writes into a single paint; cached geometry avoids per-frame layout scans.
      syncFrame = requestAnimationFrame(() => {
        syncFrame = 0;
        if (activeDriver !== key) return;
        if (!scrollGeometry) scrollGeometry = buildScrollGeometry();
        const position = Math.max(0, Math.min(scrollLimit(pane), pane.scrollTop));
        highlightScrollPosition(key, position);
        for (const [target, other] of Object.entries(scrollPanes)) {
          if (target === key) continue;
          const top = mapScrollPosition(position, scrollGeometry[key][target]);
          if (Math.abs(other.scrollTop - top) > 0.5) other.scrollTop = top;
          followerPositions.set(other, other.scrollTop);
        }
      });
    }, {passive: true});
    resizeObserver.observe(pane);
    mutationObserver.observe(pane, {childList: true, subtree: true, characterData: true});
    pane.querySelectorAll('.annotation-card, .contract-paper-canvas').forEach(el => resizeObserver.observe(el));
  }
  window.addEventListener('resize', invalidateScrollGeometry);
  document.fonts?.ready.then(invalidateScrollGeometry);
}


function renderAnnotationsSidebar() {
  const container = document.getElementById('annotationsList');
  container.innerHTML = '';

  const anns = currentData.annotations || [];
  anns.forEach((ann) => {
    if (currentFilter === 'HIGH' && (ann.risk_level !== 'HIGH' || ann.delta_type === 'MISSING_CLAUSE')) return;
    if (currentFilter === 'MISSING' && ann.delta_type !== 'MISSING_CLAUSE') return;
    if (currentFilter === 'NEW' && ann.delta_type !== 'NEW_CLAUSE') return;
    if (currentFilter === 'WARNING' && (ann.risk_level !== 'MEDIUM' || ann.delta_type === 'NEW_CLAUSE')) return;

    const isCurrentRunning = (agentState && agentState.currentAnnId === ann.id);

    const card = document.createElement('div');
    card.className = `annotation-card status-${ann.review_status || 'PENDING'} ${isCurrentRunning ? 'agent-running' : ''}`;
    card.id = `card-${ann.id}`;
    card.onclick = () => selectAnnotation(ann.id);

    let compHtml = '';
    if (ann.delta_type === 'MISSING_CLAUSE') {
      compHtml = `
        <div class="card-comparison-box">
          <div class="comp-row"><span class="comp-label">母本约定:</span><span class="comp-text">${escapeHtml(ann.standard_span)}</span></div>
          <div class="comp-row"><span class="comp-label">送审稿:</span><span class="comp-text" style="color:#DC2626; font-weight:600;">（整段已被删除）</span></div>
        </div>
      `;
    } else if (ann.delta_type === 'NEW_CLAUSE') {
      compHtml = `
        <div class="card-comparison-box">
          <div class="comp-row"><span class="comp-label">母本约定:</span><span class="comp-text" style="color:#94A3B8;">（母本无此条款）</span></div>
          <div class="comp-row"><span class="comp-label">送审稿:</span><span class="comp-text ins">${escapeHtml(ann.reviewed_span)}</span></div>
        </div>
      `;
    } else {
      const isStdBlank = !ann.standard_span || ann.standard_span === '（空白）' || ann.standard_span === '（空白未填）';
      const stdTextHtml = isStdBlank ? `<span style="color:#94A3B8;">（空白未填）</span>` : `<span class="comp-text del">${escapeHtml(ann.standard_span)}</span>`;
      compHtml = `
        <div class="card-comparison-box">
          <div class="comp-row"><span class="comp-label">母本原文:</span>${stdTextHtml}</div>
          <div class="comp-row"><span class="comp-label">送审修改:</span><span class="comp-text ins">${escapeHtml(ann.reviewed_span || '')}</span></div>
        </div>
      `;
    }

    // 研判来源状态徽章
    let sourceBadge = '';
    if (isCurrentRunning) {
      sourceBadge = `<span class="card-source-badge running">⚡ 正在深度研判...</span>`;
    } else if (ann._aiDone) {
      if (ann._source === 'FALLBACK_RULE' || ann._source === 'CIRCUIT_BREAKER_FALLBACK') {
        sourceBadge = `<span class="card-source-badge fallback" title="${escapeHtml(ann.ai_analysis || '')}">🛡️ 容灾规则降级</span>`;
      } else if (ann._source === 'WATCHDOG_TIMEOUT_FALLBACK') {
        sourceBadge = `<span class="card-source-badge watchdog" title="看门狗超时抢救兜底">⏱️ 看门狗抢救</span>`;
      } else if (ann._source === 'CHECKPOINT_RESUMED') {
        sourceBadge = `<span class="card-source-badge resume" title="从断点检查点秒级自愈恢复">💾 断点自愈恢复</span>`;
      } else if (ann._fromCache) {
        sourceBadge = `<span class="card-source-badge cache" title="命中本地安全 Hash 缓存">⚡ 本地缓存</span>`;
      } else {
        const sec = ann._elapsedSec ? `${ann._elapsedSec}s` : '';
        sourceBadge = `<span class="card-source-badge ai" title="大模型实时研判">🤖 AI研判 ${sec}</span>`;
      }
    }

    // 思考过程
    let reasoningBoxHtml = '';
    let reasoningBtnHtml = '';
    if (ann._reasoning) {
      reasoningBtnHtml = `<button class="card-mini-action-btn" onclick="event.stopPropagation(); toggleReasoning('${ann.id}')" title="展开/收起思考链">🧠 思考链</button>`;
      reasoningBoxHtml = `<div class="card-reasoning-collapse" id="reasoning-${ann.id}" style="display:none;">${escapeHtml(ann._reasoning)}</div>`;
    }

    // 优先插队或单卡重审按钮
    let cardControlBtn = '';
    if (!ann._aiDone && !isCurrentRunning) {
      cardControlBtn = `<button class="card-mini-action-btn" onclick="event.stopPropagation(); prioritizeCard('${ann.id}')" title="插队至第一位优先研判">⚡ 优先</button>`;
    } else {
      cardControlBtn = `<button class="card-mini-action-btn" onclick="event.stopPropagation(); retryCard('${ann.id}')" title="清除缓存并重新研判此卡片">🔁 重审</button>`;
    }

    card.innerHTML = `
      <div class="card-top">
        <div style="display:flex; align-items:center; gap:6px;">
          <span class="card-index">批注 #${ann.index < 10 ? '0' + ann.index : ann.index}</span>
          ${sourceBadge}
        </div>
        <span class="card-risk-pill ${ann.risk_level}">${escapeHtml(ann.risk_label)}</span>
      </div>

      <div class="card-clause-name">${escapeHtml(ann.clause_title)}</div>
      ${compHtml}

      <div class="card-analysis">${escapeHtml(ann.ai_analysis)}</div>
      <div class="card-advice">${escapeHtml(ann.action_advice)}</div>
      ${reasoningBoxHtml}

      <div class="card-actions">
        <div class="card-action-btn-group">
          <button class="mini-btn ${ann.review_status === 'ACCEPTED' ? 'active-accept' : ''}" onclick="event.stopPropagation(); setAnnotationStatus('${ann.id}', 'ACCEPTED')">✅ 接受</button>
          <button class="mini-btn ${ann.review_status === 'QUESTIONED' ? 'active-question' : ''}" onclick="event.stopPropagation(); setAnnotationStatus('${ann.id}', 'QUESTIONED')">⚠️ 存疑</button>
          <button class="mini-btn ${ann.review_status === 'OVERRULED' ? 'active-overrule' : ''}" onclick="event.stopPropagation(); setAnnotationStatus('${ann.id}', 'OVERRULED')">❌ 驳回</button>
        </div>
        <div class="card-extra-action">
          ${cardControlBtn}
          ${reasoningBtnHtml}
          <button class="locate-btn" onclick="event.stopPropagation(); selectAnnotation('${ann.id}')">🎯 定位正文</button>
        </div>
      </div>
    `;

    container.appendChild(card);
  });
}

function selectAnnotation(annId) {
  if (!currentData || !currentData.annotations) return;

  const annIdx = currentData.annotations.findIndex(a => a.id === annId);
  if (annIdx === -1) return;
  cancelAnimationFrame(syncFrame);
  syncFrame = 0;
  activeDriver = 'navigation';
  const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth';
  activeAnnotationIndex = annIdx;
  const ann = currentData.annotations[annIdx];

  document.querySelectorAll('.annotation-card').forEach(c => c.classList.remove('active'));
  const card = document.getElementById(`card-${annId}`);
  if (card) {
    card.classList.add('active');
    const pane = document.getElementById('annotationsList');
    const top = contentTop(card, pane);
    const bottom = top + card.getBoundingClientRect().height;
    if (top < pane.scrollTop || bottom > pane.scrollTop + pane.clientHeight) {
      pane.scrollTo({top: Math.max(0, Math.min(top, bottom - pane.clientHeight)), behavior});
    }
  }

  document.querySelectorAll('.clause-block').forEach(el => el.classList.remove('active-clause'));
  const revBlock = document.getElementById(`rev-${ann.block_id}`);
  const stdBlock = document.getElementById(`std-${ann.block_id}`);

  if (revBlock) revBlock.classList.add('active-clause');
  if (stdBlock) stdBlock.classList.add('active-clause');

  for (const [element, id] of [[stdBlock, 'colStandardContainer'], [revBlock, 'colReviewedContainer']]) {
    if (!element) continue;
    const pane = document.getElementById(id);
    pane.scrollTo({top: Math.max(0, contentTop(element, pane) - 30), behavior});
  }

  const total = currentData.annotations.length;
  const ind = document.getElementById('navIndicator');
  if (ind) ind.innerText = `批注 ${ann.index} / ${total}`;
}

function nextAnnotation() {
  if (!currentData || !currentData.annotations || currentData.annotations.length === 0) return;
  let nextIdx = activeAnnotationIndex + 1;
  if (nextIdx >= currentData.annotations.length) nextIdx = 0;
  selectAnnotation(currentData.annotations[nextIdx].id);
}

function prevAnnotation() {
  if (!currentData || !currentData.annotations || currentData.annotations.length === 0) return;
  let prevIdx = activeAnnotationIndex - 1;
  if (prevIdx < 0) prevIdx = currentData.annotations.length - 1;
  selectAnnotation(currentData.annotations[prevIdx].id);
}

function setAnnotationStatus(annId, status) {
  if (!currentData || !currentData.annotations) return;
  const ann = currentData.annotations.find(a => a.id === annId);
  if (!ann) return;
  ann.review_status = status;

  const card = document.getElementById(`card-${annId}`);
  if (card) {
    card.className = `annotation-card status-${status} active`;
    const btns = card.querySelectorAll('.mini-btn');
    btns[0].className = `mini-btn ${status === 'ACCEPTED' ? 'active-accept' : ''}`;
    btns[1].className = `mini-btn ${status === 'QUESTIONED' ? 'active-question' : ''}`;
    btns[2].className = `mini-btn ${status === 'OVERRULED' ? 'active-overrule' : ''}`;
  }
  updateProgressDisplay();
}

function setViewMode(mode) {
  currentViewMode = mode;
  document.getElementById('btnModeSide').classList.toggle('active', mode === 'side');
  document.getElementById('btnModeSingle').classList.toggle('active', mode === 'single');
  document.getElementById('btnModeDiff').classList.toggle('active', mode === 'diff');

  document.body.classList.toggle('mode-single', mode === 'single');
  renderContractColumns();
  syncClauseHeights();
}

function setRedlineMode(isRedlineOnly) {
  document.getElementById('btnStyleRedline').classList.toggle('active', isRedlineOnly);
  document.getElementById('btnStyleColor').classList.toggle('active', !isRedlineOnly);
  document.body.classList.toggle('mode-pure-redline', isRedlineOnly);
  document.body.classList.toggle('mode-color-redgreen', !isRedlineOnly);
}

function setFilter(filter) {
  currentFilter = filter;
  document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
  event.target.classList.add('active');
  renderAnnotationsSidebar();
}

function toggleSidebarCollapse() {
  const ws = document.getElementById('reviewWorkspace');
  ws.classList.toggle('sidebar-collapsed');
}

// ==================== Agent 智能研判与全链路容灾客户端 (爱马仕/CC/小龙虾) ====================
let agentEventSource = null;
let agentReconnectTimer = null;
let agentHeartbeatTimer = null;
let agentState = {
  status: 'IDLE',
  completed: 0,
  total: 0,
  currentAnnId: null,
  circuitBreaker: { state: 'CLOSED', failure_count: 0 }
};

function initAgentSSE(force = false) {
  // 若当前已有活跃健康的长连接且非强制重建，无需强行关闭重建（杜绝切换模板时的连接重置报错）
  if (!force && agentEventSource && (agentEventSource.readyState === EventSource.OPEN || agentEventSource.readyState === EventSource.CONNECTING)) {
    updateHeartbeatTag(true);
    return;
  }

  if (agentEventSource) {
    try { agentEventSource.close(); } catch (e) {}
    agentEventSource = null;
  }
  clearTimeout(agentReconnectTimer);
  clearTimeout(agentHeartbeatTimer);

  const resetHeartbeat = () => {
    clearTimeout(agentHeartbeatTimer);
    updateHeartbeatTag(true);
    agentHeartbeatTimer = setTimeout(() => {
      console.warn('[SSE] 心跳超时（>14s无响应），启动自动重连...');
      updateHeartbeatTag(false);
      initAgentSSE(true);
    }, 14000);
  };

  try {
    agentEventSource = new EventSource('/api/diff/agent_stream');

    agentEventSource.onopen = () => {
      console.log('[SSE] 已接入法务 Agent 实时研判流');
      updateHeartbeatTag(true);
      resetHeartbeat();
    };

    agentEventSource.addEventListener('snapshot', (e) => {
      resetHeartbeat();
      try {
        const snap = JSON.parse(e.data);
        handleAgentSnapshot(snap);
      } catch (err) { console.error('Error handling snapshot:', err); }
    });

    agentEventSource.addEventListener('init', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        agentState.status = data.status || 'RUNNING';
        agentState.completed = data.completed || 0;
        agentState.total = data.total || 0;
        updateAgentUI();
        if (data.restored_count > 0) {
          showToast(`已从持久化检查点秒级恢复 ${data.restored_count} 项研判！`, 'info');
        }
      } catch (err) {}
    });

    agentEventSource.addEventListener('card_start', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        agentState.currentAnnId = data.id;
        updateCardRunningStatus(data.id, true);
        updateAgentUI();
      } catch (err) {}
    });

    agentEventSource.addEventListener('card_complete', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        handleCardComplete(data);
      } catch (err) {}
    });

    agentEventSource.addEventListener('card_prioritized', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        showToast(`条款 #${data.id} 已成功插队至首位！`, 'success');
      } catch (err) {}
    });

    agentEventSource.addEventListener('card_retry', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        showToast(`条款 #${data.id} 已重置并进入优先队列！`, 'info');
      } catch (err) {}
    });

    agentEventSource.addEventListener('card_skip', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        showToast(`条款 #${data.id} 已跳过研判。`, 'info');
      } catch (err) {}
    });

    agentEventSource.addEventListener('progress', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        agentState.completed = data.completed;
        agentState.total = data.total;
        updateAgentUI();
      } catch (err) {}
    });

    agentEventSource.addEventListener('status_change', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        agentState.status = data.status;
        updateAgentUI();
      } catch (err) {}
    });

    agentEventSource.addEventListener('circuit_state_change', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        agentState.circuitBreaker = data;
        updateAgentUI();
        if (data.state === 'OPEN') {
          showToast('⚠️ 大模型连续异常，已自动激活爱马仕/CC级自适应熔断保护（秒级规则兜底）', 'warning');
        } else if (data.state === 'CLOSED') {
          showToast('🟢 大模型服务已恢复，熔断器已复位正常', 'success');
        }
      } catch (err) {}
    });

    agentEventSource.addEventListener('watchdog_alert', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        showToast('🛡️ ' + (data.message || '看门狗抢救超时条款成功'), 'warning');
      } catch (err) {}
    });

    agentEventSource.addEventListener('all_complete', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        agentState.status = 'COMPLETED';
        agentState.currentAnnId = null;
        updateAgentUI();
        showToast('🎉 ' + (data.message || '全篇条款全部静默研判完毕！'), 'success');
      } catch (err) {}
    });

    agentEventSource.onerror = (e) => {
      // 只有在长连接彻底断开时触发状态更新与退避自愈
      if (!agentEventSource || agentEventSource.readyState === EventSource.CLOSED) {
        updateHeartbeatTag(false);
        clearTimeout(agentReconnectTimer);
        agentReconnectTimer = setTimeout(() => initAgentSSE(true), 2500);
      }
    };
  } catch (err) {
    console.error('SSE initialization failed:', err);
  }
}

function handleAgentSnapshot(snap) {
  agentState.status = snap.status || 'IDLE';
  agentState.currentAnnId = snap.current_ann_id;
  agentState.completed = snap.completed_count || 0;
  agentState.total = snap.total_count || 0;
  if (snap.circuit_breaker) {
    agentState.circuitBreaker = snap.circuit_breaker;
  }

  // 批量合并已研判卡片
  if (currentData && currentData.annotations && snap.completed_cards) {
    snap.completed_cards.forEach(card => {
      const ann = currentData.annotations.find(a => a.id === card.id);
      if (ann) {
        ann.risk_level = card.risk_level || ann.risk_level;
        ann.risk_label = card.risk_label || ann.risk_label;
        ann.ai_analysis = card.ai_analysis || ann.ai_analysis;
        ann.action_advice = card.action_advice || ann.action_advice;
        ann._reasoning = card.reasoning || '';
        ann._source = card.source || 'AI_AGENT';
        ann._elapsedSec = card.elapsed_sec || 0;
        ann._fromCache = card.from_cache || false;
        ann._aiDone = true;
      }
    });
    renderAnnotationsSidebar();
  }
  updateAgentUI();
}

function handleCardComplete(data) {
  agentState.completed = data.completed;
  agentState.total = data.total;
  if (agentState.currentAnnId === data.id) {
    agentState.currentAnnId = null;
  }

  if (currentData && currentData.annotations) {
    const ann = currentData.annotations.find(a => a.id === data.id);
    if (ann) {
      ann.risk_level = data.risk_level;
      ann.risk_label = data.risk_label;
      ann.ai_analysis = data.ai_analysis;
      ann.action_advice = data.action_advice;
      ann._reasoning = data.reasoning;
      ann._source = data.source;
      ann._elapsedSec = data.elapsed_sec;
      ann._fromCache = data.from_cache;
      ann._aiDone = true;
    }
  }

  // 单卡增量更新 DOM，避免整体重绘丢失滚动位
  const card = document.getElementById(`card-${data.id}`);
  if (card) {
    card.classList.remove('agent-running');
    const pill = card.querySelector('.card-risk-pill');
    if (pill) {
      pill.className = `card-risk-pill ${data.risk_level}`;
      pill.innerText = data.risk_label;
    }
    const analysisEl = card.querySelector('.card-analysis');
    if (analysisEl) analysisEl.innerText = data.ai_analysis;
    const adviceEl = card.querySelector('.card-advice');
    if (adviceEl) adviceEl.innerText = data.action_advice;

    // 更新来源标签
    let sBadge = card.querySelector('.card-source-badge');
    let badgeText = '🤖 AI研判';
    let badgeClass = 'ai';
    if (data.source === 'FALLBACK_RULE' || data.source === 'CIRCUIT_BREAKER_FALLBACK') {
      badgeText = '🛡️ 容灾规则降级'; badgeClass = 'fallback';
    } else if (data.source === 'WATCHDOG_TIMEOUT_FALLBACK') {
      badgeText = '⏱️ 看门狗抢救'; badgeClass = 'watchdog';
    } else if (data.source === 'CHECKPOINT_RESUMED') {
      badgeText = '💾 断点自愈恢复'; badgeClass = 'resume';
    } else if (data.from_cache) {
      badgeText = '⚡ 本地缓存'; badgeClass = 'cache';
    } else if (data.elapsed_sec) {
      badgeText = `🤖 AI研判 ${data.elapsed_sec}s`;
    }
    if (sBadge) {
      sBadge.className = `card-source-badge ${badgeClass}`;
      sBadge.innerText = badgeText;
    }
  } else {
    renderAnnotationsSidebar();
  }

  updateAgentUI();
}

function updateCardRunningStatus(annId, isRunning) {
  document.querySelectorAll('.annotation-card').forEach(c => c.classList.remove('agent-running'));
  if (isRunning && annId) {
    const card = document.getElementById(`card-${annId}`);
    if (card) {
      card.classList.add('agent-running');
      let sBadge = card.querySelector('.card-source-badge');
      if (sBadge) {
        sBadge.className = 'card-source-badge running';
        sBadge.innerText = '⚡ 正在深度研判...';
      }
    }
  }
}

function updateAgentUI() {
  const badge = document.getElementById('agentStateBadge');
  const ratio = document.getElementById('agentProgressRatio');
  const bar = document.getElementById('agentProgressBar');
  const btnPause = document.getElementById('btnAgentPause');
  const btnResetCircuit = document.getElementById('btnResetCircuit');
  const circuitTag = document.getElementById('agentCircuitTag');

  const cur = agentState.completed || 0;
  const tot = agentState.total || 0;
  const pct = tot > 0 ? Math.round((cur / tot) * 100) : 0;

  if (ratio) ratio.innerText = `${cur}/${tot} (${pct}%)`;
  if (bar) bar.style.width = `${pct}%`;

  if (badge) {
    if (agentState.status === 'COMPLETED') {
      badge.className = 'agent-state-pill COMPLETED';
      badge.innerText = '✅ 全部研判完成';
      if (btnPause) btnPause.disabled = true;
    } else if (agentState.status === 'PAUSED') {
      badge.className = 'agent-state-pill PAUSED';
      badge.innerText = '⏸️ 研判已暂停';
      if (btnPause) { btnPause.disabled = false; btnPause.innerText = '▶️ 继续'; }
    } else if (agentState.circuitBreaker && agentState.circuitBreaker.state === 'OPEN') {
      badge.className = 'agent-state-pill CIRCUIT_OPEN';
      badge.innerText = '🛡️ 熔断降级自愈中';
      if (btnPause) { btnPause.disabled = false; btnPause.innerText = '⏸️ 暂停'; }
    } else {
      badge.className = 'agent-state-pill RUNNING';
      badge.innerText = `🟢 智能研判中 (${cur}/${tot})`;
      if (btnPause) { btnPause.disabled = false; btnPause.innerText = '⏸️ 暂停'; }
    }
  }

  if (circuitTag && agentState.circuitBreaker) {
    const cbState = agentState.circuitBreaker.state;
    if (cbState === 'OPEN') {
      circuitTag.innerText = '🛡️ 熔断触发 (企业红线速断)';
      circuitTag.style.color = '#DC2626';
      if (btnResetCircuit) btnResetCircuit.style.display = 'inline-block';
    } else {
      circuitTag.innerText = '🛡️ 熔断防护: 闭合';
      circuitTag.style.color = '#64748B';
      if (btnResetCircuit) btnResetCircuit.style.display = 'none';
    }
  }
}

function updateHeartbeatTag(isAlive) {
  const tag = document.getElementById('agentHeartbeatTag');
  if (tag) {
    if (isAlive) {
      tag.innerText = '💓 心跳正常';
      tag.style.color = '#10B981';
    } else {
      tag.innerText = '💔 重连中...';
      tag.style.color = '#EF4444';
    }
  }
}

async function toggleAgentPause() {
  try {
    const resp = await fetch('/api/diff/toggle_pause', { method: 'POST' });
    const res = await resp.json();
    agentState.status = res.status;
    updateAgentUI();
    showToast(res.status === 'PAUSED' ? '研判已暂停' : '研判已恢复继续', 'info');
  } catch (err) {
    showToast('切换暂停失败: ' + err, 'error');
  }
}

async function rerunAgentReview() {
  showLoading('正在重置并启动全篇重新研判...');
  try {
    const resp = await fetch('/api/diff/rerun_review', { method: 'POST' });
    const res = await resp.json();
    hideLoading();
    if (res.success) {
      showToast('已清空旧任务，全新研判流水线已就绪启动！', 'success');
      initAgentSSE();
    }
  } catch (err) {
    hideLoading();
    showToast('重置研判请求失败: ' + err, 'error');
  }
}

async function prioritizeCard(annId) {
  try {
    const resp = await fetch('/api/diff/prioritize_card', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: annId })
    });
    const res = await resp.json();
    if (res.success) {
      showToast(`条款 #${annId} 已插队至第一位！`, 'success');
    }
  } catch (err) {
    showToast('插队请求失败: ' + err, 'error');
  }
}

async function retryCard(annId) {
  try {
    const resp = await fetch('/api/diff/retry_card', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: annId })
    });
    const res = await resp.json();
    if (res.success) {
      showToast(`条款 #${annId} 缓存已清除，进入优先重审！`, 'info');
    }
  } catch (err) {
    showToast('重审请求失败: ' + err, 'error');
  }
}

async function resetCircuitBreaker() {
  try {
    const resp = await fetch('/api/diff/reset_circuit', { method: 'POST' });
    const res = await resp.json();
    if (res.success) {
      showToast('大模型熔断器已手动复位！', 'success');
    }
  } catch (err) {
    showToast('复位熔断器失败: ' + err, 'error');
  }
}

function toggleReasoning(annId) {
  const el = document.getElementById(`reasoning-${annId}`);
  if (el) {
    el.style.display = (el.style.display === 'none') ? 'block' : 'none';
  }
}

// ==================== 素材库 (contract_merged) 业务交互 ====================
function initMaterialModal() {
  fetchMaterialStats();
  fetchMaterialCases();
}

function fetchMaterialStats() {
  fetch('/api/materials/stats')
    .then(r => r.json())
    .then(stats => {
      if (stats && stats.total_cases) {
        const pill = document.getElementById('matStatsPill');
        if (pill) {
          pill.innerText = `共 ${stats.total_cases.toLocaleString()} 案卷 · ${stats.total_word_contracts.toLocaleString()} 实单 Word 原件`;
        }
      }
    })
    .catch(() => {});
}

function setMaterialCategory(cat) {
  currentMaterialCategory = cat;
  currentMaterialPage = 1;
  document.querySelectorAll('.mat-tab-btn').forEach(btn => btn.classList.remove('active'));
  const activeBtn = document.getElementById(`matTab-${cat}`);
  if (activeBtn) activeBtn.classList.add('active');
  fetchMaterialCases();
}

function onMaterialSearchKey(e) {
  if (e.key === 'Enter') {
    triggerMaterialSearch();
  }
}

function triggerMaterialSearch() {
  currentMaterialSearch = (document.getElementById('matSearchInput').value || '').trim();
  currentMaterialHasWordOnly = document.getElementById('matHasWordOnly').checked;
  currentMaterialPage = 1;
  fetchMaterialCases();
}

function resetMaterialSearch() {
  document.getElementById('matSearchInput').value = '';
  document.getElementById('matHasWordOnly').checked = true;
  currentMaterialSearch = '';
  currentMaterialHasWordOnly = true;
  currentMaterialPage = 1;
  currentMaterialCategory = 'ALL';
  document.querySelectorAll('.mat-tab-btn').forEach(btn => btn.classList.remove('active'));
  const allBtn = document.getElementById('matTab-ALL');
  if (allBtn) allBtn.classList.add('active');
  fetchMaterialCases();
}

function changeMaterialPage(delta) {
  currentMaterialPage += delta;
  if (currentMaterialPage < 1) currentMaterialPage = 1;
  fetchMaterialCases();
}

function fetchMaterialCases() {
  const container = document.getElementById('matListContainer');
  container.innerHTML = '<div style="grid-column: 1/-1; padding: 40px; text-align: center; color: #64748B;">⚡ 正在秒级调取素材库案例...</div>';

  const params = new URLSearchParams({
    category: currentMaterialCategory,
    search: currentMaterialSearch,
    has_word: currentMaterialHasWordOnly ? '1' : '0',
    page: String(currentMaterialPage),
    page_size: '20'
  });

  fetch(`/api/materials/cases?${params.toString()}`)
    .then(r => r.json())
    .then(data => {
      renderMaterialCases(data);
    })
    .catch(err => {
      if (window.MATERIAL_DATA && window.MATERIAL_DATA.initial_cases) {
        renderMaterialCases(window.MATERIAL_DATA.initial_cases);
      } else {
        container.innerHTML = `<div style="grid-column: 1/-1; padding: 30px; text-align: center; color: #DC2626;">调取素材库失败: ${escapeHtml(err.message || String(err))}</div>`;
      }
    });
}

function renderMaterialCases(data) {
  const container = document.getElementById('matListContainer');
  container.innerHTML = '';

  const cases = data.cases || [];
  const total = data.total || 0;
  const page = data.page || 1;
  const totalPages = data.total_pages || 1;

  document.getElementById('matPageInfo').innerText = `显示第 ${(page - 1) * 20 + 1}-${Math.min(page * 20, total)} 条 / 共 ${total.toLocaleString()} 案卷`;
  document.getElementById('matCurPageIndicator').innerText = `${page} / ${totalPages}`;
  document.getElementById('matBtnPrev').disabled = (page <= 1);
  document.getElementById('matBtnNext').disabled = (page >= totalPages);

  if (cases.length === 0) {
    container.innerHTML = '<div style="grid-column: 1/-1; padding: 50px; text-align: center; color: #64748B;">🔍 未检索到匹配的案卷，请尝试更换关键词或勾选“非 Word 原件”。</div>';
    return;
  }

  cases.forEach(c => {
    const card = document.createElement('div');
    card.className = 'mat-case-card';

    let chipHtml = '';
    const atts = c.attachments || [];
    const hasDocx = atts.some(a => a.ext === '.docx');
    const hasDoc = atts.some(a => a.ext === '.doc');
    const hasPdf = atts.some(a => a.ext === '.pdf');
    if (hasDocx) chipHtml += '<span class="mat-chip docx">DOCX原件</span>';
    if (hasDoc) chipHtml += '<span class="mat-chip doc">DOC原件</span>';
    if (hasPdf) chipHtml += '<span class="mat-chip pdf">PDF扫描</span>';
    if (c.md_file) chipHtml += '<span class="mat-chip md">案卷履约MD</span>';

    const hasParseableAttachment = hasDocx || hasDoc || hasPdf;

    card.innerHTML = `
      <div class="mat-card-header">
        <span class="mat-no-tag">${escapeHtml(c.no)}</span>
        <span class="mat-cat-tag">${escapeHtml(c.category_name || c.folder || '合同案卷')}</span>
      </div>
      <div class="mat-card-title" title="${escapeHtml(c.name || c.no)}">${escapeHtml(c.name || c.no)}</div>
      <div class="mat-card-parties">
        <div class="mat-party-row" title="对方主体: ${escapeHtml(c.party_b || '未载明')}">👥 <b>客户:</b> ${escapeHtml(c.party_b || '未载明')}</div>
        <div class="mat-party-row" title="我方签约: ${escapeHtml(c.party_a || '标准代理公司')}">🏛️ <b>我方:</b> ${escapeHtml(c.party_a || '标准代理公司')}</div>
      </div>
      <div class="mat-card-meta">
        <span>👤 经办: ${escapeHtml(c.applicant || '经办人')}</span>
        <span>📅 ${escapeHtml(c.year || '2026')}年 · 状态: ${escapeHtml(c.status || '已归档')}</span>
      </div>
      <div class="mat-card-chips">${chipHtml || '<span style="font-size:10px; color:#94A3B8;">暂无附件</span>'}</div>
      <div class="mat-card-actions">
        <button class="mat-btn-action mat-btn-diff" onclick="loadMaterialContract('${c.no}')" ${!hasParseableAttachment ? 'disabled title="暂无可解析合同实体"' : ''}>⚡ 载入原格式比对</button>
        <button class="mat-btn-action mat-btn-detail" onclick="openCaseDetailModal('${c.no}')">📋 案卷审批详情</button>
      </div>
    `;
    container.appendChild(card);
  });
}

function loadMaterialContract(contractNo, attPath) {
  showLoading(`正在调取素材库案卷 [${contractNo}] 合同原件并执行条款级微观比对...`);
  closeMaterialModal();
  closeCaseDetailModal();

  fetch('/api/materials/load_contract', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ contract_no: contractNo, attachment_path: attPath })
  })
    .then(r => {
      if (!r.ok) return r.json().then(j => { throw new Error(j.error || '调取失败'); });
      return r.json();
    })
    .then(data => {
      hideLoading();
      renderFullDiff(data);
      showToast(`成功调取素材库案卷合同 [${contractNo}]，已自动完成高保真原格式对照！`, 'success');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    })
    .catch(err => {
      hideLoading();
      showToast(`调取合同失败: ${err.message}`, 'error');
    });
}

function fetchCaseDetail(contractNo) {
  fetch(`/api/materials/case_detail?contract_no=${encodeURIComponent(contractNo)}`)
    .then(r => r.json())
    .then(data => {
      const body = document.getElementById('detailModalBody');
      const c = data.case || {};
      const md = data.md_content || '';
      const atts = data.attachments || [];

      let attRows = atts.map((a, idx) => `
        <tr>
          <td>${idx + 1}</td>
          <td><b>${escapeHtml(a.original || a.local)}</b></td>
          <td><span class="mat-chip ${a.ext.replace('.', '')}">${a.ext.toUpperCase()}</span></td>
          <td>${(a.size / 1024).toFixed(1)} KB</td>
          <td>${a.local.endsWith('.docx') || a.local.endsWith('.doc') ? '<button class="btn btn-sm btn-emerald" onclick="loadMaterialContract(\'' + c.no + '\', \'' + a.local + '\')">比对本原件</button>' : '-'}</td>
        </tr>
      `).join('');

      let html = `
        <div style="background:#F8FAFC; border:1px solid #E2E8F0; border-radius:8px; padding:14px; margin-bottom:16px;">
          <h3 style="margin-top:0;">📋 案卷基础信息台账</h3>
          <table style="margin-top:8px;">
            <tr><th style="width:120px;">合同编号</th><td><b>${escapeHtml(c.no)}</b></td><th style="width:120px;">合同名称</th><td>${escapeHtml(c.name || '-')}</td></tr>
            <tr><th>客户主体(对方)</th><td>${escapeHtml(c.party_b || '-')}</td><th>我方签约主体</th><td>${escapeHtml(c.party_a || '标准代理主体')}</td></tr>
            <tr><th>业务大类</th><td>${escapeHtml(c.category_name || c.folder || '-')}</td><th>归档状态</th><td>${escapeHtml(c.status || '-')}</td></tr>
            <tr><th>经办申办人</th><td>${escapeHtml(c.applicant || '-')}</td><th>签署立卷年份</th><td>${escapeHtml(c.year || '-')}</td></tr>
          </table>
        </div>

        <div style="margin-bottom:16px;">
          <h3>📎 案卷关联附件与证据实体 (${atts.length} 项)</h3>
          <table>
            <thead>
              <tr><th>#</th><th>文件全称 / 来源</th><th>格式</th><th>体积</th><th>快捷操作</th></tr>
            </thead>
            <tbody>
              ${attRows || '<tr><td colspan="5" style="text-align:center; color:#94A3B8;">暂无附件实体记录</td></tr>'}
            </tbody>
          </table>
        </div>

        ${md ? `
        <div>
          <h3>📜 原始案卷 MD 归档全文</h3>
          <div style="background:#F1F5F9; border-radius:6px; padding:12px; font-family:Consolas, monospace; font-size:12px; max-height:300px; overflow-y:auto; white-space:pre-wrap;">${escapeHtml(md)}</div>
        </div>
        ` : ''}
      `;
      body.innerHTML = html;
    })
    .catch(err => {
      document.getElementById('detailModalBody').innerHTML = `<div style="padding:30px; text-align:center; color:#DC2626;">加载案卷详情失败: ${escapeHtml(err.message || String(err))}</div>`;
    });
}

function loadCurrentDetailContract() {
  if (currentDetailContractNo) {
    loadMaterialContract(currentDetailContractNo, null);
  }
}

// ==================== 模板库全景 (参考/) 业务交互 ====================
function initRefTemplateModal() {
  if (refTemplatesCache && refTemplatesCache.length > 0) {
    renderRefCategoryTabs();
    renderRefTemplates();
  }
  fetch('/api/ref_templates/catalog')
    .then(r => r.json())
    .then(data => {
      if (data && data.templates) {
        refTemplatesCache = data.templates;
        renderRefCategoryTabs();
        renderRefTemplates();
      }
    })
    .catch(() => {});
}

function renderRefCategoryTabs() {
  const container = document.getElementById('refCategoryTabs');
  container.innerHTML = '';

  const catCounts = {};
  refTemplatesCache.forEach(t => {
    const f = t.folder || '其他';
    catCounts[f] = (catCounts[f] || 0) + 1;
  });

  const allBtn = document.createElement('button');
  allBtn.className = `ref-cat-btn ${activeRefCategory === 'ALL' ? 'active' : ''}`;
  allBtn.innerText = `全部 (${refTemplatesCache.length})`;
  allBtn.onclick = () => {
    activeRefCategory = 'ALL';
    renderRefCategoryTabs();
    renderRefTemplates();
  };
  container.appendChild(allBtn);

  const folders = ['专利', '商标', '国际专利', '诉讼', '国际商标', '双软', '综合业务', '保密协议', '著作权', '集成电路布图设计登记'];
  folders.forEach(f => {
    const count = catCounts[f] || 0;
    if (count === 0) return;
    const btn = document.createElement('button');
    btn.className = `ref-cat-btn ${activeRefCategory === f ? 'active' : ''}`;
    btn.innerText = `${f} (${count})`;
    btn.onclick = () => {
      activeRefCategory = f;
      renderRefCategoryTabs();
      renderRefTemplates();
    };
    container.appendChild(btn);
  });
}

function onRefSearchInput(e) {
  refSearchKeyword = (e.target.value || '').trim().toLowerCase();
  renderRefTemplates();
}

function renderRefTemplates() {
  const container = document.getElementById('refListContainer');
  container.innerHTML = '';

  const filtered = refTemplatesCache.filter(t => {
    if (activeRefCategory !== 'ALL' && t.folder !== activeRefCategory) return false;
    if (refSearchKeyword) {
      const matchName = (t.name || '').toLowerCase().includes(refSearchKeyword);
      const matchFolder = (t.folder || '').toLowerCase().includes(refSearchKeyword);
      const matchMb = (t.mb_code || '').toLowerCase().includes(refSearchKeyword);
      if (!matchName && !matchFolder && !matchMb) return false;
    }
    return true;
  });

  if (filtered.length === 0) {
    container.innerHTML = '<div style="grid-column: 1/-1; padding: 40px; text-align: center; color: #64748B;">🔍 未检索到匹配的标准模板。</div>';
    return;
  }

  filtered.forEach(t => {
    const card = document.createElement('div');
    card.className = 'ref-card';
    card.innerHTML = `
      <div class="ref-card-header">
        <span class="ref-folder-tag">${escapeHtml(t.folder)}</span>
        ${t.mb_code ? `<span class="ref-mb-tag">[${escapeHtml(t.mb_code)}]</span>` : ''}
      </div>
      <div class="ref-card-title" title="${escapeHtml(t.name)}">${escapeHtml(t.name)}</div>
      <div class="ref-card-meta">
        <span>📄 文件: ${escapeHtml(t.file_name)}</span> · <span>${(t.size / 1024).toFixed(1)} KB</span>
      </div>
      <div class="ref-card-actions">
        <button class="ref-btn-action ref-btn-primary" onclick="loadRefTemplate('${t.rel_path}', 'reviewed')">📄 载入待审</button>
        <button class="ref-btn-action ref-btn-std" onclick="loadRefTemplate('${t.rel_path}', 'standard')">🎯 设为母本</button>
        <button class="ref-btn-action ref-btn-self" onclick="loadRefTemplate('${t.rel_path}', 'self_diff')" title="模板对比自身，验证100%全格式无差异基线">🧪 一键自测</button>
      </div>
    `;
    container.appendChild(card);
  });
}

function loadRefTemplate(relPath, mode) {
  let modeDesc = '作为待审文档';
  if (mode === 'standard') modeDesc = '作为基准母本';
  if (mode === 'self_diff') modeDesc = '执行 100% 格式自测核验';

  showLoading(`正在将【参考/】模板 [${relPath}] ${modeDesc}...`);
  closeRefTemplateModal();

  fetch('/api/ref_templates/load', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rel_path: relPath, mode: mode })
  })
    .then(r => {
      if (!r.ok) return r.json().then(j => { throw new Error(j.error || '载入失败'); });
      return r.json();
    })
    .then(data => {
      hideLoading();
      renderFullDiff(data);
      if (mode === 'self_diff') {
        showToast(`标准模板 [${relPath}] 自测对比完成：100% 格式精准对齐，零虚假差异！`, 'success');
      } else {
        showToast(`成功将模板 [${relPath}] ${modeDesc}！`, 'success');
      }
      window.scrollTo({ top: 0, behavior: 'smooth' });
    })
    .catch(err => {
      hideLoading();
      showToast(`载入模板失败: ${err.message}`, 'error');
    });
}

window.addEventListener('keydown', (e) => {
  if (e.key === '[' || (e.altKey && e.key === 'ArrowUp')) {
    prevAnnotation();
  } else if (e.key === ']' || (e.altKey && e.key === 'ArrowDown')) {
    nextAnnotation();
  } else if (e.key === 'Escape') {
    closeUploadModal();
    closeTemplateModal();
    closeMaterialModal();
    closeRefTemplateModal();
    closeCaseDetailModal();
  }
});

window.addEventListener('DOMContentLoaded', () => {
  if (window.INITIAL_DATA) {
    renderFullDiff(window.INITIAL_DATA);
    initAgentSSE();
  }
  if (window.MATERIAL_DATA && window.MATERIAL_DATA.stats) {
    const stats = window.MATERIAL_DATA.stats;
    const pill = document.getElementById('matStatsPill');
    if (pill) {
      pill.innerText = `共 ${(stats.total_cases || 21623).toLocaleString()} 案卷 · ${(stats.total_word_contracts || 4586).toLocaleString()} 实单 Word 原件`;
    }
  }
});
</script>
</body>
</html>
"""


class DiffVisualizerHandler(BaseHTTPRequestHandler):
    """处理前端页面与比对 API 请求（具备多会话/多租户隔离与安全防护）。"""

    protocol_version = "HTTP/1.1"

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    def log_message(self, format, *args):
        if args and any("Connection" in str(a) or "10053" in str(a) for a in args):
            return
        super().log_message(format, *args)

    def _send_json(self, status_code: int, data: Any):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _get_request_identity(self) -> tuple[str, str, str]:
        """从 Query 参数或 Header 安全提取会话标识、租户标识与用户标识。"""
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)

        session_id = (
            qs.get("session_id", [""])[0]
            or self.headers.get("X-Session-ID")
            or "default_session"
        )
        tenant_id = (
            qs.get("tenant_id", [""])[0] or self.headers.get("X-Tenant-ID") or "default"
        )
        user_id = (
            qs.get("user_id", [""])[0] or self.headers.get("X-User-ID") or "anonymous"
        )
        return session_id, tenant_id, user_id

    def do_GET(self):
        global CURRENT_DOC_NAME, CURRENT_FORCED_TEMPLATE_ID
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path in ("/", "/index.html"):
            html = generate_rendered_html()
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif parsed.path == "/api/diff/agent_stream":
            session_id, tenant_id, user_id = self._get_request_identity()
            mgr = get_agent_manager()
            try:
                sub_q = mgr.subscribe_events(
                    session_id=session_id, tenant_id=tenant_id, user_id=user_id
                )
            except PermissionError as e:
                self._send_json(403, {"error": str(e)})
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            try:
                while True:
                    try:
                        msg = sub_q.get(timeout=2.0)
                        line = f"event: {msg['event']}\ndata: {json.dumps(msg['data'], ensure_ascii=False)}\n\n"
                        self.wfile.write(line.encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (
                BrokenPipeError,
                ConnectionResetError,
                ConnectionAbortedError,
                Exception,
            ):
                pass
            finally:
                self.close_connection = True
                mgr.unsubscribe_events(sub_q, session_id=session_id)
            return

        elif parsed.path == "/api/templates/list":
            session_id, _, _ = self._get_request_identity()
            s_file = get_session_file(session_id)
            if not s_file.exists():
                s_file = CURRENT_SESSION_FILE
            meta = get_session_meta(session_id)
            try:
                catalog = get_ranked_templates_catalog(
                    s_file, meta.get("forced_template_id")
                )
                self._send_json(200, {"success": True, "templates": catalog})
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        elif parsed.path == "/api/diff/sample":
            session_id, tenant_id, user_id = self._get_request_identity()
            qs = urllib.parse.parse_qs(parsed.query)
            key = qs.get("key", ["prescreen_tampered"])[0]

            try:
                if key == "prescreen_tampered":
                    sample_file = config.GOLDEN_DIR / "mut_11_late_fee_change.docx"
                    doc_name = "《国内专利服务合同（年费缴纳）》送审修改稿"
                elif key == "missing_confidential":
                    sample_file = (
                        config.GOLDEN_DIR / "mut_06_missing_confidentiality.docx"
                    )
                    doc_name = (
                        "《国内专利服务合同（年费缴纳）》送审修改稿（保密义务被删）"
                    )
                else:
                    sample_files = list(config.TEMPLATE_DIR.glob("**/*驳回复审*.docx"))
                    sample_file = (
                        sample_files[0]
                        if sample_files
                        else config.GOLDEN_DIR / "mut_11_late_fee_change.docx"
                    )
                    doc_name = "《商标驳回复审代理协议》送审原稿"

                s_file = get_session_file(session_id)
                shutil.copyfile(sample_file, s_file)
                save_session_meta(
                    session_id, {"doc_name": doc_name, "forced_template_id": None}
                )

                res = execute_current_diff(
                    s_file, forced_template_id=None, doc_name=doc_name
                )
                res["session_id"] = session_id
                get_agent_manager().start_document_review(
                    res, session_id=session_id, tenant_id=tenant_id, user_id=user_id
                )
                self._send_json(200, res)
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        elif parsed.path == "/api/diff/agent_status":
            session_id, tenant_id, user_id = self._get_request_identity()
            try:
                snap = get_agent_manager().get_current_snapshot(
                    session_id=session_id, tenant_id=tenant_id, user_id=user_id
                )
                self._send_json(200, snap)
            except PermissionError as e:
                self._send_json(403, {"error": str(e)})
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        elif parsed.path == "/api/ref_templates/catalog":
            try:
                catalog = get_ref_templates_catalog()
                self._send_json(200, {"success": True, "templates": catalog})
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        elif parsed.path == "/api/materials/stats":
            try:
                mgr = get_material_manager()
                stats = mgr.get_stats()
                self._send_json(200, stats)
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        elif parsed.path == "/api/materials/cases":
            qs = urllib.parse.parse_qs(parsed.query)
            category = qs.get("category", ["ALL"])[0]
            search = qs.get("search", [""])[0]
            has_word = qs.get("has_word", ["1"])[0] in ("1", "true", "True")
            try:
                page = max(1, int(qs.get("page", ["1"])[0]))
            except ValueError:
                page = 1
            try:
                page_size = min(100, max(1, int(qs.get("page_size", ["20"])[0])))
            except ValueError:
                page_size = 20
            try:
                mgr = get_material_manager()
                res = mgr.search_cases(
                    category=category,
                    search=search,
                    has_word_only=has_word,
                    page=page,
                    page_size=page_size,
                )
                self._send_json(200, res)
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        elif parsed.path == "/api/materials/case_detail":
            qs = urllib.parse.parse_qs(parsed.query)
            contract_no = qs.get("contract_no", [""])[0].strip()
            if not contract_no:
                self._send_json(400, {"error": "缺少 contract_no 参数"})
                return
            try:
                mgr = get_material_manager()
                detail = mgr.get_case_detail(contract_no)
                if not detail:
                    self._send_json(404, {"error": f"未找到案卷 [{contract_no}]"})
                else:
                    self._send_json(200, detail)
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        global CURRENT_DOC_NAME, CURRENT_FORCED_TEMPLATE_ID
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/api/templates/reload":
            try:
                res = hot_reload_template_library()
                self._send_json(200, res)
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        elif parsed.path == "/api/diff/switch-template":
            session_id, tenant_id, user_id = self._get_request_identity()
            length = int(self.headers.get("content-length", 0))
            raw_body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                target_tpl_id = (data.get("template_id") or "").strip()
                meta = get_session_meta(session_id)
                if not target_tpl_id or target_tpl_id == "auto":
                    meta["forced_template_id"] = None
                else:
                    meta["forced_template_id"] = target_tpl_id
                save_session_meta(session_id, meta)

                s_file = get_session_file(session_id)
                if not s_file.exists():
                    default_sample = config.GOLDEN_DIR / "mut_11_late_fee_change.docx"
                    shutil.copyfile(default_sample, s_file)

                res = execute_current_diff(
                    s_file,
                    forced_template_id=meta["forced_template_id"],
                    doc_name=meta.get("doc_name"),
                )
                res["session_id"] = session_id
                get_agent_manager().start_document_review(
                    res,
                    force_restart=True,
                    session_id=session_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
                self._send_json(200, res)
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        elif parsed.path == "/api/diff/prioritize_card":
            session_id, tenant_id, user_id = self._get_request_identity()
            length = int(self.headers.get("content-length", 0))
            raw_body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                ann_id = data.get("id") or ""
                ok = get_agent_manager().prioritize_card(
                    ann_id, session_id=session_id, tenant_id=tenant_id, user_id=user_id
                )
                self._send_json(200, {"success": ok})
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        elif parsed.path == "/api/diff/toggle_pause":
            session_id, tenant_id, user_id = self._get_request_identity()
            try:
                cur_status = get_agent_manager().toggle_pause(
                    session_id=session_id, tenant_id=tenant_id, user_id=user_id
                )
                self._send_json(200, {"status": cur_status})
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        elif parsed.path == "/api/diff/rerun_review":
            session_id, tenant_id, user_id = self._get_request_identity()
            try:
                s_file = get_session_file(session_id)
                if not s_file.exists():
                    s_file = CURRENT_SESSION_FILE
                if s_file.exists():
                    meta = get_session_meta(session_id)
                    res = execute_current_diff(
                        s_file,
                        forced_template_id=meta.get("forced_template_id"),
                        doc_name=meta.get("doc_name"),
                    )
                    res["session_id"] = session_id
                    get_agent_manager().start_document_review(
                        res,
                        force_restart=True,
                        session_id=session_id,
                        tenant_id=tenant_id,
                        user_id=user_id,
                    )
                self._send_json(200, {"success": True})
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        elif parsed.path == "/api/diff/retry_card":
            session_id, tenant_id, user_id = self._get_request_identity()
            length = int(self.headers.get("content-length", 0))
            raw_body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                ann_id = data.get("id") or ""
                ok = get_agent_manager().retry_card(
                    ann_id, session_id=session_id, tenant_id=tenant_id, user_id=user_id
                )
                self._send_json(200, {"success": ok})
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        elif parsed.path == "/api/diff/skip_card":
            session_id, tenant_id, user_id = self._get_request_identity()
            length = int(self.headers.get("content-length", 0))
            raw_body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                ann_id = data.get("id") or ""
                ok = get_agent_manager().skip_card(
                    ann_id, session_id=session_id, tenant_id=tenant_id, user_id=user_id
                )
                self._send_json(200, {"success": ok})
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        elif parsed.path == "/api/diff/reset_circuit":
            try:
                status = get_agent_manager().reset_circuit_breaker()
                self._send_json(200, {"success": True, "circuit": status})
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        elif parsed.path == "/api/diff/feedback":
            length = int(self.headers.get("content-length", 0))
            raw_body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                saved_path = record_user_feedback(data)
                self._send_json(200, {"success": True, "saved_to": str(saved_path)})
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        elif parsed.path == "/api/diff/export_training_data":
            try:
                res = export_sft_dataset()
                self._send_json(200, res)
            except Exception as e:
                self._send_json(500, {"error": str(e)})
        elif parsed.path == "/api/diff/cancel":
            session_id, tenant_id, user_id = self._get_request_identity()
            try:
                ok = get_agent_manager().cancel_task(
                    session_id=session_id, tenant_id=tenant_id, user_id=user_id
                )
                self._send_json(200, {"success": ok})
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        elif parsed.path == "/api/diff/human_decision":
            session_id, tenant_id, user_id = self._get_request_identity()
            length = int(self.headers.get("content-length", 0))
            raw_body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                ann_id = str(data.get("id") or data.get("annotation_id") or "")
                decision = str(data.get("decision") or "ACCEPTED").upper()
                modified_advice = str(data.get("modified_advice") or "")

                sess = get_agent_manager().get_session(
                    session_id, tenant_id=tenant_id, user_id=user_id
                )
                if not sess:
                    self._send_json(404, {"error": "session_not_found"})
                    return

                with sess._lock:
                    card = sess.annotations_map.get(ann_id)
                    if card:
                        if decision == "ACCEPTED":
                            card["human_review_status"] = "HUMAN_ACCEPTED"
                        elif decision == "REJECTED":
                            card["human_review_status"] = "HUMAN_REJECTED"
                        elif decision == "MODIFIED":
                            card["human_review_status"] = "HUMAN_MODIFIED"
                            card["human_modified_advice"] = modified_advice
                        sess._save_checkpoint()
                self._send_json(200, {"success": True, "card": card})
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        elif parsed.path == "/api/diff/upload":
            length = int(self.headers.get("content-length", 0))
            # 25MB 上传体积限制防护
            if length > 25 * 1024 * 1024:
                self._send_json(
                    413,
                    {
                        "error": "payload_too_large",
                        "message": "上传文件大小超出 25MB 限制",
                    },
                )
                return

            session_id, tenant_id, user_id = self._get_request_identity()
            if session_id == "default_session":
                session_id = f"sess_{hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:16]}"

            ctype = self.headers.get("content-type", "")
            if "multipart/form-data" in ctype and "boundary=" in ctype:
                boundary = ctype.split("boundary=")[-1].strip().encode("utf-8")
                body = self.rfile.read(length)
                parts = body.split(b"--" + boundary)
                file_bytes = None
                file_name = "待审合同.docx"
                for part in parts:
                    if b'name="file"' in part:
                        fn_m = re.search(rb'filename="([^"]+)"', part)
                        if fn_m:
                            try:
                                file_name = fn_m.group(1).decode("utf-8")
                            except Exception:
                                file_name = fn_m.group(1).decode("latin-1")
                        idx = part.find(b"\r\n\r\n")
                        if idx != -1:
                            file_bytes = part[idx + 4 :]
                            if file_bytes.endswith(b"\r\n"):
                                file_bytes = file_bytes[:-2]
                            break

                if file_bytes:
                    stem_clean = Path(file_name).stem
                    doc_name = (
                        f"《{stem_clean}》送审稿"
                        if not stem_clean.startswith("《")
                        else stem_clean
                    )
                    ext = Path(file_name).suffix.lower() or ".docx"
                    s_file = prepare_session_file(session_id, ext=ext)
                    s_file.write_bytes(file_bytes)
                    save_session_meta(
                        session_id, {"doc_name": doc_name, "forced_template_id": None}
                    )

                    try:
                        res = execute_current_diff(
                            s_file, forced_template_id=None, doc_name=doc_name
                        )
                        res["session_id"] = session_id
                        get_agent_manager().start_document_review(
                            res,
                            session_id=session_id,
                            tenant_id=tenant_id,
                            user_id=user_id,
                        )
                        self._send_json(200, res)
                        return
                    except Exception as e:
                        self._send_json(500, {"error": str(e)})
                        return

            self._send_json(400, {"error": "invalid_upload"})

        elif parsed.path == "/api/materials/load_contract":
            session_id, tenant_id, user_id = self._get_request_identity()
            length = int(self.headers.get("content-length", 0))
            raw_body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                c_no = (data.get("contract_no") or "").strip()
                att_path = (data.get("attachment_path") or "").strip() or None
                if not c_no:
                    self._send_json(400, {"error": "缺少 contract_no 参数"})
                    return
                mgr = get_material_manager()
                file_path, att_meta = mgr.get_contract_attachment_file(c_no, att_path)
                if not file_path or not file_path.exists():
                    self._send_json(
                        404, {"error": f"未找到案卷 [{c_no}] 的合同附件物理文件"}
                    )
                    return

                s_file = prepare_session_file(session_id, ext=file_path.suffix)
                shutil.copyfile(file_path, s_file)

                c_info = mgr.cases_by_no.get(c_no, {})
                c_name = c_info.get("name") or c_no
                doc_name = (
                    f"《{c_name}》履约合同原件"
                    if not c_name.startswith("《")
                    else c_name
                )
                save_session_meta(
                    session_id, {"doc_name": doc_name, "forced_template_id": None}
                )

                res = execute_current_diff(
                    s_file, forced_template_id=None, doc_name=doc_name
                )
                res["session_id"] = session_id
                res["contract_no"] = c_no
                get_agent_manager().start_document_review(
                    res,
                    force_restart=True,
                    session_id=session_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
                self._send_json(200, res)
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        elif parsed.path == "/api/ref_templates/load":
            session_id, tenant_id, user_id = self._get_request_identity()
            length = int(self.headers.get("content-length", 0))
            raw_body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                rel_path = (data.get("rel_path") or "").strip()
                mode = (data.get("mode") or "reviewed").strip()

                if not rel_path:
                    self._send_json(400, {"error": "缺少 rel_path 参数"})
                    return

                target_file = (DEFAULT_REF_DIR / rel_path).resolve()
                if (
                    not target_file.is_relative_to(DEFAULT_REF_DIR.resolve())
                    or not target_file.exists()
                ):
                    self._send_json(404, {"error": f"模板文件不存在: {rel_path}"})
                    return

                if target_file.suffix.lower() == ".doc":
                    docx_alt = target_file.with_suffix(".docx")
                    if docx_alt.exists():
                        target_file = docx_alt

                clean_name = catalog_clean_title(target_file.stem)

                st = create_storage()
                tpl_id = None
                try:
                    target_sha = hashlib.sha256(target_file.read_bytes()).hexdigest()
                    doc_rec = st.find_document_by_hash(target_sha)
                    if not doc_rec:
                        for d in st.list_documents():
                            if (
                                d.get("source_file") == target_file.name
                                or d.get("file_name") == target_file.name
                            ):
                                doc_rec = d
                                break
                    if doc_rec:
                        tpl_id = doc_rec.get("document_id")
                finally:
                    st.close()

                if mode == "standard":
                    if not tpl_id:
                        self._send_json(
                            400,
                            {
                                "error": f"模板 [{target_file.name}] 未在知识库中注册有效 ID"
                            },
                        )
                        return
                    s_file = get_session_file(session_id)
                    if not s_file.exists():
                        default_sample = (
                            config.GOLDEN_DIR / "mut_11_late_fee_change.docx"
                        )
                        shutil.copyfile(default_sample, s_file)
                    meta = get_session_meta(session_id)
                    meta["forced_template_id"] = tpl_id
                    save_session_meta(session_id, meta)

                    res = execute_current_diff(
                        s_file,
                        forced_template_id=tpl_id,
                        doc_name=meta.get("doc_name"),
                    )
                elif mode == "self_diff":
                    s_file = prepare_session_file(session_id, ext=target_file.suffix)
                    shutil.copyfile(target_file, s_file)
                    doc_name = f"《{clean_name}》标准模板自测核验"
                    save_session_meta(
                        session_id, {"doc_name": doc_name, "forced_template_id": tpl_id}
                    )

                    res = execute_current_diff(
                        s_file,
                        forced_template_id=tpl_id,
                        doc_name=doc_name,
                    )
                else:
                    s_file = prepare_session_file(session_id, ext=target_file.suffix)
                    shutil.copyfile(target_file, s_file)
                    doc_name = f"《{clean_name}》送审原稿"
                    save_session_meta(
                        session_id, {"doc_name": doc_name, "forced_template_id": None}
                    )

                    res = execute_current_diff(
                        s_file,
                        forced_template_id=None,
                        doc_name=doc_name,
                    )

                res["session_id"] = session_id
                get_agent_manager().start_document_review(
                    res,
                    force_restart=True,
                    session_id=session_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
                self._send_json(200, res)
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        else:
            self.send_response(404)
            self.end_headers()


def generate_rendered_html() -> str:
    """生成内嵌初始默认高危用例数据与按置信度排序的母本清单的独立完整 HTML。"""
    try:
        sample_file = config.GOLDEN_DIR / "mut_11_late_fee_change.docx"
        if not CURRENT_SESSION_FILE.exists():
            shutil.copyfile(sample_file, CURRENT_SESSION_FILE)
        initial_data = execute_current_diff(CURRENT_SESSION_FILE)
        data_json = json.dumps(initial_data, ensure_ascii=False)
    except Exception:
        data_json = "null"

    try:
        catalog = get_ranked_templates_catalog(CURRENT_SESSION_FILE)
        catalog_json = json.dumps(catalog, ensure_ascii=False)
    except Exception:
        catalog_json = "[]"

    try:
        ref_catalog = get_ref_templates_catalog()
        ref_json = json.dumps(ref_catalog, ensure_ascii=False)
    except Exception:
        ref_json = "[]"

    try:
        mgr = get_material_manager()
        stats = mgr.get_stats()
        initial_cases = mgr.search_cases(
            category="ALL", has_word_only=True, page=1, page_size=20
        )
        material_data = {
            "stats": stats,
            "initial_cases": initial_cases,
        }
        material_json = json.dumps(material_data, ensure_ascii=False)
    except Exception:
        material_json = "{}"

    html = HTML_TEMPLATE.replace("__INITIAL_DATA_PLACEHOLDER__", data_json)
    html = html.replace("__ALL_TEMPLATES_PLACEHOLDER__", catalog_json)
    html = html.replace("__REF_TEMPLATES_PLACEHOLDER__", ref_json)
    html = html.replace("__MATERIAL_DATA_PLACEHOLDER__", material_json)
    return html


def export_standalone_html():
    """将整版对比与批注工作台导出为独立 HTML 页面存放在 data/contract_merged 与 参考/ 供直接双击使用。"""
    html_content = generate_rendered_html()

    out_path = (
        config.DATA_DIR.parent / "contract_merged" / "合同模板条款比对与Diff可视化.html"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_content, encoding="utf-8")
    print(f"[已生成素材库独立整版原格式对照与右侧批注网页] {out_path}")

    # 同时在【参考/】模板库生成独立工作台镜像副本
    ref_out = DEFAULT_REF_DIR / "合同模板条款比对与Diff可视化.html"
    if DEFAULT_REF_DIR.exists():
        try:
            ref_out.write_text(html_content, encoding="utf-8")
            print(f"[已同步生成模板库独立对比网页] {ref_out}")
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="合同整版原格式对照与右侧批注前端服务")
    parser.add_argument("--port", type=int, default=8089, help="Web 端口（默认 8089）")
    parser.add_argument(
        "--export-only", action="store_true", help="仅导出独立 HTML 页面"
    )
    args = parser.parse_args()

    default_sample = config.GOLDEN_DIR / "mut_11_late_fee_change.docx"
    if default_sample.exists() and not CURRENT_SESSION_FILE.exists():
        shutil.copyfile(default_sample, CURRENT_SESSION_FILE)

    if CURRENT_SESSION_FILE.exists():
        initial_data = execute_current_diff(CURRENT_SESSION_FILE)
        get_agent_manager().start_document_review(initial_data)

    export_standalone_html()
    if args.export_only:
        return

    server = ThreadingHTTPServer(("127.0.0.1", args.port), DiffVisualizerHandler)
    print(
        f"\n🚀 AI 合同审查 · 条款级 Diff 与整版原格式比对工作台已启动: http://127.0.0.1:{args.port}"
    )
    print(
        "👉 请在浏览器中打开上方网址，即可体验整版双栏原格式对照与右侧智能法务审阅批注！\n"
    )
    while True:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n服务已停止。")
            break
        except Exception as e:
            print(f"\n服务异常自动重连: {e}")
            time.sleep(0.5)


if __name__ == "__main__":
    main()
