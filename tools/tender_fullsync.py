# -*- coding: utf-8 -*-
"""tender_fullsync.py — 新系统（tender_* 库）全量同步：业务表并行导出 + 全部文件高并发下载。

特性
  - 并发打满：文件下载 ThreadPool（默认 32 线程），表导出并行 mysqldump（默认 6 进程）
  - WAL 断点续传：data/tender_sync/wal.jsonl 追加式记录完成单元，重跑自动跳过已完成项
  - 实时进度：data/tender_sync/progress.json（原子写、2s 刷新）+ 内嵌进度 Web（tender_sync_web.py）
  - 断点安全：下载先写 .part 再原子替换；失败不写 WAL，重跑自动重试

输出布局
  data/tender_sync/tables/<schema>.<table>.sql.gz   全量表导出（gzip 压缩）
  data/tender_sync/manifest.csv                     文件清单
  data/tender_sync/wal.jsonl                        WAL（断点续传依据）
  data/tender_sync/progress.json                    实时进度快照
  data/tender_files/<id>_<文件名>                    文件实体

用法
  python tools/tender_fullsync.py                          # 全量：表 + 文件
  python tools/tender_fullsync.py --tables-only            # 只导表
  python tools/tender_fullsync.py --files-only --limit 20  # 冒烟小批量
  python tools/tender_fullsync.py --workers 32 --web-port 8770
  中断后重跑同一命令即自动续传（WAL 跳过已完成单元）。
"""
import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENVF = os.path.join(ROOT, "data", ".db_env")
SYNC_DIR = os.path.join(ROOT, "data", "tender_sync")
TABLES_DIR = os.path.join(SYNC_DIR, "tables")
FILES_DIR = os.path.join(ROOT, "data", "tender_files")
WAL_PATH = os.path.join(SYNC_DIR, "wal.jsonl")
PROGRESS_PATH = os.path.join(SYNC_DIR, "progress.json")

# 快照/备份表命名：_20261104 / _261030 / __20260815 / _20261104-1 等日期后缀
SNAPSHOT_RE = re.compile(r"_{1,2}\d{6,8}(-\d+)?$")
MD5_RE = re.compile(r"[0-9a-fA-F]{32}")
SAFE_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


# ---------------------------------------------------------------- 基础设施

def load_env():
    env = {}
    if os.path.exists(ENVF):
        for line in open(ENVF, encoding="utf-8"):
            line = line.strip()
            if line.startswith("export ") and "=" in line:
                k, v = line[len("export "):].split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    return env


