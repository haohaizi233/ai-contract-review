# -*- coding: utf-8 -*-
"""tender_case_export.py — 新库（tender_*）“每案一份 Markdown”三件套导出。

依据 docs/新库三件套关联与Markdown导出方案.md：
  案件信息（申请 / 档案 / 业务明细） + 附件（按来源字段分角色） + 审批流程与意见（含图片），
  旧 OA 迁移合同额外回链旧 E9 requestid、旧审批日志和旧导出产物。

关联原则（与方案一致）
  - 新库内只用显式主键 / process_instance_id；BUSINESS_KEY_ 只做补查且须流程定义属 contract_*。
  - 迁移合同：oa_source(1→-130 / 2→-474) + oa_id → ecologyE9.workflow_form.billid → requestid，
    并与 contract_apply.process_instance_id 比对，一致/不一致/未找到都如实记录。
  - 附件按 ID 命名空间区分：新系统原生 → tender_infra.infra_file；
    迁移记录的小号 ID → 旧 E9 imagefile（新库同号记录不采信，仅提示）。
  - 每个关联写 _relations.jsonl；每个缺口写 _errors.csv；“没关联上”不当作“没数据”。

输出（data/tender-case-export/）
  cases/<编号前缀>/<合同编号>__apply_<申请ID>.md
  images/<流程实例ID>/<意见ID>_<序号>.<后缀>      （清单外 / data: 图片）
  _index.csv  _relations.jsonl  _errors.csv  _coverage.json  _progress.json
  附件直接相对链接 data/tender_files/，不复制。

断点/幂等：每次全量渲染（内存中很快），内容与磁盘一致则不重写；_state.json 记录
  申请ID → (update_time, 导出版本, 内容哈希)。不修改 tender_fullsync 的下载队列与 WAL。

用法
  python tools/tender_case_export.py                 # 全量（默认带进度页 8771）
  python tools/tender_case_export.py --limit 50      # 小批样本
  python tools/tender_case_export.py --apply-id 2094961761093029890 --apply-id 29
  python tools/tender_case_export.py --no-fetch      # 不补抓附件/图片
  python tools/tender_case_export.py --refresh-oa    # 重新拉取旧 OA 缓存
"""

import argparse
import base64
import csv
import hashlib
import html as htmllib
import json
import os
import pickle
import re
import sys
import threading
import time
import traceback
import urllib.parse
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from decimal import Decimal

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(TOOLS)))
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from tender_fullsync import load_env, safe_name, download as fs_download  # noqa: E402
from tender_case_quality import (  # noqa: E402
    group_process_links,
    image_coverage,
    file_namespace,
    merge_oa_logs,
    valid_image,
    valid_file,
    OldResources,
    image_sources,
)

EXPORT_VERSION = "case-md-v2"
OUT = os.path.join(ROOT, "data", "tender-case-export")
CASES_DIR = os.path.join(OUT, "cases")
IMG_DIR = os.path.join(OUT, "images")
CACHE_DIR = os.path.join(OUT, "_cache")
PROGRESS_PATH = os.path.join(OUT, "_progress.json")
STATE_PATH = os.path.join(OUT, "_state.json")
FILES_DIR = os.path.join(ROOT, "data", "tender_files")
SYNC_DIR = os.path.join(ROOT, "data", "tender_sync")
MANIFEST = os.path.join(SYNC_DIR, "manifest.csv")
FS_WAL = os.path.join(SYNC_DIR, "wal.jsonl")
OLD_EXPORT = os.path.join(ROOT, "data", "e9-opinion-export")
OLD_INDEX = os.path.join(OLD_EXPORT, "_index.csv")
OLD_ATTACH = os.path.join(OLD_EXPORT, "attachments")
OA_BASE = os.environ.get("OA_BASE_URL", "")

SC = "tender_contract"
E9_SMALL_ID = 10**12  # 迁移记录中小于此值的文件 ID 视为旧 E9 imagefileid 候选
BILLFORM = {1: -130, 2: -474}
OA_SOURCE_NAME = {1: "130 业务合同", 2: "474 总委合同"}

# 业务表中已确认的文件字段（方案 §5.1）；角色名用于附件分组
FILE_FIELDS = {
    "contract_apply": [
        ("require_fill_file_id", "需盖章填充文件"),
        ("require_file_ids", "需盖章文件"),
        ("no_require_file_ids", "不需盖章文件"),
        ("change_agreement_file_ids", "变更协议"),
    ],
    "contract_archive": [
        ("require_fill_file_id", "需盖章填充文件"),
        ("require_file_ids", "需盖章文件"),
        ("no_require_file_ids", "不需盖章文件"),
        ("change_agreement_file_ids", "变更协议"),
    ],
    "contract_apply_archive": [
        ("seal_documents", "归档·盖章文件"),
        ("mail_scanned_documents", "归档·邮寄扫描件"),
        ("evidence_documents", "归档·佐证文件"),
    ],
    "contract_archive_arc": [
        ("seal_documents", "档案归档·盖章文件"),
        ("mail_scanned_documents", "档案归档·邮寄扫描件"),
        ("evidence_documents", "档案归档·佐证文件"),
    ],
    "contract_apply_termination": [
        ("termination_file_ids", "终止协议"),
        ("corroboration_file_ids", "终止佐证"),
    ],
    "contract_archive_termination": [
        ("termination_file_ids", "档案终止协议"),
        ("corroboration_file_ids", "档案终止佐证"),
    ],
    "contract_archive_deferred": [("contract_extended_files_ids", "顺延附件")],
    "contract_apply_process": [("contract_files", "流程合同文件")],
}

# (表, 外键列, 层级, 父表) —— 父表非空表示经父表 id 串联（item 子表）
CHILD_SPECS = [
    ("contract_customer", "contract_apply_id", "apply", None),
    ("contract_apply_cost", "contract_apply_id", "apply", None),
    ("contract_apply_cost_item", "apply_cost_id", "apply", "contract_apply_cost"),
    ("contract_apply_customer_cost_item", "contract_apply_id", "apply", None),
    ("contract_apply_matter", "contract_apply_id", "apply", None),
    ("contract_apply_matter_item", "apply_matter_id", "apply", "contract_apply_matter"),
    ("contract_apply_performance", "contract_apply_id", "apply", None),
    (
        "contract_apply_performance_item",
        "apply_performance_id",
        "apply",
        "contract_apply_performance",
    ),
    ("contract_apply_pre_invoice", "contract_apply_id", "apply", None),
    ("contract_apply_ecology", "contract_apply_id", "apply", None),
    ("contract_apply_ecology_item", "ecology_id", "apply", "contract_apply_ecology"),
    ("contract_apply_change_legal", "contract_apply_id", "apply", None),
    ("contract_apply_archive", "contract_apply_id", "apply", None),
    ("contract_apply_termination", "contract_apply_id", "apply", None),
    ("contract_apply_process", "contract_apply_id", "apply", None),
    ("contract_virtual", "contract_apply_id", "apply", None),
    ("contract_archive_arc", "contract_apply_id", "archive", None),  # 注释：合同档案ID
    ("contract_archive_termination", "contract_archive_id", "archive", None),
    ("contract_archive_deferred", "contract_archive_id", "archive", None),
    ("contract_archive_change_legal", "contract_archive_id", "archive", None),
]
FLOW_TABLES = {
    "contract_virtual": "虚拟合同流程",
    "contract_archive_deferred": "顺延流程",
}
META_COLS = {"deleted", "tenant_id", "creator", "updater", "update_time"}
USER_COLS = {
    "creator",
    "updater",
    "applicant_id",
    "follower_id",
    "performance_owner_id",
    "referral_user_id",
}
DEPT_COLS = {"applicant_dept_id", "follower_dept_id", "referral_dept_id"}

# 芋道 BPM 意见类型（BpmCommentTypeEnum 推测，待与新系统代码确认）
BPM_TYPE_GUESS = {
    "1": "通过",
    "2": "不通过",
    "3": "已取消",
    "4": "退回",
    "5": "委派",
    "6": "委派完成",
    "7": "转派",
    "8": "加签",
    "9": "减签",
    "10": "抄送",
    "11": "撤回",
    "12": "转办",
}
# E9 workflow_requestLog.logtype（泛微标准值）
E9_LOGTYPE = {
    "0": "批准",
    "1": "保存",
    "2": "提交",
    "3": "退回",
    "4": "重新打开",
    "5": "删除",
    "6": "激活",
    "7": "转发",
    "9": "批注",
    "a": "意见征询",
    "b": "征询回复",
    "c": "收回",
    "e": "强制归档",
    "h": "转办",
    "i": "干预",
    "j": "转办反馈",
    "r": "撤回",
    "s": "督办",
    "t": "抄送",
    "x": "传阅",
    "y": "传阅回复",
    "z": "超时",
}

IMG_RE = re.compile(r"""<img\b[^>]*?\bsrc\s*=\s*(["'])(.*?)\1""", re.I | re.S)
MDIMG_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
MAGIC = [
    (b"\x89PNG", "png"),
    (b"\xff\xd8", "jpg"),
    (b"GIF8", "gif"),
    (b"BM", "bmp"),
    (b"RIFF", "webp"),
]
ENUM_RE = re.compile(
    r"(\d+)\s*[-:：=]\s*(.+?)(?=\s*[,，;；、]?\s*\d+\s*[-:：=]|[\s,，;；、)）]|$)"
)


# ======================================================================= 通用工具


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sval(v):
    """数据库值 → 展示字符串（ID 一律按字符串处理）。"""
    if v is None:
        return ""
    if isinstance(v, (bytes, bytearray)):
        if len(v) == 1:  # bit(1)
            return str(v[0])
        return v.decode("utf-8", "replace")
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, Decimal):
        s = format(v, "f")
        return s.rstrip("0").rstrip(".") if "." in s else s
    return str(v)


def cell(s):
    return sval(s).replace("|", "／").replace("\r", " ").replace("\n", " ").strip()


def split_ids(raw):
    out = []
    for x in re.split(r"[,，;\s]+", sval(raw)):
        x = x.strip()
        if x and x != "0":
            out.append(x)
    return out


def link(path_from_md, label):
    label = (label or "文件").replace("[", "［").replace("]", "］")
    return "[%s](<%s>)" % (label, path_from_md.replace("\\", "/"))


def atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def html_to_md(text):
    from e9_opinion_export import html_to_markdown

    if re.search(
        r"<(p|div|br|table|img|b|i|u|strong|em|span|a|tr|td|li|ul|ol|h[1-6]|font)\b",
        text,
        re.I,
    ):
        return html_to_markdown(text)
    return text.strip()


# ======================================================================= 进度


