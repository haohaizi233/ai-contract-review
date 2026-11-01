# -*- coding: utf-8 -*-
"""查毛世梅账号对受限附件目录的下载权限（只读评估，不做任何改动）。

用法: python tools/e9_dl_checkuser.py [姓名]
"""
from __future__ import annotations

import os
import sys

import pytds

NAME = sys.argv[1] if len(sys.argv) > 1 else "毛世梅"
CATS = [20, 30, 37, 38, 1112, 1156, 1398, 1503, 1121, 1111, 1114, 1116, 1155, 25, 1410, 11]


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

    users = rows(cur, ("SELECT id, loginid, lastname, seclevel, departmentid, subcompanyid1, jobtitle, status "
                       "FROM HrmResource WHERE lastname='%s'") % NAME)
    print("== %s 的账号 ==" % NAME)
    for u in users:
        print(" ", u)
    if not users:
        print("  未找到该用户")
        return 1

    for u in users:
        uid, dept, sub = u["id"], u["departmentid"], u["subcompanyid1"]
        print()
        print("== 用户 %d（部门%d 分部%d seclevel=%s）角色 ==" % (uid, dept, sub, u["seclevel"]))
        try:
            rr = rows(cur, ("SELECT rm.roleid, r.rolesmark, r.rolesname FROM HrmRoleMembers rm "
                            "LEFT JOIN HrmRoles r ON r.id = rm.roleid WHERE rm.resourceid=%d") % uid)
            print("  角色:", rr)
        except Exception as e:
            conn.rollback()
            print("  角色查询失败:", str(e)[:60])
        role_ids = {r["roleid"] for r in rr} if rr else set()

        print()
        print("== 目录级共享规则命中（DocSecCategoryShare，只看 downloadlevel=1）==")
        for cat in CATS:
            rules = rows(cur, ("SELECT id, sharetype, seclevel, sharelevel, downloadlevel, userid, "
                               "departmentid, roleid, subcompanyid FROM DocSecCategoryShare "
                               "WHERE seccategoryid=%d") % cat)
            hits = []
            for r in rules:
                if r["downloadlevel"] != 1:
                    continue
                if r["sharetype"] == 1 and r["userid"] == uid:
                    hits.append("本人")
                elif r["sharetype"] == 3 and r["departmentid"] == dept:
                    hits.append("本部门")
                elif r["sharetype"] == 4 and r["subcompanyid"] == sub:
                    hits.append("本分部")
                elif r["sharetype"] == 2 and r["roleid"] in role_ids:
                    hits.append("角色%d" % r["roleid"])
                elif r["sharetype"] == 5 and r.get("userid") == 0:
                    hits.append("全员?")
            print("  类别 %-5s 规则%d条  可下载命中: %s" % (cat, len(rules), hits or "无"))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
