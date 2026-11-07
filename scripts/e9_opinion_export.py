# -*- coding: utf-8 -*-
"""e9 审批意见按案号导出工具（Markdown，保留前端富文本显示格式）——批量·并行·幂等版。

数据源（ecologyE9，只读账号）：
- workflow_requestbase   申请单主表（requestmark=案号/单号，requestname=标题）
- workflow_requestLog    流程流转意见（logtype/remark 富文本）
- workflow_approvelog    审批意见（nodeid/operator/remark 富文本）
- workflow_nodebase / HrmResource / workflow_base  环节 / 人名 / 流程名

性能与可靠性：
- **并行 + 长连接**：启动时建立 N 条持久数据库连接（连接池），线程池并发消费批次，
  把 SQL 连接能力打满；每条连接只做自己的查询，连接全程复用不重连；
- **幂等**：文件先写临时文件再原子替换（os.replace），重复执行不产生半截文件；
  `--skip-existing` 跳过已存在文件，可任意中断续跑；图片按 fileid 去重，
  已下载的不再请求；输出索引 _index.csv 追加写；
- **容错**：单条坏记录不拖垮整批，错误全部登记 _errors.csv，绝不静默吞掉。

输出：每个案号一个 .md（意见富文本原样保留：截图 <img>、表格、红字样式与前端一致），
按案号字母前缀分子目录；--embed-images 时图片下载到 images/ 并改写为相对路径。

用法示例：
  python scripts/e9_opinion_export.py --node-like 法务 --embed-images --oa-base http://<OA> --workers 16
  python scripts/e9_opinion_export.py --node-like 法务 --embed-images --skip-existing   # 断点续跑
  python scripts/e9_opinion_export.py --workflow-like 合同 --from-date 2026-09-01 --limit 30
  python scripts/e9_opinion_export.py --requestmark-prefix CIP2026
  python scripts/e9_opinion_export.py --recent 100

凭据默认取用户环境变量 DB_HOST / DB_PORT / DB_USER / DB_PWD_READONLY（不写死在代码里）；
登录 Cookie 存本地文件（默认 data/.e9_cookie），不入库。
"""
from __future__ import annotations

import argparse
import csv
import os
import queue
import re
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytds

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = PROJECT_ROOT / "data" / "e9-opinion-export"

INVALID_FS_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')
PREFIX_RE = re.compile(r"^([A-Za-z]+)")
IMG_SRC_RE = re.compile(
    r'src="((?:https?://[^/"]+)?/weaver/weaver\.file\.FileDownload\?fileid=([0-9a-zA-Z]+))"')
BATCH = 500


# ---------------- 连接池（长连接，全程复用） ----------------

class ConnPool:
    """N 条持久数据库连接；worker 用完归还，不关闭不重连。"""

    def __init__(self, args, size: int):
        self.q: queue.Queue = queue.Queue()
        for _ in range(size):
            self.q.put(pytds.connect(
                dsn=args.host, port=args.port, user=args.user, password=args.password,
                database=args.database, timeout=args.timeout, login_timeout=60))
        self.size = size

    @contextmanager
    def acquire(self):
        conn = self.q.get()
        try:
            yield conn
        finally:
            self.q.put(conn)

    def close(self):
        while not self.q.empty():
            try:
                self.q.get_nowait().close()
            except Exception:
                pass


def rows(cur, sql, params=None):
    cur.execute(sql, params or ())
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def load_names(cur) -> dict:
    names = {}
    for r in rows(cur, "SELECT id, lastname FROM HrmResource"):
        names[str(r["id"])] = r["lastname"] or str(r["id"])
    for r in rows(cur, "SELECT id, nodename FROM workflow_nodebase"):
        names["node:" + str(r["id"])] = r["nodename"] or ("节点" + str(r["id"]))
    for r in rows(cur, "SELECT id, workflowname FROM workflow_base"):
        names["wf:" + str(r["id"])] = r["workflowname"] or ("流程" + str(r["id"]))
    return names