class Progress:
    STEPS = [
        "MySQL 索引加载",
        "旧 OA 回链",
        "附件/图片补抓",
        "逐案生成 Markdown",
        "索引与缺口报告",
    ]

    def __init__(self):
        self.lock = threading.Lock()
        self.t0 = time.time()
        self.started = now_iso()
        self.state = "running"
        self.phase = "init"
        self.steps = [
            {"name": n, "status": "pending", "detail": ""} for n in self.STEPS
        ]
        self.cases = {"total": 0, "done": 0, "written": 0, "unchanged": 0, "fail": 0}
        self.fetch = {"total": 0, "done": 0, "ok": 0, "skip": 0, "fail": 0, "bytes": 0}
        self.cover = {}
        self.recent = deque(maxlen=40)
        self.errors = deque(maxlen=50)
        self.samples = deque(maxlen=90)

    def step(self, idx, status, detail=None):
        with self.lock:
            self.steps[idx]["status"] = status
            if detail is not None:
                self.steps[idx]["detail"] = detail
            if status == "running":
                self.phase = self.steps[idx]["name"]

    def detail(self, idx, detail):
        with self.lock:
            self.steps[idx]["detail"] = detail

    def event(self, unit, key, status):
        with self.lock:
            self.recent.append(
                {"ts": now_iso(), "unit": unit, "key": str(key), "status": status}
            )

    def error(self, unit, key, err):
        with self.lock:
            self.errors.append(
                {
                    "ts": now_iso(),
                    "unit": unit,
                    "key": str(key),
                    "error": str(err)[:300],
                }
            )

    def snapshot(self):
        with self.lock:
            t = time.time()
            self.samples.append((t, self.cases["done"], self.fetch["done"]))
            sp_c = sp_f = 0.0
            if len(self.samples) >= 2:
                t0, c0, f0 = self.samples[0]
                if t - t0 > 1:
                    sp_c = (self.cases["done"] - c0) / (t - t0)
                    sp_f = (self.fetch["done"] - f0) / (t - t0)
            c, f = dict(self.cases), dict(self.fetch)
            c["speed_per_min"] = round(sp_c * 60, 1)
            c["eta_s"] = (
                round((c["total"] - c["done"]) / sp_c)
                if sp_c > 0 and c["total"]
                else None
            )
            f["speed_per_min"] = round(sp_f * 60, 1)
            f["eta_s"] = (
                round((f["total"] - f["done"]) / sp_f)
                if sp_f > 0 and f["total"]
                else None
            )
            return {
                "pid": os.getpid(),
                "kind": "case-export",
                "version": EXPORT_VERSION,
                "state": self.state,
                "phase": self.phase,
                "started_at": self.started,
                "updated_at": now_iso(),
                "elapsed_s": round(t - self.t0, 1),
                "steps": [dict(s) for s in self.steps],
                "cases": c,
                "fetch": f,
                "cover": dict(self.cover),
                "out_dir": OUT,
                "errors": list(self.errors),
                "recent": list(self.recent),
            }

    def write(self):
        snap = self.snapshot()
        tmp = PROGRESS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)
        os.replace(tmp, PROGRESS_PATH)

    def loop(self, stop):
        while not stop.wait(2.0):
            try:
                self.write()
            except Exception:
                pass


# ======================================================================= MySQL 加载


class MySQL:
    def __init__(self, env):
        import pymysql

        self.pymysql = pymysql
        self.env = env
        self.conn = self._connect()

    def _connect(self):
        e = self.env
        return self.pymysql.connect(
            host=e["MYSQL_HOST"],
            port=int(e["MYSQL_PORT"]),
            user=e["MYSQL_USER"],
            password=e["MYSQL_PWD"],
            charset="utf8mb4",
            read_timeout=600,
            cursorclass=self.pymysql.cursors.DictCursor,
        )

    def q(self, sql, params=None):
        for attempt in range(3):
            try:
                with self.conn.cursor() as cur:
                    cur.execute(sql, params)
                    return list(cur.fetchall())
            except self.pymysql.err.OperationalError:
                if attempt == 2:
                    raise
                time.sleep(3)
                self.conn = self._connect()

    def q_in(self, sql_tpl, ids, batch=500):
        """sql_tpl 含一个 {in} 占位；ids 按字符串分批。"""
        ids = list(ids)
        out = []
        for i in range(0, len(ids), batch):
            chunk = ids[i : i + batch]
            out.extend(
                self.q(sql_tpl.format(**{"in": ",".join(["%s"] * len(chunk))}), chunk)
            )
        return out


class Cols:
    """列注释 → 标签 + 枚举映射。"""

    def __init__(self, db):
        self.meta = {}  # (table, col) -> (label, type, enum)
        self.order = defaultdict(list)
        self.table_comment = {}
        for r in db.q(
            "SELECT TABLE_NAME, TABLE_COMMENT FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s",
            (SC,),
        ):
            self.table_comment[r["TABLE_NAME"]] = r["TABLE_COMMENT"] or r["TABLE_NAME"]
        for r in db.q(
            "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME, ORDINAL_POSITION",
            (SC,),
        ):
            t, c, typ, cm = (
                r["TABLE_NAME"],
                r["COLUMN_NAME"],
                r["DATA_TYPE"],
                (r["COLUMN_COMMENT"] or "").strip(),
            )
            label = re.split(r"[\s:：(（,，;；]", cm, maxsplit=1)[0] if cm else ""
            label = label or c
            enum = {}
            if typ in ("int", "tinyint", "smallint", "bit", "bigint") and cm:
                for k, v in ENUM_RE.findall(cm[len(label) :]):
                    enum.setdefault(k, v.strip())
            self.meta[(t, c)] = (label, typ, enum, cm)
            self.order[t].append(c)

    def has(self, table):
        return table in self.order

    def label(self, t, c):
        m = self.meta.get((t, c))
        return m[0] if m else c

    def show(self, t, c, raw, ctx):
        s = sval(raw)
        if s == "":
            return ""
        m = self.meta.get((t, c))
        if c in USER_COLS:
            n = ctx.users.get(s)
            return "%s（%s）" % (n, s) if n else s
        if c in DEPT_COLS:
            n = ctx.depts.get(s)
            return "%s（%s）" % (n, s) if n else s
        if m and m[2] and s in m[2]:
            return "%s（%s）" % (s, m[2][s])
        return s


class Ctx:
    pass


