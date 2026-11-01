# -*- coding: utf-8 -*-
"""CDP 小工具：在指定 Chrome 调试端口的页面里执行 JS 表达式（表达式从文件读，避免引号转义问题）。

用法：
  python scripts/_cdp_eval.py --port 9224 --expr-file %TEMP%/expr.js [--url-match hc.hcipi.com] [--await]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import websocket  # noqa
import e9_cookie_cdp as m  # noqa: 复用 cdp() 收发


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9224)
    ap.add_argument("--expr-file", required=True)
    ap.add_argument("--url-match", default=None)
    ap.add_argument("--await", dest="await_p", action="store_true")
    ap.add_argument("--target-index", type=int, default=None, help="直接用第 N 个 page 目标")
    args = ap.parse_args()

    expr = Path(args.expr_file).read_text(encoding="utf-8")
    ver = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d/json/version" % args.port, timeout=8).read())
    ws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=30,
                                     origin="devtools://devtools")
    pages = [t for t in m.cdp(ws, "Target.getTargets")["targetInfos"] if t["type"] == "page"]
    if args.url_match:
        pages = [t for t in pages if args.url_match in t["url"]]
    if not pages:
        print("无匹配 page 目标")
        return 1
    tgt = pages[args.target_index if args.target_index is not None else 0]
    sid = m.cdp(ws, "Target.attachToTarget", {"targetId": tgt["targetId"], "flatten": True})["sessionId"]

    mid = int(time.time() * 1000) % 100000000
    ws.send(json.dumps({"id": mid,
                        "method": "Runtime.evaluate",
                        "params": {"expression": expr, "awaitPromise": bool(args.await_p),
                                   "returnByValue": True},
                        "sessionId": sid}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == mid:
            res = msg.get("result", {})
            if "exceptionDetails" in res:
                print("JS 异常:", json.dumps(res["exceptionDetails"])[:500])
            else:
                v = res.get("result", {}).get("value")
                print(v if v is not None else json.dumps(res.get("result", {}))[:2000])
            break
    ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
