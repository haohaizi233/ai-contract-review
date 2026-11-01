# -*- coding: utf-8 -*-
"""验证"请求参与人可下载"假设 + 统计整体失败规模。

用法: python tools/e9_dl_participant.py
"""
from __future__ import annotations

import os
import re
import sys
import urllib.request
from pathlib import Path

import pytds

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "e9-opinion-export"
CK = ROOT / "data" / ".e9_cookie"
OA = "http://hc.hcipi.com:8088"
UID = 3331


def rows(cur, sql):
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def http_kind(fid, cookie):
    h = {"Cookie": cookie, "User-Agent": "Mozilla/5.0"}
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            OA + "/weaver/weaver.file.FileDownload?fileid=%d" % fid, headers=h), timeout=30)
        body = r.read(64)
        return "文件OK" if not (body.lstrip()[:9].lower().startswith(b"<!doctype") or body.lstrip()[:5].lower().startswith(b"<html")) else "无权限"
    except Exception as e:
        return "异常" + repr(e)[:30]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    cookie = CK.read_text(encoding="utf-8").strip()
    conn = pytds.connect(
        dsn=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "1433")),
        user=os.environ["DB_USER"], password=os.environ["DB_PWD_READONLY"],
        database="ecologyE9", timeout=60, login_timeout=60)
    cur = conn.cursor()

    print("== 浏览器里正开着的两个请求：3331 是否参与人 ==")
    for rid in (2110135, 1702997):
        part = rows(cur, ("SELECT userid, nodeid, isremark FROM workflow_currentoperator "
                          "WHERE requestid=%d AND userid=%d") % (rid, UID))
        req = rows(cur, "SELECT requestmark, creater FROM workflow_requestbase WHERE requestid=%d" % rid)
        print("  %d %s 参与=%s" % (rid, req, bool(part)))
        fm = rows(cur, "SELECT billformid, billid FROM workflow_form WHERE requestid=%d" % rid)
        if fm and fm[0]["billformid"]:
            tbl = "formtable_main_%d" % abs(fm[0]["billformid"])
            try:
                row = rows(cur, "SELECT * FROM %s WHERE id=%d" % (tbl, fm[0]["billid"]))
                if row:
                    fids = []
                    for k, v in row[0].items():
                        if v and re.fullmatch(r"\d+", str(v).strip()) and len(str(v).strip()) >= 4:
                            pass
                        elif v and re.search(r"\d{4,}", str(v)):
                            fids += re.findall(r"\d{4,}", str(v))[:3]
                    fids = [int(x) for x in fids][:4]
                    for fid in fids:
                        print("    fileid=%d -> %s" % (fid, http_kind(fid, cookie)))
            except Exception as e:
                conn.rollback()
                print("    表查询失败", str(e)[:60])

    print()
    print("== 整体失败规模（扫全部 md 的下载失败标记）==")
    bad, total_links = set(), 0
    for p in OUT.rglob("*.md"):
        if p.name.startswith("_"):
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"FileDownload\?fileid=(\d+)\)", t):
            total_links += 1
        for m in re.finditer(r"FileDownload\?fileid=(\d+)\)\（\*\*下载失败\*\*", t):
            bad.add(int(m.group(1)))
    print("  md 内 FileDownload 链接 %d 个，其中标记下载失败 %d 个唯一 fileid" % (total_links, len(bad)))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
