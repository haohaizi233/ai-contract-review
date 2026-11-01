# -*- coding: utf-8 -*-
"""CDP 诊断：逐个 OA 页面目标做最小 eval，检测是否有 JS 对话框阻塞 evaluate。

用法: python tools/e9_cdp_diag.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import websocket  # noqa
import e9_cookie_cdp as m  # noqa: 复用 cdp() 收发


def call_tmo(ws, method, params=None, session_id=None, tmo=10):
    """带超时的 cdp 调用；超时返回 '__TIMEOUT__'。"""
    import select
    mid = int(time.time() * 1000) % 100000000
    msg = {"id": mid, "method": method, "params": params or {}}
    if session_id:
        msg["sessionId"] = session_id
    ws.send(json.dumps(msg))
    deadline = time.time() + tmo
    ws.settimeout(tmo)
    while time.time() < deadline:
        try:
            raw = ws.recv()
        except Exception:
            return "__TIMEOUT__"
        r = json.loads(raw)
        if r.get("id") == mid:
            return r
    return "__TIMEOUT__"


def main() -> int:
    ver = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=8).read())
    ws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=15)
    targets = [t for t in m.cdp(ws, "Target.getTargets")["targetInfos"] if t["type"] == "page"]
    for t in targets:
        if "hc.hcipi.com" not in t["url"] and "localhost" not in t["url"]:
            continue
        print("== 目标: %s | %s" % (t["title"][:30], t["url"][:80]))
        sid = m.cdp(ws, "Target.attachToTarget", {"targetId": t["targetId"], "flatten": True})["sessionId"]
        # 1) 先试处理可能存在的 JS 对话框
        r = call_tmo(ws, "Page.handleJavaScriptDialog", {"accept": True}, sid, tmo=5)
        if r == "__TIMEOUT__":
            print("   handleJavaScriptDialog: 超时")
        elif "error" in r:
            print("   对话框: 无（%s）" % r["error"].get("message", "")[:60])
        else:
            print("   对话框: **已处理一个阻塞对话框**")
        # 2) 最小 eval
        r = call_tmo(ws, "Runtime.evaluate",
                     {"expression": "1+1", "returnByValue": True}, sid, tmo=8)
        if r == "__TIMEOUT__":
            print("   eval(1+1): 超时 <-- renderer 可能被阻塞")
        else:
            print("   eval(1+1):", json.dumps(r.get("result", {}).get("result", {}))[:80])
        # 3) 页面状态
        r = call_tmo(ws, "Runtime.evaluate",
                     {"expression": "JSON.stringify({ready:document.readyState,title:document.title,len:(document.body&&document.body.innerText||'').length})",
                      "returnByValue": True}, sid, tmo=8)
        if r == "__TIMEOUT__":
            print("   页面状态: 超时")
        else:
            print("   页面状态:", r.get("result", {}).get("result", {}).get("value"))
    ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
