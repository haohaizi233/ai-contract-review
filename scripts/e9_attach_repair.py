# -*- coding: utf-8 -*-
"""清理伪装成附件的 HTML 错误页，并把受影响案号重置为可重跑（配合 WAL 账本续传）。

背景：FileDownload 鉴权失败时返回 HTML 登录页（HTTP 200 + text/html），
旧版下载器不校验内容，导致 .doc/.pdf/.png 等文件里存的是 HTML 假页面。
本工具按文件头识别假文件，默认干跑只统计；--apply 才删除；
--reset-ledger 同时把这些案号在账本里从 done 重置为 stalled，之后重跑
守护进程即会重新下载（需先准备好有效 Cookie：data/.e9_cookie）。

用法：
  python scripts/e9_attach_repair.py                       # 干跑统计
  python scripts/e9_attach_repair.py --apply               # 删除假文件
  python scripts/e9_attach_repair.py --apply --reset-ledger  # 删除并重置账本以便重下
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e9_form_attach_export import looks_like_html_error  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = PROJECT_ROOT / "data" / "e9-opinion-export"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--apply", action="store_true", help="真正删除假文件（默认干跑）")
    ap.add_argument("--reset-ledger", action="store_true",
                    help="把受影响案号在账本中 done -> stalled，重跑守护进程即可重下")
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    att = out_dir / "attachments"
    if not att.exists():
        print("无 attachments 目录：%s" % att)
        return 0
    files = [p for p in att.rglob("*") if p.is_file() and not p.name.endswith(".part")]
    print("附件文件总数：%d" % len(files))

    def check(p: Path):
        try:
            with open(p, "rb") as fh:
                head = fh.read(400)
            return (p, looks_like_html_error(head))
        except Exception as e:
            return (p, None)

    bads = []
    errs = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for p, bad in ex.map(check, files):
            if bad is True:
                bads.append(p)
            elif bad is None:
                errs += 1

    stems = sorted({p.relative_to(att).parts[0] for p in bads
                    if len(p.relative_to(att).parts) >= 2})
    print("HTML 假文件：%d   读取失败：%d   涉及案号：%d" % (len(bads), errs, len(stems)))
    for p in bads[:5]:
        print("  样例：%s (%d B)" % (p.relative_to(att), p.stat().st_size))

    if not args.apply:
        print("干跑模式，未做任何修改。加 --apply 删除假文件。")
        return 0

    deleted = 0
    for p in bads:
        try:
            p.unlink()
            deleted += 1
        except Exception as e:
            print("  删除失败 %s: %r" % (p, e))
    print("已删除假文件：%d" % deleted)

    if args.reset_ledger:
        db = out_dir / "_form_attach_state.sqlite3"
        if not db.exists():
            print("账本不存在，跳过重置：%s" % db)
            return 0
        c = sqlite3.connect(db, timeout=60)
        c.execute("PRAGMA busy_timeout=60000")
        now = time.time()
        cur = c.executemany(
            "UPDATE cases SET status='stalled', detail='attach-repair', updated_at=? "
            "WHERE stem=? AND status IN ('done','nomark')",
            [(now, s) for s in stems])
        c.commit()
        print("账本重置 stalled：%d 条（重跑守护进程即续传重下）" % (c.total_changes,))
        c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