def sql_query(sql, env):
    cmd = [
        "mysql", "-h" + env["MYSQL_HOST"], "-P" + env["MYSQL_PORT"],
        "-u" + env["MYSQL_USER"], "-p" + env["MYSQL_PWD"],
        "-N", "-B", "--default-character-set=utf8mb4", "-e", sql,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise RuntimeError("mysql 查询失败: " + out.stderr.strip()[:300])
    return [line.split("\t") for line in out.stdout.splitlines()]


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_name(name, limit=120):
    s = SAFE_RE.sub("_", name or "").strip().rstrip(".")
    if len(s) > limit:
        root, ext = os.path.splitext(s)
        s = root[: limit - len(ext) - 1] + ext
    return s or "file"


# ---------------------------------------------------------------- 进度跟踪

class Progress:
    """线程安全的进度累计器：WAL 追加 + progress.json 原子快照 + 速率/ETA。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.t0 = time.time()
        self.state = "running"
        self.phase = "init"
        self.recent = deque(maxlen=40)
        self.errors = deque(maxlen=50)
        self.samples = deque(maxlen=180)
        self.t_total = 0
        self.t_done = 0
        self.t_rows = 0
        self.t_skip = 0
        self.t_fail = 0
        self.t_current = set()
        self.f_total = 0
        self.f_done = 0
        self.f_ok = 0
        self.f_skip = 0
        self.f_fail = 0
        self.b_total = 0
        self.b_done = 0
        self.f_current = set()
        self._wal = open(WAL_PATH, "a", encoding="utf-8")

    # ---- WAL
    def wal(self, unit, key, status, **extra):
        rec = {"ts": now_iso(), "unit": unit, "key": str(key), "status": status}
        rec.update(extra)
        with self.lock:
            self._wal.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._wal.flush()
            self.recent.append(rec)

    # ---- 表
    def table_set_total(self, n):
        with self.lock:
            self.t_total = n

    def table_add_skips(self, n, rows):
        """WAL 恢复时批量计入已跳过的表（不再写 WAL）。"""
        with self.lock:
            self.t_skip += n
            self.t_rows += rows

    def table_enter(self, name):
        with self.lock:
            self.t_current.add(name)

    def table_leave(self, name, status, rows=0):
        with self.lock:
            self.t_current.discard(name)
            if status == "done":
                self.t_done += 1
                self.t_rows += rows
            elif status == "skip":
                self.t_skip += 1
            else:
                self.t_fail += 1
        self.wal("table", name, status, rows=rows)

    def table_error(self, name, err):
        with self.lock:
            self.errors.append({"ts": now_iso(), "unit": "table", "key": name, "error": err[:300]})

    # ---- 文件
    def files_set_total(self, count, nbytes):
        with self.lock:
            self.f_total = count
            self.b_total = nbytes

    def file_enter(self, name):
        with self.lock:
            self.f_current.add(name)

    def file_leave(self, name, status, nbytes=0):
        with self.lock:
            self.f_current.discard(name)
            self.f_done += 1
            self.b_done += nbytes
            if status == "ok":
                self.f_ok += 1
            elif status == "skip":
                self.f_skip += 1
            else:
                self.f_fail += 1
        self.wal("file", name, status, bytes=nbytes)

    def file_error(self, key, err):
        with self.lock:
            self.errors.append({"ts": now_iso(), "unit": "file", "key": str(key), "error": err[:300]})

    def set_phase(self, phase):
        with self.lock:
            self.phase = phase

    # ---- 快照
    def snapshot(self):
        with self.lock:
            t = time.time()
            self.samples.append((t, self.f_done, self.b_done))
            speed_f = speed_b = 0.0
            if len(self.samples) >= 2:
                t_old, f_old, b_old = self.samples[0]
                dt = t - t_old
                if dt > 1:
                    speed_f = (self.f_done - f_old) / dt            # 个/秒
                    speed_b = (self.b_done - b_old) / dt            # 字节/秒
            eta = 0.0
            if speed_f > 0 and self.f_total:
                eta = max(0, (self.f_total - self.f_done) / speed_f)
            snap = {
                "pid": os.getpid(),
                "state": self.state,
                "phase": self.phase,
                "started_at": now_iso(),
                "elapsed_s": round(t - self.t0, 1),
                "updated_at": now_iso(),
                "tables": {
                    "total": self.t_total, "done": self.t_done, "skip": self.t_skip,
                    "failed": self.t_fail, "rows": self.t_rows,
                    "current": sorted(self.t_current),
                },
                "files": {
                    "total": self.f_total, "done": self.f_done, "ok": self.f_ok,
                    "skip": self.f_skip, "fail": self.f_fail,
                    "bytes_total": self.b_total, "bytes_done": self.b_done,
                    "speed_per_min": round(speed_f * 60, 1),
                    "speed_mbps": round(speed_b / 1048576, 2),
                    "eta_s": round(eta),
                    "current": sorted(self.f_current),
                },
                "errors": list(self.errors),
                "recent": list(self.recent),
            }
        return snap

    def write_progress(self):
        snap = self.snapshot()
        tmp = PROGRESS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)
        os.replace(tmp, PROGRESS_PATH)

    def writer_loop(self, stop_evt):
        while not stop_evt.wait(2.0):
            try:
                self.write_progress()
            except Exception:
                pass

    def close(self):
        try:
            self._wal.close()
        except Exception:
            pass


# ---------------------------------------------------------------- WAL 恢复

def load_wal():
    done_tables, done_files = {}, set()
    if os.path.exists(WAL_PATH):
        for line in open(WAL_PATH, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("status") == "skip":
                continue
            if rec.get("unit") == "table" and rec.get("status") == "done":
                done_tables[rec.get("key")] = int(rec.get("rows") or 0)
            elif rec.get("unit") == "file" and rec.get("status") in ("ok", "skip"):
                done_files.add(str(rec.get("key")))
    return done_tables, done_files


# ---------------------------------------------------------------- 表导出

def dump_table(env, schema, table, dest_gz):
    """mysqldump 单表 -> gzip 流式落盘，.part 原子替换。"""
    cmd = [
        "mysqldump", "-h" + env["MYSQL_HOST"], "-P" + env["MYSQL_PORT"],
        "-u" + env["MYSQL_USER"], "-p" + env["MYSQL_PWD"],
        "--single-transaction", "--skip-lock-tables", "--no-tablespaces",
        "--set-gtid-purged=OFF", "--hex-blob", "--triggers",
        "--default-character-set=utf8mb4", schema, table,
    ]
    tmp = dest_gz + ".part"
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with open(tmp, "wb") as f:
            g = gzip.GzipFile(fileobj=f, mode="wb", compresslevel=1)
            while True:
                chunk = p.stdout.read(1 << 20)
                if not chunk:
                    break
                g.write(chunk)
            g.close()
        rc = p.wait()
        err = p.stderr.read().decode("utf-8", "replace").strip()
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        return "fail: %s" % e
    if rc != 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        return "fail: " + (err[-300:] if err else "rc=%d" % rc)
    os.replace(tmp, dest_gz)
    return "ok"


def run_tables(env, prog, workers, table_limit):
    rows = sql_query(
        "SELECT TABLE_SCHEMA, TABLE_NAME, IFNULL(TABLE_ROWS,0) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA LIKE 'tender\\_%' AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_SCHEMA, TABLE_NAME",
        env,
    )
    targets = []
    for schema, table, est_rows in rows:
        if SNAPSHOT_RE.search(table):
            continue
        targets.append((schema, table, int(est_rows or 0)))
    if table_limit:
        targets = targets[:table_limit]

    done_tables, _ = load_wal()
    todo = [(s, t, r) for s, t, r in targets if "%s.%s" % (s, t) not in done_tables]
    skip_rows = sum(done_tables.get("%s.%s" % (s, t), 0) for s, t, r in targets
                    if "%s.%s" % (s, t) in done_tables)
    prog.set_phase("tables")
    prog.table_set_total(len(targets))
    prog.table_add_skips(len(targets) - len(todo), skip_rows)
    print("[tables] 共 %d 张基表（已排除快照表），WAL 跳过 %d，待导 %d，并发 %d"
          % (len(targets), len(targets) - len(todo), len(todo), workers))
    os.makedirs(TABLES_DIR, exist_ok=True)

    def work(item):
        schema, table, est_rows = item
        name = "%s.%s" % (schema, table)
        dest = os.path.join(TABLES_DIR, "%s.%s.sql.gz" % (schema, table))
        prog.table_enter(name)
        status = dump_table(env, schema, table, dest)
        if status == "ok":
            prog.table_leave(name, "done", est_rows)
            print("[tables] done %s (est_rows=%d)" % (name, est_rows))
        else:
            prog.table_leave(name, "fail")
            prog.table_error(name, status)
            print("[tables] FAIL %s %s" % (name, status))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, todo))
    print("[tables] 完成: done=%d skip=%d fail=%d" % (prog.t_done, prog.t_skip, prog.t_fail))


# ---------------------------------------------------------------- 文件下载

# 直连优先（绕过系统代理，内网 MinIO 被代理 502），失败时回退系统代理
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_PROXY_OPENER = urllib.request.build_opener()
_OPENERS = (_DIRECT_OPENER, _PROXY_OPENER)


def download(url, dest, expect_md5, retries=4):
    """返回 (status, nbytes)。.part 临时文件 + 原子替换；md5 仅对合法 32 位值校验。
    网络抖动自动重试（直连/代理交替），指数退避。"""
    if os.path.exists(dest):
        return "skip", os.path.getsize(dest)
    tmp = dest + ".part"
    url = urllib.parse.quote(url, safe=":/?&=%")
    if not MD5_RE.fullmatch(expect_md5 or ""):
        expect_md5 = ""
    last_err = ""
    for attempt in range(retries):
        nbytes = 0
        try:
            opener = _OPENERS[attempt % 2]
            with opener.open(urllib.request.Request(url), timeout=90) as r, open(tmp, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    nbytes += len(chunk)
        except Exception as e:
            last_err = str(e)
            if os.path.exists(tmp):
                os.remove(tmp)
            # 确定性错误（源站拒绝/对象不存在）重试无意义，试满直连+代理两次即止
            if attempt >= 1 and any(m in last_err for m in
                                    ("10061", "refused", "502", "404", "Not Found")):
                break
            time.sleep(min(1.5 * (attempt + 1), 6))
            continue
        if expect_md5:
            h = hashlib.md5(open(tmp, "rb").read()).hexdigest()
            if h.lower() != expect_md5.lower():
                os.remove(tmp)
                last_err = "md5-mismatch"
                time.sleep(min(1.5 * (attempt + 1), 6))
                continue
        os.replace(tmp, dest)
        return "ok", nbytes
    return "fail: %s" % last_err, 0


def run_files(env, prog, workers, limit):
    sql = ("SELECT id, name, type, IFNULL(size,0), IFNULL(md5,''), url FROM tender_infra.infra_file "
           "WHERE deleted = 0 AND url IS NOT NULL AND url <> '' ORDER BY id")
    rows = sql_query(sql, env)
    if limit:
        rows = rows[:limit]

    os.makedirs(FILES_DIR, exist_ok=True)
    manifest = os.path.join(SYNC_DIR, "manifest.csv")
    with open(manifest, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "type", "size", "md5", "url", "dest"])
        for fid, name, ftype, size, md5, url in rows:
            w.writerow([fid, name, ftype, size, md5, url, "%s_%s" % (fid, safe_name(name))])

    _, done_files = load_wal()
    total_bytes = sum(int(r[3] or 0) for r in rows)
    prog.set_phase("files")
    prog.files_set_total(len(rows), total_bytes)

    todo = []
    for fid, name, ftype, size, md5, url in rows:
        dest = os.path.join(FILES_DIR, "%s_%s" % (fid, safe_name(name)))
        if fid in done_files and os.path.exists(dest):
            prog.file_leave(fid, "skip", os.path.getsize(dest))
            continue
        todo.append((fid, name, url, md5, dest))

    print("[files] 共 %d 个文件 / %.2f GB，WAL+落盘跳过 %d，待下 %d，并发 %d"
          % (len(rows), total_bytes / 1073741824.0, len(rows) - len(todo), len(todo), workers))

    def work(item):
        fid, name, url, md5, dest = item
        prog.file_enter("%s_%s" % (fid, safe_name(name)[:60]))
        status, nbytes = download(url, dest, md5)
        prog.file_leave(fid, status, nbytes)
        if status.startswith("fail") or status == "md5-mismatch":
            prog.file_error(fid, "%s %s" % (status, name))
        return status

    done_counter = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(work, it) for it in todo]
        for fut in as_completed(futures):
            fut.result()
            done_counter += 1
            if done_counter % 500 == 0:
                print("[files] %d/%d done(fail=%d)" % (done_counter, len(todo), prog.f_fail))
    print("[files] 完成: ok=%d skip=%d fail=%d" % (prog.f_ok, prog.f_skip, prog.f_fail))


# ---------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser(description="tender_* 库全量同步（表 + 文件），WAL 断点续传")
    ap.add_argument("--workers", type=int, default=32, help="文件下载并发（默认 32）")
    ap.add_argument("--table-workers", type=int, default=6, help="表导出并发（默认 6）")
    ap.add_argument("--tables-only", action="store_true")
    ap.add_argument("--files-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="文件下载上限（冒烟用）")
    ap.add_argument("--table-limit", type=int, default=0, help="表导出上限（冒烟用）")
    ap.add_argument("--web-port", type=int, default=8770, help="进度 Web 端口（默认 8770）")
    ap.add_argument("--no-web", action="store_true")
    args = ap.parse_args()

    env = load_env()
    if "MYSQL_HOST" not in env:
        sys.exit("data/.db_env 缺少 MYSQL_* 凭据")
    os.makedirs(SYNC_DIR, exist_ok=True)

    prog = Progress()
    stop_evt = threading.Event()
    threading.Thread(target=prog.writer_loop, args=(stop_evt,), daemon=True).start()

    web_url = ""
    if not args.no_web:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        try:
            import tender_sync_web
            port = tender_sync_web.start_in_thread(PROGRESS_PATH, args.web_port)
            web_url = "http://127.0.0.1:%d/" % port
            print("[web] 进度页: %s" % web_url)
        except Exception as e:
            print("[web] 启动失败（不影响同步）: %s" % e)

    t_start = time.time()
    try:
        if not args.files_only:
            run_tables(env, prog, args.table_workers, args.table_limit)
        if not args.tables_only:
            run_files(env, prog, args.workers, args.limit)
        prog.state = "done"
    except KeyboardInterrupt:
        prog.state = "stopped"
        print("\n[!] 已中断，WAL 已保存；重跑同一命令自动续传。")
    finally:
        prog.set_phase("done" if prog.state == "done" else prog.state)
        prog.write_progress()
        stop_evt.set()
        prog.close()

    mins = (time.time() - t_start) / 60.0
    print("[汇总] state=%s 耗时=%.1f 分 | 表 done=%d fail=%d | 文件 ok=%d skip=%d fail=%d"
          % (prog.state, mins, prog.t_done, prog.t_fail, prog.f_ok, prog.f_skip, prog.f_fail))
    if web_url:
        print("[汇总] 进度页: %s （重开浏览器即可复查）" % web_url)
    print("[汇总] 重跑续传: python tools/tender_fullsync.py%s%s%s"
          % (" --tables-only" if args.tables_only else "",
             " --files-only" if args.files_only else "",
             (" --limit %d" % args.limit) if args.limit else ""))


if __name__ == "__main__":
    main()
