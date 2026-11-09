"""Recover source image bytes and restore only SHA256-matching historical PNG assets."""

import argparse
import base64
import hashlib
import html
import io
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "tmp/image_recovery_20261109"
BASE = ROOT / "data/contract_merged"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def variants(raw):
    with Image.open(io.BytesIO(raw)) as image:
        image.load()
        pixels = image.convert("RGBA").tobytes()
        size = image.size
        mode = "RGBA" if image.mode in ("RGBA", "P", "LA") else "RGB"
        converted = image.convert(mode)
        yield raw
        for optimize in (True, False):
            buffer = io.BytesIO()
            converted.save(buffer, format="PNG", optimize=optimize)
            png = buffer.getvalue()
            with Image.open(io.BytesIO(png)) as check:
                assert check.size == size and check.convert("RGBA").tobytes() == pixels
            yield png


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--watch-session", action="store_true")
    args = parser.parse_args()
    if args.watch_session:
        return watch_session()

    missing = {}
    for row in __import__("csv").DictReader(
        (BASE / "recovery_missing_assets.csv").open(encoding="utf-8-sig")
    ):
        missing[row["sha256"]] = row
    urls = json.loads((WORK / "urls.json").read_text(encoding="utf8"))
    context = load_context(urls, missing)
    urls = sorted(set(urls) | set(context))

    cache = json.loads(
        (ROOT / "data/tender-case-export/_cache/oa_resources.json").read_text(
            encoding="utf8"
        )
    )
    absolute = next(
        u for u in cache if u.startswith("http") and "filedownload" in u.lower()
    )
    parts = urllib.parse.urlsplit(absolute)
    origin = parts.scheme + "://" + parts.netloc
    cookie = (ROOT / "data/.e9_cookie").read_text().strip()
    (WORK / "raw").mkdir(exist_ok=True)
    checkpoint = WORK / ("signed_results_" + digest(cookie.encode())[:12] + ".jsonl")
    done = {}
    if checkpoint.exists():
        for line in checkpoint.open(encoding="utf8"):
            r = json.loads(line)
            done[r["url_hash"]] = r
    urls = [u for u in urls if digest(u.encode()) not in done]
    if args.limit:
        urls = urls[: args.limit]
    stop = threading.Event()
    flow_cache = {}
    flow_lock = threading.Lock()

    def fetch(src):
        key = digest(src.encode())
        result = {"url_hash": key, "status": "failed", "restored": []}
        if stop.is_set():
            return result
        try:
            url = urllib.parse.urljoin(origin, html.unescape(src))
            if src.startswith("data:"):
                raw = base64.b64decode(src.split(",", 1)[1], validate=True)
            else:
                assert urllib.parse.urlsplit(url).netloc == parts.netloc
                rid = context.get(src)
                referer = origin + "/wui/index.html"
                if rid:
                    referer = (
                        origin
                        + "/weaver/workflow/request/ViewRequestForwardSPA.jsp?requestid="
                        + str(rid)
                    )
                    with flow_lock:
                        if rid not in flow_cache:
                            try:
                                flow_req = urllib.request.Request(
                                    referer,
                                    headers={
                                        "Cookie": cookie,
                                        "User-Agent": "Mozilla/5.0",
                                    },
                                )
                                with urllib.request.urlopen(
                                    flow_req, timeout=15
                                ) as response:
                                    page = response.read().decode(
                                        "utf8", errors="replace"
                                    )
                                tokens = {}
                                for name in [
                                    "authStr",
                                    "authSignatureStr",
                                    "f_weaver_belongto_userid",
                                ]:
                                    match = re.search(
                                        name + r"""["']?\s*[:=]\s*["']?([^"'\s;<>]+)""",
                                        page,
                                    )
                                    if match:
                                        tokens[name] = match.group(1)
                                flow_cache[rid] = tokens
                            except Exception:
                                flow_cache[rid] = {}
                        tokens = flow_cache[rid]
                    if tokens.get("authStr") and tokens.get("authSignatureStr"):
                        parts_url = urllib.parse.urlsplit(url)
                        query = dict(urllib.parse.parse_qsl(parts_url.query))
                        query.update(tokens)
                        query.update(
                            download="1",
                            fromrequest="1",
                            requestid=str(rid),
                            f_weaver_belongto_usertype="0",
                        )
                        url = urllib.parse.urlunsplit(
                            parts_url._replace(query=urllib.parse.urlencode(query))
                        )
                req = urllib.request.Request(
                    url,
                    headers={
                        "Cookie": cookie,
                        "User-Agent": "Mozilla/5.0",
                        "Referer": referer,
                    },
                )

                with urllib.request.urlopen(req, timeout=15) as response:
                    raw = response.read(32 * 1024 * 1024 + 1)
                if len(raw) > 32 * 1024 * 1024:
                    raise ValueError("oversize")
            rawhash = digest(raw)
            is_attachment = (
                rawhash in missing and missing[rawhash]["kind"] == "attachment"
            )
            if not is_attachment:
                with Image.open(io.BytesIO(raw)) as im:
                    im.verify()

            rawpath = WORK / "raw" / rawhash
            if not rawpath.exists():
                with rawpath.open("xb") as f:
                    f.write(raw)
            result["raw_sha256"] = rawhash
            for data in [raw] if is_attachment else variants(raw):
                h = digest(data)
                if h not in missing:
                    continue
                target = BASE / missing[h]["local"]
                if not target.exists():
                    target.parent.mkdir(exist_ok=True, parents=True)
                    try:
                        with target.open("xb") as f:
                            f.write(data)
                    except FileExistsError:
                        pass
                assert digest(target.read_bytes()) == h
                result["restored"].append(missing[h]["local"])
            result["restored"] = sorted(set(result["restored"]))
            result["status"] = "restored" if result["restored"] else "valid_unmatched"
        except Exception as exc:
            result["error_type"] = type(exc).__name__
        return result

    counts = __import__("collections").Counter()
    with (
        checkpoint.open("a", encoding="utf8") as log,
        ThreadPoolExecutor(max_workers=6) as pool,
    ):
        futures = [pool.submit(fetch, u) for u in urls]
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            counts[result["status"]] += 1
            counts["assets"] += len(result["restored"])
            log.write(json.dumps(result, ensure_ascii=False) + "\n")
            log.flush()
            if i % 25 == 0 or i == len(futures):
                print(i, "/", len(futures), dict(counts), flush=True)
    print("FINISHED", dict(counts), flush=True)


