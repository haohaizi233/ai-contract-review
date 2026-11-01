# -*- coding: utf-8 -*-
"""决胜对比：同一密级类别下，可下载 vs 无权限文件的 DocShare 差异。

用法: python tools/e9_dl_sharecheck.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytds

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "e9-opinion-export"
UID = 3331
DEPT = 1416


def rows(cur, sql):
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    cookie = (ROOT / "data" / ".e9_cookie").read_text(encoding="utf-8").strip()
    import urllib.request

    def http_ok(fid):
        h = {"Cookie": cookie, "User-Agent": "Mozilla/5.0"}
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                "http://hc.hcipi.com:8088/weaver/weaver.file.FileDownload?fileid=%d" % fid, headers=h), timeout=30)
            b = r.read(64)
            return not (b.lstrip()[:9].lower().startswith(b"<!doctype") or b.lstrip()[:5].lower().startswith(b"<html"))
        except Exception:
            return False

    failed = set()
    for p in OUT.rglob("*.md"):
        if p.name.startswith("_"):
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"FileDownload\?fileid=(\d+)\)\（\*\*下载失败\*\*", t):
            failed.add(int(m.group(1)))
    local = set()
    for p in OUT.rglob("attachments/*/*"):
        if p.is_file() and not p.name.endswith(".part"):
            m = re.match(r"(\d+)-", p.name)
            if m:
                local.add(int(m.group(1)))
    print("失败 %d / 本地 %d" % (len(failed), len(local)))

    conn = pytds.connect(
        dsn=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "1433")),
        user=os.environ["DB_USER"], password=os.environ["DB_PWD_READONLY"],
        database="ecologyE9", timeout=60, login_timeout=60)
    cur = conn.cursor()

    def anchors(fid):
        r1 = rows(cur, "SELECT docid AS d FROM DocImageFile WHERE imagefileid=%d" % fid)
        r2 = rows(cur, "SELECT docId AS d FROM DocPreviewHtmlImage WHERE imageFileId=%d OR picFileId=%d" % (fid, fid))
        return [x["d"] for x in r1 + r2]

    def cat_of(fid):
        out = []
        for d in anchors(fid):
            r = rows(cur, "SELECT seccategory FROM DocDetail WHERE id=%d" % d)
            out.append((d, r[0]["seccategory"] if r else None))
        return out

    # 在类别 20 里各找 2 个 OK / BAD 样本做 DocShare 对比
    ok_c20, bad_c20 = [], []
    for fid in list(local)[:80]:
        if len(ok_c20) >= 2:
            break
        if http_ok(fid) and any(c == 20 for _, c in cat_of(fid)):
            ok_c20.append(fid)
    for fid in list(failed)[:80]:
        if len(bad_c20) >= 2:
            break
        if any(c == 20 for _, c in cat_of(fid)):
            bad_c20.append(fid)
    print("类别20 样本 -> OK:", ok_c20, " BAD:", bad_c20)

    def share_summary(fid):
        for d, c in cat_of(fid):
            sh = rows(cur, ("SELECT TOP 30 sharetype, seclevel, sharelevel, downloadlevel, userid, "
                            "departmentid, roleid, subcompanyid FROM DocShare WHERE docid=%d") % d)
            mine = [s for s in sh if s["userid"] == UID or s["departmentid"] == DEPT]
            print("  fileid=%d doc=%d cat=%s share行=%d 其中命中3331/1416: %s"
                  % (fid, d, c, len(sh), mine))
            others = [s for s in sh if s not in mine][:8]
            for s in others:
                print("      ", s)

    print("== OK 样本 DocShare ==")
    for f in ok_c20:
        share_summary(f)
    print("== BAD 样本 DocShare ==")
    for f in bad_c20:
        share_summary(f)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
