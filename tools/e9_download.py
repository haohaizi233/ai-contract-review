# -*- coding: utf-8 -*-
"""通过 CDP 控制本机 Chrome（已登录 OA），取 authStr/authSignatureStr 并下载流程附件。
用法: python tools/e9_download.py <requestid> <fileid> [保存路径]
"""
import base64
import json
import pathlib
import re
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from cdp import WS, browser_ws_url, pages  # noqa: E402

ORIGIN = "http://hc.hcipi.com:8088"


def get_page_ws(ws, url_keyword="hc.hcipi.com"):
    """附加到一个 OA 页面目标，返回 sessionId。"""
    r = ws.call("Target.getTargets")
    target_id = None
    for t in r["result"]["targetInfos"]:
        if t["type"] == "page" and url_keyword in t["url"]:
            target_id = t["targetId"]
            break
    if not target_id:
        target_id = ws.call("Target.createTarget", {"url": ORIGIN + "/wui/index.html"})["result"]["targetId"]
    return ws.call("Target.attachToTarget", {"targetId": target_id, "flatten": True})["result"]["sessionId"]


def main():
    rid = int(sys.argv[1])
    fid = int(sys.argv[2])
    out = sys.argv[3] if len(sys.argv) > 3 else r"C:\Users\Administrator\Desktop\AI合同审核\data\download_%d_%d.bin" % (rid, fid)

    ws = WS(browser_ws_url())
    try:
        sid = get_page_ws(ws)

        # 1. 在 OA 页面上下文里取 authStr / authSignatureStr
        js = """
        fetch('/weaver/workflow/request/ViewRequestForwardSPA.jsp?requestid=%d', {credentials:'include'})
          .then(r => r.text())
          .then(t => {
            const m1 = t.match(/authStr["']?\\s*[:=]\\s*["']([^"']+)["']/);
            const m2 = t.match(/authSignatureStr["']?\\s*[:=]\\s*["']([^"']+)["']/);
            const m3 = t.match(/f_weaver_belongto_userid["']?\\s*[:=]\\s*["']?(\\d+)/);
            return JSON.stringify({auth: m1 && m1[1], sign: m2 && m2[1], uid: m3 && m3[1], len: t.length});
          })
        """ % rid
        r = ws.call("Runtime.evaluate", {"expression": js, "awaitPromise": True, "returnByValue": True}, session_id=sid)
        info = json.loads(r["result"]["result"]["value"])
        if not info.get("auth") or not info.get("sign"):
            print("未取到签名，页面长度:", info.get("len"))
            sys.exit(1)
        uid = info.get("uid") or "3426"
        print("authStr:", info["auth"][:32], "...")
        print("authSignatureStr:", info["sign"][:32], "...")
        print("userid:", uid)

        # 2. 取浏览器 Cookie（带登录态）
        r = ws.call("Network.getCookies", {"urls": [ORIGIN + "/"]}, session_id=sid)
        cookie = "; ".join(c["name"] + "=" + c["value"] for c in r["result"]["cookies"])
        print("Cookie 数量:", len(r["result"]["cookies"]))

        # 3. 用带签名的 URL 下载
        dl = ("%s/weaver/weaver.file.FileDownload?fileid=%d&download=1&fromrequest=1&requestid=%d"
              "&authStr=%s&authSignatureStr=%s&f_weaver_belongto_userid=%s&f_weaver_belongto_usertype=0"
              % (ORIGIN, fid, rid, info["auth"], info["sign"], uid))
        req = urllib.request.Request(dl, headers={
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0",
            "Referer": "%s/weaver/workflow/request/ViewRequestForwardSPA.jsp?requestid=%d" % (ORIGIN, rid),
        })
        rr = urllib.request.urlopen(req, timeout=120)
        body = rr.read()
        ct = rr.headers.get("Content-Type", "")
        disp = rr.headers.get("Content-Disposition", "")
        print("HTTP", rr.status, "| Content-Type:", ct, "| Content-Disposition:", disp, "| 大小:", len(body))
        if not disp and (b"<html" in body[:200].lower() or "text/html" in ct):
            print("返回的是 HTML，疑似未授权。前 300 字节:")
            print(body[:300])
            sys.exit(1)
        p = pathlib.Path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
        # 按 Content-Disposition 改名
        m = re.search(r"filename\*?=(?:UTF-8'')?[\"']?([^\"';]+)", disp or "")
        if m:
            name = urllib.request.unquote(m.group(1))
            p = p.with_name(name)
            p.write_bytes(body)
        print("已保存:", p)
    finally:
        ws.close()


if __name__ == "__main__":
    main()
