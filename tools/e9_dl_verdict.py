# -*- coding: utf-8 -*-
"""最终验证：大批样本 OK/BAD × 文档密级类别相关性 + 受限类别共享规则 + 可下载类别对照。

用法: python tools/e9_dl_verdict.py [样本数]
"""
from __future__ import annotations

import os
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path

import pytds

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "e9-opinion-export"
CK = ROOT / "data" / ".e9_cookie"
OA = "http://hc.hcipi.com:8088"


def rows(cur, sql):
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def http_ok(fid, cookie):
    h = {"Cookie": cookie, "User-Agent": "Mozilla/5.0"}
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            OA + "/weaver/weaver.file.FileDownload?fileid=%d" % fid, headers=h), timeout=30)
        body = r.read(64)
        return not (body.lstrip()[:9].lower().startswith(b"<!doctype") or body.lstrip()[:5].lower().startswith(b"<html"))
    except Exception:
        return False


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    cookie = CK.read_text(encoding="utf-8").strip()

    cand_ok, cand_bad = [], []
    for p in OUT.rglob("attachments/*/*"):
        if p.is_file() and not p.name.endswith(".part"):
            m = re.match(r"(\d+)-", p.name)
            if m and not p.read_bytes()[:20].lstrip().lower().startswith((b"<!doctype", b"<html")):
                cand_ok.append(int(m.group(1)))
        if len(cand_ok) >= n * 3:
            break
    for p in sorted(OUT.rglob("*.md")):
        if p.name.startswith("_"):
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"FileDownload\?fileid=(\d+)\)\（\*\*下载失败\*\*", t):
            cand_bad.append(int(m.group(1)))
        if len(cand_bad) >= n * 3:
            break

    ok = [f for f in cand_ok if http_ok(f, cookie)][:n]
    bad = [f for f in cand_bad if not http_ok(f, cookie)][:n]
    print("HTTP 复测样本：OK %d / BAD %d" % (len(ok), len(bad)))

    conn = pytds.connect(
        dsn=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "1433")),
        user=os.environ["DB_USER"], password=os.environ["DB_PWD_READONLY"],
        database="ecologyE9", timeout=60, login_timeout=60)
    cur = conn.cursor()

    print()
    print("== 1. OK/BAD 文件的文档类别分布 ==")
    dist = {"OK": Counter(), "BAD": Counter()}
    for cat, ids in (("OK", ok), ("BAD", bad)):
        for fid in ids:
            ds = rows(cur, "SELECT docid FROM DocImageFile WHERE imagefileid=%d" % fid)
            pv = rows(cur, "SELECT docId FROM DocPreviewHtmlImage WHERE imageFileId=%d OR picFileId=%d" % (fid, fid))
            docids = [r["docid"] for r in ds] + [r["docId"] for r in pv]
            if not docids:
                dist[cat]["(无锚点)"] += 1
                continue
            for did in docids[:1]:
                d = rows(cur, "SELECT seccategory FROM DocDetail WHERE id=%d" % did)
                dist[cat][str(d[0]["seccategory"]) if d else "(DocDetail缺失)"] += 1
    print("  OK 类别分布:", dict(dist["OK"]))
    print("  BAD 类别分布:", dict(dist["BAD"]))

    print()
    print("== 2. 受限类别共享规则（谁被授权）==")
    for cat in (20, 1112, 1156, 1398, 1503):
        try:
            rr = rows(cur, ("SELECT TOP 8 id, sharetype, seclevel, sharelevel, downloadlevel, userid, "
                            "departmentid, roleid, subcompanyid, foralluser "
                            "FROM DocSecCategoryShare WHERE seccategoryid=%d") % cat)
            names = []
            for r in rr:
                who = ("用户%d" % r["userid"] if r["sharetype"] == 1 and r["userid"] else
                       "部门%d" % r["departmentid"] if r["sharetype"] == 3 and r["departmentid"] else
                       "角色%d" % r["roleid"] if r["sharetype"] == 2 and r["roleid"] else
                       "分部%d" % r["subcompanyid"] if r["sharetype"] == 4 and r["subcompanyid"] else
                       "全员" if r["sharetype"] == 1 and r["userid"] == 0 else "sharetype=%s" % r["sharetype"])
                names.append("%s(dl=%s,level=%s)" % (who, r["downloadlevel"], r["sharelevel"]))
            print("  类别 %-5s 共%d条规则: %s" % (cat, len(rr), names))
        except Exception as e:
            conn.rollback()
            print("  类别 %d 查询失败 %s" % (cat, str(e)[:60]))

    print()
    print("== 3. 用户 3331(邹依柔) 所属 ==")
    print(" ", rows(cur, "SELECT id, departmentid, subcompanyid1, seclevel, jobtitle FROM HrmResource WHERE id=3331"))
    for dept in (1416,):
        print("  部门%d: %s" % (dept, rows(cur, "SELECT id, departmentname, supdepid FROM HrmDepartment WHERE id=%d" % dept)))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
