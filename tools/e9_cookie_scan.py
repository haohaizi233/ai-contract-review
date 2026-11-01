# -*- coding: utf-8 -*-
"""只读扫描本机 Chrome/Edge 全部配置里的 hc.hcipi.com 会话，看是否有高权限账号登录。
只打印 loginid，不回显 Cookie 值。

用法: python tools/e9_cookie_scan.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from e9_cookie_grab import get_aes_key, grab_from_profile  # noqa

HOST = "hc.hcipi.com"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    bases = [
        ("Chrome", Path(os.environ["LOCALAPPDATA"]) / "Google/Chrome/User Data"),
        ("Edge", Path(os.environ["LOCALAPPDATA"]) / "Microsoft/Edge/User Data"),
    ]
    for browser, ud in bases:
        if not (ud / "Local State").exists():
            print("%s: 无配置" % browser)
            continue
        key = get_aes_key(ud)
        profiles = sorted(p.name for p in ud.iterdir()
                          if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile")))
        for prof in profiles:
            try:
                cookies = grab_from_profile(ud, prof, key)
            except Exception as e:
                print("%s/%s: 读取失败 %r" % (browser, prof, e))
                continue
            mine = [c for c in cookies if HOST in (c.get("host") or "")]
            if not mine:
                continue
            names = {c["name"]: c["value"][:12] for c in mine}
            lid = next((c["value"] for c in mine if c["name"] == "loginidweaver"), None)
            js = next((c["value"] for c in mine if c["name"] == "ecology_JSessionid"), None)
            print("%s/%s: loginidweaver=%s JSESSIONID=%s... 字段=%s"
                  % (browser, prof, lid, (js or "")[:10], sorted(names)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
