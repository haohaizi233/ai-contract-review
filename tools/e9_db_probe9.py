# -*- coding: utf-8 -*-
"""汇总 OK/BAD fileid × HTTP 实测 × DocImageFile × DocDetail 权限相关列，找判别列。

用法: python tools/e9_db_probe9.py [样本数]
"""
from __future__ import annotations

import os
import re
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
        if len(cand_ok) >= n * 2:
            break
    for p in sorted(OUT.rglob("*.md")):
        if p.name.startswith("_"):
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"FileDownload\?fileid=(\d+)\)\（\*\*下载失败\*\*", t):
            cand_bad.append(int(m.group(1)))
        if len(cand_bad) >= n * 2:
            break

    ok = [f for f in cand_ok if http_ok(f, cookie)][:n]
    bad = [f for f in cand_bad if not http_ok(f, cookie)][:n]
    print("HTTP 复测：OK %d 个 / BAD %d 个" % (len(ok), len(bad)))

    conn = pytds.connect(
        dsn=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "1433")),
        user=os.environ["DB_USER"], password=os.environ["DB_PWD_READONLY"],
        database="ecologyE9", timeout=60, login_timeout=60)
    cur = conn.cursor()

    dcols = [c["c"] for c in rows(cur, ("SELECT COLUMN_NAME AS c FROM INFORMATION_SCHEMA.COLUMNS "
                                        "WHERE TABLE_NAME='DocDetail' ORDER BY ORDINAL_POSITION"))]
    keep_cols = [c for c in dcols if any(k in c.lower() for k in
                 ("id", "subject", "creater", "sec", "depart", "status", "share", "access", "scope", "level", "type"))]
    print("DocDetail 权限相关列:", keep_cols)

    print()
    print("cat | fid | docid | " + " | ".join(keep_cols))
    for cat, ids in (("OK", ok), ("BAD", bad)):
        for fid in ids:
            di = rows(cur, "SELECT docid FROM DocImageFile WHERE imagefileid=%d" % fid)
            pv = rows(cur, "SELECT docId FROM DocPreviewHtmlImage WHERE imageFileId=%d OR picFileId=%d" % (fid, fid))
            docids = [r["docid"] for r in di] + [r["docId"] for r in pv]
            if not docids:
                print("%s | %d | - | (无文档锚点)" % (cat, fid))
                continue
            for did in docids[:1]:
                d = rows(cur, "SELECT TOP 1 * FROM DocDetail WHERE id=%d" % did)
                if not d:
                    print("%s | %d | %d | DocDetail 无记录" % (cat, fid, did))
                    continue
                vals = [str(d[0].get(c)) for c in keep_cols]
                print("%s | %d | %d | %s" % (cat, fid, did, " | ".join(vals)))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
