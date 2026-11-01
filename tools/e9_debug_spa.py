# -*- coding: utf-8 -*-
"""调试：打印 ViewRequestForwardSPA.jsp 返回内容，找 authStr 所在接口。"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cdp import WS, browser_ws_url  # noqa: E402
from e9_download import get_page_ws, ORIGIN  # noqa: E402

rid = int(sys.argv[1]) if len(sys.argv) > 1 else 1475065

ws = WS(browser_ws_url())
try:
    sid = get_page_ws(ws)
    js = """
    Promise.all([
      fetch('/weaver/workflow/request/ViewRequest.jsp?requestid=%d', {credentials:'include'}).then(r => r.text()),
      fetch('/weaver/workflow/request/ViewRequestForwardSPA.jsp?requestid=%d&isrequesttab=1', {credentials:'include'}).then(r => r.text())
    ]).then(([t1, t2]) => {
      function scan(t) {
        const m = t.match(/authStr["']?\\s*[:=]\\s*["']([^"']+)["']/);
        const m2 = t.match(/authSignatureStr["']?\\s*[:=]\\s*["']([^"']+)["']/);
        const hasFile = t.includes('fileid=534064');
        return {len: t.length, auth: m && m[1], sign: m2 && m2[1], hasFile: hasFile, head: t.slice(0, 300)};
      }
      return JSON.stringify({viewJsp: scan(t1), spa: scan(t2)});
    })
    """ % (rid, rid)
    r = ws.call("Runtime.evaluate", {"expression": js, "awaitPromise": True, "returnByValue": True}, session_id=sid)
    print("=== 扫描结果 ===")
    print(r["result"]["result"].get("value", r["result"]))
finally:
    ws.close()
