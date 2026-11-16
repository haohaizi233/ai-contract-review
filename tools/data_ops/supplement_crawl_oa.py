# -*- coding: utf-8 -*-
"""OA 远端图片/附件补充爬取（补爬）。

背景：merge 后的 cases md 里仍有大量资源索引停留在远端
（OA FileDownload?fileid=...），分为两类：
  1. 审批意见内嵌图：`![图片]（未取得：`URL`）`  —— 113 位 hex fileid（新系统图片）
     或数字 fileid（旧 E9 图片）→ 落地到 albums/<sha>.<ext>
  2. 旧库附件：远程 <code>URL</code>、`**旧库附件未落盘**` —— 数字 fileid
     → 落地到 attachments/<sha>.<ext>

做法（复用 2026-11-10 已验证可用思路）：
  - Cookie 取自 data/.e9_cookie（已验证可用：HTTP 200 + 有效 PNG）
  - 有限并发（默认 4）+ 全局 0.3s 间隔，避免触发 OA 风控
  - 按内容魔数判定后缀，以 SHA-256 命名，与 albums/attachments 现有约定一致
  - 断点续传：state.jsonl 记录成功项，重跑自动跳过
  - --apply 回填 md：把"未取得/远程地址/未落盘"替换成本地相对链接

用法：
  python tools/data_ops/supplement_crawl_oa.py --pilot 100        # 试跑，只下载不回填
  python tools/data_ops/supplement_crawl_oa.py --apply            # 全量下载 + 回填
  python tools/data_ops/supplement_crawl_oa.py --only-images --apply
  python tools/data_ops/supplement_crawl_oa.py --only-attachments --apply
  python tools/data_ops/supplement_crawl_oa.py --rewrite-only     # 只按已有 state 回填
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "data" / "contract_merged"
CASES = BASE / "cases"
ALBUMS = BASE / "albums"
ATTACH = BASE / "attachments"
COOKIE_F = ROOT / "data" / ".e9_cookie"
OA = "http://hc.hcipi.com:8088/weaver/weaver.file.FileDownload?fileid="
WORK = ROOT / "tmp" / "supplement_crawl_oa"
STATE_F = WORK / "state.jsonl"
STATUS_F = WORK / "status.json"
REPORT_F = WORK / "rewrite_report.csv"

# `![图片]（未取得：`URL`）`
IMG_MARK_RE = re.compile(
    r"!\[图片\]（未取得：`" + re.escape(OA) + r"(?P<fid>[0-9A-Za-z]+)`）"
)
# 变体：相对路径形式 `![图片]（未取得：`/weaver/...?fileid=N&download=1`）`
IMG_MARK_REL_RE = re.compile(
    r"!\[图片\]（未取得：`/weaver/weaver\.file\.FileDownload\?fileid=(?P<fid>[0-9A-Za-z]+)(?:&download=1)?`）"
)
# 变体：第二 OA 入口 `![图片]（未取得：`http://183.36.37.83:8088/...?fileid=N`）`
IMG_MARK_ALT_HOST_RE = re.compile(
    r"!\[图片\]（未取得：`http://183\.36\.37\.83:8088/weaver/weaver\.file\.FileDownload\?fileid=(?P<fid>[0-9A-Za-z]+)(?:&download=1)?`）"
)
# 合并缺口里的远程 <code>URL</code>
REMOTE_CODE_RE = re.compile(r"<code>" + re.escape(OA) + r"(?P<fid>[0-9A-Za-z]+)</code>")
# 行内"未落盘"附件：提取该行的 fileid（数字）
UNLANDED_LINE_RE = re.compile(r"未落盘")
LINE_FID_RE = re.compile(re.escape(OA) + r"(?P<fid>[0-9A-Za-z]+)")
# 已本地化标记（命中则视为不需再抓）
LANDED_MARK_RE = re.compile(r"\*\*已落盘\*\*|\*\*旧库附件已补抓\*\*|已取得")


def ext_of(raw: bytes) -> str:
    if raw[:4] == b"\x89PNG":
        return "png"
    if raw[:2] == b"\xff\xd8":
        return "jpg"
    if raw[:3] == b"GIF":
        return "gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    if raw[:4] == b"%PDF":
        return "pdf"
    if raw[:2] == b"PK":
        head = raw[:4096]
        if b"word/" in head:
            return "docx"
        if b"xl/" in head:
            return "xlsx"
        if b"ppt/" in head:
            return "pptx"
        return "zip"
    if raw[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "doc"
    if raw[:5] == b"<?xml" or raw[:6] == b"<?xml ".strip():
        return "xml"
    return "bin"


IMAGE_EXTS = {"png", "jpg", "gif", "webp"}


def collect_targets():
    """扫描 cases md，返回 {fid: {'kinds': set, 'occ': n}}"""
    targets: dict[str, dict] = {}
    for dp, _, fns in os.walk(CASES):
        for fn in fns:
            if not fn.endswith(".md"):
                continue
            try:
                text = (Path(dp) / fn).read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            # 1) 审批意见内嵌图未取得
            for rx, kind in ((IMG_MARK_RE, "image"), (REMOTE_CODE_RE, "image")):
                for m in rx.finditer(text):
                    fid = m.group("fid")
                    t = targets.setdefault(fid, {"kinds": set(), "occ": 0})
                    t["kinds"].add(kind)
                    t["occ"] += 1
            # 2) 同行为"未落盘"且带 OA fileid 的附件行，且未标已落盘/已补抓
            for line in text.splitlines():
                if "未落盘" not in line:
                    continue
                if LANDED_MARK_RE.search(line):
                    continue
                for m in LINE_FID_RE.finditer(line):
                    fid = m.group("fid")
                    t = targets.setdefault(fid, {"kinds": set(), "occ": 0})
                    t["kinds"].add("attach")
                    t["occ"] += 1
    return targets


def load_state() -> dict:
    done = {}
    if STATE_F.exists():
        for ln in STATE_F.read_text(encoding="utf-8", errors="replace").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("status") == "ok":
                done[r["fid"]] = r
    return done


_lock = threading.Lock()
_next = [0.0]
_interval = [0.3]
cookie = ""


def fetch(fid: str):
    with _lock:
        pause = _next[0] - time.monotonic()
        if pause > 0:
            time.sleep(pause)
        _next[0] = time.monotonic() + _interval[0]
    url = OA + fid
    try:
        req = urllib.request.Request(
            url,
            headers={
                "Cookie": cookie,
                "User-Agent": "Mozilla/5.0",
                "Referer": "http://hc.hcipi.com:8088/wui/index.html",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read(64 * 1024 * 1024 + 1)
        if not raw:
            return {"fid": fid, "status": "empty", "bytes": 0, "url": url}
        ext = ext_of(raw)
        sha = hashlib.sha256(raw).hexdigest()
        folder = ALBUMS if ext in IMAGE_EXTS else ATTACH
        dest = folder / f"{sha}.{ext}"
        if not dest.exists():
            folder.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".part")
            tmp.write_bytes(raw)
            os.replace(tmp, dest)
        return {
            "fid": fid,
            "status": "ok",
            "bytes": len(raw),
            "ext": ext,
            "sha": sha,
            "folder": folder.name,
            "local": str(dest.relative_to(BASE)).replace("\\", "/"),
            "url": url,
        }
    except urllib.error.HTTPError as e:
        return {
            "fid": fid,
            "status": f"http_{e.code}",
            "bytes": 0,
            "error": str(e),
            "url": url,
        }
    except Exception as e:
        return {
            "fid": fid,
            "status": f"err_{type(e).__name__}",
            "bytes": 0,
            "error": str(e)[:150],
            "url": url,
        }


def rewrite_markdown(done: dict, apply: bool) -> None:
    """按 state 把 md 里的远端索引替换为本地链接。"""
    rows = []
    changed_files = 0
    for dp, _, fns in os.walk(CASES):
        for fn in fns:
            if not fn.endswith(".md"):
                continue
            path = Path(dp) / fn
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            orig = text
            hits = 0

            def repl_img(m):
                nonlocal hits
                fid = m.group("fid")
                r = done.get(fid)
                if not r or r.get("ext") not in IMAGE_EXTS:
                    return m.group(0)
                hits += 1
                return f"![图片](../../{r['local']})"

            def repl_code(m):
                nonlocal hits
                fid = m.group("fid")
                r = done.get(fid)
                if not r:
                    return m.group(0)
                hits += 1
                return f"<code>../../{r['local']}</code>"

            text = IMG_MARK_RE.sub(repl_img, text)
            text = IMG_MARK_REL_RE.sub(repl_img, text)
            text = IMG_MARK_ALT_HOST_RE.sub(repl_img, text)
            text = REMOTE_CODE_RE.sub(repl_code, text)

            # 5) 任意前缀的 FileDownload 残留（http://host、/weaver、D:\weaver 等）
            RESIDUAL_URL_RE = re.compile(
                r"(?:[a-zA-Z]:\\|https?://[^\s`<>|]+|/)?"
                r"weaver[/\\]weaver\.file\.FileDownload\?fileid="
                r"(?P<fid>[0-9A-Za-z]+)(?:&download=1)?"
            )

            def repl_residual(m):
                nonlocal hits
                r = done.get(m.group("fid"))
                if not r:
                    return m.group(0)
                hits += 1
                return "../../" + r["local"]

            text = RESIDUAL_URL_RE.sub(repl_residual, text)

            # 6) 缺口行措辞：已含本地路径的"资源未取得或内容无效"行 → 已补爬
            lines = []
            for ln in text.splitlines(keepends=True):
                if "资源未取得或内容无效" in ln and (
                    "../../albums/" in ln or "../../attachments/" in ln
                ):
                    ln = ln.replace("资源未取得或内容无效", "已补爬（2026-11-12）")
                    hits += 1
                lines.append(ln)
            text = "".join(lines)

            # 3) 数字附件行的"原地址"链接 → 本地链接 + 已落盘标记
            def fix_attach_line(line):
                nonlocal hits
                if "未落盘" not in line or LANDED_MARK_RE.search(line):
                    return line
                m = LINE_FID_RE.search(line)
                if not m:
                    return line
                r = done.get(m.group("fid"))
                if not r:
                    return line
                hits += 1
                new = line.replace(
                    "**旧库附件未落盘**", "**旧库附件已补抓（本轮补爬）**"
                )
                # 把 (原地址)(<URL>) 整段替换成本地链接
                new = re.sub(
                    r"（\[原地址\]\(<" + re.escape(OA + m.group("fid")) + r">\)）",
                    f"（[本地文件](../../{r['local']})）",
                    new,
                )
                new = re.sub(re.escape(OA + m.group("fid")), "../../" + r["local"], new)
                return new

            lines = [fix_attach_line(ln) for ln in text.splitlines(keepends=True)]
            text = "".join(lines)

            # 4) 数据缺口/合并缺口里的"图片未取得/附件尚未获取"行 → 标注已解决
            def fix_gap(line):
                nonlocal hits
                if "未取得" not in line and "尚未获取" not in line:
                    return line
                m = LINE_FID_RE.search(line)
                if not m:
                    return line
                r = done.get(m.group("fid"))
                if not r:
                    return line
                hits += 1
                return (
                    "- **已补爬（2026-11-12）**：fileid `%s` → `../../%s`，原缺口说明已解决\n"
                    % (m.group("fid"), r["local"])
                )

            def fix_stale(line):
                nonlocal hits
                if "仍为远程地址" not in line:
                    return line
                m = LINE_FID_RE.search(line)
                if not m or m.group("fid") not in done:
                    return line
                hits += 1
                return line.replace(
                    "正文图片仍为远程地址，未证明已本地化",
                    "正文图片已本地化（2026-11-12 补爬）",
                )

            lines = [fix_gap(ln) for ln in text.splitlines(keepends=True)]
            text = "".join(lines)
            lines = [fix_stale(ln) for ln in text.splitlines(keepends=True)]
            text = "".join(lines)

            if hits and text != orig:
                rows.append(
                    {
                        "case": str(path.relative_to(CASES)).replace("\\", "/"),
                        "replacements": hits,
                    }
                )
                if apply:
                    bak = path.with_suffix(path.suffix + ".precrawl.bak")
                    if not bak.exists():
                        bak.write_bytes(orig.encode("utf-8"))
                    tmp = path.with_suffix(path.suffix + ".part")
                    tmp.write_text(text, encoding="utf-8", newline="")
                    try:
                        os.replace(tmp, path)
                    except PermissionError:
                        time.sleep(0.3)
                        os.replace(tmp, path)
                changed_files += 1

    REPORT_F.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_F.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["case", "replacements"])
        w.writeheader()
        w.writerows(rows)
    print(
        "rewrite: files_touched=%d total_replacements=%d apply=%s report=%s"
        % (changed_files, sum(r["replacements"] for r in rows), apply, REPORT_F),
        flush=True,
    )


def main():
    global cookie
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rewrite-only", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--interval", type=float, default=0.3)
    a = ap.parse_args()
    _interval[0] = a.interval

    cookie = COOKIE_F.read_text(encoding="utf-8").strip()
    WORK.mkdir(parents=True, exist_ok=True)
    ALBUMS.mkdir(exist_ok=True)
    ATTACH.mkdir(exist_ok=True)

    if a.rewrite_only:
        rewrite_markdown(load_state(), a.apply)
        return

    targets = collect_targets()
    done = load_state()
    pending = [f for f in sorted(targets) if f not in done]
    print(
        "targets=%d done=%d pending=%d" % (len(targets), len(done), len(pending)),
        flush=True,
    )

    if a.limit:
        pending = pending[: a.limit]
    if a.pilot:
        pending = pending[: a.pilot]

    counts = collections.Counter()
    with STATE_F.open("a", encoding="utf-8") as f:
        with ThreadPoolExecutor(max_workers=a.workers) as pool:
            for i, res in enumerate(pool.map(fetch, pending), 1):
                counts[res["status"]] += 1
                if res["status"] == "ok":
                    counts[res["ext"]] += 1
                f.write(json.dumps(res, ensure_ascii=False) + "\n")
                f.flush()
                if i % 25 == 0:
                    STATUS_F.write_text(
                        json.dumps(
                            {
                                "processed": i,
                                "total": len(pending),
                                "counts": dict(counts),
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                    print(i, "/", len(pending), dict(counts), flush=True)
    STATUS_F.write_text(
        json.dumps(
            {
                "finished": True,
                "processed": len(pending),
                "total": len(pending),
                "counts": dict(counts),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print("DONE", dict(counts), flush=True)

    if a.apply:
        rewrite_markdown(load_state(), True)


if __name__ == "__main__":
    main()
