# -*- coding: utf-8 -*-
"""追查两个同请求内一好一坏的附件在库里的挂载点差异。

用法: python tools/e9_db_probe3.py
"""
from __future__ import annotations

import os
import sys

import pytds

FID_OK = 299892
FID_BAD = 297063
RID = 1231910


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

    fm = rows(cur, "SELECT billformid, billid FROM workflow_form WHERE requestid=%d" % RID)[0]
    table = "formtable_main_%d" % abs(fm["billformid"])
    print("requestid=%d -> %s.id=%d" % (RID, table, fm["billid"]))
    row = rows(cur, "SELECT * FROM %s WHERE id=%d" % (table, fm["billid"]))[0]
    for col, val in row.items():
        if val and str(val).find(str(FID_OK)) >= 0 or (val and str(val).find(str(FID_BAD)) >= 0):
            print("  列 %s = %r" % (col, val))

    print()
    print("== billid=-%d 的字段定义 ==" % abs(fm["billformid"]))
    for f in rows(cur, """SELECT f.id, f.fieldname, f.fieldlabel, f.fieldhtmltype, f.type, f.fielddbtype, f.dsporder,
                                i.labelname
                         FROM workflow_billfield f
                         LEFT JOIN HtmlLabelInfo i ON i.indexid=f.fieldlabel AND i.languageid=7
                         WHERE f.billid=-%d ORDER BY f.dsporder""" % abs(fm["billformid"])):
        print("  %-28s htmltype=%s type=%s dbtype=%-16s label=%s"
              % (f["fieldname"], f["fieldhtmltype"], f["type"], f["fielddbtype"], f["labelname"]))

    print()
    print("== 请求主档 ==")
    for r in rows(cur, ("SELECT requestid, requestmark, requestname, creater, creatertype, currentnodetype, "
                        "status, workflowid, isannexupload, annexmaindoc FROM workflow_requestbase "
                        "WHERE requestid=%d") % RID):
        print(" ", r)

    print()
    print("== workflow_requestbase 附件列 / docimagefile 引用 ==")
    r = rows(cur, "SELECT annex, accessory FROM workflow_requestbase WHERE requestid=%d" % RID)
    print("  annex/accessory:", r)
    for t in ("docimagefile",):
        try:
            rr = rows(cur, "SELECT TOP 5 * FROM %s WHERE imagefileid IN (%d,%d)" % (t, FID_OK, FID_BAD))
            print("  %s:" % t, rr)
        except Exception as e:
            conn.rollback()
            print("  %s 查询失败: %r" % (t, e))

    print()
    print("== 用户 3426 ==")
    for r in rows(cur, "SELECT id, loginid, lastname, seclevel, status FROM HrmResource WHERE loginid='3426' OR id=3426"):
        print(" ", r)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
