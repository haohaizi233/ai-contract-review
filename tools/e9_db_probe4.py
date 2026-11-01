# -*- coding: utf-8 -*-
"""对比 OK/BAD 附件的引用挂载点（docimagefile / 请求附件列等），找鉴权差异根源。

用法: python tools/e9_db_probe4.py
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

    print("== workflow_requestbase 实际列名（含 annex/doc/acc 关键字）==")
    cols = rows(cur, ("SELECT COLUMN_NAME AS c FROM INFORMATION_SCHEMA.COLUMNS "
                      "WHERE TABLE_NAME='workflow_requestbase' ORDER BY ORDINAL_POSITION"))
    names = [c["c"] for c in cols]
    print(" ", [n for n in names if any(k in n.lower() for k in ("annex", "doc", "acc", "file", "secr"))])

    print()
    print("== 请求主档（关键列）==")
    r = rows(cur, ("SELECT requestid, requestmark, requestname, creater, creatertype, status, workflowid, "
                   "currentnodetype, remark FROM workflow_requestbase WHERE requestid=%d") % RID)
    print(" ", r)

    print()
    print("== docimagefile 引用 ==")
    try:
        print(" ", rows(cur, "SELECT * FROM docimagefile WHERE imagefileid IN (%d,%d)" % (FID_OK, FID_BAD)))
    except Exception as e:
        conn.rollback()
        print("  失败:", e)

    print()
    print("== 其他常见挂载表探测 ==")
    for t, col in [("Accessory", "accessorycount"), ("DocDetail", "docimagefileids"),
                   ("imagefile_doc", "imagefileid"), ("workflow_annex", "imagefileid"),
                   ("formtable_main_130", "htfj")]:
        try:
            if t == "formtable_main_130":
                rr = rows(cur, "SELECT TOP 3 id, htfj, scgdhtsmj FROM %s WHERE htfj LIKE '%%%d%%' OR scgdhtsmj LIKE '%%%d%%'" % (t, FID_BAD, FID_OK))
            else:
                rr = rows(cur, "SELECT TOP 3 * FROM %s" % t)
            print("  %s: %s" % (t, str(rr)[:200]))
        except Exception as e:
            conn.rollback()
            print("  %s: 不可用 %s" % (t, str(e)[:80]))

    print()
    print("== 按 fileid 反查所有含附件字段的表（抽样几个失败/成功的）==")
    for fid in (FID_OK, FID_BAD):
        hits = rows(cur, ("SELECT f.fieldname, i.labelname FROM workflow_billfield f "
                          "LEFT JOIN HtmlLabelInfo i ON i.indexid=f.fieldlabel AND i.languageid=7 "
                          "WHERE f.billid=-130 AND f.fieldhtmltype='6'"))
        print("  fileid=%d 本表附件字段: %s" % (fid, [(h["fieldname"], h["labelname"]) for h in hits]))

    print()
    print("== HrmResource 3426 ==")
    print(" ", rows(cur, "SELECT id, loginid, lastname, seclevel, status, belongto FROM HrmResource WHERE loginid='3426'"))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