def pick_candidates(cur, args, node_ids=None) -> list[dict]:
    where = []
    params: list = []
    if node_ids is not None:
        ph = ",".join(["%s"] * len(node_ids))
        where.append(
            "(b.requestid IN (SELECT DISTINCT requestid FROM workflow_requestLog "
            "WHERE nodeid IN (%s)) OR b.requestid IN (SELECT DISTINCT requestid "
            "FROM workflow_approvelog WHERE nodeid IN (%s)))" % (ph, ph))
        params.extend(list(node_ids) + list(node_ids))
    if not args.include_unmarked:
        where.append("b.requestmark IS NOT NULL")
        where.append("LTRIM(RTRIM(b.requestmark)) <> ''")
    if args.requestmark:
        where.append("b.requestmark IN (%s)" % ",".join(["%s"] * len(args.requestmark)))
        params.extend(args.requestmark)
    if args.requestmark_prefix:
        where.append("b.requestmark LIKE %s")
        params.append(args.requestmark_prefix + "%")
    if args.workflow_like:
        where.append("b.workflowid IN (SELECT id FROM workflow_base WHERE workflowname LIKE %s)")
        params.append("%" + args.workflow_like + "%")
    if args.from_date:
        where.append("b.createdate >= %s")
        params.append(args.from_date)
    if args.to_date:
        where.append("b.createdate <= %s")
        params.append(args.to_date)
    if args.recent:
        where = ["b.requestmark IS NOT NULL", "LTRIM(RTRIM(b.requestmark)) <> ''"]
        params = []
    sql = ("SELECT b.requestid, b.requestmark, b.requestname, b.createdate, b.createtime, "
           "b.status, b.workflowid, b.creater FROM workflow_requestbase b WHERE "
           + " AND ".join(where))
    if args.recent:
        sql = sql.replace("SELECT b.requestid",
                          "SELECT TOP %d b.requestid" % int(args.recent), 1)
        sql += " ORDER BY b.requestid DESC"
    elif args.all:
        sql += " ORDER BY b.requestid"
    else:
        sql += " ORDER BY b.createdate DESC, b.requestid DESC"
        if args.limit:
            sql = sql.replace("SELECT b.requestid",
                              "SELECT TOP %d b.requestid" % int(args.limit), 1)
    return rows(cur, sql, params)


def fetch_opinions_batch(cur, ids: list[int], node_ids=None) -> dict:
    """一批 requestid 的意见（2 次 IN 查询，走 requestid 索引）。"""
    ph = ",".join(["%s"] * len(ids))
    node_filter = set(node_ids) if node_ids is not None else None
    out: dict[int, list] = {i: [] for i in ids}
    for r in rows(cur,
                  "SELECT requestid, logtype, nodeid, operator, operatedate, operatetime, "
                  "CAST(remark AS nvarchar(max)) AS remark FROM workflow_requestLog "
                  "WHERE requestid IN (%s)" % ph, ids):
        if r["remark"] and r["remark"].strip():
            if node_filter is not None and r["nodeid"] not in node_filter:
                continue
            out.setdefault(r["requestid"], []).append(
                {"src": "流转意见", "nodeid": r["nodeid"], "operator": r["operator"],
                 "date": r["operatedate"], "time": r["operatetime"],
                 "html": r["remark"].strip()})
    for r in rows(cur,
                  "SELECT requestid, nodeid, operator, logdate, logtime, "
                  "CAST(remark AS nvarchar(max)) AS remark FROM workflow_approvelog "
                  "WHERE requestid IN (%s)" % ph, ids):
        if r["remark"] and r["remark"].strip():
            if node_filter is not None and r["nodeid"] not in node_filter:
                continue
            out.setdefault(r["requestid"], []).append(
                {"src": "审批意见", "nodeid": r["nodeid"], "operator": r["operator"],
                 "date": r["logdate"], "time": r["logtime"], "html": r["remark"].strip()})
    for rid, items in out.items():
        items.sort(key=lambda x: (x["date"] or "", x["time"] or ""))
        seen, dedup = set(), []
        for it in items:
            key = (it["operator"], it["html"][:300])   # 两表重复意见去重
            if key in seen:
                continue
            seen.add(key)
            dedup.append(it)
        out[rid] = dedup
    return out


