# -*- coding: utf-8 -*-
"""决定性测试：3426 自己发起/可查看的请求，其表单附件能否下载。

用法: python tools/e9_dl_ownreq.py [requestid...]
"""
from __future__ import annotations

import os
import re
import sys
import urllib.request
from pathlib import Path

import pytds

ROOT = Path(__file__).resolve().parent.parent
CK = ROOT / "data" / ".e9_cookie"
OA = "http://hc.hcipi.com:8088"
RIDS = [int(x) for x in sys.argv[1:]] or [2110135, 1702997]


def rows(cur, sql):
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def http_kind(fid, cookie):
    h = {"Cookie": cookie, "User-Agent": "Mozilla/5.0"}
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            OA + "/weaver/weaver.file.FileDownload?fileid=%d" % fid, headers=h), timeout=30)
        b = r.read(64)
        return "文件OK" if not (b.lstrip()[:9].lower().startswith(b"<!doctype")
                               or b.lstrip()[:5].lower().startswith(b"<html")) else "无权限"
    except Exception as e:
        return "异常" + repr(e)[:30]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    cookie = CK.read_text(encoding="utf-8").strip()
    conn = pytds.connect(
        dsn=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "1433")),
        user=os.environ["DB_USER"], password=os.environ["DB_PWD_READONLY"],
        database="ecologyE9", timeout=60, login_timeout=60)
    cur = conn.cursor()

    for rid in RIDS:
        req = rows(cur, "SELECT requestmark, creater, status FROM workflow_requestbase WHERE requestid=%d" % rid)
        fm = rows(cur, "SELECT billformid, billid FROM workflow_form WHERE requestid=%d" % rid)
        print("== requestid=%d %s form=%s" % (rid, req, fm))
        if not fm or not fm[0]["billformid"]:
            continue
        bill_abs = abs(fm[0]["billformid"])
        table = "formtable_main_%d" % bill_abs
        row = rows(cur, "SELECT * FROM %s WHERE id=%d" % (table, fm[0]["billid"]))
        if not row:
            print("   无主表行")
            continue
        att_fields = rows(cur, ("SELECT fieldname FROM workflow_billfield "
                                "WHERE billid=-%d AND fieldhtmltype='6'") % bill_abs)
        names = [f["fieldname"] for f in att_fields]
        for col, val in row[0].items():
            if col.lower() in names and val:
                for fid in re.findall(r"\d+", str(val)):
                    print("   %s=%s -> fileid=%s %s" % (col, val, fid, http_kind(int(fid), cookie)))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
