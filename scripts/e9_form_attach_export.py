# -*- coding: utf-8 -*-
"""e9 审批表单 + 附件 → 回填到按案号导出的 Markdown（批量·并行·幂等）。

数据模型（ecologyE9 只读库实测）：
- workflow_form(requestid, billformid, billid)  → 主数据行 formtable_main_{abs(billformid)}.id = billid
- workflow_bill(id, tablename) / Workflow_billdetailtable(billid=-N, tablename, title, orderid)
- workflow_billfield(billid=-N, fieldname, fielddbtype, fieldhtmltype, type, childfieldid,
                     dsporder, fieldlabel→HtmlLabelInfo(indexid, languageid=7).labelname)
- workflow_SelectItem(fieldid=billfield.id, isbill=1, selectvalue, selectname)  下拉/单选/复选项
- 浏览框(html=3)：type=1 人员(HrmResource) / 2 日期 / 4 部门(HrmDepartment) /
  12 分部(HrmSubCompany) / 16 流程(workflow_requestbase) / 17 文档(DocDetail) /
  自定义 browser.* → workflow_browserurl(fielddbtype, tablename, columname, keycolumname)
- 附件字段(html=6, text)：逗号分隔 imagefileid → imagefile 元数据 +
  {oa}/weaver/weaver.file.FileDownload?fileid=N（需登录 Cookie，本地 data/.e9_cookie）

输出：在每个案号 .md 的首个 ## 之前插入（带幂等标记整体替换）：
  ## 审批表单（主表字段按 dsporder） + ### 明细表（表格） + ## 附件（本地文件相对链接）

用法：
  python scripts/e9_form_attach_export.py --oa-base http://hc.hcipi.com:8088 --workers 16
  python scripts/e9_form_attach_export.py --requestmark CIP2024HT02082 --workers 2   # 单案试跑
  python scripts/e9_form_attach_export.py --no-download --skip-existing              # 只补链接断点续跑

凭据取环境变量 DB_HOST / DB_PORT / DB_USER / DB_PWD_READONLY；Cookie 存本地不入库。
"""
from __future__ import annotations

import argparse
import csv
import html as html_mod
import os
import queue
import re
import sqlite3
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
HTML_BR_RE = re.compile(r"<br\s*/?>", re.I)
HTML_TAG_RE = re.compile(r"<[^>]+>")
NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")

MARK_BEGIN = "<!-- e9-form-attach-begin -->"
MARK_END = "<!-- e9-form-attach-end -->"

TECH_FIELDS = {"billid", "returnid", "mxid", "dw", "requestid", "id", "mainid", "formmodeid",
               "modedatacreater", "modedatacreatertype", "modedatacreatedate",
               "modedatacreatetime", "modedatamodifier", "modedatamodifydatetime",
               "modeuuid", "form_biz_id"}
TECH_LABELS = {"billid", "returnid", "returnId", "BILLID", "明细id", "id"}


# ---------------- 连接池（长连接，全程复用） ----------------

class ConnPool:
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


def rows(cur, sql):
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def esc(v) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def norm_type(dbtype: str) -> str:
    t = (dbtype or "").lower()
    if t.startswith("browser"):
        return "browser"
    if "char" in t or "text" in t or t in ("24",):
        return "str"
    if "decimal" in t or "numeric" in t or "float" in t or "money" in t:
        return "num"
    if "int" in t:
        return "int"
    return "str"


def clean_html(s) -> str:
    """富文本 → 纯文本（<br> 转换行，去标签，反转义）。"""
    s = str(s)
    s = HTML_BR_RE.sub("\n", s)
    s = HTML_TAG_RE.sub("", s)
    s = html_mod.unescape(s)
    lines = [re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in s.split("\n")]
    return "\n".join(ln for ln in lines if ln != "")


def looks_like_html_error(data: bytes) -> bool:
    """FileDownload 鉴权失败/异常时返回 HTML 页面而非文件，识别之避免伪装落盘。"""
    s = data.lstrip()[:200].lower()
    return s.startswith(b"<!doctype") or s.startswith(b"<html") or s.startswith(b"<head")


def fs_name(s: str) -> str:
    return INVALID_FS_CHARS.sub("_", s).strip() or "_"


# ---------------- 元数据缓存（线程安全，惰性按 bill 加载） ----------------

