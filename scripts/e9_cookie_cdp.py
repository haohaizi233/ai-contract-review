# -*- coding: utf-8 -*-
"""经 CDP（DevToolsActivePort）直连本机 Chrome，抓取 hc.hcipi.com 会话 Cookie。

比读 Cookie 库更可靠：不碰加密库，直接向 Chrome 要 Storage.getCookies（含 HttpOnly）。
用法：python scripts/e9_cookie_cdp.py [--verify]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

HOST = "hc.hcipi.com"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
COOKIE_OUT = PROJECT_ROOT / "data" / ".e9_cookie"


def cdp(ws, method, params=None, _id=[0]):
    _id[0] += 1
    ws.send(json.dumps({"id": _id[0], "method": method, "params": params or {}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == _id[0]:
            if "error" in msg:
                raise RuntimeError("%s -> %s" % (method, msg["error"]))
            return msg.get("result", {})


def main(argv=None) -> int:
    import websocket  # noqa
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--user-data-dir", default=None,
                    help="从指定 user-data-dir 读取 DevToolsActivePort（默认主配置）")
    ap.add_argument("--port", type=int, default=None,
                    help="直接指定调试端口（无 DevToolsActivePort 时用 /json/version 发现 ws）")
    args = ap.parse_args(argv)

    base = Path(args.user_data_dir) if args.user_data_dir else (
        Path.home() / "AppData/Local/Google/Chrome/User Data")
    dap = base / "DevToolsActivePort"
    if args.port:
        ver = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:%d/json/version" % args.port, timeout=8).read())
        url = ver["webSocketDebuggerUrl"]
        print("经 /json/version 发现端点：%s" % url)
    else:
        lines = dap.read_text(encoding="utf-8").splitlines()
        port, ws_path = int(lines[0].strip()), lines[1].strip()
        url = "ws://127.0.0.1:%d%s" % (port, ws_path)
    # 注意：Chrome 136+ 会拒绝带 Origin: devtools://devtools 的握手，除非启动时
    # 加 --remote-allow-origins=*；这里不带 Origin 头即可直连。
    ws = websocket.create_connection(url, timeout=20)

    targets = cdp(ws, "Target.getTargets")["targetInfos"]
    print("Chrome 目标 %d 个：" % len(targets))
    for t in targets:
        if t["type"] == "page":
            print("  [page] %s  %s" % (t["title"][:24], t["url"][:80]))

    res = cdp(ws, "Storage.getCookies")["cookies"]
    picked = [c for c in res if HOST in (c.get("domain") or "")]
    if not picked:
        print("CDP 未取到 %s 的 Cookie（该 Chrome 未登录？）" % HOST)
        ws.close()
        return 1
    picked.sort(key=lambda c: (len(c["domain"]), c["path"]))
    cookie_header = "; ".join("%s=%s" % (c["name"], c["value"]) for c in picked)
    if COOKIE_OUT.exists():
        bak = COOKIE_OUT.parent / (COOKIE_OUT.name + ".bak.%d" % int(time.time()))
        shutil.move(str(COOKIE_OUT), str(bak))
        print("旧 Cookie 已备份 -> %s" % bak.name)
    COOKIE_OUT.write_text(cookie_header, encoding="utf-8")
    print("写入 %s（长度 %d）" % (COOKIE_OUT, len(cookie_header)))
    for c in picked:
        print("  %-28s domain=%-20s httpOnly=%s" % (c["name"], c["domain"], c.get("httpOnly")))
    ws.close()

    if args.verify:
        h = {"Cookie": cookie_header, "User-Agent": "Mozilla/5.0"}
        body = urllib.request.urlopen(urllib.request.Request(
            "http://%s:8088/api/portal/quickSearch/getQuickSearchTypes?__random__=9" % HOST,
            headers=h), timeout=20).read(160).decode("utf-8", "replace")
        print("API 实测:", body.strip()[:120])
        r2 = urllib.request.urlopen(urllib.request.Request(
            "http://%s:8088/weaver/weaver.file.FileDownload?fileid=290754" % HOST, headers=h), timeout=30)
        head = r2.read(16)
        print("下载实测: content-type=%s 头=%s 首字节=%r" % (
            r2.headers.get("Content-Type"), head[:8].hex(), head[:8]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
