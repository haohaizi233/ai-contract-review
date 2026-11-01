# -*- coding: utf-8 -*-
"""定位有下载权限的范围：查受限类别的共享规则 + 探测备用下载入口/API。

用法: python tools/e9_dl_lasttry2.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import pytds

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import websocket  # noqa
from e9_attach_probe import call  # noqa
from e9_cdp_diag import call_tmo  # noqa

ROOT = Path(__file__).resolve().parent.parent
CK = ROOT / "data" / ".e9_cookie"
OA = "http://hc.hcipi.com:8088"
FID_BAD = 297063
FID_OK = 299892


def rows(cur, sql):
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def dl_test(path, cookie, tag, extra_headers=None):
    h = {"Cookie": cookie, "User-Agent": "Mozilla/5.0", "Referer": OA + "/wui/index.html"}
    if extra_headers:
        h.update(extra_headers)
    try:
        rr = urllib.request.urlopen(urllib.request.Request(OA + path, headers=h), timeout=30)
        body = rr.read()
        is_html = body.lstrip()[:9].lower().startswith(b"<!doctype") or body.lstrip()[:5].lower().startswith(b"<html")
        if is_html:
            txt = re.sub(r"<[^>]+>", " ", body.decode("utf-8", "replace"))
            msg = re.sub(r"\s+", " ", txt).strip().replace("-->", "")[:36]
            print("  %-34s -> HTML %s" % (tag, msg))
        else:
            print("  %-34s -> 文件OK %s %d字节" % (tag, rr.headers.get("Content-Type"), len(body)))
    except Exception as e:
        print("  %-34s -> 异常 %s" % (tag, str(e)[:60]))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    cookie = CK.read_text(encoding="utf-8").strip()

    print("== 1. 受限类别的共享规则（谁能下载）==")
    conn = pytds.connect(
        dsn=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "1433")),
        user=os.environ["DB_USER"], password=os.environ["DB_PWD_READONLY"],
        database="ecologyE9", timeout=60, login_timeout=60)
    cur = conn.cursor()
    for cat in (20, 1112, 1156, 1503):
        try:
            rr = rows(cur, ("SELECT TOP 6 id, seccategoryid, sharetype, seclevel, sharelevel, downloadlevel, "
                            "userid, departmentid, roleid, subcompanyid, foralluser, allmanagers "
                            "FROM DocSecCategoryShare WHERE seccategoryid=%d") % cat)
            print("  类别 %d: %s" % (cat, rr))
        except Exception as e:
            conn.rollback()
            print("  类别 %d 查询失败 %s" % (cat, str(e)[:60]))
    # 3331 的角色/部门
    print("  3331:", rows(cur, ("SELECT id, loginid, lastname, seclevel, departmentid, subcompanyid1, jobtitle "
                              "FROM HrmResource WHERE id=3331")))
    try:
        print("  3331 角色:", rows(cur, "SELECT * FROM HrmRoleMembers WHERE resourceid=3331"))
    except Exception as e:
        conn.rollback()
        print("  HrmRoleMembers 查询失败", str(e)[:60])
    conn.close()

    print()
    print("== 2. 备用下载入口实测（BAD fileid=%d / OK 对照 %d）==" % (FID_BAD, FID_OK))
    for fid, tag in ((FID_OK, "OK对照"), (FID_BAD, "BAD目标")):
        print(" --", tag)
        dl_test("/weaver/weaver.file.FileDownload?fileid=%d" % fid, cookie, "标准 FileDownload")
        dl_test("/weaver/weaver.file.ImageDownload?fileid=%d" % fid, cookie, "ImageDownload")
        dl_test("/weaver/weaver.file.FileDownloadByToken?fileid=%d" % fid, cookie, "FileDownloadByToken")
        dl_test("/weaver/docs/docs/DocDsp.ext?imagefileId=%d" % fid, cookie, "DocDsp.ext?imagefileId")
        dl_test("/api/doc/acc/download?imagefileid=%d" % fid, cookie, "api/doc/acc/download")
        dl_test("/api/doc/acc/getDownloadUrl?imagefileid=%d" % fid, cookie, "api/doc/acc/getDownloadUrl")

    print()
    print("== 3. 页面上下文 fetch 探测 API（带会话）==")
    ver = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=8).read())
    ws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=20)
    targets = [t for t in call(ws, "Target.getTargets")["targetInfos"]
               if t["type"] == "page" and "hc.hcipi.com" in t["url"] and "ViewRequest" not in t["url"]]
    if not targets:
        targets = [t for t in call(ws, "Target.getTargets")["targetInfos"]
                   if t["type"] == "page" and "hc.hcipi.com" in t["url"]]
    sid = call(ws, "Target.attachToTarget", {"targetId": targets[0]["targetId"], "flatten": True})["sessionId"]
    js = r"""
    (async () => {
      const out = {};
      const tries = [
        ['acc', '/api/doc/acc/getAccesory?imagefileid=%d'],
        ['rename', '/api/doc/acc/rename'],
        ['dl', '/api/doc/acc/download?imagefileid=%d'],
      ];
      for (const [k, u] of tries) {
        try {
          const r = await fetch(u, {credentials: 'include'});
          const t = await r.text();
          out[k] = r.status + ' ' + t.slice(0, 120);
        } catch (e) { out[k] = String(e); }
      }
      return JSON.stringify(out);
    })()
    """ % (FID_BAD, FID_BAD)
    r = call_tmo(ws, "Runtime.evaluate", {"expression": js, "awaitPromise": True, "returnByValue": True}, sid, tmo=20)
    if r != "__TIMEOUT__":
        print(" ", r.get("result", {}).get("result", {}).get("value"))
    else:
        print("  超时")
    ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
