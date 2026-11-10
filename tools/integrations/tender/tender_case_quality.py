"""三件套导出质量规则及经过校验的旧 OA 资源缓存。"""

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
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


class CookieSafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        old, new = (
            urllib.parse.urlsplit(request.full_url),
            urllib.parse.urlsplit(newurl),
        )
        if redirected and (old.scheme, old.netloc) != (new.scheme, new.netloc):
            for name in list(redirected.headers):
                if name.lower() in (
                    "cookie",
                    "cookie2",
                    "authorization",
                    "proxy-authorization",
                ):
                    redirected.remove_header(name)
        return redirected


def group_process_links(links):
    groups = OrderedDict()
    for pid, source in links:
        sources = groups.setdefault(pid, [])
        if source not in sources:
            sources.append(source)
    return [(pid, "、".join(sources)) for pid, sources in groups.items()]


def image_coverage(total, local):
    if not total:
        return "无图片", "无"
    return (
        "全部本地" if local == total else "全部缺失" if not local else "部分缺失",
        f"{local}/{total} 本地",
    )


def file_namespace(migrated, new_exists, old_exists):
    if not migrated:
        return "tender_infra" if new_exists else "unknown"
    if new_exists and old_exists:
        return "ambiguous"
    return "tender_infra" if new_exists else "e9_imagefile" if old_exists else "unknown"


def merge_oa_logs(rows):
    out, cross_source = [], {}
    for row in sorted(
        rows,
        key=lambda r: (
            str(r.get("operatedate") or ""),
            str(r.get("operatetime") or ""),
            str(r.get("LOGID") or ""),
        ),
    ):
        row = dict(row)
        source = row.get("source", "workflow_requestLog")
        key = tuple(
            str(row.get(k) or "")
            for k in (
                "requestid",
                "nodeid",
                "operator",
                "operatedate",
                "operatetime",
                "remark",
            )
        )
        previous = cross_source.get(key)
        provenance = {"table": source, "id": str(row.get("LOGID", ""))}
        if previous is not None and source not in {
            x["table"] for x in previous["sources"]
        }:
            previous["sources"].append(provenance)
            continue
        row["sources"] = [provenance]
        out.append(row)
        cross_source[key] = row
    return out


def valid_image(path):
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
        return True
    except Exception:
        return False


def valid_file(path):
    try:
        p = Path(path)
        if not p.is_file() or not p.stat().st_size:
            return False
        head = p.open("rb").read(1024).lstrip().lower()
        return not (
            head.startswith(b"<!doctype html")
            or head.startswith(b"<html")
            or head.startswith(b"<script")
        )
    except OSError:
        return False


def image_sources(body):
    return [
        m.group(2)
        for m in re.finditer(
            r"""<img\b[^>]*?\bsrc\s*=\s*(["'])(.*?)\1""", body, re.I | re.S
        )
    ] + re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", body)


def resource_error(message):
    if "cannot identify image file" in message:
        return "源站响应不是有效图片，未保存；可能为错误页或无权访问"
    return message


