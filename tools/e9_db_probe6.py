# -*- coding: utf-8 -*-
"""深挖下载权限差异：file_download_permission 全表 + 附件字段定义对比 + 含 file 关键字列的引用扫描。

用法: python tools/e9_db_probe6.py
"""
from __future__ import annotations

import os
import sys

import pytds

SAMPLES_OK = [299892, 347766]
SAMPLES_BAD = [297063, 299990, 173654]
ALL = SAMPLES_OK + SAMPLES_BAD


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

    print("== file_download_permission 结构 ==")
    for c in rows(cur, ("SELECT COLUMN_NAME AS c, DATA_TYPE AS t, CHARACTER_MAXIMUM_LENGTH AS l "
                        "FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='file_download_permission' "
                        "ORDER BY ORDINAL_POSITION")):
        print(" ", c)
    total = rows(cur, "SELECT COUNT(*) AS n FROM file_download_permission")[0]["n"]
    print("总行数:", total)
    for f in ALL:
        try:
            rr = rows(cur, "SELECT * FROM file_download_permission WHERE imagefileid='%d'" % f)
            print("  fileid=%d -> %s" % (f, rr))
        except Exception as e:
            conn.rollback()
            print("  fileid=%d 查询失败 %s" % (f, str(e)[:80]))
    print("样例行:", rows(cur, "SELECT TOP 10 * FROM file_download_permission"))

    print()
    print("== htfj / scgdhtsmj 字段定义全列对比 ==")
    for fn in ("htfj", "scgdhtsmj"):
        rr = rows(cur, "SELECT * FROM workflow_billfield WHERE billid=-130 AND fieldname='%s'" % fn)
        if rr:
            keep = {k: v for k, v in rr[0].items() if v not in (None, "")}
            print(" %s: %s" % (fn, keep))

    print()
    print("== 全库含 file 字样的列（探测引用）==")
    cols = rows(cur, ("SELECT TABLE_NAME AS t, COLUMN_NAME AS c FROM INFORMATION_SCHEMA.COLUMNS "
                      "WHERE COLUMN_NAME LIKE '%file%' AND TABLE_NAME NOT LIKE 'syncobj%' "
                      "ORDER BY TABLE_NAME"))
    print("候选列 %d 个" % len(cols))
    for cb in cols:
        t, c = cb["t"], cb["c"]
        try:
            cond = " OR ".join(["CAST(%s AS VARCHAR(4000)) LIKE '%%%d%%'" % (c, f) for f in ALL])
            rr = rows(cur, "SELECT TOP 6 * FROM [%s] WHERE %s" % (t, cond))
            if rr:
                keys = list(rr[0].keys())[:8]
                print("  [%s.%s] 命中 %d 行 例: %s" % (t, c, len(rr),
                      {k: rr[0][k] for k in keys}))
        except Exception:
            conn.rollback()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
