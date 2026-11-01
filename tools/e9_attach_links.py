# -*- coding: utf-8 -*-
"""在已登录的 Chrome 里打开流程详情页，抓附件下载链接（含 authStr 签名）。
用法: python tools/e9_attach_links.py <requestid> [fileid]
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cdp import WS, browser_ws_url  # noqa: E402

ORIGIN = "http://hc.hcipi.com:8088"


def main():
    rid = int(sys.argv[1])
    fid = sys.argv[2] if len(sys.argv) > 2 else None

    ws = WS(browser_ws_url())
    try:
        r = ws.call("Target.getTargets")
        sid_target = None
        for t in r["result"]["targetInfos"]:
            if t["type"] == "page" and "hc.hcipi.com" in t["url"]:
                sid_target = t["targetId"]
                break
        if not sid_target:
            sid_target = ws.call("Target.createTarget", {"url": ORIGIN + "/wui/index.html"})["result"]["targetId"]
        sid = ws.call("Target.attachToTarget", {"targetId": sid_target, "flatten": True})["result"]["sessionId"]

        # 打开流程详情页（SPA 前端）
        ws.call("Page.navigate", {"url": ORIGIN + "/weaver/workflow/request/ViewRequestForwardSPA.jsp?requestid=%d" % rid}, session_id=sid)
        import time
        time.sleep(8)

        js = """
        (() => {
          const links = [];
          for (const a of document.querySelectorAll('a[href], iframe, [data-src]')) {
            const u = a.href || a.src || a.getAttribute('data-src') || '';
            if (/FileDownload|download|fileid|attach/i.test(u)) links.push(u);
          }
          const html = document.documentElement.outerHTML;
          const m = [...html.matchAll(/https?:\\/\\/[^"'\\s]*FileDownload[^"'\\s]*/g)].map(x => x[0]);
          return JSON.stringify({title: document.title, url: location.href, links: links.slice(0, 20), inline: [...new Set(m)].slice(0, 20), textLen: document.body.innerText.length});
        })()
        """
        r = ws.call("Runtime.evaluate", {"expression": js, "returnByValue": True}, session_id=sid)
        print(json.dumps(r["result"]["result"].get("value", r["result"]), ensure_ascii=False, indent=1))
    finally:
        ws.close()


if __name__ == "__main__":
    main()
