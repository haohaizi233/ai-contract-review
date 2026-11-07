# -*- coding: utf-8 -*-
"""tender_attach_export.py — 从新系统（<TENDER_MYSQL_HOST>:<PORT>）导出文件清单并批量下载。

零第三方依赖：SQL 走 mysql CLI，下载走 urllib。凭据从 data/.db_env 读 MYSQL_* 。

用法:
  python tools/tender_attach_export.py --manifest-only              # 只出清单
  python tools/tender_attach_export.py --limit 100 --type pdf,docx  # 限量下载
  python tools/tender_attach_export.py --since 2026-01-01            # 按创建时间
  python tools/tender_attach_export.py --id 2118160871610552321     # 下载指定文件
"""

import argparse
import csv
import hashlib
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENVF = os.path.join(ROOT, "data", ".db_env")


def load_env():
    env = {}
    if os.path.exists(ENVF):
        for line in open(ENVF, encoding="utf-8"):
            line = line.strip()
            if line.startswith("export ") and "=" in line:
                k, v = line[len("export ") :].split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    return env


def sql_query(sql, env):
    cmd = [
        "mysql",
        "-h" + env["MYSQL_HOST"],
        "-P" + env["MYSQL_PORT"],
        "-u" + env["MYSQL_USER"],
        "-N",
        "-B",
        "--default-character-set=utf8mb4",
        "-e",
        sql,
    ]
    proc_env = {**os.environ, "MYSQL_PWD": env.get("MYSQL_PWD", "")}
    out = subprocess.run(
        cmd,
        env=proc_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if out.returncode != 0:
        sys.exit("mysql 查询失败: " + out.stderr.strip()[:300])
    rows = []
    for line in out.stdout.splitlines():
        rows.append(line.split("\t"))
    return rows


def build_sql(args):
    where = ["deleted = 0", "url IS NOT NULL", "url <> ''"]
    if args.id:
        where.append("id = %d" % args.id)
    if args.type:
        types = ",".join("'%s'" % t.strip() for t in args.type.split(","))
        where.append("type IN (%s)" % types)
    if args.since:
        where.append("create_time >= '%s'" % args.since)
    if args.until:
        where.append("create_time < '%s'" % args.until)
    sql = (
        "SELECT id, name, type, size, IFNULL(md5, ''), url FROM tender_infra.infra_file WHERE "
        + " AND ".join(where)
        + " ORDER BY id DESC"
    )
    if args.limit:
        sql += " LIMIT %d" % args.limit
    return sql


def download(url, dest, expect_md5):
    tmp = dest + ".part"
    if os.path.exists(dest):
        return "skip"
    # URL 里的中文文件名需要百分号编码；md5 列只有 32 位十六进制才参与校验
    url = urllib.parse.quote(url, safe=":/?&=%")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", expect_md5 or ""):
        expect_md5 = ""
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        return "fail: %s" % e
    if expect_md5:
        h = hashlib.md5(open(tmp, "rb").read()).hexdigest()
        if h.lower() != expect_md5.lower():
            os.remove(tmp)
            return "md5-mismatch"
    os.replace(tmp, dest)
    return "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "tender_files"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--type", default="")
    ap.add_argument("--since", default="")
    ap.add_argument("--until", default="")
    ap.add_argument("--id", type=int, default=0)
    ap.add_argument("--manifest-only", action="store_true")
    args = ap.parse_args()

    env = load_env()
    if "MYSQL_HOST" not in env:
        sys.exit("data/.db_env 缺少 MYSQL_* 凭据")

    rows = sql_query(build_sql(args), env)
    os.makedirs(args.out, exist_ok=True)
    manifest = os.path.join(args.out, "manifest.csv")
    with open(manifest, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "type", "size", "md5", "url", "status"])
        stats = {"ok": 0, "skip": 0, "fail": 0}
        for i, r in enumerate(rows, 1):
            fid, name, ftype, size, md5, url = r
            if args.manifest_only:
                w.writerow([fid, name, ftype, size, md5, url, ""])
                continue
            safe = "".join(c if c not in '\\/:*?"<>|' else "_" for c in (name or fid))
            dest = os.path.join(args.out, "%s_%s" % (fid, safe))
            status = download(url, dest, md5)
            w.writerow([fid, name, ftype, size, md5, url, status])
            key = "skip" if status == "skip" else ("ok" if status == "ok" else "fail")
            stats[key] += 1
            print("[%d/%d] %s %s" % (i, len(rows), status, safe[:60]))
    print("完成: %d 条 | 清单: %s" % (len(rows), manifest))
    if not args.manifest_only:
        print("下载统计:", stats)


if __name__ == "__main__":
    main()