class MetaCache:
    def __init__(self):
        self._lock = threading.Lock()
        self._bills: dict[int, dict] = {}
        self._tables: dict[str, list[str]] = {}          # 表名 → 列名列表
        self._names: dict[str, str] = {}                 # "u:<id>" / "d:<id>" / "s:<id>" / "r:<id>" / "doc:<id>"
        self._browser: dict[str, dict] = {}              # browser.XXX → {table,col,key}
        self._browser_val: dict[tuple, str] = {}

    # ---- 通用名解析 ----
    def resolve_ref(self, cur, kind: str, ident) -> str | None:
        key = "%s:%s" % (kind, ident)
        with self._lock:
            if key in self._names:
                return self._names[key]
        sql = {
            "u": "SELECT lastname AS n FROM HrmResource WHERE id=%s",
            "d": "SELECT departmentname AS n FROM HrmDepartment WHERE id=%s",
            "s": "SELECT subcompanyname AS n FROM HrmSubCompany WHERE id=%s",
            "r": "SELECT requestname AS n FROM workflow_requestbase WHERE requestid=%s",
            "doc": "SELECT docsubject AS n FROM DocDetail WHERE id=%s",
        }[kind] % int(ident)
        val = None
        try:
            r = rows(cur, sql)
            if r:
                val = r[0]["n"]
        except Exception:
            cur.connection.rollback()
            val = None
        with self._lock:
            self._names[key] = val
        return val

    def browser_meta(self, cur, fielddbtype: str) -> dict | None:
        with self._lock:
            if fielddbtype in self._browser:
                return self._browser[fielddbtype]
        meta = None
        try:
            r = rows(cur, ("SELECT tablename, columname, keycolumname FROM workflow_browserurl "
                           "WHERE fielddbtype=%s AND useable IN ('1','y')") % esc(fielddbtype))
            if not r:
                r = rows(cur, ("SELECT tablename, columname, keycolumname FROM workflow_browserurl "
                               "WHERE fielddbtype=%s") % esc(fielddbtype))
            if r and r[0]["tablename"]:
                meta = {"table": r[0]["tablename"], "col": r[0]["columname"], "key": r[0]["keycolumname"]}
        except Exception:
            cur.connection.rollback()
        with self._lock:
            self._browser[fielddbtype] = meta
        return meta

    def sys_browser(self, cur, ftype) -> dict | None:
        """workflow_browserurl.id == billfield.type（html=3 浏览框通用映射）。"""
        key = "sys:%s" % ftype
        with self._lock:
            if key in self._browser:
                return self._browser[key]
        meta = None
        try:
            r = rows(cur, "SELECT tablename, columname, keycolumname FROM workflow_browserurl WHERE id=%d" % int(ftype))
            if r and r[0]["tablename"]:
                meta = {"table": r[0]["tablename"], "col": r[0]["columname"], "key": r[0]["keycolumname"]}
        except Exception:
            cur.connection.rollback()
        with self._lock:
            self._browser[key] = meta
        return meta

    def mode_browser(self, cur, name: str) -> dict | None:
        """建模自定义浏览框 MODE_BROWSER（fielddbtype='browser.<name>'）。"""
        key = "mb:%s" % name
        with self._lock:
            if key in self._browser:
                return self._browser[key]
        meta = None
        try:
            r = rows(cur, "SELECT TOP 1 sqltext1, keyfield, showfield FROM MODE_BROWSER WHERE name=%s OR showname=%s"
                     % (esc(name), esc(name)))
            if r and r[0]["sqltext1"]:
                meta = {"sql": r[0]["sqltext1"], "key": r[0]["keyfield"]}
        except Exception:
            cur.connection.rollback()
        with self._lock:
            self._browser[key] = meta
        return meta

    def mode_val(self, cur, meta: dict, ident) -> str | None:
        bkey = ("mode", id(meta), str(ident))
        with self._lock:
            if bkey in self._browser_val:
                return self._browser_val[bkey]
        val = None
        try:
            sql = meta["sql"].replace("?", esc(ident))
            r = rows(cur, sql)
            if r:
                val = str(list(r[0].values())[0])
        except Exception:
            cur.connection.rollback()
        with self._lock:
            self._browser_val[bkey] = val
        return val

    def browser_val(self, cur, meta: dict, ident) -> str | None:
        bkey = (meta["table"], meta["col"], str(ident))
        with self._lock:
            if bkey in self._browser_val:
                return self._browser_val[bkey]
        val = None
        try:
            r = rows(cur, "SELECT %s AS n FROM %s WHERE %s=%s"
                     % (meta["col"], meta["table"], meta["key"], int(ident)))
            if r:
                val = str(r[0]["n"])
        except Exception:
            cur.connection.rollback()
        with self._lock:
            self._browser_val[bkey] = val
        return val

    def table_columns(self, cur, table: str) -> list[str]:
        with self._lock:
            if table in self._tables:
                return self._tables[table]
        r = rows(cur, ("SELECT COLUMN_NAME AS c FROM INFORMATION_SCHEMA.COLUMNS "
                       "WHERE TABLE_NAME=%s ORDER BY ORDINAL_POSITION") % esc(table))
        cols = [x["c"] for x in r]
        with self._lock:
            self._tables[table] = cols
        return cols

    # ---- bill 元数据 ----
    def bill(self, cur, bill_abs: int) -> dict | None:
        with self._lock:
            if bill_abs in self._bills:
                return self._bills[bill_abs]
        info = None
        try:
            b = rows(cur, "SELECT tablename FROM workflow_bill WHERE id=-%d" % bill_abs)
            if not b:
                return None
            info = {"table": b[0]["tablename"], "fields": {}, "details": []}
            flds = rows(cur, """SELECT f.id, f.fieldname, f.fielddbtype, f.fieldhtmltype, f.type,
                                      f.childfieldid, f.dsporder, i.labelname
                               FROM workflow_billfield f
                               LEFT JOIN HtmlLabelInfo i ON i.indexid=f.fieldlabel AND i.languageid=7
                               WHERE f.billid=-%d""" % bill_abs)
            for f in flds:
                f["label"] = (f["labelname"] or f["fieldname"]).strip()
                info["fields"].setdefault(f["fieldname"], []).append(f)
            for lst in info["fields"].values():
                lst.sort(key=lambda x: (x["dsporder"] or 0, x["id"]))
            opts = rows(cur, """SELECT fieldid, selectvalue, selectname FROM workflow_SelectItem
                                WHERE isbill=1 AND fieldid IN
                                (SELECT id FROM workflow_billfield WHERE billid=-%d)""" % bill_abs)
            o: dict[int, dict] = {}
            for x in opts:
                o.setdefault(x["fieldid"], {})[str(x["selectvalue"])] = (x["selectname"] or "").strip()
            info["options"] = o
            for d in rows(cur, """SELECT tablename, title, orderid FROM Workflow_billdetailtable
                                  WHERE billid=-%d ORDER BY orderid, id""" % bill_abs):
                info["details"].append({"table": d["tablename"], "title": d["title"], "orderid": d["orderid"]})
        except Exception:
            cur.connection.rollback()
            raise
        with self._lock:
            self._bills[bill_abs] = info
        return info

    def pick_field(self, info: dict, column: str, col_type: str, value):
        cands = info["fields"].get(column)
        if not cands:
            return None
        if len(cands) == 1:
            return cands[0]
        want = norm_type(col_type)
        same = [c for c in cands if norm_type(c["fielddbtype"]) == want]
        pool = same or cands
        if value not in (None, ""):
            toks = [t for t in re.split(r"[,;\s]+", str(value)) if t]
            for c in pool:
                opts = info["options"].get(c["id"])
                if opts and toks and all(t in opts for t in toks):
                    return c
        return pool[0]


