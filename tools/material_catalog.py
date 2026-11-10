# -*- coding: utf-8 -*-
"""模板库（参考/）与素材库（data/contract_merged）统一资产目录与检索引擎。

提供：
1. get_ref_templates_catalog: 全量扫描【参考/】目录 54 套官方标准模板（覆盖 10 大业务板块），
   提取纯正公文名称、业务大类、相对路径、文件大小、.docx/.doc 实体状态与映射编码。
2. MaterialLibraryManager: 超高速内存索引 data/contract_merged 下 2.1万+ 历史案卷及 4,500+ 实单 Word 合同，
   支持业务前缀分流（CIP/GW/HCI/HCCX等）、模糊检索、原件附件直通解析与案卷履约审批详情提取。
"""

from __future__ import annotations

import csv
import re
import threading
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REF_DIR = PROJECT_ROOT / "参考"
DEFAULT_MERGED_DIR = PROJECT_ROOT / "data" / "contract_merged"

# 业务板块中文映射与排序
REF_FOLDER_ORDER = [
    "专利",
    "商标",
    "国际专利",
    "国际商标",
    "诉讼",
    "综合业务",
    "著作权",
    "集成电路布图设计登记",
    "双软",
    "保密协议",
    "根目录",
]


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


def get_ref_templates_catalog(ref_dir: Path | None = None) -> list[dict[str, Any]]:
    """扫描【参考/】目录，返回所有可用标准模板按业务大类分组的完整清单。"""
    base_dir = ref_dir or DEFAULT_REF_DIR
    templates: list[dict[str, Any]] = []
    if not base_dir.exists():
        return templates

    # 读取映射字典以补充标准 MB 编码（若可用）
    dict_mapping: dict[str, str] = {}
    dict_file = DEFAULT_MERGED_DIR / "contract_mapping_dictionary.json"
    if dict_file.exists():
        try:
            import json

            data = json.loads(dict_file.read_text(encoding="utf-8"))
            for t in data.get("templates", []):
                tname = t.get("template_name", "")
                tcode = t.get("template_code", "")
                if tname and tcode:
                    dict_mapping[tname] = tcode
        except Exception:
            pass

    for p in sorted(base_dir.glob("**/*")):
        if p.is_file() and p.suffix.lower() in (".docx", ".doc"):
            rel = p.relative_to(base_dir)
            folder = rel.parts[0] if len(rel.parts) > 1 else "根目录"
            clean_name = clean_template_title(p.stem)

            # 查找是否存在同名 .docx / .doc
            has_docx = (p.parent / f"{p.stem}.docx").exists()
            has_doc = (p.parent / f"{p.stem}.doc").exists()

            # 若同时存在 doc 与 docx，以 docx 为主条目展示，避免列表重复
            if p.suffix.lower() == ".doc" and has_docx:
                continue

            # 匹配对应 MB 编码
            mb_code = dict_mapping.get(clean_name, "")
            if not mb_code:
                for k, v in dict_mapping.items():
                    if clean_name in k or k in clean_name:
                        mb_code = v
                        break

            templates.append(
                {
                    "rel_path": str(rel).replace("\\", "/"),
                    "folder": folder,
                    "name": clean_name,
                    "file_name": p.name,
                    "ext": p.suffix.lower(),
                    "size": p.stat().st_size,
                    "has_docx": has_docx,
                    "has_doc": has_doc,
                    "mb_code": mb_code,
                }
            )

    # 按照标准板块顺序排序
    def sort_key(item: dict[str, Any]) -> tuple[int, str]:
        f = item.get("folder", "")
        idx = REF_FOLDER_ORDER.index(f) if f in REF_FOLDER_ORDER else 99
        return idx, item.get("name", "")

    templates.sort(key=sort_key)
    return templates


