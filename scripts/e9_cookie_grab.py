# -*- coding: utf-8 -*-
"""从本机 Google Chrome 的 Cookie 库提取 hc.hcipi.com 会话，写入 data/.e9_cookie。

原理：Chrome 用 DPAPI + AES-GCM 加密 Cookie 值（%LOCALAPPDATA%/Google/Chrome/
User Data/<profile>/Network/Cookies，密钥在 Local State 的 os_crypt.encrypted_key）。
仅在本机受控环境运行；输出只打印 Cookie 名称，不回显敏感值。

用法：
  python scripts/e9_cookie_grab.py            # 提取并写 data/.e9_cookie
  python scripts/e9_cookie_grab.py --verify   # 提取后顺带用该 Cookie 实测 API/下载鉴权
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HOST = "hc.hcipi.com"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
COOKIE_OUT = PROJECT_ROOT / "data" / ".e9_cookie"


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def dpapi_decrypt(blob: bytes) -> bytes:
    in_buf = ctypes.create_string_buffer(blob)
    in_blob = DATA_BLOB(len(blob), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_byte)))
    out_blob = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob))
    if not ok:
        raise OSError("CryptUnprotectData 失败 err=%s" % ctypes.GetLastError())
    data = ctypes.string_at(out_blob.pbData, out_blob.cbData)
    ctypes.windll.kernel32.LocalFree(out_blob.pbData)
    return data


def get_aes_key(user_data: Path) -> bytes:
    state = json.loads((user_data / "Local State").read_text(encoding="utf-8"))
    enc = base64.b64decode(state["os_crypt"]["encrypted_key"])
    assert enc.startswith(b"DPAPI")
    return dpapi_decrypt(enc[5:])


def decrypt_cookie(enc: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if enc[:3] not in (b"v10", b"v11"):
        return dpapi_decrypt(enc)          # 老版本直接 DPAPI
    nonce, ct = enc[3:15], enc[15:]
    return AESGCM(key).decrypt(nonce, ct, None)


def grab_from_profile(user_data: Path, profile: str, key: bytes):
    db_src = user_data / profile / "Network" / "Cookies"
    if not db_src.exists():
        return []
    tmp = Path(tempfile.mkdtemp()) / "Cookies"
    owned = False
    try:
        tmp.write_bytes(db_src.read_bytes())          # 整读绕过 CopyFile2 的独占锁
        db_path, owned = str(tmp), True
    except Exception as e:
        print("  [warn] 无法复制 %s（%r），改用只读 URI 直连" % (db_src.parent.parent.name, e))
        db_path = "%s?mode=ro&immutable=1" % db_src.resolve().as_uri()
    con = sqlite3.connect(db_path, uri=not owned)
    rows = con.execute(
        "SELECT name, value, encrypted_value, host_key, path, expires_utc "
        "FROM cookies WHERE host_key LIKE ?", ("%" + HOST + "%",)).fetchall()
    con.close()
    out = []
    for name, value, enc, host, path, exp in rows:
        try:
            raw = decrypt_cookie(enc or b"", key) if enc else (value or "").encode()
        except Exception:
            continue
        out.append({"name": name, "value": raw.decode("utf-8", "replace"),
                    "host": host, "path": path, "exp": exp, "profile": profile})
    if owned:
        shutil.rmtree(tmp.parent, ignore_errors=True)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="写入后实测 API/下载鉴权")
    args = ap.parse_args(argv)

    ud = Path(os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "User Data"
    key = get_aes_key(ud)
    all_cookies = []
    for prof in sorted(p.name for p in ud.iterdir()
                       if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile"))):
        all_cookies.extend(grab_from_profile(ud, prof, key))
    if not all_cookies:
        print("未在任何 Chrome 配置中找到 %s 的 Cookie" % HOST)
        return 1

    # 会话 Cookie 关键项：JSESSIONID / ecology_JSessionid 优先，取最近更新的配置
    all_cookies.sort(key=lambda c: c["exp"] or 0, reverse=True)
    seen, picked = set(), []
    for c in all_cookies:                    # exp 大者（或同名后写入者）优先
        if c["name"] in seen:
            continue
        seen.add(c["name"])
        picked.append(c)
    picked.sort(key=lambda c: (len(c["host"]), c["path"]))

    cookie_header = "; ".join("%s=%s" % (c["name"], c["value"]) for c in picked)
    if COOKIE_OUT.exists():
        bak = COOKIE_OUT.parent / (COOKIE_OUT.name + ".bak.%d" % int(time.time()))
        shutil.move(str(COOKIE_OUT), str(bak))
        print("旧 Cookie 已备份 -> %s" % bak.name)
    COOKIE_OUT.write_text(cookie_header, encoding="utf-8")
    print("写入 %s（长度 %d）" % (COOKIE_OUT, len(cookie_header)))
    for c in picked:
        print("  %-28s host=%-22s profile=%s" % (c["name"], c["host"], c["profile"]))

    if args.verify:
        req = urllib.request.Request(
            "http://%s:8088/api/portal/quickSearch/getQuickSearchTypes?__random__=9" % HOST,
            headers={"Cookie": cookie_header, "User-Agent": "Mozilla/5.0"})
        body = urllib.request.urlopen(req, timeout=20).read(160).decode("utf-8", "replace")
        print("API 实测:", body.strip()[:120])
        req2 = urllib.request.Request(
            "http://%s:8088/weaver/weaver.file.FileDownload?fileid=290754" % HOST,
            headers={"Cookie": cookie_header, "User-Agent": "Mozilla/5.0"})
        r2 = urllib.request.urlopen(req2, timeout=30)
        head = r2.read(24)
        print("下载实测: content-type=%s 头=%s" % (r2.headers.get("Content-Type"), head[:8].hex()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
