# -*- coding: utf-8 -*-
"""收口：取密级类别共享规则原始行（含 foralluser/roleid），解释 OK/BAD 判别规则。

用法: python tools/e9_dl_rulecheck.py
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

    print("== DocSecCategoryShare 原始行 ==")
    cols = [c["c"] for c in rows(cur, ("SELECT COLUMN_NAME AS c FROM INFORMATION_SCHEMA.COLUMNS "
                                       "WHERE TABLE_NAME='DocSecCategoryShare' ORDER BY ORDINAL_POSITION"))]
    keep = [c for c in cols if c.lower() in (
        "id", "seccategoryid", "sharetype", "userid", "departmentid", "roleid", "subcompanyid",
        "foralluser", "seclevel", "sharelevel", "downloadlevel", "crmid", "hrmresid", "projectid")]
    print("列:", keep)
    for cat in (20, 45, 5, 1110, 1112, 1156, 1398, 1503):
        rr = rows(cur, ("SELECT %s FROM DocSecCategoryShare WHERE seccategoryid=%d" % (",".join(keep), cat)))
        print("\n类别 %d (%d 行):" % (cat, len(rr)))
        for r in rr:
            print("   ", r)

    print()
    print("== 涉及角色名 ==")
    role_ids = set()
    for cat in (20, 45, 5, 1110, 1112, 1156, 1398, 1503):
        for r in rows(cur, "SELECT roleid FROM DocSecCategoryShare WHERE seccategoryid=%d" % cat):
            if r["roleid"]:
                role_ids.add(int(r["roleid"]))
    for rid in sorted(role_ids):
        rr = rows(cur, "SELECT id, rolename, seclevel FROM HrmRoles WHERE id=%d" % rid)
        print("  角色 %d: %s" % (rid, rr))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
