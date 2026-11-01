# -*- coding: utf-8 -*-
"""测试 file_download_permission.uuid 是否是下载免鉴权令牌。

用法: python tools/e9_dl_uuid.py [fileid]
"""
from __future__ import annotations

import os
import re
import sys
import urllib.request
from pathlib import Path

import pytds

FID = int(sys.argv[1]) if len(sys.argv) > 1 else 297063
ROOT = Path(__file__).resolve().parent.parent
CK = ROOT / "data" / ".e9_cookie"
OA = "http://hc.hcipi.com:8088"


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
    r = rows(cur, "SELECT * FROM file_download_permission WHERE imagefileid='%d'" % FID)
    conn.close()
    print("file_download_permission 行:", r)
    uuid = r[0]["uuid"] if r else "00000000-0000-0000-0000-000000000000"
    item1 = r[0]["item1"] if r else None
    item2 = r[0]["item2"] if r else None

    ck = CK.read_text(encoding="utf-8").strip()
    variants = [
        ("裸 fileid", "/weaver/weaver.file.FileDownload?fileid=%d" % FID),
        ("+uuid", "/weaver/weaver.file.FileDownload?fileid=%d&uuid=%s" % (FID, uuid)),
        ("+uuid=token", "/weaver/weaver.file.FileDownload?fileid=%d&token=%s" % (FID, uuid)),
        ("+authStr=uuid", "/weaver/weaver.file.FileDownload?fileid=%d&authStr=%s" % (FID, uuid)),
        ("+download&uuid", "/weaver/weaver.file.FileDownload?fileid=%d&download=1&uuid=%s" % (FID, uuid)),
        ("+item1/2", "/weaver/weaver.file.FileDownload?fileid=%d&item1=%s&item2=%s" % (FID, item1 or "", item2 or "")),
    ]
    for name, path in variants:
        h = {"Cookie": ck, "User-Agent": "Mozilla/5.0", "Referer": OA + "/wui/index.html"}
        try:
            rr = urllib.request.urlopen(urllib.request.Request(OA + path, headers=h), timeout=30)
            body = rr.read()
            is_html = body.lstrip()[:9].lower().startswith(b"<!doctype") or body.lstrip()[:5].lower().startswith(b"<html")
            if is_html:
                txt = re.sub(r"<[^>]+>", " ", body.decode("utf-8", "replace"))
                msg = re.sub(r"\s+", " ", txt).strip().replace("-->", "")[:50]
                print("%-16s -> HTML %s" % (name, msg))
            else:
                print("%-16s -> 文件 OK %s %d字节 %s" % (name, rr.headers.get("Content-Type"), len(body), rr.headers.get("Content-Disposition")))
        except Exception as e:
            print("%-16s -> 异常 %r" % (name, e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
