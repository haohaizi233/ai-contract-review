# -*- coding: utf-8 -*-
"""评估指定用户对"下载失败"附件的文档级权限命中率（只读）。

用法: python tools/e9_dl_checkuser2.py [userid] [抽样数]
"""
from __future__ import annotations

import os
import random
import re
import sys
from pathlib import Path

import pytds

UID = int(sys.argv[1]) if len(sys.argv) > 1 else 2160   # 毛世梅
N = int(sys.argv[2]) if len(sys.argv) > 2 else 40
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "e9-opinion-export"


def rows(cur, sql):
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    random.seed(7)
    failed = []
    for p in OUT.rglob("*.md"):
        if p.name.startswith("_"):
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"FileDownload\?fileid=(\d+)\)\（\*\*下载失败\*\*", t):
            failed.append(int(m.group(1)))
    failed = sorted(set(failed))
    sample = random.sample(failed, min(N, len(failed)))
    print("失败 fileid %d，抽样 %d" % (len(failed), len(sample)))

    conn = pytds.connect(
        dsn=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "1433")),
        user=os.environ["DB_USER"], password=os.environ["DB_PWD_READONLY"],
        database="ecologyE9", timeout=60, login_timeout=60)
    cur = conn.cursor()
    u = rows(cur, "SELECT id, departmentid, subcompanyid1, seclevel FROM HrmResource WHERE id=%d" % UID)[0]
    roles = {r["roleid"] for r in rows(cur, "SELECT roleid FROM HrmRoleMembers WHERE resourceid=%d" % UID)}
    print("用户%d 部门%d 分部%d seclevel=%s 角色=%s" % (UID, u["departmentid"], u["subcompanyid1"], u["seclevel"], roles))

    hit = miss = noanchor = 0
    for fid in sample:
        di = rows(cur, "SELECT docid FROM DocImageFile WHERE imagefileid=%d" % fid)
        pv = rows(cur, "SELECT docId FROM DocPreviewHtmlImage WHERE imageFileId=%d OR picFileId=%d" % (fid, fid))
        docids = [r["docid"] for r in di] + [r["docId"] for r in pv]
        if not docids:
            noanchor += 1
            continue
        ok = False
        for did in docids[:2]:
            for s in rows(cur, "SELECT * FROM DocShare WHERE docid=%d" % did):
                dl = s.get("downloadlevel")
                if dl != 1:
                    continue
                st = s.get("sharetype")
                if st == 1 and s.get("userid") == UID:
                    ok = True
                elif st == 3 and s.get("departmentid") == u["departmentid"]:
                    ok = True
                elif st == 4 and s.get("subcompanyid") == u["subcompanyid1"]:
                    ok = True
                elif st == 2 and s.get("roleid") in roles:
                    ok = True
                elif st in (5,) and s.get("userid") == 0:
                    ok = True
                elif st == 1 and s.get("userid") == 0 and s.get("foralluser") in (1, "1"):
                    ok = True
        if ok:
            hit += 1
        else:
            miss += 1
    print("文档级 DocShare 命中: 命中 %d / 未命中 %d / 无锚点 %d（命中率 %.1f%%）"
          % (hit, miss, noanchor, 100.0 * hit / max(len(sample), 1)))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
