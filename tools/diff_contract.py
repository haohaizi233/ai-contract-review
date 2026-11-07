# -*- coding: utf-8 -*-
"""合同比对工具（Contract Diff CLI）：
用法：
  python tools/diff_contract.py <待比对的DOCX文件路径> [--template-id 强制指定模板ID]

功能：
  1. 自动解析送审合同 DOCX；
  2. 自动识别业务类型并匹配 78 套标准模板中的对应基准版本；
  3. 逐条款类别对齐并执行深度微观 Diff（数值/期限/主体/否定词/义务强度/新增/删除）；
  4. 输出终端彩色与结构化对比报告。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 保证项目根目录在 sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from contract_kb.storage import Storage
from contract_kb.ingest import import_template_file, security_check_file
from contract_kb.clause_diff import diff_clause_sets
from contract_kb import config


def run_diff(docx_path: str | Path, forced_template_id: str | None = None) -> dict:
    docx_path = Path(docx_path)
    if not docx_path.exists():
        print(f"[错误] 文件不存在: {docx_path}", file=sys.stderr)
        sys.exit(1)

    # 1. 安全检查 + 解析待审文档
    sec = security_check_file(docx_path)
    if not sec.passed:
        print(f"[拒绝] 安全检查未通过: {sec.rejected_reason}", file=sys.stderr)
        sys.exit(1)

    rec, err = import_template_file(docx_path)
    if rec is None:
        print(f"[解析失败] {err}", file=sys.stderr)
        sys.exit(1)

    # 2. 匹配基准标准模板
    st = Storage(config.DB_PATH)
    matched_tpl = None

    if forced_template_id:
        row = st.conn.execute(
            "SELECT * FROM documents WHERE document_id=?", (forced_template_id,)
        ).fetchone()
        if row:
            matched_tpl = dict(row)
        else:
            print(f"[警告] 指定的模板 ID {forced_template_id} 在数据库中未找到，尝试自动匹配。")

    if not matched_tpl:
        # 依分类器自动识别的 subtype 匹配
        row = st.conn.execute(
            "SELECT * FROM documents WHERE contract_subtype=? AND status='ACTIVE'",
            (rec.subtype,),
        ).fetchone()
        if row:
            matched_tpl = dict(row)
        else:
            # 兜底：按业务大类匹配首个模板
            row = st.conn.execute(
                "SELECT * FROM documents WHERE business_category=? AND status='ACTIVE' LIMIT 1",
                (rec.category,),
            ).fetchone()
            if row:
                matched_tpl = dict(row)

    if not matched_tpl:
        print(f"[未匹配] 未能在 78 套知识库中找到业务类型 [{rec.subtype}] 的基准模板", file=sys.stderr)
        sys.exit(1)

    # 3. 读取基准模板的全部标准条款
    tpl_clauses = [
        dict(r)
        for r in st.conn.execute(
            "SELECT * FROM clauses WHERE document_id=?", (matched_tpl["document_id"],)
        ).fetchall()
    ]

    # 待审文档条款
    rev_clauses = rec.clauses or []

    # 4. 执行结构化集合比对
    diff_report = diff_clause_sets(tpl_clauses, rev_clauses)

    return {
        "uploaded_file": str(docx_path),
        "detected_subtype": rec.subtype,
        "classification_confidence": rec.classification_confidence,
        "matched_template_id": matched_tpl["document_id"],
        "matched_template_name": matched_tpl.get("source_file", ""),
        "standard_clause_count": len(tpl_clauses),
        "uploaded_clause_count": len(rev_clauses),
        "diff_report": diff_report,
    }


def print_diff_report(res: dict):
    print("=" * 80)
    print("【AI 合同模板对比与条款级 Diff 报告】")
    print("=" * 80)
    print(f"待审文档: {res['uploaded_file']}")
    print(f"识别类型: {res['detected_subtype']} (置信度: {res['classification_confidence']})")
    print(f"匹配模板: [{res['matched_template_id']}] {res['matched_template_name']}")
    print(f"条款规模: 标准母本 {res['standard_clause_count']} 条 | 待审文档 {res['uploaded_clause_count']} 条")
    print("-" * 80)

    categories = res["diff_report"]
    missing = [c for c in categories if c["status"] == "MISSING_IN_REVIEWED"]
    new_in = [c for c in categories if c["status"] == "NEW_IN_REVIEWED"]
    diffs = [c for c in categories if c["status"] == "DIFF"]
    matches = [c for c in categories if c["status"] == "MATCH"]

    print(f"比对统计: 一致类别 {len(matches)} 项 | 实质差异 {len(diffs)} 项 | 客户删除 {len(missing)} 项 | 客户新增 {len(new_in)} 项\n")

    if missing:
        print("🚩 【高危缺失】待审文档删除了标准模板中的必要条款类别：")
        for m in missing:
            print(f"  ❌ 缺失类别: {m['clause_category']}")
            print(f"     标准母本原文摘录: {m.get('standard_text', '')[:100]}...\n")

    if new_in:
        print("⚠️ 【新增条款】待审文档包含了标准模板中不存在的自定义条款：")
        for n in new_in:
            print(f"  ➕ 新增类别: {n['clause_category']} ({n.get('note', '')})\n")

    if diffs:
        print("🔍 【实质修改】以下同类条款存在关键法律要素或表述变动：")
        for d in diffs:
            print(f"  ▶ 条款类别: {d['clause_category']}")
            for delta in d.get("deltas", []):
                dtype = delta["delta_type"]
                desc = delta["description"]
                s_text = delta.get("standard_text", "")
                r_text = delta.get("reviewed_text", "")
                print(f"    - [变动类型: {dtype}] {desc}")
                if dtype in ("NUMBER_CHANGE", "DEADLINE_CHANGE", "PARTY_CHANGE", "NEGATION_CHANGE", "OBLIGATION_CHANGE"):
                    print(f"      • 标准模板原文: {s_text[:80]}")
                    print(f"      • 待审修改原文: {r_text[:80]}")
            print()

    if not missing and not new_in and not diffs:
        print("✅ 完美匹配：待审合同与标准模板条款架构与核心要素完全一致，未发现篡改。")
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="合同模板比对与条款级 Diff 工具")
    parser.add_argument("docx_path", help="待审合同 DOCX 文件路径")
    parser.add_argument("--template-id", help="强制指定基准模板 ID (如 MB20251205012)")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出完整比对结构")
    args = parser.parse_args()

    res = run_diff(args.docx_path, args.template_id)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print_diff_report(res)


if __name__ == "__main__":
    main()
