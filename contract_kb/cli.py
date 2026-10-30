# -*- coding: utf-8 -*-
"""CLI：import / inventory / report 三组命令。运行示例：
  python -m contract_kb.cli import     # 导入 26 份模板 → data/contract-kb/
  python -m contract_kb.cli inventory  # 生成 reports/contract-kb/source-inventory.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import config
from .ingest import scan_and_import


def _load_or_import() -> dict:
    config.ensure_dirs()
    out_path = config.DATA_DIR / "import_result.json"
    if out_path.exists():
        data = json.loads(out_path.read_text(encoding="utf-8"))
        return data
    return _do_import()


def _do_import() -> dict:
    config.ensure_dirs()
    result = scan_and_import(config.TEMPLATE_DIR)
    raw = result.pop("_raw")
    out_path = config.DATA_DIR / "import_result.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    # AST / 条款 / Schema 分文件保存（Layer A 原始层）
    ast_dir = config.DATA_DIR / "ast"
    ast_dir.mkdir(exist_ok=True)
    layer = {"clauses": [], "table_schemas": [], "documents": []}
    for d in raw:
        (ast_dir / f"{d.template_id}.json").write_text(
            json.dumps(d.ast, ensure_ascii=False, indent=1), encoding="utf-8")
        layer["clauses"].extend(d.clauses)
        layer["table_schemas"].extend(d.table_schemas)
        layer["documents"].append(d.to_dict())
    (config.DATA_DIR / "layer_a_raw.json").write_text(
        json.dumps(layer, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"imported={len(result['imported'])} failures={len(result['failures'])}")
    for f in result["failures"]:
        print(f"  FAIL {f['file']}: {f['error']}")
    return result


def _cmd_inventory() -> None:
    result = _load_or_import()
    lines = ["# 源文件清单（source-inventory）", "",
             f"- 扫描目录：`{result['scanned_dir']}`",
             f"- 扫描文件总数：{result['file_count']}",
             f"- 导入成功：{len(result['imported'])}",
             f"- 导入失败/拒绝：{len(result['failures'])}", "",
             "> SHA-256 为文件级哈希；段落数/表格数来自 OOXML 结构解析（word/document.xml）。",
             "> 页码不提供：DOCX 无稳定页码概念，需 PDF 渲染映射（page_mapping_available=false），不伪造。", ""]
    lines.append("| # | 原始文件名 | 大小(B) | SHA-256(前16) | 文档类型 | 段落数 | 表格数 | 业务分类 | 二级分类 | 主要章节 | 解析 | 无法读取结构 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, d in enumerate(result["imported"], 1):
        secs = " / ".join(s.replace("第", "").replace("条 ", "") for s in d["main_sections"][:8])
        unhandled = ", ".join(d["unhandled_structures"]) if d["unhandled_structures"] else "无"
        lines.append(
            f"| {i} | {d['source_file']} | {d['file_size']} | `{d['sha256'][:16]}` | {d['document_type']} "
            f"| {d['paragraph_count']} | {d['table_count']} | {d['category']} | {d['subtype']} "
            f"| {secs} | {'成功' if d['parse_success'] else '失败'} | {unhandled} |")
    if result["failures"]:
        lines += ["", "## 导入失败/安全拒绝", ""]
        for f in result["failures"]:
            lines.append(f"- `{f['file']}`（{f['size']}B）：{f['error']}")
    lines += ["", "## 分类冲突标记", ""]
    any_conflict = False
    for d in result["imported"]:
        if d["classification_conflicts"]:
            any_conflict = True
            for c in d["classification_conflicts"]:
                lines.append(f"- `{d['source_file']}`：{json.dumps(c, ensure_ascii=False)}")
    if not any_conflict:
        lines.append("（无）")
    config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = config.REPORT_DIR / "source-inventory.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"written: {out}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "import"
    if cmd == "import":
        _do_import()
    elif cmd == "inventory":
        _cmd_inventory()
    else:
        print("usage: python -m contract_kb.cli [import|inventory]")


if __name__ == "__main__":
    main()
