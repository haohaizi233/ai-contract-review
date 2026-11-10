# -*- coding: utf-8 -*-
"""合同比对工具（Contract Diff CLI）：
用法：
  python tools/diff_contract.py <待比对的DOCX文件路径> [--template-id 强制指定模板ID]

功能：
  1. 自动解析送审合同 DOCX；
  2. 自动识别业务类型并匹配 78 套标准模板中的对应基准版本；
  3. 逐条款对齐并执行整版微观 Diff（数值/期限/主体/否定词/义务强度/新增/删除）；
  4. 生成整版双栏对齐与智能法务审阅批注；
  5. 输出终端彩色与结构化对比报告。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 保证项目根目录在 sys.path
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from contract_kb.full_diff import build_full_contract_diff  # noqa: E402


def run_diff(docx_path: str | Path, forced_template_id: str | None = None) -> dict:
    """运行整版条款对比与法务批注生成"""
    docx_path = Path(docx_path)
    if not docx_path.exists():
        print(f"[错误] 文件不存在: {docx_path}", file=sys.stderr)
        sys.exit(1)

    try:
        return build_full_contract_diff(docx_path, forced_template_id)
    except Exception as e:
        print(f"[比对失败] {e}", file=sys.stderr)
        sys.exit(1)


def print_diff_report(res: dict):
    print("=" * 80)
    print("【AI 合同模板整版对照与条款级批注审阅报告】")
    print("=" * 80)
    print(f"待审文档: {res['uploaded_file']}")
    print(
        f"识别类型: {res['detected_subtype']} (置信度: {res['classification_confidence']})"
    )
    print(f"匹配模板: [{res['matched_template_id']}] {res['matched_template_name']}")
    print(
        f"条款规模: 标准母本 {res['standard_clause_count']} 条 | 待审文档 {res['uploaded_clause_count']} 条"
    )
    print("-" * 80)

    stats = res.get("stats", {})
    annotations = res.get("annotations", [])

    print(
        f"比对统计: 全文对齐 {stats.get('total_blocks', 0)} 块 | "
        f"完全一致 {stats.get('matched_blocks', 0)} 块 | "
        f"实质修改 {stats.get('diff_blocks', 0)} 块 | "
        f"生成批注 {stats.get('total_annotations', len(annotations))} 处 "
        f"(🔴高危: {stats.get('high_risk', 0)} | 🚩缺失: {stats.get('missing_clauses', 0)} | ➕新增: {stats.get('new_clauses', 0)})\n"
    )

    if annotations:
        print("📋 【智能法务审阅批注清单 (按整版阅读顺序)】:")
        for a in annotations:
            lvl_icon = (
                "🔴"
                if a["risk_level"] == "HIGH"
                else ("🟠" if a["risk_level"] == "MEDIUM" else "ℹ️")
            )
            print(
                f"  [{lvl_icon} 批注 #{a['index']:02d}] {a['clause_title']} · {a['risk_label']}"
            )
            if a["standard_span"]:
                print(
                    f"     🏛️ 母本原文: {a['standard_span'][:80].replace(chr(10), ' ')}"
                )
            if a["reviewed_span"]:
                print(
                    f"     ✏️ 送审变动: {a['reviewed_span'][:80].replace(chr(10), ' ')}"
                )
            print(f"     💡 AI剖析: {a['ai_analysis']}")
            print(f"     ⚖️ 处置建议: {a['action_advice']}")
            print()
    else:
        print(
            "✅ 完美匹配：待审合同与标准模板条款架构与核心要素完全一致，未发现任何篡改或缺失。"
        )
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="合同模板比对与整版对照批注工具")
    parser.add_argument("docx_path", help="待审合同 DOCX 文件路径")
    parser.add_argument("--template-id", help="强制指定基准模板 ID (如 MB20251205012)")
    parser.add_argument(
        "--json", action="store_true", help="以 JSON 格式输出完整比对结构"
    )
    args = parser.parse_args()

    res = run_diff(args.docx_path, args.template_id)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print_diff_report(res)


if __name__ == "__main__":
    main()