def load_mysql(db, prog, apply_filter, limit, min_year=2024):
    ctx = Ctx()
    ctx.cols = Cols(db)
    prog.detail(0, "用户 / 部门")
    ctx.users = {
        sval(r["id"]): sval(r["nickname"])
        for r in db.q("SELECT id, nickname FROM tender_system.system_users")
    }
    ctx.depts = {
        sval(r["id"]): sval(r["name"])
        for r in db.q("SELECT id, name FROM tender_system.system_dept")
    }

    prog.detail(0, "合同申请 contract_apply")
    sql = "SELECT * FROM %s.contract_apply WHERE deleted=0" % SC
    if apply_filter:
        sql += " AND id IN (%s)" % ",".join(["%s"] * len(apply_filter))
        applies = db.q(sql + " ORDER BY id", list(apply_filter))
    else:
        applies = db.q(sql + " ORDER BY id")
    if min_year:
        filtered = []
        for a in applies:
            sdate = a.get("signing_date")
            yr = sdate.year if sdate else None
            if not yr:
                m = re.search(r"20(\d{2})", a.get("contract_no") or "")
                if m:
                    yr = int("20" + m.group(1))
            if yr is None or yr >= min_year:
                filtered.append(a)
        applies = filtered
    if limit:
        applies = pick_sample(applies, limit)
    ctx.applies = applies
    with prog.lock:
        prog.cases["total"] = len(applies)
    {sval(a["id"]) for a in applies}

    prog.detail(0, "合同档案 contract_archive（含已删除，用于核验）")
    ctx.archives = {
        sval(r["id"]): r for r in db.q("SELECT * FROM %s.contract_archive" % SC)
    }
    ctx.archive_by_apply = defaultdict(list)
    for r in ctx.archives.values():
        if sval(r.get("contract_apply_id")):
            ctx.archive_by_apply[sval(r["contract_apply_id"])].append(r)

    # 子表
    ctx.children = {}  # table -> fk value -> [rows]
    for table, fk, level, parent in CHILD_SPECS:
        if not ctx.cols.has(table) or fk not in ctx.cols.order[table]:
            continue
        prog.detail(0, "子表 %s" % table)
        where = " WHERE deleted=0" if "deleted" in ctx.cols.order[table] else ""
        g = defaultdict(list)
        for r in db.q("SELECT * FROM %s.%s%s ORDER BY id" % (SC, table, where)):
            g[sval(r.get(fk))].append(r)
        ctx.children[table] = g
    ctx.changes = defaultdict(list)
    if ctx.cols.has("contract_apply_change"):
        for r in db.q("SELECT * FROM %s.contract_apply_change WHERE deleted=0" % SC):
            for k in ("before_contract_id", "after_contract_id"):
                if sval(r.get(k)):
                    ctx.changes[sval(r[k])].append(r)

    prog.detail(0, "文件元数据 infra_file / manifest")
    ctx.files = {
        sval(r["id"]): r
        for r in db.q(
            "SELECT id, name, type, size, md5, url, deleted, create_time FROM tender_infra.infra_file"
        )
    }
    ctx.manifest_by_id, ctx.manifest_by_url = {}, {}
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                ctx.manifest_by_id[row["id"]] = row
                ctx.manifest_by_url.setdefault(row["url"], row)
    ctx.fs_status = {}
    if os.path.exists(FS_WAL):
        with open(FS_WAL, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("unit") == "file":
                    ctx.fs_status[str(rec.get("key"))] = rec.get("status", "")

    # 流程
    prog.detail(0, "BPM 流程实例")
    ctx.procs = {}
    for r in db.q(
        "SELECT PROC_INST_ID_, BUSINESS_KEY_, PROC_DEF_ID_, TENANT_ID_, NAME_, START_TIME_, END_TIME_, "
        "START_USER_ID_, DELETE_REASON_, BUSINESS_STATUS_ FROM tender_bpm.act_hi_procinst"
    ):
        ctx.procs[sval(r["PROC_INST_ID_"])] = r
    ctx.procs_by_bkey = defaultdict(list)
    for p in ctx.procs.values():
        if sval(p["BUSINESS_KEY_"]):
            ctx.procs_by_bkey[sval(p["BUSINESS_KEY_"])].append(p)

    # 案件 → 流程链接
    ctx.proc_links = defaultdict(list)  # apply_id -> [(proc_id, source)]
    need_procs = set()
    for a in applies:
        aid = sval(a["id"])
        seen = set()

        def add(pid, src):
            pid = sval(pid)
            if not pid or (pid, src) in seen:
                return
            seen.add((pid, src))
            ctx.proc_links[aid].append((pid, src))
            if pid in ctx.procs:
                need_procs.add(pid)

        add(a.get("process_instance_id"), "申请.process_instance_id")
        for arc in archives_of(ctx, a):
            add(arc.get("process_instance_id"), "档案.process_instance_id")
            for r in ctx.children.get("contract_archive_deferred", {}).get(
                sval(arc["id"]), []
            ):
                add(r.get("process_instance_id"), "顺延.process_instance_id")
        for r in ctx.children.get("contract_virtual", {}).get(aid, []):
            add(r.get("process_instance_id"), "虚拟合同.process_instance_id")
        direct = {p for p, _ in ctx.proc_links[aid] if p in ctx.procs}
        for p in ctx.procs_by_bkey.get(aid, []):
            pid = sval(p["PROC_INST_ID_"])
            if pid in direct:
                continue
            tenant = sval(p.get("TENANT_ID_"))
            if sval(p["PROC_DEF_ID_"]).startswith(
                "contract_application:"
            ) and tenant in ("", sval(a.get("tenant_id"))):
                add(
                    pid,
                    "BUSINESS_KEY_补查（%s）" % sval(p["PROC_DEF_ID_"]).split(":")[0],
                )
    ctx.proc_links = {
        aid: group_process_links(links) for aid, links in ctx.proc_links.items()
    }
    ctx.need_procs = need_procs

    prog.detail(0, "BPM 任务 / 意见（%d 个流程）" % len(need_procs))
    ctx.tasks = defaultdict(list)
    for r in db.q_in(
        "SELECT ID_, PROC_INST_ID_, NAME_, TASK_DEF_KEY_, ASSIGNEE_, OWNER_, START_TIME_, END_TIME_, "
        "DELETE_REASON_, PARENT_TASK_ID_ FROM tender_bpm.act_hi_taskinst WHERE PROC_INST_ID_ IN ({in})",
        need_procs,
    ):
        ctx.tasks[sval(r["PROC_INST_ID_"])].append(r)
    ctx.comments = defaultdict(list)
    for r in db.q_in(
        "SELECT ID_, TYPE_, TIME_, USER_ID_, TASK_ID_, PROC_INST_ID_, ACTION_, MESSAGE_, FULL_MSG_ "
        "FROM tender_bpm.act_hi_comment WHERE PROC_INST_ID_ IN ({in})",
        need_procs,
    ):
        ctx.comments[sval(r["PROC_INST_ID_"])].append(r)
    prog.detail(
        0,
        "申请 %d · 档案 %d · 流程 %d · 意见 %d · 文件元数据 %d"
        % (
            len(applies),
            len(ctx.archives),
            len(need_procs),
            sum(len(v) for v in ctx.comments.values()),
            len(ctx.files),
        ),
    )
    return ctx


def pick_sample(applies, limit):
    """小批样本：覆盖新原生、迁移 130/474、有档案、空编号等形态。"""
    buckets = defaultdict(list)
    for a in applies:
        k = (
            sval(a.get("data_source")),
            sval(a.get("oa_source")),
            bool(sval(a.get("contract_archive_id"))),
            bool(sval(a.get("contract_no"))),
        )
        buckets[k].append(a)
    out = []
    while len(out) < limit and any(buckets.values()):
        for k in list(buckets):
            if buckets[k] and len(out) < limit:
                out.append(buckets[k].pop(len(buckets[k]) // 2))
    return out


def archives_of(ctx, a):
    """两个方向分别取档案，去重保序；冲突由 render 报告。"""
    out, seen = [], set()
    aid = sval(a["id"])
    x = sval(a.get("contract_archive_id"))
    if x and x in ctx.archives:
        out.append(ctx.archives[x])
        seen.add(x)
    for r in ctx.archive_by_apply.get(aid, []):
        if sval(r["id"]) not in seen:
            out.append(r)
            seen.add(sval(r["id"]))
    return out


# ======================================================================= 旧 OA


def _save_oa_safe(cache_dir, oa):
    """使用 JSON 安全落盘 OA 缓存（避免不安全反序列化风险）。"""
    json_path = os.path.join(cache_dir, "oa.json")
    payload = {
        "form": {f"{k[0]}:{k[1]}": list(v) for k, v in oa.get("form", {}).items()},
        "reqs": {str(k): v for k, v in oa.get("reqs", {}).items()},
        "logs": {str(k): v for k, v in oa.get("logs", {}).items()},
        "images": {str(k): v for k, v in oa.get("images", {}).items()},
        "names": {str(k): v for k, v in oa.get("names", {}).items()},
        "nodes": {str(k): v for k, v in oa.get("nodes", {}).items()},
        "wfs": {str(k): v for k, v in oa.get("wfs", {}).items()},
        "covered_reqs": sorted(oa.get("covered_reqs", set())),
        "covered_files": sorted(oa.get("covered_files", set())),
        "covered_approvals": sorted(oa.get("covered_approvals", set())),
    }
    tmp_path = json_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, default=str)
    os.replace(tmp_path, json_path)


def _load_oa_safe(cache_dir, prog):
    """优先加载安全 JSON 缓存；仅当显式设置 ALLOW_UNSAFE_PICKLE_CACHE=1 时才兼容旧 oa.pkl。"""
    json_path = os.path.join(cache_dir, "oa.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, encoding="utf-8") as f:
                raw_json = json.load(f)
            form = {}
            for k, v in raw_json.get("form", {}).items():
                bf_s, bid_s = k.split(":", 1)
                form[(int(bf_s), int(bid_s))] = [int(x) for x in v]
            oa = {
                "form": form,
                "reqs": {int(k): v for k, v in raw_json.get("reqs", {}).items()},
                "logs": {int(k): v for k, v in raw_json.get("logs", {}).items()},
                "images": {int(k): v for k, v in raw_json.get("images", {}).items()},
                "names": {int(k): v for k, v in raw_json.get("names", {}).items()},
                "nodes": {int(k): v for k, v in raw_json.get("nodes", {}).items()},
                "wfs": {int(k): v for k, v in raw_json.get("wfs", {}).items()},
                "covered_reqs": {int(x) for x in raw_json.get("covered_reqs", [])},
                "covered_files": {int(x) for x in raw_json.get("covered_files", [])},
                "covered_approvals": {
                    int(x) for x in raw_json.get("covered_approvals", [])
                },
            }
            prog.detail(1, "使用安全缓存 oa.json（--refresh-oa 可重拉）")
            return oa
        except Exception:
            pass

    legacy_pkl = os.path.join(cache_dir, "oa.pkl")
    if (
        os.path.exists(legacy_pkl)
        and os.environ.get("ALLOW_UNSAFE_PICKLE_CACHE", "0") == "1"
    ):
        try:
            with open(legacy_pkl, "rb") as f:
                oa = pickle.load(f)
            prog.detail(
                1, "使用兼容缓存 oa.pkl（已由 ALLOW_UNSAFE_PICKLE_CACHE=1 显式放行）"
            )
            return oa
        except Exception:
            return None
    return None


def load_oa(env, ctx, prog, refresh):
    """旧 E9 回链：workflow_form 映射、requestbase、requestLog、imagefile、旧导出索引（结果缓存）。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    oa = None if refresh else _load_oa_safe(CACHE_DIR, prog)

    req_ids, e9_files = set(), set()
    for a in ctx.applies:
        if sval(a.get("oa_request_id")).isdigit():
            req_ids.add(int(sval(a["oa_request_id"])))
        if sval(a.get("data_source")) == "2" or sval(a.get("oa_source")):
            pid = sval(a.get("process_instance_id"))
            if pid.isdigit():
                req_ids.add(int(pid))
            for ref in collect_file_refs(ctx, a):
                for fid in ref[5]:
                    if fid.isdigit() and int(fid) <= 9223372036854775807:
                        e9_files.add(int(fid))

    if oa is not None:
        for a in ctx.applies:
            src, oid = sval(a.get("oa_source")), sval(a.get("oa_id"))
            if src.isdigit() and oid.isdigit():
                req_ids.update(oa["form"].get((BILLFORM.get(int(src)), int(oid)), []))
    if (
        oa is None
        or not req_ids <= oa["covered_reqs"]
        or not e9_files <= oa["covered_files"]
    ):
        if oa is None:
            oa = {
                "form": {},
                "reqs": {},
                "logs": {},
                "images": {},
                "names": {},
                "nodes": {},
                "wfs": {},
                "covered_reqs": set(),
                "covered_files": set(),
            }
        import pytds

        conn = pytds.connect(
            dsn=env["DB_HOST"],
            port=int(env["DB_PORT"]),
            user=env["DB_USER"],
            password=env["DB_PWD_READONLY"],
            database="ecologyE9",
            timeout=900,
            login_timeout=60,
        )
        cur = conn.cursor()

        def rows(sql, params=()):
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

        prog.detail(1, "workflow_form 映射（-130 / -474）")
        for r in rows(
            "SELECT requestid, billformid, billid FROM workflow_form WHERE billformid IN (-130,-474)"
        ):
            oa["form"].setdefault((int(r["billformid"]), int(r["billid"])), []).append(
                int(r["requestid"])
            )

        for a in ctx.applies:
            src = sval(a.get("oa_source"))
            oaid = sval(a.get("oa_id"))
            if src.isdigit() and oaid.isdigit():
                bf = BILLFORM.get(int(src))
                if bf:
                    mapped = oa["form"].get((bf, int(oaid)))
                    if mapped:
                        req_ids.update(mapped)

        prog.detail(1, "人员 / 节点 / 流程名")
        oa["names"] = {
            int(r["id"]): r["lastname"]
            for r in rows("SELECT id, lastname FROM HrmResource")
        }
        oa["nodes"] = {
            int(r["id"]): r["nodename"]
            for r in rows("SELECT id, nodename FROM workflow_nodebase")
        }
        oa["wfs"] = {
            int(r["id"]): r["workflowname"]
            for r in rows("SELECT id, workflowname FROM workflow_base")
        }
        todo = sorted(req_ids - oa["covered_reqs"])
        for i in range(0, len(todo), 400):
            chunk = todo[i : i + 400]
            ph = ",".join(str(x) for x in chunk)
            for r in rows(
                "SELECT requestid, requestmark, requestname, status, currentnodetype, createdate, "
                "createtime, creater, workflowid FROM workflow_requestbase WHERE requestid IN (%s)"
                % ph
            ):
                oa["reqs"][int(r["requestid"])] = r
            for r in rows(
                "SELECT requestid, nodeid, logtype, operator, operatedate, operatetime, LOGID, "
                "annexdocids, CAST(remark AS nvarchar(max)) AS remark FROM workflow_requestLog "
                "WHERE requestid IN (%s)" % ph
            ):
                oa["logs"].setdefault(int(r["requestid"]), []).append(r)
            oa["covered_reqs"].update(chunk)
            prog.detail(
                1, "旧请求 / 审批日志 %d/%d" % (min(i + 400, len(todo)), len(todo))
            )
        todo = sorted(e9_files - oa["covered_files"])
        for i in range(0, len(todo), 1000):
            chunk = todo[i : i + 1000]
            for r in rows(
                "SELECT imagefileid, imagefilename, filesize FROM imagefile WHERE imagefileid IN (%s)"
                % ",".join(str(x) for x in chunk)
            ):
                oa["images"][int(r["imagefileid"])] = r
            oa["covered_files"].update(chunk)
            prog.detail(
                1, "旧 imagefile 元数据 %d/%d" % (min(i + 1000, len(todo)), len(todo))
            )
        conn.close()
        _save_oa_safe(CACHE_DIR, oa)

    # 旧缓存独立增量升级，不能因 requestLog 已覆盖而漏读 approvelog。
    oa["form"] = {
        key: list(dict.fromkeys(values)) for key, values in oa["form"].items()
    }
    approval_covered = oa.setdefault("covered_approvals", set())
    for a in ctx.applies:
        src, oid = sval(a.get("oa_source")), sval(a.get("oa_id"))
        if src.isdigit() and oid.isdigit():
            req_ids.update(oa["form"].get((BILLFORM.get(int(src)), int(oid)), []))
    todo_approvals = sorted(req_ids - approval_covered)
    if todo_approvals:
        import pytds

        with pytds.connect(
            dsn=env["DB_HOST"],
            port=int(env["DB_PORT"]),
            user=env["DB_USER"],
            password=env["DB_PWD_READONLY"],
            database="ecologyE9",
            timeout=180,
            login_timeout=30,
        ) as approval_conn:
            approval_cur = approval_conn.cursor()
            for i in range(0, len(todo_approvals), 1000):
                chunk = todo_approvals[i : i + 1000]
                approval_cur.execute(
                    "SELECT requestid,nodeid,operator,logdate,logtime,CAST(remark AS nvarchar(max)) AS remark "
                    "FROM workflow_approvelog WHERE requestid IN ("
                    + ",".join(map(str, chunk))
                    + ")"
                )
                columns = [x[0] for x in approval_cur.description]
                for values in approval_cur.fetchall():
                    r = dict(zip(columns, values))
                    if not sval(r["remark"]).strip():
                        continue
                    r.update(
                        source="workflow_approvelog",
                        operatedate=r.pop("logdate"),
                        operatetime=r.pop("logtime"),
                        logtype="审批意见",
                        annexdocids="",
                    )
                    r["LOGID"] = int(
                        hashlib.sha256(repr(values).encode()).hexdigest()[:16], 16
                    )
                    oa["logs"].setdefault(int(r["requestid"]), []).append(r)
                approval_covered.update(chunk)
                prog.detail(
                    1,
                    "补齐旧审批意见 %d/%d"
                    % (min(i + 1000, len(todo_approvals)), len(todo_approvals)),
                )
        _save_oa_safe(CACHE_DIR, oa)
    oa["logs"] = {rid: merge_oa_logs(logs) for rid, logs in oa["logs"].items()}

    # 旧导出索引与旧附件落盘索引
    oa["old_md"] = {}
    if os.path.exists(OLD_INDEX):
        with open(OLD_INDEX, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("requestid", "").isdigit():
                    oa["old_md"][int(row["requestid"])] = row.get("文件", "")
    oa["old_attach"] = {}
    if os.path.isdir(OLD_ATTACH):
        for d in os.scandir(OLD_ATTACH):
            if d.is_dir():
                for f in os.scandir(d.path):
                    m = re.match(r"(\d+)-", f.name)
                    if m and f.is_file():
                        oa["old_attach"].setdefault(
                            int(m.group(1)), "%s/%s" % (d.name, f.name)
                        )
    prog.detail(
        1,
        "workflow_form %d · 旧请求 %d · 旧日志请求 %d · 旧导出 MD %d · 旧附件落盘 %d"
        % (
            len(oa["form"]),
            len(oa["reqs"]),
            len(oa["logs"]),
            len(oa["old_md"]),
            len(oa["old_attach"]),
        ),
    )
    return oa


# ======================================================================= 附件 / 图片解析


def file_dest(ctx, fid):
    m = ctx.manifest_by_id.get(fid)
    if m:
        return m["dest"]
    f = ctx.files.get(fid)
    if f:
        return "%s_%s" % (fid, safe_name(sval(f["name"])))
    return ""


def is_migrated(a):
    return sval(a.get("data_source")) == "2" or bool(sval(a.get("oa_source")))


def resolve_file(ctx, oa, a, fid):
    """返回文件引用的解析结果 dict（命名空间、元数据、本地路径、状态）。"""
    r = {"file_id": fid}
    fid.isdigit() and int(fid) < E9_SMALL_ID
    old_exists = fid.isdigit() and oa and int(fid) in oa["images"]
    namespace = file_namespace(is_migrated(a), fid in ctx.files, bool(old_exists))
    if namespace in ("ambiguous", "unknown"):
        r.update(
            namespace=namespace,
            status="来源待核验",
            gap="文件 ID 命名空间冲突"
            if namespace == "ambiguous"
            else "新旧文件表均无记录",
        )
        return r
    if namespace == "e9_imagefile":
        r["namespace"] = "e9_imagefile"
        meta = oa["images"].get(int(fid)) if oa else None
        r["name"] = sval(meta["imagefilename"]) if meta else ""
        r["size"] = sval(meta["filesize"]) if meta else ""
        local = oa["old_attach"].get(int(fid)) if oa else None
        if fid in ctx.files:
            r["note"] = "新库 infra_file 存在同号记录（%s），按命名空间不采信" % sval(
                ctx.files[fid]["name"]
            )
        cached, error = (
            ctx.old_resources.lookup(
                "%s/weaver/weaver.file.FileDownload?fileid=%s" % (OA_BASE, fid),
                image=False,
            )
            if hasattr(ctx, "old_resources")
            else (None, "")
        )
        if local and not valid_file(os.path.join(OLD_ATTACH, local)):
            local = None
        if cached and not local:
            r["local"] = cached
            r["status"] = "旧库附件已补抓"
        elif local:
            r["local"] = os.path.join(OLD_ATTACH, local)
            r["status"] = "旧库附件已落盘"
            if (
                r["size"].isdigit()
                and int(r["size"])
                and os.path.getsize(r["local"]) != int(r["size"])
            ):
                r["status"] += "·大小不符"
                r["gap"] = "旧附件实际大小与旧库记录不一致；保留本地文件供核验"
        elif meta:
            r["status"] = "旧库附件未落盘"
            r["gap"] = "旧 E9 附件尚未获取：" + error
            r["url"] = "%s/weaver/weaver.file.FileDownload?fileid=%s" % (OA_BASE, fid)
        else:
            r["status"] = "旧库 imagefile 无记录"
            r["gap"] = (
                "来源不明：新库按迁移规则视为旧 imagefileid，但旧 imagefile 查无此号"
            )
        return r
    r["namespace"] = "tender_infra"
    f = ctx.files.get(fid)
    if not f:
        r["status"] = "新库无此文件记录"
        r["gap"] = "来源不明：infra_file 查无此 ID"
        return r
    r["name"] = sval(f["name"])
    r["size"] = sval(f["size"])
    r["md5"] = sval(f["md5"])
    r["url"] = sval(f["url"])
    deleted = sval(f["deleted"]) == "1"
    dest = file_dest(ctx, fid)
    path = os.path.join(FILES_DIR, dest) if dest else ""
    if path and valid_file(path):
        r["local"] = path
        size = os.path.getsize(path)
        if r["size"] and r["size"].isdigit() and int(r["size"]) != size:
            r["status"] = "已落盘·大小不符（库 %s / 本地 %d）" % (r["size"], size)
            r["gap"] = "附件大小与库记录不一致"
        else:
            r["status"] = "已落盘"
        if deleted:
            r["status"] += "·库中已删除"
    else:
        st = ctx.fetch_result.get(fid) or ctx.fs_status.get(fid, "")
        if deleted:
            r["status"] = "库中已删除(deleted=1)，未落盘"
        elif st.startswith("fail"):
            r["status"] = "下载失败：%s" % st[5:].strip()
        else:
            r["status"] = "尚未获取"
        r["gap"] = r["status"]
    return r


def collect_file_refs(ctx, a):
    """[(角色, 来源表, 来源字段, 来源行ID, 原始值, [file_id])]"""
    refs = []
    aid = sval(a["id"])

    def add_row(table, row, role_override=None):
        for col, role in FILE_FIELDS.get(table, []):
            raw = sval(row.get(col))
            ids = split_ids(raw)
            if ids:
                refs.append(
                    (role_override or role, table, col, sval(row.get("id")), raw, ids)
                )

    add_row("contract_apply", a)
    apply_ids = {i for r in refs for i in r[5]}
    for arc in archives_of(ctx, a):
        for col, role in FILE_FIELDS["contract_archive"]:
            raw = sval(arc.get(col))
            ids = [i for i in split_ids(raw) if i not in apply_ids]
            if ids:
                refs.append(
                    ("档案·" + role, "contract_archive", col, sval(arc["id"]), raw, ids)
                )
        for t in (
            "contract_archive_arc",
            "contract_archive_termination",
            "contract_archive_deferred",
        ):
            for row in ctx.children.get(t, {}).get(sval(arc["id"]), []):
                add_row(t, row)
    for t in (
        "contract_apply_archive",
        "contract_apply_termination",
        "contract_apply_process",
    ):
        for row in ctx.children.get(t, {}).get(aid, []):
            add_row(t, row)
    return refs


def ext_of(data, url=""):
    for mg, e in MAGIC:
        if data.startswith(mg):
            return e
    e = os.path.splitext(urllib.parse.urlparse(url).path)[1].lstrip(".").lower()
    return e if e and len(e) <= 5 else "bin"


def comment_body(c):
    raw = c.get("FULL_MSG_")
    if raw:
        try:
            return raw.decode("utf-8"), "FULL_MSG_"
        except Exception:
            pass
    return sval(c.get("MESSAGE_")), (
        "MESSAGE_（FULL_MSG_ 解码失败）" if raw else "MESSAGE_"
    )


def classify_img(ctx, src):
    """→ (kind, key)：manifest/http/data/oa/local"""
    s = htmllib.unescape(src.strip())
    if s.startswith("data:"):
        return "data", s
    if "FileDownload" in s or s.startswith("/weaver"):
        return "oa", s
    if s.startswith("http://") or s.startswith("https://"):
        m = ctx.manifest_by_url.get(s) or ctx.manifest_by_url.get(
            urllib.parse.unquote(s)
        )
        if m:
            return "manifest", m["id"]
        return "http", s
    return "local", s


def plan_images(ctx):
    """遍历关联意见中的图片，生成补抓任务；结果 ctx.img_map[(comment_id, n)] = {...}"""
    ctx.img_map = {}
    jobs_files, jobs_http = set(), {}
    for pid, cs in ctx.comments.items():
        for c in cs:
            body, _ = comment_body(c)
            srcs = [m.group(2) for m in IMG_RE.finditer(body)] + MDIMG_RE.findall(body)
            for n, src in enumerate(srcs, 1):
                kind, key = classify_img(ctx, src)
                ent = {
                    "src": src[:300] if not src.startswith("data:") else src[:40] + "…",
                    "kind": kind,
                }
                if kind == "manifest":
                    ent["file_id"] = key
                    dest = ctx.manifest_by_id[key]["dest"]
                    ent["path"] = os.path.join(FILES_DIR, dest)
                    if not os.path.exists(ent["path"]):
                        jobs_files.add(key)
                elif kind == "http":
                    ent["path_base"] = os.path.join(
                        IMG_DIR,
                        safe_name(pid),
                        "%s_%d" % (safe_name(sval(c["ID_"])), n),
                    )
                    jobs_http[ent["path_base"]] = htmllib.unescape(src)
                elif kind == "data":
                    try:
                        head, b64 = htmllib.unescape(src).split(",", 1)
                        data = base64.b64decode(b64)
                        p = os.path.join(
                            IMG_DIR,
                            safe_name(pid),
                            "%s_%d.%s" % (safe_name(sval(c["ID_"])), n, ext_of(data)),
                        )
                        if not os.path.exists(p):
                            os.makedirs(os.path.dirname(p), exist_ok=True)
                            with open(p, "wb") as f:
                                f.write(data)
                        ent["path"] = p
                    except Exception as e:
                        ent["gap"] = "data 图片解码失败：%s" % e
                elif kind == "oa":
                    ent["path"], ent["gap"] = ctx.old_resources.lookup(src)
                ctx.img_map[(sval(c["ID_"]), n, src)] = ent
    return jobs_files, jobs_http


def fetch_all(ctx, prog, file_jobs, http_jobs, workers, retry_404):
    """补抓：引用到但未落盘的 tender_infra 文件 + 清单外 http 图片。不写 fullsync WAL。"""
    ctx.fetch_result = {}
    file_items = []
    for fid in sorted(file_jobs):
        f = ctx.files.get(fid)
        if not f or not sval(f["url"]):
            continue
        dest = os.path.join(FILES_DIR, file_dest(ctx, fid))
        if os.path.exists(dest):
            continue
        prev = ctx.fs_status.get(fid, "")
        if not retry_404 and ("404" in prev or "Not Found" in prev):
            ctx.fetch_result[fid] = prev
            continue
        file_items.append(
            (fid, sval(f["url"]), sval(f["md5"]), dest, int(f["size"] or 0))
        )
    http_items = [
        (base, url)
        for base, url in http_jobs.items()
        if not any(
            os.path.exists(base + "." + e)
            for e in ("png", "jpg", "gif", "bmp", "webp", "jpeg", "bin")
        )
    ]
    total = len(file_items) + len(http_items)
    with prog.lock:
        prog.fetch.update(
            {"total": total, "done": 0, "ok": 0, "skip": 0, "fail": 0, "bytes": 0}
        )
    prog.detail(
        2,
        "待补抓 附件/清单内图片 %d · 清单外图片 %d（已知 404 跳过 %d）"
        % (len(file_items), len(http_items), sum(1 for v in ctx.fetch_result.values())),
    )
    if not total:
        return

    def do_file(it):
        fid, url, md5, dest, size = it
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        st, nb = fs_download(url, dest, md5, retries=2)
        ctx.fetch_result[fid] = st if st != "ok" else "ok"
        return ("file", fid, st, nb)

    def do_http(it):
        base, url = it
        import urllib.request

        try:
            q = urllib.parse.quote(url, safe=":/?&=%#")
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(
                urllib.request.Request(q, headers={"User-Agent": "Mozilla/5.0"}),
                timeout=60,
            ) as r:
                data = r.read()
            if len(data) < 16:
                raise ValueError("内容过短")
            p = base + "." + ext_of(data, url)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p + ".part", "wb") as f:
                f.write(data)
            os.replace(p + ".part", p)
            return ("img", url, "ok", len(data))
        except Exception as e:
            ctx.fetch_result["img:" + base] = "fail: %s" % e
            return ("img", url, "fail: %s" % e, 0)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(do_file, it) for it in file_items] + [
            ex.submit(do_http, it) for it in http_items
        ]
        for fu in as_completed(futs):
            try:
                unit, key, st, nb = fu.result()
            except Exception as e:
                unit, key, st, nb = "file", "?", "fail: %s" % e, 0
            with prog.lock:
                prog.fetch["done"] += 1
                prog.fetch["bytes"] += nb
                if st == "ok":
                    prog.fetch["ok"] += 1
                elif st == "skip":
                    prog.fetch["skip"] += 1
                else:
                    prog.fetch["fail"] += 1
            prog.event(
                "fetch",
                key if unit == "file" else key[-60:],
                "ok" if st == "ok" else st[:60],
            )
            if st not in ("ok", "skip"):
                prog.error("fetch", key, st)


# ======================================================================= 渲染


class Case:
    def __init__(self, ctx, oa, a):
        self.ctx, self.oa, self.a = ctx, oa, a
        self.aid = sval(a["id"])
        self.no = sval(a.get("contract_no")).strip()
        self.gaps = []  # (kind, detail)
        self.rel = {"apply_id": self.aid, "contract_no": self.no}
        self.cover = {}

    # -------- 路径
    def md_path(self):
        if self.no:
            m = re.match(r"([A-Za-z]+)", self.no)
            shard = m.group(1).upper() if m else "_其他"
            name = safe_name(self.no, 80) + "__apply_" + self.aid + ".md"
        else:
            shard, name = "_无编号", "apply_" + self.aid + ".md"
        return os.path.join(CASES_DIR, shard, name)

    def rel_from_md(self, abs_path):
        return os.path.relpath(abs_path, os.path.dirname(self.md_path())).replace(
            "\\", "/"
        )

    def gap(self, kind, detail):
        self.gaps.append((kind, detail))

    # -------- 主渲染
    def render(self):
        a, ctx = self.a, self.ctx
        L = []
        cols = ctx.cols
        arcs = archives_of(ctx, a)
        src_kind = (
            "旧 OA 迁移（%s）"
            % OA_SOURCE_NAME.get(int(sval(a["oa_source"])), sval(a["oa_source"]))
            if sval(a.get("oa_source"))
            else ("旧 OA 迁移" if sval(a.get("data_source")) == "2" else "新系统原生")
        )
        self.rel.update(
            {"source": src_kind, "archive_ids": [sval(r["id"]) for r in arcs]}
        )

        body_info = self.sec_info(arcs)
        body_detail = self.sec_detail(arcs)
        body_files = self.sec_files()
        body_oa = self.sec_oa()  # 先算旧 OA（流程覆盖判断依赖 oa_requestids）
        body_flow = self.sec_flow()

        L.append("# %s" % (self.no or "（无合同编号）申请 %s" % self.aid))
        L.append("")
        L.append("| 项 | 内容 |")
        L.append("|---|---|")
        rows = [
            ("合同编号", self.no or "（空）"),
            ("合同名称", sval(a.get("contract_name"))),
            ("申请ID", self.aid),
            ("档案ID", "、".join(self.rel["archive_ids"]) or "（无）"),
            ("数据来源", src_kind),
            ("流程实例", sval(a.get("process_instance_id"))),
            (
                "旧 requestid",
                "、".join(str(x) for x in self.rel.get("oa_requestids", [])) or "–",
            ),
            (
                "合同状态",
                cols.show(
                    "contract_apply", "contract_status", a.get("contract_status"), ctx
                ),
            ),
            (
                "归档后状态",
                cols.show(
                    "contract_apply",
                    "contract_sub_status",
                    a.get("contract_sub_status"),
                    ctx,
                ),
            ),
            (
                "申请人",
                "%s · %s"
                % (sval(a.get("applicant_name")), sval(a.get("applicant_dept_name"))),
            ),
            ("我方主体", sval(a.get("my_main_body"))),
            ("他方主体", sval(a.get("other_main_body"))),
            (
                "创建 / 更新",
                "%s / %s" % (sval(a.get("create_time")), sval(a.get("update_time"))),
            ),
            (
                "三件套",
                "案件信息 ✔ ｜ 附件 %s ｜ 流程 %s ｜ 意见图片 %s"
                % (
                    self.cover.get("attach_text"),
                    self.cover.get("flow_text"),
                    self.cover.get("image_text"),
                ),
            ),
            ("导出", "%s · %s" % (EXPORT_VERSION, "tender_* 库实时读取")),
        ]
        for k, v in rows:
            L.append("| %s | %s |" % (k, cell(v) or "–"))
        L += ["", "---", ""]
        L += body_info + body_detail + body_files + body_flow + body_oa
        L.append("## 数据缺口")
        L.append("")
        if self.gaps:
            for k, d in self.gaps:
                L.append("- **%s**：%s" % (k, d))
        else:
            L.append("- 无已知缺口")
        L.append("")
        return "\n".join(L)

    # -------- 案件信息
    def kv_block(self, table, row, skip=(), only=None):
        out = []
        cols = self.ctx.cols
        file_cols = {c for c, _ in FILE_FIELDS.get(table, [])}
        for c in cols.order[table]:
            if c in skip or c in file_cols or c in ("deleted", "tenant_id"):
                continue
            if only is not None and c not in only:
                continue
            v = row.get(c)
            s = cols.show(table, c, v, self.ctx)
            if s == "":
                continue
            lab = cols.label(table, c)
            if len(s) > 160 or "\n" in s or "<" in s and ">" in s:
                txt = html_to_md(s) if "<" in s else s
                out.append("- **%s**（`%s`）：" % (lab, c))
                out.append("")
                out += ["  > " + ln if ln.strip() else "  >" for ln in txt.splitlines()]
                out.append("")
            else:
                out.append("- **%s**（`%s`）：%s" % (lab, c, s.replace("\n", " ")))
        return out

    def sec_info(self, arcs):
        a = self.a
        L = ["## 案件信息", "", "### 合同申请（contract_apply · id=%s）" % self.aid, ""]
        L += self.kv_block("contract_apply", a)
        L.append("")
        x = sval(a.get("contract_archive_id"))
        back = [sval(r["id"]) for r in self.ctx.archive_by_apply.get(self.aid, [])]
        self.rel["archive_check"] = {
            "apply.contract_archive_id": x,
            "archive.contract_apply_id→": back,
        }
        if x and x not in self.ctx.archives:
            self.gap(
                "档案关联",
                "contract_apply.contract_archive_id=%s 在 contract_archive 中不存在"
                % x,
            )
        if x and back and x not in back:
            self.gap(
                "档案关联冲突",
                "申请指向档案 %s，但反向 contract_archive.contract_apply_id 指向本申请的是 %s"
                % (x, "、".join(back)),
            )
        if (
            x
            and x in self.ctx.archives
            and sval(self.ctx.archives[x].get("contract_apply_id"))
            not in ("", self.aid)
        ):
            self.gap(
                "档案关联冲突",
                "档案 %s 的 contract_apply_id=%s ≠ 本申请"
                % (x, sval(self.ctx.archives[x].get("contract_apply_id"))),
            )
        for arc in arcs:
            L.append(
                "### 合同档案（contract_archive · id=%s%s）"
                % (
                    sval(arc["id"]),
                    " · 已删除" if sval(arc.get("deleted")) == "1" else "",
                )
            )
            L.append("")
            diff = []
            for c in self.ctx.cols.order["contract_archive"]:
                if (
                    c in ("id", "deleted", "tenant_id")
                    or c not in self.ctx.cols.order["contract_apply"]
                ):
                    if c not in self.ctx.cols.order["contract_apply"] and sval(
                        arc.get(c)
                    ):
                        diff.append(c)
                    continue
                if sval(arc.get(c)) != sval(a.get(c)) and sval(arc.get(c)) != "":
                    diff.append(c)
            if diff:
                L.append("与申请不同 / 档案独有的字段：")
                L.append("")
                L += self.kv_block("contract_archive", arc, only=set(diff))
            else:
                L.append("- 档案字段与申请一致（不重复展示）")
            L.append("")
        return L

    # -------- 业务明细
    def table_block(self, table, rows, title=None):
        cols = self.ctx.cols
        file_cols = {c for c, _ in FILE_FIELDS.get(table, [])}
        use = [
            c
            for c in cols.order[table]
            if c not in META_COLS
            and c not in file_cols
            and any(sval(r.get(c)) != "" for r in rows)
        ]
        L = [
            "### %s（%s · %d 行）"
            % (title or cols.table_comment.get(table, table), table, len(rows)),
            "",
        ]
        if len(use) <= 10:
            L.append("| " + " | ".join(cell(cols.label(table, c)) for c in use) + " |")
            L.append("|" + "---|" * len(use))
            for r in rows:
                L.append(
                    "| "
                    + " | ".join(
                        cell(cols.show(table, c, r.get(c), self.ctx)) for c in use
                    )
                    + " |"
                )
            L.append("")
        else:
            for i, r in enumerate(rows, 1):
                L.append("**第 %d 行**（id=%s）" % (i, sval(r.get("id"))))
                L.append("")
                L += self.kv_block(table, r, skip=META_COLS)
                L.append("")
        return L

    def sec_detail(self, arcs):
        ctx = self.ctx
        L = ["## 业务明细", ""]
        n = 0
        parents = {}
        for table, fk, level, parent in CHILD_SPECS:
            if table not in ctx.children:
                continue
            if level == "apply":
                if parent:
                    rows = []
                    for p in parents.get(parent, []):
                        rows += ctx.children[table].get(sval(p["id"]), [])
                else:
                    rows = ctx.children[table].get(self.aid, [])
            else:
                rows = []
                for arc in arcs:
                    rows += ctx.children[table].get(sval(arc["id"]), [])
            parents[table] = rows
            if rows:
                n += len(rows)
                L += self.table_block(table, rows)
        chg = ctx.changes.get(self.aid, [])
        if chg:
            L += self.table_block(
                "contract_apply_change", chg, "合同变更（前/后合同指向本申请）"
            )
            n += len(chg)
        if not n:
            L += [
                "- 无子表记录（已检查：%s）"
                % "、".join(t for t, *_ in CHILD_SPECS if t in ctx.children),
                "",
            ]
        L.append(
            "> 档案副本明细（contract_archive_cost / matter / performance 等）为归档时的复制，未重复展示。"
        )
        L.append("")
        return L

    # -------- 附件
    def sec_files(self):
        L = ["## 附件", ""]
        refs = collect_file_refs(self.ctx, self.a)
        total = local = 0
        self.rel["files"] = []
        if not refs:
            L += ["- 业务表文件字段均为空（确实无附件）", ""]
            self.cover["attach"] = "无附件"
            self.cover["attach_text"] = "无"
            return L
        by_role = defaultdict(list)
        for role, table, col, rowid, raw, ids in refs:
            by_role[role].append((table, col, rowid, raw, ids))
        for role, items in by_role.items():
            L.append("### %s" % role)
            L.append("")
            for table, col, rowid, raw, ids in items:
                for fid in ids:
                    total += 1
                    r = resolve_file(self.ctx, self.oa, self.a, fid)
                    r.update(
                        {"role": role, "table": table, "field": col, "row_id": rowid}
                    )
                    self.rel["files"].append(
                        {k: v for k, v in r.items() if k != "local"}
                        | {
                            "local": self.rel_from_md(r["local"])
                            if r.get("local")
                            else ""
                        }
                    )
                    name = r.get("name") or ("文件 %s" % fid)
                    if r.get("local"):
                        local += 1
                        head = link(self.rel_from_md(r["local"]), name)
                    elif r.get("url"):
                        head = "%s（[原地址](<%s>)）" % (name, r["url"])
                    else:
                        head = name
                    meta = [
                        "来源 `%s.%s`（行 %s）" % (table, col, rowid),
                        "文件ID `%s`" % fid,
                        "命名空间 %s" % r["namespace"],
                    ]
                    if r.get("size"):
                        meta.append("%s B" % r["size"])
                    meta.append("**%s**" % r["status"])
                    if r.get("note"):
                        meta.append(r["note"])
                    L.append("- %s（%s）" % (head, " · ".join(meta)))
                    if r.get("gap"):
                        self.gap("附件", "%s `%s` %s：%s" % (role, fid, name, r["gap"]))
            L.append("")
        self.cover["attach"] = (
            "全部落盘" if local == total else ("部分缺失" if local else "全部缺失")
        )
        self.cover["attach_text"] = "%d/%d 落盘" % (local, total)
        self.cover["files_total"], self.cover["files_local"] = total, local
        return L

    # -------- 新库流程
    def img_rewrite(self, cid, body):
        """把意见正文中的图片地址改为本地相对路径；返回 (新正文, 总数, 本地数)。"""
        cnt = {"n": 0, "ok": 0, "i": 0}
        srcs = [m.group(2) for m in IMG_RE.finditer(body)] + MDIMG_RE.findall(body)
        mapping = {}
        for n, src in enumerate(srcs, 1):
            ent = self.ctx.img_map.get((cid, n, src)) or {}
            if ent.get("kind") == "oa":
                ent["path"], ent["gap"] = self.ctx.old_resources.lookup(src)
            cnt["n"] += 1
            path = ent.get("path")
            if not path and ent.get("path_base"):
                for e in ("png", "jpg", "gif", "bmp", "webp", "jpeg", "bin"):
                    if os.path.exists(ent["path_base"] + "." + e):
                        path = ent["path_base"] + "." + e
                        break
            rec = {
                "comment_id": cid,
                "src": ent.get("src", src[:200]),
                "kind": ent.get("kind"),
            }
            if path and valid_image(path):
                cnt["ok"] += 1
                mapping[src] = self.rel_from_md(path)
                rec["local"] = mapping[src]
                rec["status"] = "已本地化"
            else:
                why = ent.get("gap") or {
                    "oa": "旧 OA FileDownload 图片，需旧库鉴权链路恢复",
                    "local": "Windows 本地路径 / blob 引用，不可从服务器下载",
                    "manifest": "清单内图片未落盘：%s"
                    % (
                        self.ctx.fetch_result.get(ent.get("file_id", ""), "")
                        or self.ctx.fs_status.get(ent.get("file_id", ""), "尚未获取")
                    ),
                    "http": "清单外图片下载失败：%s"
                    % self.ctx.fetch_result.get(
                        "img:" + ent.get("path_base", ""), "尚未获取"
                    ),
                }.get(ent.get("kind"), "未解析")
                rec["status"] = why
                self.gap(
                    "意见图片",
                    "意见 `%s` 图片 %s：%s" % (cid, ent.get("src", src)[:120], why),
                )
            self.rel.setdefault("images", []).append(rec)

        def rep_html(m):
            s = m.group(2)
            return m.group(0).replace(s, mapping[s]) if s in mapping else m.group(0)

        def rep_md(m):
            s = m.group(1)
            return (
                m.group(0).replace(s, "<%s>" % mapping[s])
                if s in mapping
                else m.group(0)
            )

        body = IMG_RE.sub(rep_html, body)
        body = MDIMG_RE.sub(rep_md, body)
        return body, cnt["n"], cnt["ok"]

    def user(self, uid):
        uid = sval(uid)
        if not uid:
            return "–"
        n = self.ctx.users.get(uid)
        return "%s（%s）" % (n, uid) if n else uid

    def sec_flow(self):
        ctx = self.ctx
        L = ["## 审批流程与意见", ""]
        links = ctx.proc_links.get(self.aid, [])
        self.rel["processes"] = []
        img_total = img_ok = n_comment = n_event = 0
        found = 0
        if not links:
            L += ["- 业务表未填写任何流程实例 ID", ""]
        for pid, src in links:
            p = ctx.procs.get(pid)
            self.rel["processes"].append(
                {"proc_id": pid, "source": src, "matched": bool(p)}
            )
            if not p:
                if is_migrated(self.a) and pid.isdigit():
                    L += [
                        "- `%s`（%s）：新库 act_hi_procinst 无此实例；迁移记录的该值按旧 OA requestid 处理，见“旧 OA 历史”"
                        % (pid, src),
                        "",
                    ]
                else:
                    L += [
                        "- `%s`（%s）：**新库 act_hi_procinst 未找到该流程**"
                        % (pid, src),
                        "",
                    ]
                    self.gap(
                        "流程未匹配", "%s=%s 在 act_hi_procinst 中不存在" % (src, pid)
                    )
                continue
            found += 1
            L.append("### 流程 %s" % pid)
            L.append("")
            L.append("| 项 | 内容 |")
            L.append("|---|---|")
            for k, v in [
                ("关联来源", src),
                ("流程定义", sval(p["PROC_DEF_ID_"])),
                ("名称", sval(p["NAME_"])),
                ("BUSINESS_KEY_", sval(p["BUSINESS_KEY_"])),
                ("发起人", self.user(p["START_USER_ID_"])),
                ("开始", sval(p["START_TIME_"])),
                ("结束", sval(p["END_TIME_"]) or "（未结束）"),
                ("业务状态", sval(p["BUSINESS_STATUS_"])),
                ("删除/终止原因", sval(p["DELETE_REASON_"])),
                ("租户", sval(p["TENANT_ID_"])),
            ]:
                if v:
                    L.append("| %s | %s |" % (k, cell(v)))
            L.append("")
            if src.startswith("BUSINESS_KEY_"):
                self.gap(
                    "流程补查",
                    "流程 %s 仅通过 BUSINESS_KEY_ 关联（未出现在业务表流程字段），需人工核验"
                    % pid,
                )
            tasks = sorted(
                ctx.tasks.get(pid, []),
                key=lambda t: (sval(t["START_TIME_"]), sval(t["ID_"])),
            )
            comments = sorted(
                ctx.comments.get(pid, []),
                key=lambda c: (sval(c["TIME_"]), sval(c["ID_"])),
            )
            by_task = defaultdict(list)
            task_ids = {sval(t["ID_"]) for t in tasks}
            orphan = []
            for c in comments:
                tid = sval(c["TASK_ID_"])
                (by_task[tid] if tid in task_ids else orphan).append(c)
            L.append(
                "共 %d 个任务节点、%d 条意见/事件记录。" % (len(tasks), len(comments))
            )
            L.append("")
            i = 0
            for t in tasks:
                i += 1
                state = (
                    "进行中"
                    if not sval(t["END_TIME_"])
                    else (
                        "已结束·%s" % sval(t["DELETE_REASON_"])
                        if sval(t["DELETE_REASON_"])
                        else "已完成"
                    )
                )
                L.append(
                    "#### %d. %s · %s · %s → %s · %s"
                    % (
                        i,
                        sval(t["NAME_"]) or sval(t["TASK_DEF_KEY_"]),
                        self.user(t["ASSIGNEE_"]),
                        sval(t["START_TIME_"]),
                        sval(t["END_TIME_"]) or "…",
                        state,
                    )
                )
                L.append("")
                cs = by_task.get(sval(t["ID_"]), [])
                if not cs:
                    L += ["- （该节点无意见记录）", ""]
                for c in cs:
                    a, b, x, y = self.comment_md(c)
                    L += a
                    img_total += b
                    img_ok += x
                    n_comment += y == "comment"
                    n_event += y == "event"
            if orphan:
                L.append("#### 流程级意见 / 事件（无对应任务节点）")
                L.append("")
                for c in orphan:
                    a, b, x, y = self.comment_md(c)
                    L += a
                    img_total += b
                    img_ok += x
                    n_comment += y == "comment"
                    n_event += y == "event"
        self.cover["procs_found"] = found
        n_comment += self.cover.get("old_comments", 0)
        n_event += self.cover.get("old_events", 0)
        img_total += self.cover.get("old_images", 0)
        img_ok += self.cover.get("old_images_local", 0)
        self.cover["comments"], self.cover["events"] = n_comment, n_event
        self.cover["images_total"], self.cover["images_local"] = img_total, img_ok
        has_oa = bool(self.cover.get("old_requests_found"))
        missing_flow = (
            bool(links or self.rel.get("oa_requestids")) and not found and not has_oa
        )
        self.cover["flow"] = (
            "新库流程"
            if found
            else ("仅旧 OA" if has_oa else "流程未取得" if missing_flow else "无流程")
        )
        self.cover["flow_text"] = (
            ("新库 %d 个" % found)
            if found
            else ("见旧 OA" if has_oa else "未取得" if missing_flow else "无")
        )
        self.cover["image"], self.cover["image_text"] = image_coverage(
            img_total, img_ok
        )
        if not img_total and (
            missing_flow or any(k in ("旧 OA", "旧 OA 映射") for k, _ in self.gaps)
        ):
            self.cover["image"], self.cover["image_text"] = (
                "待核验",
                "来源不全，无法判定无图片",
            )
        return L

    def comment_md(self, c):
        cid = sval(c["ID_"])
        typ, action = sval(c["TYPE_"]), sval(c["ACTION_"])
        kind = "event" if typ == "event" or action != "AddComment" else "comment"
        body, src = comment_body(c)
        if src != "FULL_MSG_" and c.get("FULL_MSG_"):
            self.gap("意见正文", "意见 %s FULL_MSG_ 解码失败，已回退 MESSAGE_" % cid)
        body, n, ok = self.img_rewrite(cid, body)
        guess = BPM_TYPE_GUESS.get(typ)
        tlabel = (
            ("类型 %s（推测：%s，待确认）" % (typ, guess))
            if guess
            else ("类型 %s" % typ)
        )
        L = [
            "- **%s** · %s · %s · %s · `%s`"
            % (sval(c["TIME_"]), self.user(c["USER_ID_"]), tlabel, action, cid),
            "",
        ]
        text = html_to_md(body) if body else ""
        # 本地相对路径含空格时用 <> 包裹，保证 Markdown 图片可解析
        text = re.sub(r"!\[([^\]]*)\]\(([^<)][^)]*\s[^)]*)\)", r"![\1](<\2>)", text)
        if text:
            L += ["  " + ln if ln.strip() else "" for ln in text.splitlines()]
        else:
            L.append("  （无正文）")
        L.append("")
        return L, n, ok, kind

    # -------- 旧 OA
    def sec_oa(self):
        a, oa = self.a, self.oa
        src = sval(a.get("oa_source"))
        reqs = []  # (requestid, how)
        if src or sval(a.get("data_source")) == "2" or sval(a.get("oa_request_id")):
            pass
        else:
            self.rel["oa_requestids"] = []
            return []
        L = ["## 旧 OA 历史", ""]
        if oa is None:
            L += ["- 未连接旧 OA（--no-oa）", ""]
            self.gap("旧 OA", "本次未回链旧 OA")
            return L
        pid = sval(a.get("process_instance_id"))
        if src:
            bf = BILLFORM.get(int(src))
            mapped = (
                oa["form"].get((bf, int(sval(a["oa_id"]))))
                if bf and sval(a.get("oa_id")).isdigit()
                else None
            )
            L.append(
                "- 映射：oa_source=%s（%s）、oa_id=%s → workflow_form(billformid=%s, billid=%s) → requestid %s"
                % (
                    src,
                    OA_SOURCE_NAME.get(int(src), "?"),
                    sval(a.get("oa_id")),
                    bf,
                    sval(a.get("oa_id")),
                    "、".join(map(str, mapped)) if mapped else "**未找到**",
                )
            )
            if mapped:
                for r in mapped:
                    reqs.append((r, "workflow_form 映射"))
                if pid.isdigit():
                    if int(pid) in mapped:
                        L.append(
                            "- 核对：contract_apply.process_instance_id=%s 与映射 requestid **一致**"
                            % pid
                        )
                    else:
                        L.append(
                            "- 核对：contract_apply.process_instance_id=%s 与映射 requestid **不一致**（两者均保留）"
                            % pid
                        )
                        self.gap(
                            "旧 OA 映射",
                            "process_instance_id=%s 与 workflow_form 映射 %s 不一致"
                            % (pid, mapped),
                        )
                        reqs.append((int(pid), "process_instance_id"))
            else:
                self.gap(
                    "旧 OA 映射",
                    "oa_source=%s oa_id=%s 在 workflow_form 中未找到"
                    % (src, sval(a.get("oa_id"))),
                )
                if pid.isdigit():
                    reqs.append(
                        (int(pid), "process_instance_id（未经 workflow_form 证实）")
                    )
        elif sval(a.get("data_source")) == "2" and pid.isdigit():
            reqs.append((int(pid), "process_instance_id（oa_source 为空）"))
        if sval(a.get("oa_request_id")).isdigit():
            r = int(sval(a["oa_request_id"]))
            if r not in [x for x, _ in reqs]:
                reqs.append((r, "oa_request_id"))
            L.append("- oa_request_id=%s（直接到旧 workflow_requestbase 核验）" % r)
        L.append("")
        reqs = group_process_links(reqs)
        self.rel["oa_requestids"] = [r for r, _ in reqs]
        self.rel["oa"] = []
        for rid, how in reqs:
            rb = oa["reqs"].get(rid)
            old = oa["old_md"].get(rid)
            self.rel["oa"].append(
                {
                    "requestid": rid,
                    "how": how,
                    "in_requestbase": bool(rb),
                    "old_md": old or "",
                }
            )
            L.append("### 旧请求 %s（%s）" % (rid, how))
            L.append("")
            if not rb:
                L += ["- **旧 workflow_requestbase 查无此 requestid**", ""]
                self.gap(
                    "旧 OA", "requestid %s 在旧 workflow_requestbase 中不存在" % rid
                )
                continue
            self.cover["old_requests_found"] = (
                self.cover.get("old_requests_found", 0) + 1
            )
            L.append("| 项 | 内容 |")
            L.append("|---|---|")
            for k, v in [
                ("旧编号 requestmark", sval(rb["requestmark"])),
                ("标题", sval(rb["requestname"])),
                (
                    "流程",
                    oa["wfs"].get(int(rb["workflowid"] or 0), sval(rb["workflowid"])),
                ),
                (
                    "发起人",
                    oa["names"].get(int(rb["creater"] or 0), sval(rb["creater"])),
                ),
                ("发起", "%s %s" % (sval(rb["createdate"]), sval(rb["createtime"]))),
                ("状态", sval(rb["status"])),
            ]:
                L.append("| %s | %s |" % (k, cell(v)))
            if (
                self.no
                and sval(rb["requestmark"])
                and sval(rb["requestmark"]).strip() != self.no
            ):
                L.append(
                    "| 编号核对 | 旧 requestmark 与新合同编号不同（仅作交叉核验，不影响主键关联） |"
                )
            if old:
                L.append(
                    "| 旧导出 | %s（旧导出按法务节点筛选，表单/附件以其为准，意见可能不全） |"
                    % link(self.rel_from_md(os.path.join(OLD_EXPORT, old)), old)
                )
            else:
                L.append(
                    "| 旧导出 | 旧库尚未导出该 requestid（旧表单 / 旧附件未落盘） |"
                )
                self.gap("旧 OA", "requestid %s 旧表单与附件尚未导出" % rid)
            L.append("")
            logs = sorted(
                oa["logs"].get(rid, []),
                key=lambda r: (
                    sval(r["operatedate"]),
                    sval(r["operatetime"]),
                    int(r["LOGID"] or 0),
                ),
            )
            L.append(
                "#### 旧审批日志（workflow_requestLog + workflow_approvelog，%d 条已取得记录）"
                % len(logs)
            )
            L.append("")
            for i, lg in enumerate(logs, 1):
                for source in lg.get(
                    "sources",
                    [{"table": "workflow_requestLog", "id": str(lg["LOGID"])}],
                ):
                    self.rel.setdefault("old_comments", []).append(
                        {"requestid": rid, **source}
                    )
                lt = sval(lg["logtype"]).strip()
                L.append(
                    "- **%d. %s %s** · %s · %s · %s"
                    % (
                        i,
                        sval(lg["operatedate"]),
                        sval(lg["operatetime"]),
                        oa["nodes"].get(int(lg["nodeid"] or 0), sval(lg["nodeid"])),
                        oa["names"].get(int(lg["operator"] or 0), sval(lg["operator"])),
                        "%s（%s）" % (E9_LOGTYPE.get(lt, "logtype"), lt),
                    )
                )
                rm = sval(lg["remark"]).strip()
                field = "old_comments" if rm else "old_events"
                self.cover[field] = self.cover.get(field, 0) + 1
                if rm:
                    rm = re.sub(
                        r'src="(/weaver/[^"]+)"',
                        lambda m: 'src="%s%s"' % (OA_BASE, m.group(1)),
                        rm,
                    )
                    rm = self.old_img_rewrite(rid, lg["LOGID"], rm)
                    txt = html_to_md(rm)
                    L.append("")
                    L += ["  " + ln if ln.strip() else "" for ln in txt.splitlines()]
                    L.append("")
                if sval(lg["annexdocids"]).strip():
                    L.append(
                        "  - 意见附件 docid：`%s`（旧库文档，未展开）"
                        % sval(lg["annexdocids"]).strip()
                    )
            if not logs:
                L.append("- 旧库无审批日志记录")
            L.append("")
        return L

    def old_img_rewrite(self, rid, logid, body):
        store = self.ctx.old_resources

        def replace(src):
            self.cover["old_images"] = self.cover.get("old_images", 0) + 1
            path, error = store.lookup(src)
            record = {
                "requestid": rid,
                "log_id": str(logid),
                "src": src,
                "source": "old_oa",
            }
            if path:
                self.cover["old_images_local"] = (
                    self.cover.get("old_images_local", 0) + 1
                )
                record.update(local=self.rel_from_md(path), status="已本地化")
            else:
                record["status"] = error
                self.gap(
                    "旧 OA 图片",
                    "requestid %s 日志 %s 图片未取得：%s" % (rid, logid, error),
                )
            self.rel.setdefault("images", []).append(record)
            return record.get("local", src)

        body = IMG_RE.sub(
            lambda m: m.group(0).replace(m.group(2), replace(m.group(2))), body
        )
        return MDIMG_RE.sub(lambda m: "![](<%s>)" % replace(m.group(1)), body)


# ======================================================================= 主流程


def main():
    ap = argparse.ArgumentParser(description="新库三件套 → 每案一份 Markdown")
    ap.add_argument(
        "--limit", type=int, default=0, help="小批样本数量（按形态分层抽样）"
    )
    ap.add_argument(
        "--apply-id", action="append", default=[], help="只导出指定申请 ID（可多次）"
    )
    ap.add_argument(
        "--scope-index", help="按现有索引中的申请ID精确限定重建范围，仍输出正式索引"
    )
    ap.add_argument(
        "--min-year",
        type=int,
        default=2024,
        help="最小年份（默认 2024，过滤 2024 以前的数据；传 0 表示不过滤）",
    )
    ap.add_argument("--workers", type=int, default=32, help="补抓并发")
    ap.add_argument("--no-fetch", action="store_true", help="不补抓附件/图片")
    ap.add_argument(
        "--retry-404", action="store_true", help="对 fullsync 已判 404 的文件也重试"
    )
    ap.add_argument("--no-oa", action="store_true", help="不连旧 OA")
    ap.add_argument("--refresh-oa", action="store_true", help="忽略旧 OA 缓存重拉")
    ap.add_argument("--web-port", type=int, default=8771)
    ap.add_argument("--no-web", action="store_true")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="演练模式：无破坏性副作用，无需真实数据库凭据",
    )
    args = ap.parse_args()

    env = load_env()
    os.makedirs(OUT, exist_ok=True)
    prog = Progress()
    stop = threading.Event()
    threading.Thread(target=prog.loop, args=(stop,), daemon=True).start()
    if not args.no_web:
        try:
            import tender_case_web

            port = tender_case_web.start_in_thread(PROGRESS_PATH, args.web_port)
            print("[web] 进度页: http://127.0.0.1:%d/" % port, flush=True)
        except Exception as e:
            print("[web] 启动失败（不影响导出）: %s" % e, flush=True)

    t0 = time.time()
    try:
        prog.step(0, "running")
        if args.dry_run:
            print("[DRY_RUN] 演练模式：跳过真实数据库连接与写出，无副作用。")
            prog.step(0, "done", "dry-run")
            prog.step(1, "done", "dry-run")
            prog.step(2, "done", "dry-run")
            prog.step(3, "done", "dry-run")
            prog.step(4, "done", "dry-run")
            prog.state = "done"
            return
        db = MySQL(env)
        scope_ids = args.apply_id
        if args.scope_index:
            if args.apply_id or args.limit:
                raise ValueError("--scope-index 不与 --apply-id/--limit 混用")
            with open(args.scope_index, encoding="utf-8-sig", newline="") as scope:
                scope_ids = list(
                    dict.fromkeys(row["申请ID"] for row in csv.DictReader(scope))
                )
            if not scope_ids or not all(x.isdigit() for x in scope_ids):
                raise ValueError("范围索引为空或申请ID无效")
        ctx = load_mysql(
            db, prog, scope_ids, args.limit, 0 if args.scope_index else args.min_year
        )
        if args.scope_index and {sval(a["id"]) for a in ctx.applies} != set(scope_ids):
            raise ValueError("范围索引中的部分申请不存在或已删除，停止以免静默丢案")
        ctx.old_resources = OldResources(ROOT, OUT, OA_BASE)
        ctx.fetch_result = {}
        prog.step(0, "done")
        print("[load] %s" % prog.steps[0]["detail"], flush=True)

        oa = None
        if args.no_oa:
            prog.step(1, "skip", "--no-oa")
        else:
            prog.step(1, "running")
            oa = load_oa(env, ctx, prog, args.refresh_oa)
            ctx.old_resources.file_sizes = {
                OA_BASE + "/weaver/weaver.file.FileDownload?fileid=" + str(fid): int(
                    r.get("filesize") or 0
                )
                for fid, r in oa["images"].items()
            }
            prog.step(1, "done")
            print("[oa] %s" % prog.steps[1]["detail"], flush=True)

        prog.step(2, "running")
        file_jobs = set()
        for a in ctx.applies:
            for _, table, col, rowid, raw, ids in collect_file_refs(ctx, a):
                for fid in ids:
                    old_exists = bool(oa and fid.isdigit() and int(fid) in oa["images"])
                    if (
                        file_namespace(is_migrated(a), fid in ctx.files, old_exists)
                        == "tender_infra"
                    ):
                        file_jobs.add(fid)
        img_files, http_jobs = plan_images(ctx)
        file_jobs |= img_files
        old_jobs = []
        for comments in ctx.comments.values():
            for comment in comments:
                body, _ = comment_body(comment)
                for source in image_sources(body):
                    if classify_img(ctx, source)[0] == "oa":
                        old_jobs.append((source, True))
        if oa:
            needed = set()
            for a in ctx.applies:
                if sval(a.get("oa_request_id")).isdigit():
                    needed.add(int(a["oa_request_id"]))
                if is_migrated(a) and sval(a.get("process_instance_id")).isdigit():
                    needed.add(int(a["process_instance_id"]))
                src, oid = sval(a.get("oa_source")), sval(a.get("oa_id"))
                if src.isdigit() and oid.isdigit():
                    needed.update(
                        oa["form"].get((BILLFORM.get(int(src)), int(oid)), [])
                    )
                for ref in collect_file_refs(ctx, a):
                    for fid in ref[5]:
                        local_old = (
                            oa["old_attach"].get(int(fid)) if fid.isdigit() else None
                        )
                        if (
                            fid.isdigit()
                            and file_namespace(
                                is_migrated(a),
                                fid in ctx.files,
                                int(fid) in oa["images"],
                            )
                            == "e9_imagefile"
                            and not (
                                local_old
                                and valid_file(os.path.join(OLD_ATTACH, local_old))
                            )
                        ):
                            old_jobs.append(
                                (
                                    OA_BASE
                                    + "/weaver/weaver.file.FileDownload?fileid="
                                    + fid,
                                    False,
                                )
                            )
            for rid in needed:
                for lg in oa["logs"].get(rid, []):
                    text = sval(lg.get("remark"))
                    old_jobs.extend((m.group(2), True) for m in IMG_RE.finditer(text))
                    old_jobs.extend((u, True) for u in MDIMG_RE.findall(text))
        if args.no_fetch:
            prog.step(
                2,
                "skip",
                "--no-fetch（引用文件 %d、清单外图片 %d 未补抓）"
                % (len(file_jobs), len(http_jobs)),
            )
        else:
            fetch_all(ctx, prog, file_jobs, http_jobs, args.workers, args.retry_404)
            old_counts = ctx.old_resources.prepare(old_jobs, args.workers, prog)
            with prog.lock:
                prog.fetch["total"] += sum(old_counts.values())
                prog.fetch["done"] += sum(old_counts.values())
                for key in ("ok", "skip", "fail"):
                    prog.fetch[key] += old_counts[key]
            prog.detail(2, prog.steps[2]["detail"] + "；旧 OA " + str(old_counts))
            prog.step(
                2,
                "done",
                prog.steps[2]["detail"]
                + " → ok %d · fail %d" % (prog.fetch["ok"], prog.fetch["fail"]),
            )
        print("[fetch] %s" % prog.steps[2]["detail"], flush=True)

        prog.step(3, "running")
        state = {}
        if os.path.exists(STATE_PATH):
            try:
                state = json.load(open(STATE_PATH, encoding="utf-8"))
            except Exception:
                state = {}
        with prog.lock:
            prog.cases["total"] = len(ctx.applies)
        index_rows, errors, cover = [], [], defaultdict(lambda: defaultdict(int))
        rel_f = open(os.path.join(OUT, "_relations.jsonl.tmp"), "w", encoding="utf-8")
        used = {}
        for a in ctx.applies:
            case = Case(ctx, oa, a)
            try:
                text = case.render()
                path = case.md_path()
                if path in used:  # 极端：同名清理后冲突
                    path = path[:-3] + "_%s.md" % case.aid
                used[path] = case.aid
                h = hashlib.sha1(text.encode("utf-8")).hexdigest()
                old = state.get(case.aid) or {}
                if (
                    old.get("hash") == h
                    and old.get("path") == path
                    and os.path.exists(path)
                ):
                    st = "unchanged"
                else:
                    if (
                        old.get("path")
                        and old.get("path") != path
                        and os.path.exists(old["path"])
                    ):
                        os.remove(old["path"])
                    atomic_write(path, text)
                    st = "written"
                state[case.aid] = {
                    "hash": h,
                    "path": path,
                    "update_time": sval(a.get("update_time")),
                    "version": EXPORT_VERSION,
                }
                rel_md = os.path.relpath(path, OUT).replace("\\", "/")
                cv = case.cover
                index_rows.append(
                    [
                        case.aid,
                        case.no,
                        sval(a.get("contract_name")),
                        case.rel["source"],
                        "、".join(case.rel["archive_ids"]),
                        sval(a.get("process_instance_id")),
                        "、".join(map(str, case.rel.get("oa_requestids", []))),
                        cv.get("attach_text"),
                        cv.get("flow_text"),
                        cv.get("image_text"),
                        cv.get("comments", 0),
                        len(case.gaps),
                        rel_md,
                    ]
                )
                for k in ("attach", "flow", "image"):
                    cover[k][cv.get(k, "?")] += 1
                cover["source"][case.rel["source"]] += 1
                for kind, d in case.gaps:
                    errors.append([case.aid, case.no, kind, d, rel_md])
                    cover["gap_kinds"][kind] += 1
                cover["totals"]["comments"] += cv.get("comments", 0)
                cover["totals"]["events"] += cv.get("events", 0)
                cover["totals"]["files"] += cv.get("files_total", 0)
                cover["totals"]["files_local"] += cv.get("files_local", 0)
                cover["totals"]["images"] += cv.get("images_total", 0)
                cover["totals"]["images_local"] += cv.get("images_local", 0)
                case.rel["md"] = rel_md
                rel_f.write(
                    json.dumps(case.rel, ensure_ascii=False, default=str) + "\n"
                )
                with prog.lock:
                    prog.cases["done"] += 1
                    prog.cases[st] += 1
                    if (
                        prog.cases["done"] % 200 == 0
                        or prog.cases["done"] == prog.cases["total"]
                    ):
                        prog.cover = {k: dict(v) for k, v in cover.items()}
                if st == "written":
                    prog.event("case", case.no or case.aid, "written")
            except Exception as e:
                with prog.lock:
                    prog.cases["done"] += 1
                    prog.cases["fail"] += 1
                prog.error(
                    "case", case.aid, "%s\n%s" % (e, traceback.format_exc()[-400:])
                )
                errors.append([case.aid, case.no, "导出异常", repr(e), ""])
            if prog.cases["done"] % 2000 == 0:
                print(
                    "[cases] %d/%d" % (prog.cases["done"], prog.cases["total"]),
                    flush=True,
                )
        rel_f.close()
        prog.cover = {k: dict(v) for k, v in cover.items()}
        prog.step(
            3,
            "done",
            "写入 %d · 未变 %d · 失败 %d"
            % (prog.cases["written"], prog.cases["unchanged"], prog.cases["fail"]),
        )
        print("[cases] %s" % prog.steps[3]["detail"], flush=True)

        prog.step(4, "running")
        partial = bool(args.limit or args.apply_id)
        sfx = "_sample" if partial else ""
        os.replace(
            os.path.join(OUT, "_relations.jsonl.tmp"),
            os.path.join(OUT, "_relations%s.jsonl" % sfx),
        )
        with open(
            os.path.join(OUT, "_index%s.csv" % sfx),
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "申请ID",
                    "合同编号",
                    "合同名称",
                    "数据来源",
                    "档案ID",
                    "流程实例",
                    "旧requestid",
                    "附件",
                    "新库流程",
                    "意见图片",
                    "意见条数",
                    "缺口数",
                    "文件",
                ]
            )
            w.writerows(index_rows)
        with open(
            os.path.join(OUT, "_errors%s.csv" % sfx),
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as f:
            w = csv.writer(f)
            w.writerow(["申请ID", "合同编号", "缺口类型", "说明", "文件"])
            w.writerows(errors)
        with open(
            os.path.join(OUT, "_coverage%s.json" % sfx), "w", encoding="utf-8"
        ) as f:
            json.dump(
                {
                    "generated_at": now_iso(),
                    "version": EXPORT_VERSION,
                    "cases": len(index_rows),
                    "cover": prog.cover,
                    "fetch": prog.fetch,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        with open(STATE_PATH + ".tmp", "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(STATE_PATH + ".tmp", STATE_PATH)
        prog.step(
            4,
            "done",
            "_index%s.csv · _relations%s.jsonl · _errors%s.csv（%d 条缺口）· _coverage%s.json"
            % (sfx, sfx, sfx, len(errors), sfx),
        )
        prog.state = "done"
    except KeyboardInterrupt:
        prog.state = "stopped"
    except Exception as e:
        prog.state = "error"
        prog.error("main", "fatal", "%s\n%s" % (e, traceback.format_exc()[-800:]))
        traceback.print_exc()
    finally:
        prog.phase = {"done": "完成", "stopped": "已中断", "error": "异常"}.get(
            prog.state, prog.state
        )
        prog.write()
        stop.set()
    print(
        "[汇总] state=%s 耗时 %.1f 分 | 案件 %d（写入 %d / 未变 %d / 失败 %d）| 补抓 ok %d fail %d"
        % (
            prog.state,
            (time.time() - t0) / 60,
            prog.cases["done"],
            prog.cases["written"],
            prog.cases["unchanged"],
            prog.cases["fail"],
            prog.fetch["ok"],
            prog.fetch["fail"],
        ),
        flush=True,
    )
    if (
        not args.no_web
        and os.environ.get("CASE_EXPORT_KEEP_WEB", "0") == "1"
        and prog.state == "done"
    ):
        print("[web] 导出完成，进度页继续保留（Ctrl+C 退出）", flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
