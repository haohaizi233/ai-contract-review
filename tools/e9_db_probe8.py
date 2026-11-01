# -*- coding: utf-8 -*-
"""统计验证下载权限假设：OK/BAD fileid 的 DocImageFile / DocPreviewHtmlImage 挂载相关性。

用法: python tools/e9_db_probe8.py [样本数]
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import urllib.request
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


def collect_ids(n):
    ok = []
    for p in OUT.rglob("attachments/*/*"):
        if p.is_file() and not p.name.endswith(".part"):
            m = re.match(r"(\d+)-", p.name)
            if m and not p.read_bytes()[:20].lstrip().lower().startswith((b"<!doctype", b"<html")):
                ok.append(int(m.group(1)))
        if len(ok) >= n:
            break
    bad = []
    for p in sorted(OUT.rglob("*.md")):
        if p.name.startswith("_"):
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"FileDownload\?fileid=(\d+)\)\（\*\*下载失败\*\*", t):
            bad.append(int(m.group(1)))
        if len(bad) >= n:
            break
    return ok, bad


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    ok, bad = collect_ids(n)
    print("样本 OK=%d BAD=%d" % (len(ok), len(bad)))

    conn = pytds.connect(
        dsn=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "1433")),
        user=os.environ["DB_USER"], password=os.environ["DB_PWD_READONLY"],
        database="ecologyE9", timeout=60, login_timeout=60)
    cur = conn.cursor()

    def anchor(fid):
        di = rows(cur, "SELECT docid FROM DocImageFile WHERE imagefileid=%d" % fid)
        pv = rows(cur, ("SELECT docId, imageFileId, picFileId FROM DocPreviewHtmlImage "
                        "WHERE imageFileId=%d OR picFileId=%d") % (fid, fid))
        return di, pv

    stat = {"ok_has_anchor": 0, "ok_no_anchor": 0, "bad_has_anchor": 0, "bad_no_anchor": 0}
    docids = set()
    detail = []
    for cat, ids in (("OK", ok), ("BAD", bad)):
        for fid in ids:
            di, pv = anchor(fid)
            has = bool(di or pv)
            stat["%s_%s" % ("ok" if cat == "OK" else "bad", "has_anchor" if has else "no_anchor")] += 1
            ds = [r["docid"] for r in di] + [r["docId"] for r in pv]
            docids.update(ds)
            detail.append((cat, fid, ds))
    print("统计:", stat)
    print()
    for cat, fid, ds in detail:
        print("  %s %d -> docs=%s" % (cat, fid, ds))

    # 浏览器可读性：对涉及文档用会话 cookie 请求 DocDsp，看是否无权限
    print()
    print("== 文档可读性（DocDsp 返回体判定）==")
    ck = CK.read_text(encoding="utf-8").strip()
    h = {"Cookie": ck, "User-Agent": "Mozilla/5.0"}
    for did in sorted(docids):
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                OA + "/weaver/docs/docs/DocDsp.jsp?id=%d" % did, headers=h), timeout=30)
            body = r.read()
            t = body.decode("utf-8", "replace")
            no_perm = ("没有权限" in t) or ("无权限" in t) or ("暂时没有权限" in t)
            print("  doc %d: %d 字节 %s" % (did, len(body), "不可读(无权限)" if no_perm else "可读/其他"))
        except Exception as e:
            print("  doc %d: 异常 %r" % (did, e))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
