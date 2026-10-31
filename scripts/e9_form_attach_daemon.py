# -*- coding: utf-8 -*-
"""e9 表单+附件回填守护进程：多进程并发 + 崩溃自动拉起 + 断点续传。

幂等由 SQLite WAL 状态账本（e9_form_attach_export.py 的 StateDB）保证：
同一案号只会被一个 worker 原子抢占处理一次；worker 崩溃后其 running 条目
超时会被还原为 stalled，守护进程重启 worker 时自动续跑，已完成的直接跳过。

用法：
  python scripts/e9_form_attach_daemon.py --procs 4 --workers 10
  python scripts/e9_form_attach_daemon.py --procs 4 --workers 10 --no-download
  python scripts/e9_form_attach_daemon.py --procs 4 --workers 10 --no-seed   # 连已回填的也重跑

停止：Ctrl+C（会先 terminate 再 kill 所有 worker）。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPORTER = Path(__file__).resolve().parent / "e9_form_attach_export.py"
DEFAULT_OUT = PROJECT_ROOT / "data" / "e9-opinion-export"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--procs", type=int, default=4, help="并发进程数（同时是分片数）")
    ap.add_argument("--workers", type=int, default=10, help="每进程线程数")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--state-db", default=None,
                    help="状态账本路径（默认 <out>/_form_attach_state.sqlite3）")
    ap.add_argument("--max-attempts", type=int, default=3, help="单案号最大尝试次数")
    ap.add_argument("--stale-seconds", type=int, default=1800,
                    help="running 超时视为中断，允许重新抢占")
    ap.add_argument("--poll-seconds", type=float, default=20.0, help="巡检间隔")
    ap.add_argument("--max-restarts", type=int, default=8, help="单个分片最大重启次数")
    ap.add_argument("--no-download", action="store_true", help="只回填表单与链接，不下载附件")
    ap.add_argument("--no-seed", action="store_true", help="不把已有回填标记块的 md 预置为 done")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    if not out_dir.exists():
        print("输出目录不存在：%s" % out_dir, file=sys.stderr)
        return 2
    state_db = Path(args.state_db) if args.state_db else out_dir / "_form_attach_state.sqlite3"

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from e9_form_attach_export import MARK_BEGIN, StateDB  # noqa: E402

    state = StateDB(state_db)
    md_all = sorted(p for p in out_dir.rglob("*.md") if not p.name.startswith("_"))
    shard_stems = [[p.stem for p in md_all[i::args.procs]] for i in range(args.procs)]
    n_reset = state.reset_running()
    if n_reset:
        print("[daemon] 启动接管：running -> stalled %d 条（上次中断任务可续跑）" % n_reset, flush=True)
    if not args.no_seed:
        state.register([p.stem for p in md_all])
        n_seeded = state.seed_done(md_all, MARK_BEGIN)
        print("[daemon] 账本预置 done（已有回填标记块）：%d" % n_seeded, flush=True)

    py = sys.executable
    handles: dict[int, object] = {}
    procs: dict[int, subprocess.Popen | None] = {}
    restarts = {i: 0 for i in range(args.procs)}

    def spawn(i: int):
        cmd = [py, str(EXPORTER),
               "--shard-index", str(i), "--shard-count", str(args.procs),
               "--workers", str(args.workers),
               "--out", str(out_dir),
               "--state-db", str(state_db),
               "--max-attempts", str(args.max_attempts),
               "--stale-seconds", str(args.stale_seconds),
               "--no-seed"]  # 预置由守护进程统一做一次，worker 不重复扫
        if args.no_download:
            cmd.append("--no-download")
        fh = open(out_dir / ("_daemon_worker_s%d.log" % i), "a", encoding="utf-8", errors="replace")
        fh.write("\n===== daemon spawn shard %d at %s =====\n" % (i, time.strftime("%F %T")))
        fh.flush()
        handles[i] = fh
        procs[i] = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, cwd=str(PROJECT_ROOT))

    for i in range(args.procs):
        spawn(i)
    print("[daemon] 已启动 %d 个 worker（每进程 %d 线程）" % (args.procs, args.workers), flush=True)

    try:
        while True:
            time.sleep(args.poll_seconds)
            counts, remaining = state.counts(args.max_attempts)
            alive = sum(1 for p in procs.values() if p is not None and p.poll() is None)
            print("[daemon] %s alive=%d/%d remaining=%d counts=%s"
                  % (time.strftime("%T"), alive, args.procs, remaining, counts), flush=True)

            if remaining <= 0 and alive == 0:
                print("[daemon] 账本无待处理项，全部完成", flush=True)
                return 0

            for i, p in procs.items():
                if p is None or p.poll() is None:
                    continue
                rc = p.returncode
                if rc == 2:
                    print("[daemon] shard %d 参数/配置错误(rc=2)，中止" % i, flush=True)
                    return 2
                if remaining > 0 and restarts[i] < args.max_restarts:
                    print("[daemon] shard %d 退出(rc=%s)，自动重启（第 %d 次）"
                          % (i, rc, restarts[i] + 1), flush=True)
                    n_r = state.reset_running(shard_stems[i])
                    if n_r:
                        print("[daemon] shard %d 回收孤儿任务 %d 条" % (i, n_r), flush=True)
                    spawn(i)
                    restarts[i] += 1
                elif remaining > 0:
                    print("[daemon] shard %d 重启次数超限(%d)" % (i, args.max_restarts), flush=True)

            if remaining > 0 and all(p is None or p.poll() is not None for p in procs.values()):
                print("[daemon] 所有 worker 已退出但账本仍有剩余（尝试次数用尽或重启超限）", flush=True)
                return 1
    except KeyboardInterrupt:
        print("\n[daemon] 收到中断，停止所有 worker", flush=True)
        for p in procs.values():
            if p is not None and p.poll() is None:
                p.terminate()
        time.sleep(2)
        for p in procs.values():
            if p is not None and p.poll() is None:
                p.kill()
        return 130
    finally:
        for fh in handles.values():
            try:
                fh.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
