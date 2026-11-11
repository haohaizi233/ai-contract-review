# -*- coding: utf-8 -*-
"""Web 工作台合同对比编排、模板匹配置信度排序与模板库热加载服务（Diff Service）。"""

from __future__ import annotations

import re
import hashlib
from pathlib import Path
from typing import Any

from contract_kb import config
from contract_kb.infrastructure.persistence.sqlite_storage import create_storage
from contract_kb.modules.contract_comparison.full_diff import build_full_contract_diff
from contract_kb.modules.document_processing.ingestion import (
    import_template_file,
    scan_and_import,
)

from .session_files import (
    CURRENT_DOC_NAME,
    CURRENT_FORCED_TEMPLATE_ID,
    CURRENT_SESSION_FILE,
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
        # Historical imports and evaluation fixtures are not current mother templates.
        sources = {
            (p.name, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in config.TEMPLATE_DIR.rglob("*.docx")
            if not p.name.startswith(("~", "synth_")) and "合同编号制度" not in p.name
        }
        docs = []
        seen = set()
        for d in st.list_documents(tenant_id="default"):
            key = (d.get("source_file"), d.get("source_hash"))
            if key not in sources or d.get("status") != "ACTIVE" or key in seen:
                continue
            seen.add(key)
            docs.append(d)

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

        display_code = doc_id if not is_hex_hash(doc_id) else (ver or "")

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
                "source_hash": d.get("source_hash") or "",
                "template_code": fname.split("_")[0] if fname.startswith("MB") else "",
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
    res["doc_title"] = target_name
    res["reviewed_doc_title"] = target_name
    res["friendly_doc_title"] = target_name
    res["display_doc_name"] = target_name
    res["uploaded_file"] = target_name
    res["uploaded_file_name"] = target_name
    res["file_name"] = target_name

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
