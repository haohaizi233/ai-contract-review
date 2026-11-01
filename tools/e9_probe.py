# -*- coding: utf-8 -*-
"""探测 authStr 来源：页面上下文 fetch + 本机 HTTP 直连两种通道对比。

用法: python tools/e9_probe.py [requestid]
"""
import json
import pathlib
import re
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cdp import WS, browser_ws_url  # noqa: E402
from e9_download import get_page_ws, ORIGIN  # noqa: E402

RID = int(sys.argv[1]) if len(sys.argv) > 1 else 1232688
FID = int(sys.argv[2]) if len(sys.argv) > 2 else 297629
CK = pathlib.Path(__file__).resolve().parent.parent / "data" / ".e9_cookie"


def http_probe(cookie: str):
    print("===== 本机 HTTP 直连（不走浏览器）=====")
    h = {"Cookie": cookie, "User-Agent": "Mozilla/5.0"}
    for name, url in [
        ("ViewRequestForwardSPA", "/weaver/workflow/request/ViewRequestForwardSPA.jsp?requestid=%d" % RID),
        ("ViewRequest", "/weaver/workflow/request/ViewRequest.jsp?requestid=%d" % RID),
    ]:
        try:
            body = urllib.request.urlopen(urllib.request.Request(ORIGIN + url, headers=h), timeout=30).read()
            t = body.decode("utf-8", "replace")
            m1 = re.search(r'authStr["\']?\s*[:=]\s*["\']([^"\']+)', t)
            m2 = re.search(r'authSignatureStr["\']?\s*[:=]\s*["\']([^"\']+)', t)
            print("%s: %d 字节 authStr=%s authSig=%s" % (
                name, len(body), (m1.group(1)[:24] + "...") if m1 else None,
                (m2.group(1)[:24] + "...") if m2 else None))
        except Exception as e:
            print("%s: 异常 %r" % (name, e))


def cdp_probe():
    print("===== 页面上下文 fetch（CDP）=====")
    ws = WS(browser_ws_url())
    try:
        sid = get_page_ws(ws)

        def ev(expr, await_=False, tmo=12000):
            if await_:
                expr = ("Promise.race([(%s), new Promise(r=>setTimeout(()=>r('__TIMEOUT__'),%d))])"
                        % (expr, tmo))
            r = ws.call("Runtime.evaluate", {"expression": expr, "awaitPromise": True,
                                             "returnByValue": True}, session_id=sid, )
            res = r.get("result", {})
            if "exceptionDetails" in res:
                return "EXC " + json.dumps(res["exceptionDetails"], ensure_ascii=False)[:300]
            return res.get("result", {}).get("value")

        print("页面状态:", ev("JSON.stringify({ready:document.readyState,url:location.href})"))
        print("fetch 状态码:", ev("fetch('/api/portal/quickSearch/getQuickSearchTypes?__random__=1',{credentials:'include'}).then(r=>r.status)", True))
        js = ("fetch('/weaver/workflow/request/ViewRequestForwardSPA.jsp?requestid=%d',{credentials:'include'})"
              ".then(r=>r.text()).then(t=>JSON.stringify({len:t.length,"
              "hasAuthStr:t.includes('authStr'),hasAuthSig:t.includes('authSignatureStr'),"
              "snips:[...t.matchAll(/authS[^\"',;]{0,80}/g)].slice(0,5).map(x=>x[0])}))" % RID)
        print("SPA 页 fetch:", ev(js, True))
    finally:
        ws.close()


def main():
    cookie = CK.read_text(encoding="utf-8").strip()
    http_probe(cookie)
    cdp_probe()


if __name__ == "__main__":
    main()
