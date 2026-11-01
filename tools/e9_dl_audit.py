# -*- coding: utf-8 -*-
"""汇总：失败附件的文档密级类别分布 + 谁有下载权 + 对比可下载子集。

产出: data/_failed_fileids.txt（失败 fileid 清单，供后续重下复用）
用法: python tools/e9_dl_audit.py
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter
from pathlib import Path

import pytds

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "e9-opinion-export"
FAILED_TXT = ROOT / "data" / "_failed_fileids.txt"


def rows(cur, sql):
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    failed, ok_local = set(), set()
    for p in OUT.rglob("*.md"):
        if p.name.startswith("_"):
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"FileDownload\?fileid=(\d+)\)\（\*\*下载失败\*\*", t):
            failed.add(int(m.group(1)))
    for p in OUT.rglob("attachments/*/*"):
        if p.is_file() and not p.name.endswith(".part"):
            m = re.match(r"(\d+)-", p.name)
            if m:
                ok_local.add(int(m.group(1)))
    print("md 标记下载失败 fileid：%d 个" % len(failed))
    print("本地已有附件 fileid：%d 个" % len(ok_local))
    FAILED_TXT.write_text("\n".join(str(x) for x in sorted(failed)), encoding="utf-8")
    print("失败清单已存:", FAILED_TXT)

    conn = pytds.connect(
        dsn=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "1433")),
        user=os.environ["DB_USER"], password=os.environ["DB_PWD_READONLY"],
        database="ecologyE9", timeout=60, login_timeout=60)
    cur = conn.cursor()

    def cat_dist(ids):
        dist = Counter()
        ids = list(ids)
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            cond = ",".join(str(x) for x in chunk)
            rr = rows(cur, """
                SELECT dd.seccategory AS c, COUNT(*) AS n
                FROM DocImageFile dif JOIN DocDetail dd ON dd.id = dif.docid
                WHERE dif.imagefileid IN (%s)
                GROUP BY dd.seccategory""" % cond)
            for r in rr:
                dist[int(r["c"] or -1)] += int(r["n"])
            rr2 = rows(cur, """
                SELECT dd.seccategory AS c, COUNT(*) AS n
                FROM DocPreviewHtmlImage pv JOIN DocDetail dd ON dd.id = pv.docId
                WHERE pv.imageFileId IN (%s) OR pv.picFileId IN (%s)
                GROUP BY dd.seccategory""" % (cond, cond))
            for r in rr2:
                dist[int(r["c"] or -1)] += int(r["n"])
        return dist

    print()
    print("== 失败附件的文档密级类别分布 ==")
    d_fail = cat_dist(failed)
    total = sum(d_fail.values())
    for c, n in d_fail.most_common(15):
        name = rows(cur, "SELECT categoryname FROM DocSecCategory WHERE id=%d" % c) if c > 0 else []
        print("  类别 %-5s %-22s %6d 个（%.1f%%）" % (c, name[0]["categoryname"] if name else "(无)", n, 100.0 * n / max(total, 1)))
    print("  覆盖锚点合计 %d / %d（其余无 doc 锚点）" % (total, len(failed)))

    print()
    print("== 本地已下载(成功)附件的类别分布 ==")
    d_ok = cat_dist(ok_local)
    for c, n in d_ok.most_common(10):
        name = rows(cur, "SELECT categoryname FROM DocSecCategory WHERE id=%d" % c) if c > 0 else []
        print("  类别 %-5s %-22s %6d 个" % (c, name[0]["categoryname"] if name else "(无)", n))

    print()
    print("== 谁能下载：角色 26 及关键用户 ==")
    tabs = rows(cur, ("SELECT TABLE_NAME AS t FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME LIKE '%Role%'"))
    print("  角色表:", [t["t"] for t in tabs])
    for t in ("HrmRoles", "hrmroles"):
        cols = [c["c"] for c in rows(cur, ("SELECT COLUMN_NAME AS c FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='%s'") % t)] if any(x["t"] == t for x in tabs) else []
        if cols:
            print("  %s 列: %s" % (t, cols[:12]))
            rr = rows(cur, "SELECT TOP 3 * FROM %s WHERE id=26" % t)
            print("  角色26:", rr)
            break
    try:
        rr = rows(cur, ("SELECT TOP 10 rm.resourceid, h.lastname, h.loginid, h.departmentid "
                        "FROM HrmRoleMembers rm JOIN HrmResource h ON h.id = rm.resourceid "
                        "WHERE rm.roleid = 26"))
        print("  角色26 成员:", rr)
    except Exception as e:
        conn.rollback()
        print("  角色成员查询失败:", str(e)[:80])
    for uid in (2190, 1792, 6, 163):
        rr = rows(cur, "SELECT id, loginid, lastname, departmentid, seclevel FROM HrmResource WHERE id=%d" % uid)
        print("  用户%d:" % uid, rr)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
