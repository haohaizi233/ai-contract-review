# -*- coding: utf-8 -*-
"""在临时标签页打开流程详情页，抓带 authStr 的附件下载链接，并实测下载。

用法: python tools/e9_attach_probe.py <requestid> [fileid]
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import websocket  # noqa
import e9_cookie_cdp as m  # noqa
from e9_cdp_diag import call_tmo  # noqa

RID = int(sys.argv[1]) if len(sys.argv) > 1 else 1232688
FID = int(sys.argv[2]) if len(sys.argv) > 2 else 297629
ORIGIN = "http://hc.hcipi.com:8088"
CK = Path(__file__).resolve().parent.parent / "data" / ".e9_cookie"


def call(ws, method, params=None, session_id=None):
    """cdp() 不支持 sessionId，这里自带一个。"""
    import itertools
    if not hasattr(call, "_seq"):
        call._seq = itertools.count(1000)
    mid = next(call._seq)
    msg = {"id": mid, "method": method, "params": params or {}}
    if session_id:
        msg["sessionId"] = session_id
    ws.send(json.dumps(msg))
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == mid:
            return r.get("result", {})


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ver = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=8).read())
    ws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=30)

    # 新开临时标签页（不动用户已有标签）
    tgt = call(ws, "Target.createTarget", {"url": "about:blank"})["targetId"]
    sid = call(ws, "Target.attachToTarget", {"targetId": tgt, "flatten": True})["sessionId"]
    try:
        url = "%s/weaver/workflow/request/ViewRequestForwardSPA.jsp?requestid=%d" % (ORIGIN, RID)
        call(ws, "Page.navigate", {"url": url}, session_id=sid)
        for i in range(12):
            time.sleep(2)
            r = call_tmo(ws, "Runtime.evaluate",
                         {"expression": "JSON.stringify({ready:document.readyState,url:location.href,len:(document.body&&document.body.innerText||'').length})",
                          "returnByValue": True}, sid, tmo=8)
            if r == "__TIMEOUT__":
                print("[%ds] eval 超时（页面阻塞）" % (i * 2))
                continue
            v = r.get("result", {}).get("result", {}).get("value")
            print("[%ds] %s" % (i * 2, v))
            if v and json.loads(v).get("len", 0) > 200:
                break

        js = r"""
        (() => {
          const out = {links: [], imgs: [], textHead: (document.body.innerText||'').slice(0, 300)};
          const scan = (doc) => {
            for (const a of doc.querySelectorAll('a[href], iframe, [data-src], [data-href]')) {
              const u = a.href || a.src || a.getAttribute('data-src') || a.getAttribute('data-href') || '';
              if (/FileDownload|fileid=|attach/i.test(u)) out.links.push(u);
            }
            const html = doc.documentElement.outerHTML;
            for (const mm of html.matchAll(/[^"'\s]*FileDownload[^"'\s]*/g)) out.links.push(mm[0]);
            for (const mm of html.matchAll(/authStr[^"'\s&]{0,60}/g)) out.imgs.push(mm[0]);
            for (const fr of doc.querySelectorAll('iframe')) {
              try { if (fr.contentDocument) scan(fr.contentDocument); } catch (e) {}
            }
          };
          scan(document);
          out.links = [...new Set(out.links)].slice(0, 30);
          out.authSnips = [...new Set(out.imgs)].slice(0, 10);
          return JSON.stringify(out);
        })()
        """
        r = call_tmo(ws, "Runtime.evaluate", {"expression": js, "returnByValue": True}, sid, tmo=15)
        if r == "__TIMEOUT__":
            print("抓链接超时")
        else:
            data = json.loads(r["result"]["result"]["value"])
            print("页面文本头:", data["textHead"][:150])
            print("authStr 片段:", data["authSnips"])
            for u in data["links"]:
                print("  链接:", u[:180])

            # 找含目标 fileid 的完整链接并实测
            hit = [u for u in data["links"] if ("fileid=%d" % FID) in u and "authStr" in u]
            if hit:
                u = hit[0]
                if u.startswith("/"):
                    u = ORIGIN + u
                ck = CK.read_text(encoding="utf-8").strip()
                req = urllib.request.Request(u, headers={"Cookie": ck, "User-Agent": "Mozilla/5.0",
                                                         "Referer": url})
                rr = urllib.request.urlopen(req, timeout=60)
                body = rr.read()
                is_html = body.lstrip()[:9].lower().startswith(b"<!doctype") or body.lstrip()[:5].lower().startswith(b"<html")
                print("签名下载 %d: %s %d字节 %s" % (FID, rr.headers.get("Content-Type"), len(body),
                                                 "HTML(仍无权限)" if is_html else "文件OK -> " + str(rr.headers.get("Content-Disposition"))))
                if not is_html:
                    out = Path(__file__).resolve().parent.parent / "data" / ("_test_dl_%d.bin" % FID)
                    out.write_bytes(body)
                    print("已存:", out)
            else:
                print("未找到 fileid=%d 的带签名链接" % FID)
    finally:
        call(ws, "Target.closeTarget", {"targetId": tgt})
        ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
