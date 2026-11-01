# -*- coding: utf-8 -*-
"""查请求状态/参与人 + 文档密级类别，验证"权限不足"假设。

用法: python tools/e9_db_probe10.py
"""
from __future__ import annotations

import os
import sys

import pytds

RIDS = [1231910, 1232688, 1235615, 1702997]
DOCS = [156655, 155287, 156641]
UID = 3331   # HrmResource.id（loginid=3426）


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

    print("== workflow_requestbase ==")
    for rid in RIDS:
        r = rows(cur, ("SELECT requestid, requestmark, requestname, creater, creatertype, status, "
                       "workflowid, currentnodetype, deleted FROM workflow_requestbase WHERE requestid=%d") % rid)
        if not r:
            print("  %d: 无记录" % rid)
            continue
        d = r[0]
        print("  %d mark=%s creater=%s status=%s wf=%s node=%s deleted=%s"
              % (rid, d["requestmark"], d["creater"], d["status"], d["workflowid"], d["currentnodetype"], d["deleted"]))

    print()
    print("== 参与人（workflow_currentoperator 里是否有 3331）==")
    for rid in RIDS:
        try:
            r = rows(cur, ("SELECT userid, usertype, nodeid, isremark, takisremark FROM workflow_currentoperator "
                           "WHERE requestid=%d AND userid=%d") % (rid, UID))
            print("  %d: %s" % (rid, r if r else "3331 不是参与人"))
        except Exception as e:
            conn.rollback()
            print("  %d 查询失败: %r" % (rid, e))

    print()
    print("== DocDetail 密级类别 ==")
    for did in DOCS:
        r = rows(cur, "SELECT id, docsubject, seccategory, secretLevel, doccreaterid, docdepartmentid, docstatus FROM DocDetail WHERE id=%d" % did)
        print("  doc %d: %s" % (did, r))

    print()
    print("== SecCategory（这些类别的读权限设置）==")
    cats = set()
    for did in DOCS:
        for r in rows(cur, "SELECT seccategory FROM DocDetail WHERE id=%d" % did):
            cats.add(r["seccategory"])
    for c in sorted(cats):
        r = rows(cur, ("SELECT id, categoryname, seclevel, readperm, docmouldid, cancelable "
                       "FROM SecCategory WHERE id=%d") % c)
        print("  cat %d: %s" % (c, r))

    print()
    print("== HrmResource 3331 / 3426 相关 ==")
    print(" ", rows(cur, "SELECT id, loginid, lastname, seclevel, departmentid, subcompanyid1, jobtitle FROM HrmResource WHERE id=3331 OR loginid='3426'"))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