META = MetaCache()


# ---------------- 附件下载 ----------------

class AttachFetcher:
    """fileid → 本地 attachments/<案号>/<fileid>-<文件名>；限速 + 退避重试。"""

    def __init__(self, out_dir: Path, oa_base: str, cookie: str, max_concurrent: int = 2,
                 min_interval: float = 0.4, do_download: bool = True):
        self.out_dir = out_dir
        self.oa_base = oa_base.rstrip("/")
        self.do_download = do_download
        self._sem = threading.BoundedSemaphore(max_concurrent)
        self._rate_lock = threading.Lock()
        self._next_start = 0.0
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self.downloaded = 0
        self.failed = 0
        self.skipped = 0
        self.opener = urllib.request.build_opener()
        self.opener.addheaders = [
            ("User-Agent", "Mozilla/5.0"),
            ("Cookie", cookie),
            ("Referer", self.oa_base + "/wui/index.html"),
        ]

    def remote_url(self, fileid) -> str:
        return self.oa_base + "/weaver/weaver.file.FileDownload?fileid=" + str(fileid)
    def fetch(self, mark_dir_name: str, fileid, orig_name: str, size_hint) -> dict:
        """返回 {ok, relpath, name, size, url}；relpath 为 md 相对路径。"""
        url = self.remote_url(fileid)
        safe = fs_name(orig_name or ("%s.bin" % fileid))
        fname = "%s-%s" % (fileid, safe)
        rel = "attachments/%s/%s" % (mark_dir_name, fname)
        target = self.out_dir / "attachments" / mark_dir_name / fname
        if not self.do_download:
            return {"ok": True, "relpath": None, "name": orig_name or fname,
                    "size": size_hint, "url": url, "remote_only": True}
        if target.exists() and target.stat().st_size > 0:
            with open(target, "rb") as fh:
                head = fh.read(300)
            if not looks_like_html_error(head):
                with self._lock:
                    self.skipped += 1
                return {"ok": True, "relpath": rel, "name": orig_name or fname,
                        "size": target.stat().st_size, "url": url, "remote_only": False}
            target.unlink()  # 历史遗留的 HTML 错误页伪装文件，删掉重新下载
        data = b""
        for attempt in range(3):
            try:
                with self._sem:
                    with self._rate_lock:
                        wait = self._next_start - time.time()
                        if wait > 0:
                            time.sleep(wait)
                        self._next_start = time.time() + self.min_interval
                    r = self.opener.open(urllib.request.Request(url), timeout=60)
                    data = r.read()
            except Exception:
                data = b""
            if len(data) >= 32:
                break
            time.sleep(1.0 * (attempt + 1))
        if len(data) >= 32 and not looks_like_html_error(data):
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".part")
            tmp.write_bytes(data)
            os.replace(tmp, target)
            with self._lock:
                self.downloaded += 1
            return {"ok": True, "relpath": rel, "name": orig_name or fname,
                    "size": len(data), "url": url, "remote_only": False}
        with self._lock:
            self.failed += 1
        return {"ok": False, "relpath": None, "name": orig_name or fname,
                "size": size_hint, "url": url, "remote_only": False,
                "error": "html-error-page" if (len(data) >= 32 and looks_like_html_error(data)) else "empty"}


