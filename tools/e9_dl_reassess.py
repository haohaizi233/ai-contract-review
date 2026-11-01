# -*- coding: utf-8 -*-
"""抽样评估：19982 个失败 fileid 中现在实际可下载的比例（按密级类别分层）。

用法: python tools/e9_dl_reassess.py [抽样数]
"""
from __future__ import annotations

import os
import random
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
        b = r.read(64)
        return not (b.lstrip()[:9].lower().startswith(b"<!doctype")
                    or b.lstrip()[:5].lower().startswith(b"<html"))
    except Exception:
        return False


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    random.seed(42)
    cookie = CK.read_text(encoding="utf-8").strip()

    failed = []
    for p in OUT.rglob("*.md"):
        if p.name.startswith("_"):
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"FileDownload\?fileid=(\d+)\)\（\*\*下载失败\*\*", t):
            failed.append(int(m.group(1)))
    failed = sorted(set(failed))
    print("失败 fileid 总数：%d" % len(failed))
    sample = random.sample(failed, min(n, len(failed)))

    conn = pytds.connect(
        dsn=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "1433")),
        user=os.environ["DB_USER"], password=os.environ["DB_PWD_READONLY"],
        database="ecologyE9", timeout=60, login_timeout=60)
    cur = conn.cursor()

    by_cat: dict[str, Counter] = {}
    detail = []
    for fid in sample:
        ok = http_ok(fid, cookie)
        di = rows(cur, "SELECT TOP 1 docid FROM DocImageFile WHERE imagefileid=%d" % fid)
        pv = rows(cur, ("SELECT TOP 1 docId FROM DocPreviewHtmlImage "
                        "WHERE imageFileId=%d OR picFileId=%d") % (fid, fid))
        cat = "(无锚点)"
        for d in ([r["docid"] for r in di] + [r["docId"] for r in pv])[:1]:
            r = rows(cur, "SELECT seccategory FROM DocDetail WHERE id=%d" % d)
            cat = str(r[0]["seccategory"]) if r else "(Doc缺失)"
        by_cat.setdefault(cat, Counter())["ok" if ok else "denied"] += 1
        detail.append((fid, cat, ok))
        import time
        time.sleep(0.25)

    print()
    print("fileid | 类别 | 现在可下载")
    for fid, cat, ok in detail:
        print("  %d | %s | %s" % (fid, cat, "OK" if ok else "无权限"))
    print()
    print("类别 | 可下载 | 无权限 | 可下载率")
    tot_ok = tot = 0
    for cat, c in sorted(by_cat.items(), key=lambda kv: -sum(kv[1].values())):
        o, d = c["ok"], c["denied"]
        tot_ok += o
        tot += o + d
        print("  %-8s %4d %6d   %5.1f%%" % (cat, o, d, 100.0 * o / (o + d)))
    print("整体可下载率: %d/%d = %.1f%%" % (tot_ok, tot, 100.0 * tot_ok / max(tot, 1)))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
