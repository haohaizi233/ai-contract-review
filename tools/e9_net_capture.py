# -*- coding: utf-8 -*-
"""在临时标签打开可查看的流程页，抓网络请求，找附件列表/下载 API 与 authStr 生成接口。

用法: python tools/e9_net_capture.py [requestid]
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import websocket  # noqa
import e9_cookie_cdp as m  # noqa
from e9_attach_probe import call  # noqa
from e9_cdp_diag import call_tmo  # noqa

RID = int(sys.argv[1]) if len(sys.argv) > 1 else 2110135
ORIGIN = "http://hc.hcipi.com:8088"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ver = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=8).read())
    ws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=30)
    tgt = call(ws, "Target.createTarget", {"url": "about:blank"})["targetId"]
    sid = call(ws, "Target.attachToTarget", {"targetId": tgt, "flatten": True})["sessionId"]
    reqs: list[str] = []
    try:
        call(ws, "Network.enable", {}, session_id=sid)
        url = "%s/wui/index.html#/main/req?requestid=%d" % (ORIGIN, RID)
        call(ws, "Page.navigate", {"url": url}, session_id=sid)
        t0 = time.time()
        interesting = []
        while time.time() - t0 < 25:
            ws.settimeout(1.0)
            try:
                msg = json.loads(ws.recv())
            except Exception:
                continue
            m_ = msg.get("method", "")
            if m_ in ("Network.requestWillBeSent", "Network.responseReceived"):
                p = msg.get("params", {})
                u = p.get("request", {}).get("url") or p.get("response", {}).get("url", "")
                if any(k in u for k in ("api/", "FileDownload", "Download", "attach", "Attach", "auth")):
                    interesting.append(u)
        print("== 捕获的网络请求 ==")
        for u in sorted(set(interesting)):
            print("  ", u[:170])

        # 页面加载完后再取 DOM 链接 + window 上的 authStr
        js = r"""
        (() => {
          const out = {title: document.title, len: (document.body.innerText||'').length,
                       auth: [], links: []};
          for (const k of ['authStr','authSignatureStr','f_weaver_belongto_userid']) {
            try { if (window[k]) out.auth.push(k + '=' + window[k]); } catch(e) {}
          }
          const html = document.documentElement.outerHTML;
          for (const mm of html.matchAll(/authS[^"'\s&,;]{0,60}/g)) out.auth.push(mm[0]);
          for (const a of document.querySelectorAll('a[href]')) {
            if (/FileDownload|fileid=/i.test(a.href)) out.links.push(a.href);
          }
          for (const mm of html.matchAll(/[^"'\s]*FileDownload[^"'\s]*/g)) out.links.push(mm[0]);
          out.auth = [...new Set(out.auth)].slice(0, 12);
          out.links = [...new Set(out.links)].slice(0, 20);
          return JSON.stringify(out);
        })()
        """
        r = call_tmo(ws, "Runtime.evaluate", {"expression": js, "returnByValue": True}, sid, tmo=15)
        if r != "__TIMEOUT__":
            d = json.loads(r["result"]["result"]["value"])
            print("== DOM ==")
            print("  title=%s len=%d" % (d["title"], d["len"]))
            print("  auth:", d["auth"])
            for u in d["links"]:
                print("  链接:", u[:170])
    finally:
        call(ws, "Target.closeTarget", {"targetId": tgt})
        ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