# ---------------- 表单渲染 ----------------

def fmt_value(cur, field: dict | None, col_type: str, raw, info: dict | None):
    """字段值 → 展示文本（多值合并为 '、'）；返回 None 表示不展示。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "":
        return None
    if field and (field["fieldname"] in TECH_FIELDS or field["label"] in TECH_LABELS):
        return None
    htmltype = (field or {}).get("fieldhtmltype")
    ftype = (field or {}).get("type")

    if htmltype == "5":        # 下拉/单选/复选
        opts = (info or {}).get("options", {}).get(field["id"]) if field else None
        toks = [t for t in re.split(r"[,;\s]+", s) if t]
        if opts and toks:
            resolved = [opts.get(t) for t in toks]
            if all(resolved):
                return "、".join(resolved)
        return s

    if htmltype == "3":        # 浏览框
        toks = [t for t in re.split(r"[,;\s]+", s) if t]
        if not toks:
            return None
        if ftype == 2:          # 日期
            return s
        # 1) 通用浏览框映射（workflow_browserurl.id == type）
        meta = META.sys_browser(cur, ftype) if ftype is not None else None
        if meta:
            names = [META.browser_val(cur, meta, t) or t for t in toks]
            return "、".join(names)
        # 2) 建模自定义浏览框（browser.<name> → MODE_BROWSER.sqltext1）
        if field and str(field["fielddbtype"]).startswith("browser."):
            mb = META.mode_browser(cur, str(field["fielddbtype"]).split(".", 1)[1])
            if mb:
                names = [META.mode_val(cur, mb, t) or t for t in toks]
                return "、".join(names)
        # 3) 兜底：老映射
        kind = {1: "u", 4: "d", 12: "s", 16: "r", 17: "doc"}.get(ftype)
        if kind:
            names = [META.resolve_ref(cur, kind, t) or t for t in toks]
            return "、".join(names)
        return s

    if NUM_RE.match(s):
        return s
    txt = clean_html(s)
    return txt.replace("\n", "<br>") if txt else None


def render_case(cur, mark: str, req: dict, out_dir: Path, fetcher: AttachFetcher):
    """生成 md 补丁块；返回 (block, stats)。"""
    stats = {"fields": 0, "attach_total": 0, "attach_ok": 0, "attach_fail": 0, "detail_rows": 0}
    fm = rows(cur, "SELECT billformid, billid FROM workflow_form WHERE requestid=%d" % req["requestid"])
    lines: list[str] = [MARK_BEGIN, "", "## 审批表单", ""]
    attach_lines: list[str] = []
    mark_dir = fs_name(mark)

    if not fm:
        lines.append("（该申请单无表单数据记录）")
        lines += ["", MARK_END]
        return "\n".join(lines), stats

    bill_abs = abs(fm[0]["billformid"] or 0)
    billid = fm[0]["billid"]
    if not bill_abs or billid is None:
        lines.append("（该申请单未挂载建模表单）")
        lines += ["", MARK_END]
        return "\n".join(lines), stats

    info = META.bill(cur, bill_abs)
    if not info:
        lines.append("（未找到 billid=%d 的表单定义）" % bill_abs)
        lines += ["", MARK_END]
        return "\n".join(lines), stats

    # ---- 主表 ----
    cols = META.table_columns(cur, info["table"])
    main = rows(cur, "SELECT * FROM %s WHERE id=%d" % (info["table"], billid))
    if not main:
        lines.append("（主表 %s 中无 id=%d 的数据行）" % (info["table"], billid))
    else:
        row = main[0]
        colset = {c.lower(): c for c in cols}
        rendered = []
        for col, val in row.items():
            if col.lower() in ("id", "requestid"):
                continue
            col_type = "varchar"
            f = META.pick_field(info, col, col_type, val)
            if f is None and (col in TECH_FIELDS or col.lower() in TECH_FIELDS):
                continue
            if f and (f["fieldname"] in TECH_FIELDS or f["label"] in TECH_LABELS):
                continue
            if f and (f["fieldhtmltype"] == "6"):     # 附件字段，稍后单独渲染
                continue
            txt = fmt_value(cur, f, col_type, val, info)
            if txt is None:
                continue
            label = f["label"] if f else col
            rendered.append((f["id"] if f else 999999, label, txt))
        rendered.sort()
        for _, label, txt in rendered:
            lines.append("- **%s**：%s" % (label, txt))
            stats["fields"] += 1

    # ---- 明细表 ----
    for d in info["details"]:
        dcols = META.table_columns(cur, d["table"])
        if "mainid" not in [c.lower() for c in dcols]:
            continue
        drows = rows(cur, "SELECT * FROM %s WHERE mainid=%d ORDER BY id" % (d["table"], billid))
        if not drows:
            continue
        title = d["title"]
        tlabel = None
        if title not in (None, "") and str(title).strip().lstrip("-").isdigit():
            r = rows(cur, "SELECT labelname FROM HtmlLabelInfo WHERE indexid=%d AND languageid=7" % int(title))
            if r:
                tlabel = r[0]["labelname"]
        tlabel = tlabel or str(title or "").strip() or d["table"]
        lines += ["", "### 明细：%s" % tlabel, ""]
        use_cols = []
        for c in dcols:
            if c.lower() in ("id", "mainid"):
                continue
            f = META.pick_field(info, c, "varchar", None)
            if f and (f["fieldname"] in TECH_FIELDS or f["label"] in TECH_LABELS):
                continue
            if f and f["fieldhtmltype"] == "6":
                continue
            use_cols.append((c, (f["label"] if f else c), f))
        if not use_cols:
            continue
        lines.append("| " + " | ".join(lb for _, lb, _ in use_cols) + " |")
        lines.append("|" + "---|" * len(use_cols))
        for dr in drows:
            cells = []
            for c, _, f in use_cols:
                txt = fmt_value(cur, f, "varchar", dr.get(c), info)
                cells.append((txt or "").replace("|", "\\|"))
            lines.append("| " + " | ".join(cells) + " |")
            stats["detail_rows"] += 1
        lines.append("")

    # ---- 附件（主表 + 明细的 html=6 字段）----
    def collect(row: dict, tbl_cols: list[str]):
        for col, val in row.items():
            if val in (None, ""):
                continue
            f = META.pick_field(info, col, "text", val)
            if not f or f["fieldhtmltype"] != "6":
                continue
            s = str(val).strip()
            # 附件字段的值应为逗号分隔的 imagefileid（如 "123,456"）。
            # 非此形态的值（夹杂文件名/文本）整值跳过：否则 re.findall 会把
            # 文本里的数字误当 fileid，产生错误关联（历史 bug 来源之一）。
            if not re.match(r"^\d+(\s*,\s*\d+)*$", s):
                stats["attach_value_skipped"] = stats.get("attach_value_skipped", 0) + 1
                continue
            for fid in re.findall(r"\d+", s):
                yield f["label"], int(fid)

    att_items = []
    if main:
        for label, fid in collect(main[0], cols):
            att_items.append((label, fid))
    for d in info["details"]:
        dcols = META.table_columns(cur, d["table"])
        if "mainid" not in [c.lower() for c in dcols]:
            continue
        for dr in rows(cur, "SELECT * FROM %s WHERE mainid=%d ORDER BY id" % (d["table"], billid)):
            for label, fid in collect(dr, dcols):
                att_items.append((label, fid))

    seen = set()
    for label, fid in att_items:
        if fid in seen:
            continue
        seen.add(fid)
        stats["attach_total"] += 1
        meta = rows(cur, "SELECT imagefilename, imagefiletype, fileSize FROM imagefile WHERE imagefileid=%d" % fid)
        orig_name = meta[0]["imagefilename"] if meta else None
        size_hint = meta[0]["fileSize"] if meta else None
        res = fetcher.fetch(mark_dir, fid, orig_name, size_hint)
        if res.get("remote_only"):
            attach_lines.append("- **%s**：[%s](%s)（%s，仅远端链接）" % (label, res["name"], res["url"], size_hint or "大小未知"))
            stats["attach_ok"] += 1
        elif res["ok"]:
            sz = res["size"]
            szs = ("%s B" % sz) if isinstance(sz, int) else (str(sz) if sz else "大小未知")
            attach_lines.append("- **%s**：[%s](%s)（%s）" % (label, res["name"], res["relpath"], szs))
            stats["attach_ok"] += 1
        else:
            attach_lines.append("- **%s**：[%s](%s)（**下载失败**，%s）" % (label, res["name"], res["url"], size_hint or "大小未知"))
            stats["attach_fail"] += 1

    if attach_lines:
        lines += ["## 附件", ""] + attach_lines
    else:
        lines += ["## 附件", "", "（本单无附件字段数据）"]

    lines += ["", MARK_END]
    return "\n".join(lines), stats


def patch_md(md_path: Path, block: str) -> str:
    """幂等回填：存在标记则整体替换，否则插入到首个 '## ' 之前；原子写。"""
    text = md_path.read_text(encoding="utf-8")
    if MARK_BEGIN in text and MARK_END in text:
        pre = text.split(MARK_BEGIN, 1)[0]
        post = text.split(MARK_END, 1)[1]
        new = pre + block + post
        action = "replaced"
    else:
        idx = text.find("\n## ")
        if idx < 0:
            new = text.rstrip() + "\n\n" + block + "\n"
        else:
            new = text[:idx + 1] + block + "\n\n" + text[idx + 1:]
        action = "inserted"
    if new != text:
        tmp = md_path.with_suffix(md_path.suffix + ".tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, md_path)
    return action


class StateDB:
    """SQLite WAL 状态账本：跨进程幂等 + 断点续传。

    - register()     登记全部案号为 pending（INSERT OR IGNORE，可重复调用）
    - seed_done()    已含回填标记块的 md 预置为 done，保留历史成果不重跑
    - claim()        原子抢占：pending/failed/stalled/超时 running → running，
                     抢到才处理，保证同一案号同一时刻只被一个 worker 处理
    - finish()       落终态（done / nomark / failed）
    - reset_stale()  把超时 running 还原为 stalled，供守护进程续跑中断任务
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._local = threading.local()
        c = self._conn()
        c.execute(
            "CREATE TABLE IF NOT EXISTS cases("
            "stem TEXT PRIMARY KEY,"
            "status TEXT NOT NULL DEFAULT 'pending',"
            "attempts INTEGER NOT NULL DEFAULT 0,"
            "detail TEXT NOT NULL DEFAULT '',"
            "updated_at REAL NOT NULL DEFAULT 0)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status)")
        c.commit()

    def _conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=60)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA busy_timeout=60000")
            self._local.conn = c
        return c

    def register(self, stems):
        c = self._conn()
        now = time.time()
        c.executemany(
            "INSERT OR IGNORE INTO cases(stem, status, updated_at) VALUES(?, 'pending', ?)",
            [(s, now) for s in stems])
        c.commit()

    def seed_done(self, md_files, marker):
        c = self._conn()
        now = time.time()
        rows_ = []
        mb = marker.encode("utf-8")

        def has_marker(p: Path) -> bool:
            try:
                with open(p, "rb") as fh:
                    return mb in fh.read(8192)
            except Exception:
                return False

        with ThreadPoolExecutor(max_workers=16) as ex:
            for p, ok in zip(md_files, ex.map(has_marker, md_files)):
                if ok:
                    rows_.append((now, p.stem))
        c.executemany(
            "UPDATE cases SET status='done', detail='seeded:marker', updated_at=? "
            "WHERE stem=? AND status='pending' AND attempts=0", rows_)
        c.commit()
        return len(rows_)

    def claim(self, stem, stale_seconds, max_attempts):
        c = self._conn()
        now = time.time()
        cur = c.execute(
            "UPDATE cases SET status='running', attempts=attempts+1, updated_at=? "
            "WHERE stem=? AND attempts<? AND (status IN ('pending','failed','stalled') "
            "OR (status='running' AND updated_at<?))",
            (now, stem, max_attempts, now - stale_seconds))
        c.commit()
        return cur.rowcount == 1

    def finish(self, stem, status, detail=""):
        c = self._conn()
        c.execute("UPDATE cases SET status=?, detail=?, updated_at=? WHERE stem=?",
                  (status, str(detail)[:300], time.time(), stem))
        c.commit()

    def reset_stale(self, stale_seconds):
        c = self._conn()
        now = time.time()
        cur = c.execute(
            "UPDATE cases SET status='stalled', updated_at=? "
            "WHERE status='running' AND updated_at<?",
            (now, now - stale_seconds))
        c.commit()
        return cur.rowcount

    def reset_running(self, stems=None):
        """把 running 还原为 stalled：守护进程启动/重启 worker 时这些任务已无归属。"""
        c = self._conn()
        now = time.time()
        before = c.total_changes
        if stems is None:
            c.execute("UPDATE cases SET status='stalled', updated_at=? WHERE status='running'",
                      (now,))
        else:
            c.executemany(
                "UPDATE cases SET status='stalled', updated_at=? WHERE stem=? AND status='running'",
                [(now, s) for s in stems])
        c.commit()
        return c.total_changes - before

    def counts(self, max_attempts=3):
        c = self._conn()
        by_status = dict(c.execute("SELECT status, COUNT(*) FROM cases GROUP BY status").fetchall())
        row = c.execute(
            "SELECT COUNT(*) FROM cases WHERE status IN ('pending','stalled','running') "
            "OR (status='failed' AND attempts<?)", (max_attempts,)).fetchone()
        return by_status, int(row[0])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("DB_HOST", ""))
    ap.add_argument("--port", type=int, default=int(os.environ.get("DB_PORT", "1433")))
    ap.add_argument("--user", default=os.environ.get("DB_USER", ""))
    ap.add_argument("--password", default=os.environ.get("DB_PWD_READONLY", ""))
    ap.add_argument("--database", default="ecologyE9")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--oa-base", default="http://hc.hcipi.com:8088")
    ap.add_argument("--cookie-file", default=str(PROJECT_ROOT / "data" / ".e9_cookie"))
    ap.add_argument("--requestmark", nargs="*", default=None, help="只处理指定案号")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip-existing", action="store_true", help="跳过已有回填标记的 md（断点续跑）")
    ap.add_argument("--no-download", action="store_true", help="附件只出远端链接，不下载")
    ap.add_argument("--shard-index", type=int, default=0,
                    help="分片序号（从 0 开始）；多进程并发时各进程处理互不重叠的子集")
    ap.add_argument("--shard-count", type=int, default=1, help="分片总数")
    ap.add_argument("--state-db", default=None,
                    help="SQLite WAL 状态账本路径（默认 <out>/_form_attach_state.sqlite3）")
    ap.add_argument("--no-state-db", action="store_true", help="禁用状态账本（不保证跨进程幂等）")
    ap.add_argument("--max-attempts", type=int, default=3, help="单案号最大处理尝试次数")
    ap.add_argument("--stale-seconds", type=int, default=1800,
                    help="running 超过该秒数视为中断，允许被重新抢占")
    ap.add_argument("--no-seed", action="store_true", help="不把已有回填标记块的 md 预置为 done")
    args = ap.parse_args(argv)

    if not (args.host and args.user and args.password):
        print("缺少数据库信息（DB_HOST/DB_USER/DB_PWD_READONLY）", file=sys.stderr)
        return 2
    out_dir = Path(args.out)
    if not out_dir.exists():
        print("输出目录不存在：%s" % out_dir, file=sys.stderr)
        return 2

    cookie = ""
    if not args.no_download:
        ck = Path(args.cookie_file)
        if not ck.exists():
            print("下载附件需要登录 Cookie：%s 不存在（或用 --no-download 只出链接）" % ck, file=sys.stderr)
            return 2
        cookie = ck.read_text(encoding="utf-8").strip()
    fetcher = AttachFetcher(out_dir, args.oa_base, cookie,
                            max_concurrent=2, min_interval=0.4,
                            do_download=not args.no_download)

    # 目标 md：磁盘上已有的案号文档
    md_files = [p for p in out_dir.rglob("*.md") if not p.name.startswith("_")]
    if args.requestmark:
        want = set(args.requestmark)
        md_files = [p for p in md_files if p.stem in want]
    md_files.sort()
    if args.shard_count > 1:
        if not (0 <= args.shard_index < args.shard_count):
            print("shard-index 必须在 [0, shard-count) 内", file=sys.stderr)
            return 2
        md_files = md_files[args.shard_index::args.shard_count]
    if args.limit:
        md_files = md_files[:args.limit]
    print("待回填 md：%d 个" % len(md_files), flush=True)

    state = None
    if not args.no_state_db:
        state = StateDB(Path(args.state_db) if args.state_db else out_dir / "_form_attach_state.sqlite3")
        state.reset_stale(args.stale_seconds)
        state.register([p.stem for p in md_files])
        if not args.no_seed:
            n_seeded = state.seed_done(md_files, MARK_BEGIN)
            if n_seeded:
                print("账本预置 done（已有回填标记块）：%d" % n_seeded, flush=True)

    pool = ConnPool(args, max(2, args.workers))
    t0 = time.time()
    stats_lock = threading.Lock()
    totals = {"done": 0, "replaced": 0, "inserted": 0, "skipped": 0, "no_mark": 0,
              "fields": 0, "attach_total": 0, "attach_ok": 0, "attach_fail": 0, "detail_rows": 0}
    shard_tag = ("_s%d" % args.shard_index) if args.shard_count > 1 else ""
    err_path = out_dir / ("_form_attach_errors%s.csv" % shard_tag)
    rep_path = out_dir / ("_form_attach_report%s.csv" % shard_tag)
    err_rows: list[list] = []
    rep_rows: list[list] = []

    def work(md_path: Path):
        stem = md_path.stem
        try:
            if args.skip_existing:
                with open(md_path, "rb") as fh:
                    head = fh.read(8192).decode("utf-8", "replace")
                if MARK_BEGIN in head:
                    with stats_lock:
                        totals["skipped"] += 1
                    if state is not None:
                        state.finish(stem, "done", "skip-existing")
                    return
            if state is not None and not state.claim(stem, args.stale_seconds, args.max_attempts):
                with stats_lock:
                    totals["skipped"] += 1
                return
            with pool.acquire() as conn:
                cur = conn.cursor()
                r = rows(cur, "SELECT requestid, requestmark, workflowid FROM workflow_requestbase WHERE requestmark=%s" % esc(stem))
                if not r:
                    r = rows(cur, "SELECT requestid, requestmark, workflowid FROM workflow_requestbase WHERE requestmark LIKE %s" % esc(stem + "%"))
                if not r:
                    with stats_lock:
                        totals["no_mark"] += 1
                    if state is not None:
                        state.finish(stem, "nomark", "no requestmark")
                        err_rows.append([stem, "lookup", "数据库无对应 requestmark"])
                    return
                req = r[0]
                mark = req["requestmark"] or stem
                block, st = render_case(cur, mark, req, out_dir, fetcher)
                action = patch_md(md_path, block)
                if state is not None:
                    state.finish(stem, "done", action)
                with stats_lock:
                    totals["done"] += 1
                    totals[action] = totals.get(action, 0) + 1
                    for k in ("fields", "attach_total", "attach_ok", "attach_fail", "detail_rows"):
                        totals[k] += st[k]
                    rep_rows.append([mark, req["requestid"], st["fields"], st["detail_rows"],
                                     st["attach_total"], st["attach_ok"], st["attach_fail"], action])
        except Exception as e:
            with stats_lock:
                err_rows.append([stem, "work", repr(e)[:300]])
            if state is not None:
                try:
                    state.finish(stem, "failed", repr(e)[:300])
                except Exception:
                    pass
            try:
                conn.rollback()  # noqa
            except Exception:
                pass

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(work, p) for p in md_files]
        for i, f in enumerate(futs, 1):
            f.result()
            if i % 200 == 0 or i == len(futs):
                print("进度 %d/%d  附件: 下载%d 失败%d 跳过%d"
                      % (i, len(futs), fetcher.downloaded, fetcher.failed, fetcher.skipped), flush=True)

    pool.close()

    with open(err_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["案号或文件", "阶段", "详情"])
        w.writerows(err_rows)
    with open(rep_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["案号", "requestid", "字段数", "明细行数", "附件数", "附件OK", "附件失败", "写入方式"])
        w.writerows(rep_rows)

    print("\n===== 汇总 =====", flush=True)
    print("处理 md：%d（插入 %d / 替换 %d / 跳过 %d / 无案号 %d）"
          % (totals["done"], totals["inserted"], totals["replaced"], totals["skipped"], totals["no_mark"]))
    print("表单字段：%d   明细行：%d" % (totals["fields"], totals["detail_rows"]))
    print("附件：%d（下载 %d / 已存在 %d / 失败 %d）"
          % (totals["attach_total"], fetcher.downloaded, fetcher.skipped, fetcher.failed))
    print("错误 %d 条 → %s" % (len(err_rows), err_path))
    print("明细报告 → %s" % rep_path)
    print("耗时 %.1f s" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