def load_context(urls, missing):
    from tender_case_quality import image_sources

    context = {}
    source = WORK / "fresh_source_opinions.jsonl"
    if source.exists():
        for line in source.open(encoding="utf8"):
            row = json.loads(line)
            for src in image_sources(row.get("html", "")):
                context.setdefault(src, row["requestid"])
    records = [
        json.loads(line)
        for line in (BASE / "contracts_merged.jsonl").open(encoding="utf8")
    ]
    by_case = {
        r["分组键"]: [x for x in r["旧requestid"].split(";") if x.isdigit()]
        for r in records
    }
    wanted = {r["case"] for r in missing.values() if r["kind"] == "attachment"}
    import csv

    for row in csv.DictReader(
        (ROOT / "data/contract_unified/attachments_unified.csv").open(
            encoding="utf-8-sig"
        )
    ):
        case = row["合同编号"]
        fid = row.get("文件ID", "")
        if case in wanted and fid.isdigit() and by_case.get(case):
            context[
                "http://hc.hcipi.com:8088/weaver/weaver.file.FileDownload?fileid=" + fid
            ] = by_case[case][0]
    return context


def refresh_cdp_cookie():
    import websocket

    profile = (
        Path(os.environ["LOCALAPPDATA"]) / "Google/Chrome/User Data/DevToolsActivePort"
    )
    endpoints = []
    if profile.exists():
        parts = profile.read_text().splitlines()
        if len(parts) >= 2:
            endpoints.append("ws://127.0.0.1:" + parts[0] + parts[1])
    for port in [9333]:
        try:
            endpoints.append(
                json.load(
                    urllib.request.urlopen(
                        "http://127.0.0.1:" + str(port) + "/json/version", timeout=2
                    )
                )["webSocketDebuggerUrl"]
            )
        except Exception:
            pass
    for endpoint in endpoints:
        try:
            with websocket.create_connection(
                endpoint, timeout=3, suppress_origin=True, http_proxy_host=None
            ) as ws:
                ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
                while True:
                    result = json.loads(ws.recv())
                    if result.get("id") == 1:
                        break
                cookies = [
                    c
                    for c in result.get("result", {}).get("cookies", [])
                    if c["domain"].lstrip(".") == "hc.hcipi.com"
                ]
                if cookies:
                    (ROOT / "data/.e9_cookie").write_text(
                        "; ".join(c["name"] + "=" + c["value"] for c in cookies),
                        encoding="utf8",
                    )
        except Exception:
            continue


def watch_session():
    import subprocess

    initial = digest((ROOT / "data/.e9_cookie").read_bytes())
    status = WORK / "watch_status.json"
    while True:
        refresh_cdp_cookie()
        current = digest((ROOT / "data/.e9_cookie").read_bytes())
        if current != initial:
            status.write_text(
                json.dumps({"state": "running", "started": time.time()}),
                encoding="utf8",
            )
            with (WORK / "signed_progress.log").open("a", encoding="utf8") as log:
                run = subprocess.run(
                    [
                        __import__("sys").executable,
                        "-u",
                        "-X",
                        "utf8",
                        str(Path(__file__).resolve()),
                    ],
                    cwd=ROOT,
                    stdout=log,
                    stderr=log,
                )
            status.write_text(
                json.dumps(
                    {
                        "state": "finished_requires_independent_acceptance",
                        "exit_code": run.returncode,
                        "finished": time.time(),
                    }
                ),
                encoding="utf8",
            )
            return
        status.write_text(
            json.dumps(
                {"state": "waiting_for_refreshed_oa_session", "checked": time.time()}
            ),
            encoding="utf8",
        )
        time.sleep(30)


if __name__ == "__main__":
    main()