class OldResources:
    def __init__(self, root, out, oa_base):
        self.root, self.out, self.base = Path(root), Path(out), oa_base
        self.dir = self.out / "images" / "oa-cache"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cookie = (
            (self.root / "data/.e9_cookie").read_text(encoding="utf-8").strip()
            if (self.root / "data/.e9_cookie").exists()
            else ""
        )
        # 失败缓存绑定会话；更新 Cookie 后自动重新尝试。
        self.session = hashlib.sha256(self.cookie.encode()).hexdigest()[:12]
        self.index_path = self.out / "_cache/oa_resources.json"
        try:
            self.index = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.index = {}
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), CookieSafeRedirect()
        )
        self.auth_failed = threading.Event()
        self.network_failures = {}
        self.network_lock = threading.Lock()
        self.file_sizes = {}

    def absolute(self, url):
        return urllib.parse.urljoin(self.base + "/", html.unescape(url))

    def lookup(self, url, image=True):
        url = self.absolute(url)
        fid = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get(
            "fileid", [""]
        )[0]
        parsed, origin = urllib.parse.urlsplit(url), urllib.parse.urlsplit(self.base)
        expected = self.file_sizes.get(url)
        if (
            (parsed.scheme, parsed.netloc) == (origin.scheme, origin.netloc)
            and parsed.path.lower() == "/weaver/weaver.file.filedownload"
            and fid
            and re.fullmatch(r"[a-zA-Z0-9]+", fid)
        ):
            candidate = self.root / "data/e9-opinion-export/images" / (fid + ".png")
            if (valid_image(candidate) if image else valid_file(candidate)) and (
                image or not expected or candidate.stat().st_size == expected
            ):
                return str(candidate), ""
        r = self.index.get(url, {})
        p = r.get("path")
        expected = self.file_sizes.get(url)
        if (
            p
            and not image
            and expected
            and Path(p).exists()
            and Path(p).stat().st_size != expected
        ):
            return None, "文件大小与旧库元数据不符，未计入有效附件"
        if p and (valid_image(p) if image else valid_file(p)):
            return p, ""
        return None, resource_error(r.get("error", "尚未获取"))

    def fetch(self, job):
        url, image = job
        url = self.absolute(url)
        p, _ = self.lookup(url, image)
        if p:
            return url, {"path": p, "session": self.session}, "skip"
        old = self.index.get(url, {})
        if (
            old.get("error")
            and old.get("session") == self.session
            and old.get("retryable") is False
        ):
            return url, old, "cached-fail"
        try:
            hostname = urllib.parse.urlsplit(url).netloc
            if self.network_failures.get(hostname, 0) >= 8:
                raise ValueError(
                    "本轮该主机连续网络失败，剩余资源暂缓；未发起此项请求，下次运行可重试"
                )
            if (
                self.auth_failed.is_set()
                and urllib.parse.urlsplit(url).netloc
                == urllib.parse.urlsplit(self.base).netloc
            ):
                raise ValueError("当前旧 OA 会话失效，需更新登录 Cookie")
            if url.startswith("data:image/"):
                head, body = url.split(",", 1)
                data = (
                    base64.b64decode(body)
                    if ";base64" in head
                    else urllib.parse.unquote_to_bytes(body)
                )
                target = ""
            else:
                parts = urllib.parse.urlsplit(url)
                if parts.scheme not in ("http", "https"):
                    raise ValueError("本地路径或 blob 引用无法从服务器下载")
                headers = {"User-Agent": "Mozilla/5.0"}
                if parts.netloc == urllib.parse.urlsplit(self.base).netloc:
                    headers.update(
                        Cookie=self.cookie, Referer=self.base + "/wui/index.html"
                    )
                with self.opener.open(
                    urllib.request.Request(
                        urllib.parse.quote(url, safe=":/?&=%"), headers=headers
                    ),
                    timeout=8,
                ) as response:
                    chunks, started = [], time.monotonic()
                    while True:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        chunks.append(chunk)
                        if time.monotonic() - started > 60:
                            raise TimeoutError("单项下载超过60秒，本轮暂缓")
                    data = b"".join(chunks)
                    target = response.geturl().lower()
                with self.network_lock:
                    self.network_failures[parts.netloc] = 0
            prefix = data[:8192].lower()
            target_parts = urllib.parse.urlsplit(target)
            oa_origin = urllib.parse.urlsplit(self.base).netloc
            oa_response = (
                urllib.parse.urlsplit(url).netloc == oa_origin
                or target_parts.netloc == oa_origin
            )
            if (
                oa_response
                and b"<html" in prefix
                and (
                    target_parts.path.lower().endswith("/login.jsp")
                    or any(
                        x in prefix for x in (b"login.jsp", b"loginid", b"loginform")
                    )
                )
            ):
                self.auth_failed.set()
                raise ValueError("旧 OA 返回登录页；当前 Cookie 失效")
            if image:
                from PIL import Image

                with Image.open(io.BytesIO(data)) as im:
                    extension = (im.format or "png").lower()
                    im.verify()
            else:
                if not data or prefix.lstrip().startswith(
                    (b"<html", b"<!doctype html", b"<script")
                ):
                    raise ValueError("旧 OA 返回空内容或 HTML 错误页，未保存为附件")
                expected = self.file_sizes.get(url)
                if expected and len(data) != expected:
                    raise ValueError(
                        "文件大小与旧库元数据不符：实际 %d / 数据库 %d，未保存"
                        % (len(data), expected)
                    )
                extension = "bin"
            p = self.dir / (hashlib.sha256(url.encode()).hexdigest() + "." + extension)
            temp = str(p) + ".part"
            Path(temp).write_bytes(data)
            os.replace(temp, p)
            return url, {"path": str(p), "session": self.session}, "ok"
        except Exception as error:
            message = resource_error(str(error))
            if any(
                x in message.lower()
                for x in (
                    "timed out",
                    "timeout",
                    "10060",
                    "10061",
                    "name resolution",
                    "getaddrinfo",
                    "超过60秒",
                )
            ):
                with self.network_lock:
                    hostname = urllib.parse.urlsplit(url).netloc
                    self.network_failures[hostname] = (
                        self.network_failures.get(hostname, 0) + 1
                    )
            deterministic = any(
                x in message
                for x in (
                    "HTTP Error 403",
                    "HTTP Error 404",
                    "Cookie 失效",
                    "会话失效",
                    "本地路径或 blob",
                )
            )
            return (
                url,
                {
                    "error": message,
                    "session": self.session,
                    "retryable": not deterministic,
                },
                "fail",
            )

    def prepare(self, jobs, workers, prog):
        jobs = sorted(
            dict.fromkeys((self.absolute(u), image) for u, image in jobs),
            key=lambda job: not job[1],
        )
        prog.detail(2, f"旧 OA 资源校验/补抓 0/{len(jobs)}")
        counts = {"ok": 0, "skip": 0, "fail": 0}
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = [executor.submit(self.fetch, job) for job in jobs]
            for i, future in enumerate(as_completed(futures), 1):
                url, result, status = future.result()
                self.index[url] = result
                counts["fail" if status in ("fail", "cached-fail") else status] += 1
                if i % 10 == 0 or i == len(jobs):
                    prog.detail(
                        2,
                        f"旧 OA 资源 {i}/{len(jobs)}，成功 {counts['ok']}，复用 {counts['skip']}，未获取 {counts['fail']}",
                    )
                if i % 100 == 0 or i == len(jobs):
                    self.save()
        self.save()
        return counts

    def save(self):
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        temp = str(self.index_path) + ".tmp"
        Path(temp).write_text(
            json.dumps(self.index, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(temp, self.index_path)
