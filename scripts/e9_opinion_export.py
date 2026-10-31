# -*- coding: utf-8 -*-
"""e9 审批意见按案号导出工具（Markdown，保留前端富文本显示格式）。

数据源（ecologyE9，只读账号）：
- workflow_requestbase   申请单主表（requestmark=案号/单号，requestname=标题）
- workflow_requestLog    流程流转意见（logtype/remark 富文本）
- workflow_approvelog    审批意见（nodeid/operator/remark 富文本）
- workflow_nodebase / HrmResource  环节名称 / 人员姓名

输出：每个案号一个 .md（意见区保留原始 HTML：截图 <img>、表格、红色编号样式与前端一致），
外加 _index.csv 汇总。默认输出目录 data/e9-opinion-export/（gitignore 内，不入库）。

用法示例：
  python scripts/e9_opinion_export.py --workflow-like 合同 --from-date 2026-09-01 --limit 50
  python scripts/e9_opinion_export.py --requestmark HCCX-KH001-20261030-1234
  python scripts/e9_opinion_export.py --requestmark-prefix LABH2026 --limit 200
  python scripts/e9_opinion_export.py --recent 100        # 按 requestid 倒序取最近 100 个有案号的单

凭据默认取用户环境变量 DB_HOST / DB_PORT / DB_USER / DB_PWD_READONLY（不写死在代码里）。
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path

import pytds

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = PROJECT_ROOT / "data" / "e9-opinion-export"

INVALID_FS_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def connect(args):
    return pytds.connect(
        dsn=args.host, port=args.port, user=args.user, password=args.password,
        database=args.database, timeout=args.timeout, login_timeout=30)


def rows(cur, sql, params=None):
    cur.execute(sql, params or ())
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def pick_candidates(cur, args) -> list[dict]:
    """按过滤条件挑选待导出的申请单（含案号 requestmark）。"""
    where = ["b.requestmark IS NOT NULL", "LTRIM(RTRIM(b.requestmark)) <> ''"]
    params: list = []
    if args.requestmark:
        where.append("b.requestmark IN (%s)" % ",".join("%s" * len(args.requestmark)))
        params.extend(args.requestmark)
    if args.requestmark_prefix:
        where.append("b.requestmark LIKE %s")
        params.append(args.requestmark_prefix + "%")
    if args.workflow_like:
        where.append("b.workflowid IN (SELECT id FROM workflow_base WHERE workflowname LIKE %s)")
        params.append("%" + args.workflow_like + "%")
    if args.from_date:
        where.append("b.createdate >= %s")
        params.append(args.from_date)
    if args.to_date:
        where.append("b.createdate <= %s")
        params.append(args.to_date)
    sql = ("SELECT b.requestid, b.requestmark, b.requestname, b.createdate, b.createtime, "
           "b.status, b.workflowid, b.creater FROM workflow_requestbase b WHERE "
           + " AND ".join(where))
    if args.recent:
        sql += " ORDER BY b.requestid DESC"
    else:
        sql += " ORDER BY b.createdate DESC, b.requestid DESC"
    # SQL Server 分页：TOP
    if args.limit:
        sql = sql.replace("SELECT b.requestid", "SELECT TOP %d b.requestid" % int(args.limit), 1)
    return rows(cur, sql, params)


def load_names(cur) -> dict:
    names = {}
    for r in rows(cur, "SELECT id, lastname FROM HrmResource"):
        names[str(r["id"])] = r["lastname"] or str(r["id"])
    for r in rows(cur, "SELECT id, nodename FROM workflow_nodebase"):
        names["node:" + str(r["id"])] = r["nodename"] or ("节点" + str(r["id"]))
    for r in rows(cur, "SELECT id, workflowname FROM workflow_base"):
        names["wf:" + str(r["id"])] = r["workflowname"] or ("流程" + str(r["id"]))
    return names


def fetch_opinions(cur, requestid: int) -> list[dict]:
    """一个申请单的全部意见（两表合并、按时间排序、去重）。"""
    items = []
    for r in rows(cur,
                  "SELECT logtype, nodeid, operator, operatedate, operatetime, "
                  "CAST(remark AS nvarchar(max)) AS remark FROM workflow_requestLog "
                  "WHERE requestid=%s ORDER BY operatedate, operatetime", (requestid,)):
        if r["remark"] and r["remark"].strip():
            items.append({"src": "流转意见", "logtype": r["logtype"], "nodeid": r["nodeid"],
                          "operator": r["operator"], "date": r["operatedate"],
                          "time": r["operatetime"], "html": r["remark"].strip()})
    for r in rows(cur,
                  "SELECT nodeid, operator, logdate, logtime, "
                  "CAST(remark AS nvarchar(max)) AS remark FROM workflow_approvelog "
                  "WHERE requestid=%s ORDER BY logdate, logtime", (requestid,)):
        if r["remark"] and r["remark"].strip():
            items.append({"src": "审批意见", "logtype": None, "nodeid": r["nodeid"],
                          "operator": r["operator"], "date": r["logdate"],
                          "time": r["logtime"], "html": r["remark"].strip()})
    items.sort(key=lambda x: (x["date"] or "", x["time"] or ""))
    seen, out = set(), []
    for it in items:
        key = (it["operator"], it["date"], it["time"], it["html"][:200])
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def rewrite_imgs(html: str, oa_base: str) -> str:
    if not oa_base:
        return html
    return html.replace('src="/', 'src="%s/' % oa_base.rstrip("/"))


def render_markdown(req: dict, opinions: list[dict], names: dict, oa_base: str) -> str:
    wf = names.get("wf:" + str(req["workflowid"]), req["workflowid"])
    creater = names.get(str(req.get("creater") or ""), "") or ""
    lines = [
        "# %s" % (req["requestmark"] or req["requestid"]),
        "",
        "| 项 | 内容 |",
        "|---|---|",
        "| 案号/单号 | %s |" % (req["requestmark"] or ""),
        "| 流程 | %s |" % wf,
        "| 标题 | %s |" % (req["requestname"] or ""),
        "| 发起人 | %s |" % creater,
        "| 发起日期 | %s %s |" % (req["createdate"], req["createtime"]),
        "| 状态 | %s |" % (req["status"] or ""),
        "| 意见条数 | %d |" % len(opinions),
        "",
        "---",
        "",
        "## 审批意见（按时间顺序）",
        "",
    ]
    for i, op in enumerate(opinions, 1):
        who = names.get(str(op["operator"]), op["operator"])
        node = names.get("node:" + str(op["nodeid"]), op["nodeid"])
        lines.append("### %d. %s · %s · %s %s" % (i, node, who, op["date"], op["time"]))
        lines.append("")
        lines.append("<!-- 来源：%s -->" % op["src"])
        lines.append("")
        lines.append(rewrite_imgs(op["html"], oa_base))
        lines.append("")
    return "\n".join(lines)


def safe_name(mark: str, fallback: str) -> str:
    name = INVALID_FS_CHARS.sub("_", mark or fallback).strip()
    return name or fallback


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="e9 审批意见按案号导出（Markdown）")
    ap.add_argument("--host", default=os.environ.get("DB_HOST", ""))
    ap.add_argument("--port", type=int, default=int(os.environ.get("DB_PORT", "1433")))
    ap.add_argument("--user", default=os.environ.get("DB_USER", ""))
    ap.add_argument("--password", default=os.environ.get("DB_PWD_READONLY", ""))
    ap.add_argument("--database", default="ecologyE9")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--oa-base", default="", help="OA 站点地址（用于改写意见内图片为绝对地址）")
    ap.add_argument("--requestmark", nargs="*", default=None, help="指定案号（可多个）")
    ap.add_argument("--requestmark-prefix", default=None, help="案号前缀")
    ap.add_argument("--workflow-like", default=None, help="流程名称包含（如：合同）")
    ap.add_argument("--from-date", default=None, help="发起日期起（YYYY-MM-DD）")
    ap.add_argument("--to-date", default=None, help="发起日期止（YYYY-MM-DD）")
    ap.add_argument("--recent", type=int, default=0, help="按 requestid 倒序取最近 N 个有案号的单")
    ap.add_argument("--limit", type=int, default=0, help="最多导出多少个案号")
    args = ap.parse_args(argv)

    if not (args.host and args.user and args.password):
        print("缺少连接信息：请设置 DB_HOST/DB_USER/DB_PWD_READONLY 或传参", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = connect(args)
    cur = conn.cursor()
    names = load_names(cur)

    if args.recent:
        cand = rows(cur, "SELECT TOP %d b.requestid, b.requestmark, b.requestname, b.createdate, "
                         "b.createtime, b.status, b.workflowid, b.creater FROM workflow_requestbase b "
                         "WHERE b.requestmark IS NOT NULL AND LTRIM(RTRIM(b.requestmark)) <> '' "
                         "ORDER BY b.requestid DESC" % int(args.recent))
    else:
        cand = pick_candidates(cur, args)

    index_rows = []
    for req in cand:
        opinions = fetch_opinions(cur, req["requestid"])
        fname = safe_name(req["requestmark"], str(req["requestid"])) + ".md"
        md = render_markdown(req, opinions, names, args.oa_base)
        (out_dir / fname).write_text(md, encoding="utf-8")
        index_rows.append([req["requestmark"], req["requestid"], req["requestname"],
                           names.get("wf:" + str(req["workflowid"]), req["workflowid"]),
                           req["createdate"], len(opinions), fname])
        print("exported %s -> %s (%d 意见)" % (req["requestmark"], fname, len(opinions)))

    with (out_dir / "_index.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["案号", "requestid", "标题", "流程", "发起日期", "意见条数", "文件"])
        w.writerows(index_rows)
    conn.close()
    print("完成：%d 个案号 -> %s" % (len(index_rows), out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
