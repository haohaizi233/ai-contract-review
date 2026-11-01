# -*- coding: utf-8 -*-
"""验证"DocImageFile 挂载决定下载权限"假设，并取请求创建人 / 文档权限信息。

用法: python tools/e9_db_probe7.py
"""
from __future__ import annotations

import os
import sys

import pytds

ALL = [299892, 347766, 297063, 299990, 173654, 297629, 299877, 310500, 184441]
RIDS = {299892: 1231910, 297063: 1231910, 299990: 1232688, 297629: 1232688,
        299877: 1235615, 310500: 1235615}


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

    print("== DocImageFile 挂载（fileid -> docid）==")
    mapping = {}
    for fid in ALL:
        rr = rows(cur, "SELECT id, docid, imagefileid, imagefilename, docfiletype FROM DocImageFile WHERE imagefileid=%d" % fid)
        mapping[fid] = rr
        print("  %d -> %s" % (fid, rr))

    print()
    print("== 涉及文档 DocDetail ==")
    for fid, rr in mapping.items():
        for r in rr:
            d = rows(cur, ("SELECT id, docsubject, doccreaterid, docdepartmentid, docseccategory, docseclevel, "
                           "docstatus, doclanguage FROM DocDetail WHERE id=%d") % r["docid"])
            print("  fileid=%d docid=%d -> %s" % (fid, r["docid"], d))

    print()
    print("== 请求创建人 ==")
    for fid, rid in RIDS.items():
        rr = rows(cur, ("SELECT requestid, requestmark, requestname, creater, creatertype, status, workflowid "
                        "FROM workflow_requestbase WHERE requestid=%d") % rid)
        print("  fileid=%d rid=%d -> %s" % (fid, rid, rr))

    print()
    print("== 3426 本人 ==")
    print(" ", rows(cur, "SELECT id, loginid, lastname, seclevel, departmentid, subcompanyid1, managerid FROM HrmResource WHERE loginid='3426'"))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
