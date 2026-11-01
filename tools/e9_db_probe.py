# -*- coding: utf-8 -*-
"""对比可下载 / 无权限的 imagefile 行，找 FileDownload 鉴权差异。

用法: python tools/e9_db_probe.py
"""
from __future__ import annotations

import os
import sys

import pytds

FID_OK = 299892          # 实测可下载（CIPCX2023HT03123）
FID_BAD = [297629, 299990, 297063, 299877, 310500]   # 实测"对不起，您暂时没有权限"


def rows(cur, sql):
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    conn = pytds.connect(
        dsn=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "1433")),
        user=os.environ["DB_USER"], password=os.environ["DB_PWD_READONLY"],
        database="ecologyE9", timeout=60, login_timeout=60)
    cur = conn.cursor()

    for fid in [FID_OK] + FID_BAD:
        r = rows(cur, "SELECT * FROM imagefile WHERE imagefileid=%d" % fid)
        if not r:
            print("imagefile %d: 无记录" % fid)
            continue
        d = r[0]
        keep = {k: v for k, v in d.items()
                if v not in (None, "", 0) and str(k).lower() not in ("imagedata",)}
        print("imagefile %d [%s]: %s" % (fid, "OK" if fid == FID_OK else "BAD", keep))

    print()
    for rid, fid in ((1231910, 297063), (1231910, 299892), (1232688, 297629)):
        fm = rows(cur, "SELECT billformid, billid FROM workflow_form WHERE requestid=%d" % rid)
        print("requestid=%d fileid=%d -> workflow_form=%s" % (rid, fid, fm))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