class ImageFetcher:
    """把意见里的截图下载到本地 images/（fileid 去重），改写为相对路径。

    线程安全：claim 机制防止同一 fileid 并发重复下载；失败保留原链接并计数。
    """

    def __init__(self, out_dir: Path, oa_base: str, cookie: str, max_concurrent: int = 2,
                 min_interval: float = 0.4):
        self.img_dir = out_dir / "images"
        self.img_dir.mkdir(parents=True, exist_ok=True)
        self.oa_base = oa_base.rstrip("/")
        self.cookie = cookie
        self._lock = threading.Lock()
        self._sem = threading.BoundedSemaphore(max_concurrent)   # 服务端限并发：下载节流
        self._rate_lock = threading.Lock()
        self._next_start = 0.0
        self.min_interval = min_interval
        self._events: dict[str, threading.Event] = {}
        self.done: set = set()
        self.failed: set = set()
        self.count = 0
        self.opener = urllib.request.build_opener()
        self.opener.addheaders = [
            ("User-Agent", "Mozilla/5.0"),
            ("Cookie", cookie),
            ("Referer", self.oa_base + "/wui/index.html"),
        ]

    def _fetch(self, fileid: str) -> bool:
        with self._lock:
            if fileid in self.done:
                return True
            if fileid in self.failed:
                return False
            ev = self._events.get(fileid)
            if ev is None:
                ev = threading.Event()
                self._events[fileid] = ev
                owner = True
            else:
                owner = False
        if not owner:                      # 同一 fileid 只由一个线程下载，其余等结果
            ev.wait(120)
            return fileid in self.done
        ok = self._download(fileid)
        ev.set()
        return ok

    def _download(self, fileid: str) -> bool:
        path = self.img_dir / (fileid + ".png")
        if path.exists() and path.stat().st_size > 0:
            with self._lock:
                self.done.add(fileid)
            return True
        url = self.oa_base + "/weaver/weaver.file.FileDownload?fileid=" + fileid
        data = b""
        for attempt in range(3):          # 服务端限流：失败退避重试
            try:
                with self._sem:
                    with self._rate_lock:  # 全局节奏：请求间隔，避免触发服务端限流
                        wait = self._next_start - time.time()
                        if wait > 0:
                            time.sleep(wait)
                        self._next_start = time.time() + self.min_interval
                    r = self.opener.open(urllib.request.Request(url), timeout=30)
                    data = r.read()
            except Exception:
                data = b""
            if len(data) >= 8:
                break
            time.sleep(5 * (attempt + 1))
        if len(data) < 8:
            with self._lock:
                self.failed.add(fileid)
            return False
        tmp = path.with_suffix(".png.tmp%d" % os.getpid())
        tmp.write_bytes(data)
        os.replace(tmp, path)          # 原子写，幂等可重入
        with self._lock:
            self.done.add(fileid)
            self.count += 1
        return True

    def embed(self, html: str) -> str:
        def repl(m):
            fileid = m.group(2)
            if self._fetch(fileid):
                return 'src="../images/%s.png"' % fileid
            original = m.group(1)
            if original.startswith("http"):
                return 'src="%s"' % original
            return 'src="%s%s"' % (self.oa_base, original)
        return IMG_SRC_RE.sub(repl, html)


def rewrite_imgs(html: str, oa_base: str) -> str:
    if not oa_base:
        return html
    return html.replace('src="/', 'src="%s/' % oa_base.rstrip("/"))


def render_markdown(req: dict, opinions: list[dict], names: dict, oa_base: str) -> str:
    wf = names.get("wf:" + str(req["workflowid"]), req["workflowid"])
    creater = names.get(str(req.get("creater") or ""), "") or ""
    lines = [
        "# %s" % (req["requestmark"] or req["requestid"]),
        "",
        "| 项 | 内容 |",
        "|---|---|",
        "| 案号/单号 | %s |" % (req["requestmark"] or ""),
        "| 流程 | %s |" % wf,
        "| 标题 | %s |" % (req["requestname"] or ""),
        "| 发起人 | %s |" % creater,
        "| 发起日期 | %s %s |" % (req["createdate"], req["createtime"]),
        "| 状态 | %s |" % (req["status"] or ""),
        "| 意见条数 | %d |" % len(opinions),
        "",
        "---",
        "",
        "## 审批意见（按时间顺序）",
        "",
    ]
    for i, op in enumerate(opinions, 1):
        who = names.get(str(op["operator"]), op["operator"])
        node = names.get("node:" + str(op["nodeid"]), op["nodeid"])
        lines.append("### %d. %s · %s · %s %s" % (i, node, who, op["date"], op["time"]))
        lines.append("")
        lines.append("<!-- 来源：%s -->" % op["src"])
        lines.append("")
        lines.append(rewrite_imgs(op["html"], oa_base))
        lines.append("")
    return "\n".join(lines)