class MaterialLibraryManager:
    """素材库（data/contract_merged）案例与合同资产高速索引与检索引擎。"""

    def __init__(self, merged_dir: Path | None = None):
        self.merged_dir = merged_dir or DEFAULT_MERGED_DIR
        self.cases_list: list[dict[str, Any]] = []
        self.cases_by_no: dict[str, dict[str, Any]] = {}
        self.att_by_case: dict[str, list[dict[str, Any]]] = {}
        self.category_counts: dict[str, int] = {}
        self.total_word_contracts: int = 0
        self._loaded: bool = False
        self._lock = threading.Lock()

    def ensure_loaded(self):
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._load()
            self._loaded = True

    def _load(self):
        att_csv = self.merged_dir / "attachments_merged.csv"
        if att_csv.exists():
            try:
                with open(att_csv, "r", encoding="utf-8", errors="ignore") as f:
                    for row in csv.DictReader(f):
                        c = row.get("case") or row.get("\ufeffcase")
                        loc = row.get("local")
                        if c and loc:
                            ext = Path(loc).suffix.lower()
                            self.att_by_case.setdefault(c, []).append(
                                {
                                    "original": row.get("original", ""),
                                    "local": loc,
                                    "size": int(row.get("size") or 0),
                                    "ext": ext,
                                }
                            )
            except Exception:
                pass

        cls_csv = self.merged_dir / "contracts_classification.csv"
        if cls_csv.exists():
            try:
                with open(cls_csv, "r", encoding="utf-8", errors="ignore") as f:
                    for row in csv.DictReader(f):
                        no = (
                            row.get("合同编号") or row.get("\ufeff合同编号") or ""
                        ).strip()
                        if not no:
                            continue
                        cat_code = (row.get("业务分类代字") or "").strip()
                        cat_name = (row.get("业务分类名称") or "").strip()
                        atts = self.att_by_case.get(no, [])
                        word_atts = [a for a in atts if a["ext"] in (".docx", ".doc")]
                        has_word = len(word_atts) > 0
                        if has_word:
                            self.total_word_contracts += 1

                        item = {
                            "no": no,
                            "name": (row.get("合同名称") or "").strip(),
                            "category_code": cat_code,
                            "category_name": cat_name,
                            "folder": (row.get("归档文件夹") or "").strip(),
                            "year": (row.get("年份") or "").strip(),
                            "status": (row.get("合同状态") or "").strip(),
                            "applicant": (row.get("申请人") or "").strip(),
                            "party_a": (row.get("我方签约主体") or "").strip(),
                            "party_b": (row.get("对方主体") or "").strip(),
                            "md_file": (row.get("案卷MD文件") or "").strip(),
                            "has_word": has_word,
                            "word_count": len(word_atts),
                            "attachment_count": len(atts),
                            "attachments": atts,
                        }
                        self.cases_list.append(item)
                        self.cases_by_no[no] = item

                        group_key = self._get_group_key(cat_code, no)
                        self.category_counts[group_key] = (
                            self.category_counts.get(group_key, 0) + 1
                        )
            except Exception:
                pass

    @staticmethod
    def _get_group_key(cat_code: str, no: str) -> str:
        code = (cat_code or "").upper()
        if code.startswith("CIP"):
            return "CIP"
        if code in ("GW", "GWI", "MG", "TZ", "BJH"):
            return "TRADEMARK"
        if "诉讼" in code or code in ("HCI", "HCZ"):
            return "LITIGATION"
        if code in ("HCCX", "HKP", "HCG", "HCH", "HCHZS"):
            return "FRAMEWORK"
        return "OTHER"

    def get_stats(self) -> dict[str, Any]:
        self.ensure_loaded()
        return {
            "total_cases": len(self.cases_list),
            "total_word_contracts": self.total_word_contracts,
            "total_attachments": sum(len(v) for v in self.att_by_case.values()),
            "categories": self.category_counts,
        }

    def search_cases(
        self,
        category: str = "ALL",
        search: str = "",
        has_word_only: bool = True,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        self.ensure_loaded()
        search_kw = (search or "").strip().lower()
        filtered = []

        for c in self.cases_list:
            if has_word_only and not c["has_word"]:
                continue
            if category and category != "ALL":
                g_key = self._get_group_key(c["category_code"], c["no"])
                if category == "CIP" and g_key != "CIP":
                    continue
                elif category == "TRADEMARK" and g_key != "TRADEMARK":
                    continue
                elif category == "LITIGATION" and g_key != "LITIGATION":
                    continue
                elif category == "FRAMEWORK" and g_key != "FRAMEWORK":
                    continue
                elif category == "OTHER" and g_key != "OTHER":
                    continue

            if search_kw:
                matches = (
                    search_kw in c["no"].lower()
                    or search_kw in c["name"].lower()
                    or search_kw in c["party_b"].lower()
                    or search_kw in c["applicant"].lower()
                    or search_kw in c["year"]
                    or search_kw in c["category_name"].lower()
                )
                if not matches:
                    continue

            filtered.append(c)

        total = len(filtered)
        start = max(0, (page - 1) * page_size)
        items = filtered[start : start + page_size]
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size),
            "cases": items,
        }

    def get_case_detail(self, contract_no: str) -> dict[str, Any] | None:
        self.ensure_loaded()
        item = self.cases_by_no.get(contract_no)
        if not item:
            return None

        md_content = ""
        if item.get("md_file"):
            md_path = (self.merged_dir / item["md_file"]).resolve()
            if md_path.is_relative_to(self.merged_dir.resolve()) and md_path.exists():
                try:
                    md_content = md_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass

        return {
            "case": item,
            "md_content": md_content,
            "attachments": item["attachments"],
        }

    def get_contract_attachment_file(
        self, contract_no: str, attachment_local_path: str | None = None
    ) -> tuple[Path | None, dict[str, Any] | None]:
        """获取案卷对应的 Word 合同附件文件实体与附件元数据（内置路径穿越防护）。"""
        self.ensure_loaded()
        item = self.cases_by_no.get(contract_no)
        if not item:
            return None, None

        atts = item["attachments"]

        # 1. 若指定特定附件本地相对路径（严格限制在 merged_dir 内且必须属于本案卷声明清单）
        if attachment_local_path:
            p = (self.merged_dir / attachment_local_path).resolve()
            if p.is_relative_to(self.merged_dir.resolve()) and p.exists():
                meta = next(
                    (a for a in atts if a.get("local") == attachment_local_path), None
                )
                if meta:
                    return p, meta
            return None, None

        # 2. 优先查找 .docx，次优查找 .doc，再次查找 .pdf
        # 并对图纸模型（如 .vsd.doc, .prt.doc, .dwg 等）进行降权，优先提取真正的合同法律文本
        candidates: list[tuple[int, int, Path, dict[str, Any]]] = []
        for a in atts:
            ext = a.get("ext", "").lower()
            if ext in (".docx", ".doc", ".pdf"):
                p = self.merged_dir / a["local"]
                if p.exists():
                    orig_low = str(a.get("original") or a.get("local") or "").lower()
                    is_draw = any(
                        s in orig_low
                        for s in (
                            ".vsd",
                            ".prt",
                            ".dwg",
                            ".dxf",
                            ".cad",
                            ".step",
                            ".stp",
                            "绘图",
                            "图纸",
                        )
                    )
                    ext_priority = 0 if ext == ".docx" else (1 if ext == ".doc" else 2)
                    draw_priority = 1 if is_draw else 0
                    candidates.append((draw_priority, ext_priority, p, a))

        if candidates:
            candidates.sort(key=lambda x: (x[0], x[1]))
            return candidates[0][2], candidates[0][3]

        return None, None


# 单例管理
_MATERIAL_MANAGER: MaterialLibraryManager | None = None
_MGR_LOCK = threading.Lock()


def get_material_manager() -> MaterialLibraryManager:
    global _MATERIAL_MANAGER
    if _MATERIAL_MANAGER is None:
        with _MGR_LOCK:
            if _MATERIAL_MANAGER is None:
                _MATERIAL_MANAGER = MaterialLibraryManager()
    return _MATERIAL_MANAGER
