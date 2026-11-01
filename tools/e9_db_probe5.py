# -*- coding: utf-8 -*-
"""系统对比"可下载"与"无权限"的 fileid：批量实测 + imagefile 行 + 引用挂载点。

用法: python tools/e9_db_probe5.py [样本数]
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


def test_dl(fid: int, cookie: str) -> bool:
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
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    cookie = CK.read_text(encoding="utf-8").strip()

    ok_ids = []
    for p in OUT.rglob("attachments/*/*"):
        if p.is_file() and not p.name.endswith(".part"):
            m = re.match(r"(\d+)-", p.name)
            if m:
                ok_ids.append(int(m.group(1)))
        if len(ok_ids) >= n * 3:
            break
    bad_ids = []
    for p in sorted(OUT.rglob("*.md")):
        if p.name.startswith("_"):
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"FileDownload\?fileid=(\d+)\)\（\*\*下载失败\*\*", t):
            bad_ids.append(int(m.group(1)))
        if len(bad_ids) >= n * 3:
            break

    print("样本：本地已有(假定OK) %d 个，md 标记失败 %d 个" % (len(ok_ids), len(bad_ids)))
    confirmed_ok = [f for f in ok_ids if test_dl(f, cookie)][:n]
    confirmed_bad = [f for f in bad_ids if not test_dl(f, cookie)][:n]
    print("复测确认：OK=%s" % confirmed_ok)
    print("复测确认：BAD=%s" % confirmed_bad)

    conn = pytds.connect(
        dsn=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "1433")),
        user=os.environ["DB_USER"], password=os.environ["DB_PWD_READONLY"],
        database="ecologyE9", timeout=60, login_timeout=60)
    cur = conn.cursor()

    all_ids = confirmed_ok + confirmed_bad
    if not all_ids:
        print("无样本可查")
        return 1
    recs = {}
    for fid in all_ids:
        r = rows(cur, "SELECT * FROM imagefile WHERE imagefileid=%d" % fid)
        recs[fid] = r[0] if r else None

    cols = list(next(v for v in recs.values() if v).keys())
    print()
    print("== 逐列对比（OK=第一个样本, BAD=第一个失败样本）==")
    a, b = recs[confirmed_ok[0]], recs[confirmed_bad[0]]
    for k in cols:
        va, vb = a[k], b[k]
        if k.lower() == "imagedata":
            va = "<%d字节>" % (len(va) if va else 0)
            vb = "<%d字节>" % (len(vb) if vb else 0)
        mark = "" if va == vb else "   <<< 不同"
        print("%-18s | %-30s | %-30s%s" % (k, va, vb, mark))

    print()
    print("== 逐 fileid 关键列 ==")
    keys = ["imagefileused", "iszip", "isencrypt", "isaesencrypt", "secretLevel", "StorageStatus",
            "comefrom", "isFTP", "downloads", "mainimagefile"]
    print("fid | 类别 | " + " | ".join(keys))
    for fid in all_ids:
        r = recs[fid]
        cat = "OK " if fid in confirmed_ok else "BAD"
        print("%d | %s | %s" % (fid, cat, " | ".join(str(r[k]) for k in keys)))

    print()
    print("== 引用挂载点探测（哪些表提到这些 fileid）==")
    tables = rows(cur, ("SELECT TABLE_NAME AS t, COLUMN_NAME AS c FROM INFORMATION_SCHEMA.COLUMNS "
                        "WHERE COLUMN_NAME IN ('imagefileid','imagefileids','docimagefileids','accessory','accessorycount')"))
    for tb in tables:
        t, c = tb["t"], tb["c"]
        try:
            if c.lower() in ("imagefileids", "docimagefileids", "accessory"):
                cond = " OR ".join(["%s LIKE '%%%d%%'" % (c, f) for f in all_ids])
            else:
                cond = " OR ".join(["%s=%d" % (c, f) for f in all_ids])
            rr = rows(cur, "SELECT TOP 20 * FROM [%s] WHERE %s" % (t, cond))
            if rr:
                print("  [%s.%s] %d 行: %s" % (t, c, len(rr), str(rr)[:260]))
        except Exception:
            conn.rollback()

    print()
    print("== HrmResource 3426 ==")
    print(" ", rows(cur, "SELECT id, loginid, lastname, seclevel, status FROM HrmResource WHERE loginid='3426'"))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