def shard_and_name(mark: str, requestid, used: dict) -> tuple[str, str]:
    m = PREFIX_RE.match(mark or "")
    shard = m.group(1).upper() if m else "R%d" % (requestid // 100000)
    name = INVALID_FS_CHARS.sub("_", mark or ("R%d" % requestid)).strip() or ("R%d" % requestid)
    if len(name) > 80:   # Windows 路径长度保护
        name = name[:80] + "_" + str(requestid)
    if name in used:
        name = "%s_%s" % (name, requestid)
    used[name] = requestid
    return shard, name + ".md"


def atomic_write(path: Path, text: str):
    tmp = path.with_suffix(path.suffix + ".tmp%d" % os.getpid())
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def fix_images(out_dir: Path, fetcher: ImageFetcher, workers: int = 8) -> tuple[int, int]:
    """后处理模式：扫描已导出 md，把绝对图片链接下载到本地并改写为相对路径（幂等）。

    已嵌图的文件跳过；下载失败的保留原链接并计入失败数，可重复执行补漏。
    """
    files = [p for p in out_dir.rglob("*.md") if p.name != "_index.csv"]
    fixed = unchanged = 0
    lock = threading.Lock()

    def work(p: Path):
        nonlocal fixed, unchanged
        text = p.read_text(encoding="utf-8")
        if 'src="http' not in text:
            with lock:
                unchanged += 1
            return
        new = fetcher.embed(text)
        if new != text:
            atomic_write(p, new)
            with lock:
                fixed += 1
        else:
            with lock:
                unchanged += 1

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, files))
    return fixed, unchanged


MIME_BY_MAGIC = ((b"\x89PNG", "image/png"), (b"\xff\xd8\xff", "image/jpeg"),
                 (b"GIF8", "image/gif"), (b"RIFF", "image/webp"))


# ---------------- HTML → Markdown（正文纯文本化） ----------------

from html.parser import HTMLParser

