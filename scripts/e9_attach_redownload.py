# -*- coding: utf-8 -*-
"""附件重下与 md 回填修复（流式·幂等·可续跑·带拒绝缓存）。

处理两类问题行（只动含 FileDownload 链接或 attachments/ 本地链接的行）：
  1) `[名](…FileDownload?fileid=N)（**下载失败**，…）` —— 重新下载；
  2) `[名](attachments/<案号>/<N>-<名>)` 但本地文件缺失或是 HTML 错误页伪装的假文件 —— 重新下载并清理假文件。

下载成功：落盘 attachments/<案号>/<fileid>-<文件名> 并把 md 该行改为本地相对链接；
下载失败（无权限页）：该行统一为远端链接 + **下载失败** 标记，并记入拒绝缓存
（data/_dl_denied.txt），下次默认跳过；换到有权限的账号后用 --retry-denied 全量重试。

用法：
  python scripts/e9_attach_redownload.py --limit 30        # 先试 30 条
  python scripts/e9_attach_redownload.py                   # 全量（2 工人限速）
  python scripts/e9_attach_redownload.py --retry-denied    # 换有权限账号后重试此前被拒的
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import threading
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = PROJECT_ROOT / "data" / "e9-opinion-export"
DEFAULT_COOKIE = PROJECT_ROOT / "data" / ".e9_cookie"
DEFAULT_OA = "http://hc.hcipi.com:8088"
DENIED_CACHE = PROJECT_ROOT / "data" / "_dl_denied.txt"

INVALID_FS_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')
LINE_RE = re.compile(r"^(?P<pre>-\s+\*\*(?P<label>[^*]+)\*\*：\[(?P<name>[^\]]+)\]\((?P<link>[^)]+)\))"
                     r"(?P<tail>.*)$")
FID_RE = re.compile(r"[?&]fileid=(\d+)")
HTML_HEADS = (b"<!doctype", b"<html", b"<head")


def looks_like_html_error(data: bytes) -> bool:
    s = data.lstrip()[:200].lower()
    return s.startswith(HTML_HEADS)


def fs_name(s: str) -> str:
    return INVALID_FS_CHARS.sub("_", s).strip() or "_"


def looks_fake(path: Path) -> bool:
    """本地附件文件缺失、为空或是 HTML 错误页伪装（假文件）。"""
    try:
        if not path.exists() or path.stat().st_size == 0:
            return True
        with open(path, "rb") as fh:
            return looks_like_html_error(fh.read(300))
    except OSError:
        return True


class Fetcher:
    def __init__(self, cookie: str, oa: str, workers: int, min_interval: float):
        self.oa = oa.rstrip("/")
        self._rate_lock = threading.Lock()
        self._next = 0.0
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self.stats = defaultdict(int)
        self.opener = urllib.request.build_opener()
        self.opener.addheaders = [("User-Agent", "Mozilla/5.0"), ("Cookie", cookie),
                                  ("Referer", self.oa + "/wui/index.html")]

    def get(self, fileid: int):
        """返回 (data, kind)；kind ∈ downloaded/denied/error。"""
        url = "%s/weaver/weaver.file.FileDownload?fileid=%d" % (self.oa, fileid)
        data = None
        for attempt in range(3):
            try:
                with self._rate_lock:
                    wait = self._next - time.time()
                    if wait > 0:
                        time.sleep(wait)
                    self._next = time.time() + self.min_interval
                data = self.opener.open(urllib.request.Request(url), timeout=90).read()
                break
            except Exception:
                data = None
                time.sleep(1.0 * (attempt + 1))
        with self._lock:
            if data and len(data) >= 32 and not looks_like_html_error(data):
                self.stats["downloaded"] += 1
                return data, "downloaded"
            if data is not None:
                self.stats["denied"] += 1
                return data, "denied"
            self.stats["error"] += 1
        return None, "error"


def parse_items(md_path: Path, out_dir: Path):
    """扫描单个 md，产出待办项列表（含整行文本与行号）。"""
    text = md_path.read_text(encoding="utf-8", errors="replace")
    lines = text.split("\n")
    items = []
    for i, line in enumerate(lines):
        m = LINE_RE.match(line)
        if not m:
            continue
        link = m.group("link")
        local: Path | None = None
        if "FileDownload" in link or FID_RE.search(link):
            fm = FID_RE.search(link)
            if not fm:
                continue
            fid = int(fm.group(1))
            kind = "failed" if "下载失败" in m.group("tail") else "remote"
        elif link.startswith("attachments/"):
            fm = re.match(r"(\d+)-", Path(link).name)
            if not fm:
                continue
            fid = int(fm.group(1))
            local = out_dir / link
            kind = "local"
        else:
            continue
        items.append({"line": i, "label": m.group("label"), "name": m.group("name"),
                      "link": link, "tail": m.group("tail"), "fileid": fid,
                      "local": local, "kind": kind})
    return lines, items


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--cookie-file", default=str(DEFAULT_COOKIE))
    ap.add_argument("--oa-base", default=DEFAULT_OA)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--min-interval", type=float, default=0.4)
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 条待办（0=全部）")
    ap.add_argument("--retry-denied", action="store_true",
                    help="重试拒绝缓存里的 fileid（换有权限账号后用）")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    cookie = Path(args.cookie_file).read_text(encoding="utf-8").strip()
    fetcher = Fetcher(cookie, args.oa_base, args.workers, args.min_interval)

    denied: set[int] = set()
    if DENIED_CACHE.exists():
        if args.retry_denied:
            DENIED_CACHE.unlink()
            print("已清空拒绝缓存，全量重试", flush=True)
        else:
            denied = {int(x) for x in DENIED_CACHE.read_text(encoding="utf-8").split() if x.strip()}
            print("拒绝缓存 %d 个 fileid（--retry-denied 可重试）" % len(denied), flush=True)

    denied_new: set[int] = set()
    denied_lock = threading.Lock()
    report_rows: list[list] = []
    rep_lock = threading.Lock()
    counters = defaultdict(int)
    cnt_lock = threading.Lock()

    def process_md(md: Path):
        try:
            lines, items = parse_items(md, out_dir)
        except OSError as e:
            with cnt_lock:
                counters["md_read_error"] += 1
            print("  读取失败 %s: %r" % (md.name, e), flush=True)
            return
        changed = False
        for it in items:
            if it["kind"] == "local" and it["local"] is not None:
                if not looks_fake(it["local"]):
                    with cnt_lock:
                        counters["skip_real"] += 1
                    continue
            if it["fileid"] in denied:
                with cnt_lock:
                    counters["skip_denied"] += 1
                continue
            if args.limit and counters.get("handled", 0) >= args.limit:
                break
            with cnt_lock:
                counters["handled"] = counters.get("handled", 0) + 1

            data, kind = fetcher.get(it["fileid"])
            mm = re.search(r"(\d+)", it["tail"])
            size_hint = mm.group(1) if mm else ""
            if kind == "downloaded":
                safe = fs_name(it["name"] or ("%d.bin" % it["fileid"]))
                fname = "%d-%s" % (it["fileid"], safe)
                rel = "attachments/%s/%s" % (md.stem, fname)
                target = out_dir / "attachments" / md.stem / fname
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_suffix(target.suffix + ".part")
                tmp.write_bytes(data)
                os.replace(tmp, target)
                if it["local"] and it["local"] != target and it["local"].exists():
                    try:
                        it["local"].unlink()
                    except OSError:
                        pass
                lines[it["line"]] = "- **%s**：[%s](%s)（%d B）" % (
                    it["label"], it["name"], rel, len(data))
                changed = True
                with rep_lock:
                    report_rows.append([md.stem, it["fileid"], "downloaded", len(data), rel])
            elif kind == "denied":
                url = "%s/weaver/weaver.file.FileDownload?fileid=%d" % (args.oa_base, it["fileid"])
                if it["local"] and it["local"].exists():
                    try:
                        it["local"].unlink()
                    except OSError:
                        pass
                lines[it["line"]] = "- **%s**：[%s](%s)（**下载失败**，%s）" % (
                    it["label"], it["name"], url, size_hint or "大小未知")
                changed = True
                with denied_lock:
                    denied_new.add(it["fileid"])
                with rep_lock:
                    report_rows.append([md.stem, it["fileid"], "denied", "", ""])
            else:
                with rep_lock:
                    report_rows.append([md.stem, it["fileid"], "error", "", ""])
        if changed:
            tmp = md.with_suffix(md.suffix + ".tmp")
            tmp.write_text("\n".join(lines), encoding="utf-8")
            os.replace(tmp, md)

    t0 = time.time()
    md_files = sorted(p for p in out_dir.rglob("*.md") if not p.name.startswith("_"))
    print("md 文件 %d 个，开始流式处理（workers=%d, interval=%.2fs）"
          % (len(md_files), args.workers, args.min_interval), flush=True)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {}
        done = 0
        for md in md_files:
            # 控制在飞任务数量，保持内存与输出节奏
            while len(futs) >= args.workers * 4:
                for f in [x for x in futs if x.done()]:
                    f.result()
                    del futs[f]
                    done += 1
                if len(futs) >= args.workers * 4:
                    time.sleep(0.2)
            if args.limit and counters.get("handled", 0) >= args.limit:
                break
            futs[ex.submit(process_md, md)] = md
        for f in as_completed(list(futs)):
            f.result()
            done += 1
            if done % 100 == 0:
                print("  已扫 %d/%d md | 待办处理 %d 下载%d 拒绝%d 跳过%d | %.0fs"
                      % (done, len(md_files), counters.get("handled", 0),
                         fetcher.stats["downloaded"], fetcher.stats["denied"],
                         counters.get("skip_real", 0) + counters.get("skip_denied", 0),
                         time.time() - t0), flush=True)

    if denied_new:
        old = set()
        if DENIED_CACHE.exists():
            old = {int(x) for x in DENIED_CACHE.read_text(encoding="utf-8").split() if x.strip()}
        DENIED_CACHE.write_text("\n".join(str(x) for x in sorted(old | denied_new)), encoding="utf-8")
    rep = out_dir / "_attach_redownload_report.csv"
    with open(rep, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["案号", "fileid", "结果", "字节数", "路径"])
        w.writerows(report_rows)

    print("\n===== 汇总 =====", flush=True)
    print("扫描 md %d 个 | 待办 %d | 真文件跳过 %d | 缓存跳过 %d"
          % (len(md_files), counters.get("handled", 0),
             counters.get("skip_real", 0), counters.get("skip_denied", 0)))
    print("下载成功 %d / 拒绝 %d / 网络错误 %d（耗时 %.0f s）"
          % (fetcher.stats["downloaded"], fetcher.stats["denied"],
             fetcher.stats["error"], time.time() - t0))
    if denied_new:
        print("拒绝缓存更新：%s（累计 %d 个 fileid，换有权限账号后 --retry-denied）"
              % (DENIED_CACHE, len(denied) | len(denied_new)))
    print("明细报告：%s" % rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
