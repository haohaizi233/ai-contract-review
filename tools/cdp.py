# -*- coding: utf-8 -*-
"""最小 CDP 客户端：通过 ws://127.0.0.1:9222 控制本机 Chrome。
用法: python cdp.py eval "document.title"     # 在第一个页面执行 JS
      python cdp.py open "http://example.com"  # 新开标签页
      python cdp.py tabs                       # 列出页面
"""
import base64
import itertools
import json
import os
import socket
import struct
import sys

HOST = ("127.0.0.1", 9222)


def http_get(path):
    with socket.create_connection(HOST, timeout=8) as s:
        s.sendall(("GET %s HTTP/1.1\r\nHost: localhost:9222\r\nConnection: close\r\n\r\n" % path).encode())
        data = b""
        try:
            while True:
                b = s.recv(65536)
                if not b:
                    break
                data += b
                head, sep, body = data.partition(b"\r\n\r\n")
                if sep and ("Content-Length: %d" % len(body)).encode() in head.replace(b"content-length", b"Content-Length"):
                    break
                if sep and b"Transfer-Encoding: chunked" in head and body.endswith(b"0\r\n\r\n"):
                    break
        except socket.timeout:
            pass
    return data.split(b"\r\n\r\n", 1)[1].decode("utf-8", "replace")


class WS:
    """极简 websocket 客户端（只支持本机 CDP 的小消息）"""

    def __init__(self, url):
        import re
        m = re.match(r"ws://([^:/]+):(\d+)(/.*)", url)
        host, port, path = m.group(1), int(m.group(2)), m.group(3)
        self.sock = socket.create_connection((host, port), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
             "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, key)).encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.sock.recv(4096)
        if b"101" not in resp.split(b"\r\n")[0]:
            raise RuntimeError("握手失败: " + resp.decode("utf-8", "replace")[:200])
        self.buf = resp.split(b"\r\n\r\n", 1)[1]
        self.seq = itertools.count(1)

    def _recv_exact(self, n):
        while len(self.buf) < n:
            self.buf += self.sock.recv(65536)
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, payload):
        data = payload.encode("utf-8")
        hdr = b"\x81"
        n = len(data)
        if n < 126:
            hdr += struct.pack("B", 0x80 | n)
        elif n < 65536:
            hdr += struct.pack("!BH", 0x80 | 126, n)
        else:
            hdr += struct.pack("!BQ", 0x80 | 127, n)
        mask = os.urandom(4)
        masked = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
        self.sock.sendall(hdr + mask + masked)

    def recv(self):
        while True:
            b0, b1 = self._recv_exact(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._recv_exact(8))[0]
            if b1 & 0x80:
                self._recv_exact(4)  # 服务器帧无掩码，忽略
            payload = self._recv_exact(n)
            if b0 & 0x0F == 0x1:  # text frame
                return json.loads(payload.decode("utf-8", "replace"))

    def call(self, method, params=None, session_id=None):
        i = next(self.seq)
        msg = {"id": i, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        self.send(json.dumps(msg))
        while True:
            r = self.recv()
            if r.get("id") == i:
                return r

    def close(self):
        self.sock.close()


def browser_ws_url():
    return json.loads(http_get("/json/version"))["webSocketDebuggerUrl"]


def pages():
    return [t for t in json.loads(http_get("/json/list")) if t.get("type") == "page"]


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "tabs"
    ws = WS(browser_ws_url())
    try:
        if cmd == "tabs":
            r = ws.call("Target.getTargets")
            for t in r["result"]["targetInfos"]:
                print(t["type"], "|", t["title"][:50], "|", t["url"][:90])
        elif cmd == "open":
            r = ws.call("Target.createTarget", {"url": sys.argv[2]})
            print("新标签:", r["result"]["targetId"])
        elif cmd == "eval":
            ps = pages()
            if not ps:
                print("没有页面目标")
                return
            sid = ws.call("Target.attachToTarget", {"targetId": ps[0]["id"], "flatten": True})["result"]["sessionId"]
            r = ws.call("Runtime.evaluate", {"expression": sys.argv[2], "returnByValue": True}, session_id=sid)
            print(json.dumps(r.get("result", {}), ensure_ascii=False))
    finally:
        ws.close()


if __name__ == "__main__":
    main()
