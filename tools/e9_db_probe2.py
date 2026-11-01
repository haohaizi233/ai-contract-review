# -*- coding: utf-8 -*-
"""全列对比 imagefile 行（不过滤任何列），找 OK/BAD 差异。

用法: python tools/e9_db_probe2.py
"""
from __future__ import annotations

import os
import sys

import pytds


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
    a = rows(cur, "SELECT * FROM imagefile WHERE imagefileid=299892")[0]
    b = rows(cur, "SELECT * FROM imagefile WHERE imagefileid=297063")[0]
    print("列名 | 299892(OK) | 297063(BAD)")
    print("---|---|---")
    for k in a:
        va, vb = a[k], b.get(k)
        if k.lower() == "imagedata":
            va, vb = ("<%d字节>" % (len(va) if va else 0)), ("<%d字节>" % (len(vb) if vb else 0))
        mark = "" if va == vb else "  <<"
        print("%s | %s | %s%s" % (k, va, vb, mark))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