_BLOCK_TAGS = {"p", "div", "section", "article", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6"}
_SKIP_TAGS = {"script", "style", "head", "title"}


class _HtmlToMd(HTMLParser):
    """把富文本意见转成 Markdown：段落/换行/加粗/斜体/列表/表格/图片。

    不转义普通字符（正文是中文合同文本）；图片保留 data: 内嵌或原始链接。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list = []        # 输出块
        self.inline: list = []       # 当前行缓冲
        self.bold = 0
        self.italic = 0
        self.skip = 0
        self.in_table = 0
        self._a_href = ""
        self.table_rows: list = []
        self.row_cells: list | None = None
        self.cell_buf: list | None = None

    def _flush_inline(self):
        text = "".join(self.inline).strip()
        self.inline = []
        return text

    def _flush_para(self):
        text = self._flush_inline()
        if text:
            self.parts.append(text)

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self.skip += 1
            return
        a = dict(attrs)
        if tag in ("b", "strong"):
            self.bold += 1
        elif tag in ("i", "em"):
            self.italic += 1
        elif tag == "img":
            src = a.get("src", "")
            if src:
                if self.cell_buf is not None:
                    self.cell_buf.append("![图片](%s)" % src)
                else:
                    self._flush_para()
                    self.parts.append("![图片](%s)" % src)
        elif tag == "a":
            self._a_href = a.get("href", "")
            self.inline.append("[")
        elif tag == "table":
            self._flush_para()
            self.in_table += 1
            self.table_rows = []
            self.row_cells = None
        elif tag == "tr" and self.in_table:
            self.row_cells = []
        elif tag in ("td", "th") and self.row_cells is not None:
            self.cell_buf = []
        elif tag in ("ul", "ol"):
            self._flush_para()
        elif tag in _BLOCK_TAGS:
            if self.cell_buf is not None:      # 单元格内的段落/换行 → 空格
                self.cell_buf.append(" ")
            elif tag == "li":
                self._flush_para()
                self.inline.append("- ")
            else:
                self._flush_para()

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if tag in ("b", "strong"):
            self.bold = max(0, self.bold - 1)
        elif tag in ("i", "em"):
            self.italic = max(0, self.italic - 1)
        elif tag == "a":
            self.inline.append("](%s)" % getattr(self, "_a_href", ""))
        elif tag in ("td", "th") and self.row_cells is not None:
            cell = " ".join("".join(self.cell_buf or []).split())
            self.row_cells.append(cell)
            self.cell_buf = None
        elif tag == "tr" and self.row_cells is not None:
            if self.row_cells:
                self.table_rows.append(self.row_cells)
            self.row_cells = None
        elif tag == "table" and self.in_table:
            if self.table_rows:
                rows = [" | ".join(c.replace("|", "／") for c in r) for r in self.table_rows]
                ncol = max(len(r) for r in self.table_rows)
                header = rows[0].split(" | ")
                header += [""] * (ncol - len(header))
                lines = [" | ".join(header), " | ".join(["---"] * ncol)] + rows[1:]
                self.parts.append("\n".join("| " + l + " |" for l in lines))
            self.table_rows = []
            self.in_table = max(0, self.in_table - 1)
        elif tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush_para()

    def handle_data(self, data):
        if self.skip:
            return
        t = data
        if self.bold:
            t = "**%s**" % t
        if self.italic:
            t = "*%s*" % t
        if self.row_cells is not None:
            if self.cell_buf is None:
                self.cell_buf = []
            self.cell_buf.append(t)
        else:
            self.inline.append(t)

    def result(self) -> str:
        self._flush_para()
        return "\n\n".join(self.parts)


_MD_HTML_RE = re.compile(r"<(p|div|br|table|img|b|i|u|strong|em|span|a|tr|td|th|li|ul|ol|h[1-6])\b", re.I)


def html_to_markdown(html: str) -> str:
    p = _HtmlToMd()
    p.feed(html)
    p.close()
    text = p.result()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def to_text(out_dir: Path, workers: int = 8) -> tuple[int, int]:
    """把 md 中残留的富文本 HTML 全部解析为 Markdown 纯文本（幂等）。"""
    files = [p for p in out_dir.rglob("*.md") if not p.name.startswith("_")]
    changed = skipped = 0
    lock = threading.Lock()

    def work(p: Path):
        nonlocal changed, skipped
        text = p.read_text(encoding="utf-8")
        if not _MD_HTML_RE.search(text):
            with lock:
                skipped += 1
            return
        atomic_write(p, html_to_markdown(text))
        with lock:
            changed += 1

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, files))
    return changed, skipped


def inline_images(out_dir: Path, workers: int = 8) -> tuple[int, int]:
    """把 md 里的本地图片引用改为 base64 data URI 内嵌（自包含单文件，幂等）。"""
    pat = re.compile(r'src="\.\./images/([0-9a-zA-Z]+)\.png"')
    img_dir = out_dir / "images"
    files = [p for p in out_dir.rglob("*.md") if not p.name.startswith("_")]
    changed = skipped = 0
    lock = threading.Lock()

    def work(p: Path):
        nonlocal changed, skipped
        text = p.read_text(encoding="utf-8")
        ids = pat.findall(text)
        if not ids:
            with lock:
                skipped += 1
            return
        def repl(m):
            f = img_dir / (m.group(1) + ".png")
            data = f.read_bytes()
            mime = next((v for k, v in MIME_BY_MAGIC if data.startswith(k)), "image/png")
            import base64
            return 'src="data:%s;base64,%s"' % (mime, base64.b64encode(data).decode())
        atomic_write(p, pat.sub(repl, text))
        with lock:
            changed += 1

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, files))
    return changed, skipped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="e9 审批意见按案号批量导出（Markdown，并行/幂等）")
    ap.add_argument("--host", default=os.environ.get("DB_HOST", ""))
    ap.add_argument("--port", type=int, default=int(os.environ.get("DB_PORT", "1433")))
    ap.add_argument("--user", default=os.environ.get("DB_USER", ""))
    ap.add_argument("--password", default=os.environ.get("DB_PWD_READONLY", ""))
    ap.add_argument("--database", default="ecologyE9")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--workers", type=int, default=16, help="并行线程数（=长连接数，打满连接能力）")
    ap.add_argument("--batch", type=int, default=BATCH, help="每批 requestid 数量")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--oa-base", default="", help="OA 站点地址（改写意见内图片为绝对地址）")
    ap.add_argument("--requestmark", nargs="*", default=None, help="指定案号（可多个）")
    ap.add_argument("--requestmark-prefix", default=None, help="案号前缀")
    ap.add_argument("--workflow-like", default=None, help="流程名称包含（如：合同）")
    ap.add_argument("--from-date", default=None, help="发起日期起（YYYY-MM-DD）")
    ap.add_argument("--to-date", default=None, help="发起日期止（YYYY-MM-DD）")
    ap.add_argument("--recent", type=int, default=0, help="按 requestid 倒序取最近 N 个有案号的单")
    ap.add_argument("--limit", type=int, default=0, help="最多导出多少个案号（0=不限）")
    ap.add_argument("--all", action="store_true", help="全量导出全部有案号记录")
    ap.add_argument("--include-unmarked", action="store_true",
                    help="连没有案号的请求一起导（文件名用 R<requestid>）")
    ap.add_argument("--skip-existing", action="store_true", help="跳过已存在文件（断点续跑，幂等）")
    ap.add_argument("--embed-images", action="store_true",
                    help="把意见里的截图下载到本地 images/ 并改写为相对路径（需登录 Cookie）")
    ap.add_argument("--fix-images", action="store_true",
                    help="后处理模式：只处理已有 md 里的图片（下载+改写为相对路径），不重新导出")
    ap.add_argument("--inline-images", action="store_true",
                    help="后处理模式：把 md 里的本地图片改为 base64 内嵌（自包含单文件，幂等）")
    ap.add_argument("--to-text", action="store_true",
                    help="后处理模式：把 md 里的富文本 HTML 解析成 Markdown 纯文本（幂等）")
    ap.add_argument("--cookie-file", default=str(PROJECT_ROOT / "data" / ".e9_cookie"),
                    help="登录 Cookie 字符串文件（本地保存，不入库）")
    ap.add_argument("--node-like", default=None,
                    help="只导审批环节名包含该关键词的记录（如：法务），"
                         "且意见只保留该类环节（其他环节意见不要）")
    args = ap.parse_args(argv)

    if not (args.host and args.user and args.password):
        print("缺少连接信息：请设置 DB_HOST/DB_USER/DB_PWD_READONLY 或传参", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    fetcher = None
    if args.embed_images or args.fix_images:
        ck_path = Path(args.cookie_file)
        if not ck_path.exists():
            print("嵌图需要登录 Cookie：请把浏览器 Cookie 保存到 %s" % ck_path, file=sys.stderr)
            return 2
        fetcher = ImageFetcher(out_dir, args.oa_base or os.environ.get("OA_BASE_URL", ""),
                               ck_path.read_text(encoding="utf-8").strip())
        print("嵌图模式：图片下载到 %s（fileid 去重，限速 %s/s + 失败退避）"
              % (fetcher.img_dir, round(1 / fetcher.min_interval, 1)), flush=True)

    if args.fix_images:
        t0 = time.time()
        fixed, unchanged = fix_images(out_dir, fetcher)
        print("图片后处理完成：改写 %d 个文件，%d 个无需处理（%.1f 分钟）"
              % (fixed, unchanged, (time.time() - t0) / 60), flush=True)
        print("图片：新下载 %d 张，失败 %d 个 fileid（保留原链接，可重复执行补漏）"
              % (fetcher.count, len(fetcher.failed)), flush=True)
        return 0

    if args.inline_images:
        t0 = time.time()
        changed, skipped = inline_images(out_dir, max(2, args.workers))
        print("base64 内嵌完成：改写 %d 个文件，%d 个无需处理（%.1f 分钟）"
              % (changed, skipped, (time.time() - t0) / 60), flush=True)
        return 0

    if args.to_text:
        t0 = time.time()
        changed, skipped = to_text(out_dir, max(2, args.workers))
        print("HTML→Markdown 完成：改写 %d 个文件，%d 个无需处理（%.1f 分钟）"
              % (changed, skipped, (time.time() - t0) / 60), flush=True)
        return 0

    pool = ConnPool(args, max(2, args.workers))
    print("连接池：%d 条长连接" % pool.size, flush=True)
    with pool.acquire() as conn:
        cur = conn.cursor()
        names = load_names(cur)
        node_ids = None
        if args.node_like:
            node_ids = [r["id"] for r in rows(
                cur, "SELECT id FROM workflow_nodebase WHERE nodename LIKE %s",
                ("%" + args.node_like + "%",))]
            if not node_ids:
                print("未找到名称包含「%s」的审批环节" % args.node_like, file=sys.stderr)
                return 3
            print("筛选审批环节：%s → %d 个节点" % (args.node_like, len(node_ids)), flush=True)
        cand = pick_candidates(cur, args, node_ids)
    print("候选记录：%d 条，开始并行导出（workers=%d batch=%d）…"
          % (len(cand), pool.size, args.batch), flush=True)

    used_lock = threading.Lock()
    used: dict[str, int] = {}
    stats_lock = threading.Lock()
    index_rows: list = []
    errors: list = []
    done = skipped = 0
    t0 = time.time()

    batches = [cand[i:i + args.batch] for i in range(0, len(cand), args.batch)]

    def work(batch):
        nonlocal done, skipped
        with pool.acquire() as conn:
            cur = conn.cursor()
            opin_map = fetch_opinions_batch(cur, [r["requestid"] for r in batch], node_ids)
        local_index, local_errors = [], []
        local_done = local_skipped = 0
        for req in batch:
            try:
                opinions = opin_map.get(req["requestid"], [])
                if node_ids is not None and not opinions:
                    local_skipped += 1        # 目标环节无意见文本：不生成文件
                    continue
                with used_lock:
                    shard, fname = shard_and_name(req["requestmark"], req["requestid"], used)
                sdir = out_dir / shard
                sdir.mkdir(parents=True, exist_ok=True)
                fpath = sdir / fname
                if args.skip_existing and fpath.exists():
                    local_skipped += 1
                    continue
                md = render_markdown(req, opinions, names, args.oa_base)
                if fetcher is not None:
                    md = fetcher.embed(md)
                atomic_write(fpath, md)      # 原子写：幂等，可随时中断
                local_index.append([req["requestmark"], req["requestid"], req["requestname"],
                                    names.get("wf:" + str(req["workflowid"]), req["workflowid"]),
                                    req["createdate"], len(opinions), "%s/%s" % (shard, fname)])
                local_done += 1
            except Exception as e:           # 单条坏记录不拖垮整批；错误登记上报
                local_errors.append([req.get("requestmark"), req.get("requestid"),
                                     type(e).__name__, str(e)[:200]])
        with stats_lock:
            done += local_done
            skipped += local_skipped
            index_rows.extend(local_index)
            errors.extend(local_errors)
            finished = done + skipped
        if finished and finished % 5000 < args.batch:
            el = time.time() - t0
            speed = finished / el if el else 0
            eta = (len(cand) - finished) / speed / 60 if speed else -1
            print("进度 %d/%d（跳过 %d，失败 %d） %.1f 条/秒 预计剩余 %.0f 分钟"
                  % (finished, len(cand), skipped, len(errors), speed, eta), flush=True)

    with ThreadPoolExecutor(max_workers=pool.size) as ex:
        list(ex.map(work, batches))

    index_path = out_dir / "_index.csv"
    need_header = not index_path.exists()
    with index_path.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if need_header:
            w.writerow(["案号", "requestid", "标题", "流程", "发起日期", "意见条数", "文件"])
        w.writerows(index_rows)
    if errors:
        with (out_dir / "_errors.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["案号", "requestid", "错误类型", "错误信息"])
            w.writerows(errors)
    pool.close()
    print("完成：导出 %d，跳过 %d，失败 %d -> %s（耗时 %.1f 分钟，%.1f 条/秒）"
          % (done, skipped, len(errors), out_dir,
             (time.time() - t0) / 60, done / max(time.time() - t0, 1e-9)), flush=True)
    if errors:
        print("失败明细见 %s（前 3 条：%s）" % (out_dir / "_errors.csv", errors[:3]), flush=True)
    if fetcher is not None:
        print("图片：新下载 %d 张，失败 %d 个 fileid（保留原链接）"
              % (fetcher.count, len(fetcher.failed)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
