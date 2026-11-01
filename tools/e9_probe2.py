# -*- coding: utf-8 -*-
"""在**可响应**的页面上下文里抓 authStr，并测带签名的 FileDownload。

用法: python tools/e9_probe2.py [requestid] [fileid]
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import websocket  # noqa
import e9_cookie_cdp as m  # noqa
from e9_cdp_diag import call_tmo  # noqa

RID = int(sys.argv[1]) if len(sys.argv) > 1 else 1232688
FID = int(sys.argv[2]) if len(sys.argv) > 2 else 297629
ORIGIN = "http://hc.hcipi.com:8088"
CK = Path(__file__).resolve().parent.parent / "data" / ".e9_cookie"


def pick_live_page(ws):
    """选一个 evaluate 能返回的 OA 页面目标（跳过被阻塞的 SPA）。"""
    targets = [t for t in m.cdp(ws, "Target.getTargets")["targetInfos"] if t["type"] == "page"]
    for t in targets:
        if "hc.hcipi.com" not in t["url"]:
            continue
        sid = m.cdp(ws, "Target.attachToTarget", {"targetId": t["targetId"], "flatten": True})["sessionId"]
        r = call_tmo(ws, "Runtime.evaluate", {"expression": "1+1", "returnByValue": True}, sid, tmo=8)
        if r != "__TIMEOUT__" and r.get("result", {}).get("result", {}).get("value") == 2:
            print("选用目标: %s | %s" % (t["title"][:24], t["url"][:70]))
            return sid
        print("跳过阻塞目标: %s" % t["url"][:70])
    return None


def main() -> int:
    ver = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=8).read())
    ws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=20)
    sid = pick_live_page(ws)
    if not sid:
        print("无可用页面目标")
        return 1

    js = r"""
    (async () => {
      const out = {};
      for (const [k, u] of [
        ['spa', '/weaver/workflow/request/ViewRequestForwardSPA.jsp?requestid=%d&isrequesttab=1'],
        ['classic', '/weaver/workflow/request/ViewRequest.jsp?requestid=%d'],
      ]) {
        try {
          const r = await fetch(u, {credentials: 'include'});
          const t = await r.text();
          const m1 = t.match(/authStr["']?\s*[:=]\s*["']([^"']+)["']/);
          const m2 = t.match(/authSignatureStr["']?\s*[:=]\s*["']([^"']+)["']/);
          const hasFile = t.includes('fileid=%d');
          const fm = t.match(/https?:\/\/[^"'\\s]*FileDownload[^"'\\s]*fileid=%d[^"'\\s]*/);
          out[k] = {status: r.status, len: t.length, auth: m1 && m1[1], sign: m2 && m2[1],
                    hasFile: hasFile, fileUrl: fm && fm[0]};
        } catch (e) { out[k] = {err: String(e)}; }
      }
      return JSON.stringify(out);
    })()
    """ % (RID, RID, FID, FID)
    r = call_tmo(ws, "Runtime.evaluate", {"expression": js, "awaitPromise": True,
                                          "returnByValue": True}, sid, tmo=30)
    if r == "__TIMEOUT__":
        print("fetch 超时")
        return 1
    res = r.get("result", {})
    if "exceptionDetails" in res:
        print("JS 异常:", json.dumps(res["exceptionDetails"], ensure_ascii=False)[:400])
        return 1
    info = json.loads(res["result"]["value"])
    print(json.dumps(info, ensure_ascii=False, indent=1))

    auth, sign = None, None
    for v in info.values():
        if isinstance(v, dict) and v.get("auth") and v.get("sign"):
            auth, sign = v["auth"], v["sign"]
            break
    if not (auth and sign):
        print("未取得 authStr/authSignatureStr")
        return 1

    # 用签名 URL 在 Python 侧直接下载
    uid = "3426"
    dl = ("%s/weaver/weaver.file.FileDownload?fileid=%d&download=1&fromrequest=1&requestid=%d"
          "&authStr=%s&authSignatureStr=%s&f_weaver_belongto_userid=%s&f_weaver_belongto_usertype=0"
          % (ORIGIN, FID, RID, auth, sign, uid))
    ck = CK.read_text(encoding="utf-8").strip()
    req = urllib.request.Request(dl, headers={
        "Cookie": ck, "User-Agent": "Mozilla/5.0",
        "Referer": "%s/weaver/workflow/request/ViewRequestForwardSPA.jsp?requestid=%d" % (ORIGIN, RID)})
    try:
        rr = urllib.request.urlopen(req, timeout=60)
        body = rr.read()
        ct = rr.headers.get("Content-Type", "")
        disp = rr.headers.get("Content-Disposition", "")
        is_html = body.lstrip()[:9].lower().startswith(b"<!doctype") or body.lstrip()[:5].lower().startswith(b"<html")
        print("签名下载: HTTP %s | %s | %d 字节 | disp=%s | %s"
              % (rr.status, ct, len(body), disp, "HTML页(仍无权限)" if is_html else "文件OK"))
        if is_html:
            txt = re.sub(r"<[^>]+>", " ", body.decode("utf-8", "replace"))
            print("  正文:", re.sub(r"\s+", " ", txt).strip()[:120])
        else:
            out = Path(__file__).resolve().parent.parent / "data" / ("_test_dl_%d.bin" % FID)
            out.write_bytes(body)
            print("  已存:", out)
    except Exception as e:
        print("签名下载异常:", repr(e))
    ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
