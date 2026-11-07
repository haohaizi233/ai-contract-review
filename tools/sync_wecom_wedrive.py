# -*- coding: utf-8 -*-
"""企业微信微盘（WeCom WeDrive）动态同步与 RAG 自动增量热更新服务。

核心机制（零重新训练、零停机部署）：
1. 监听/拉取：通过企微微盘 API 或微盘本地同步盘扫描最新 DOCX 模板；
2. 增量感知：基于 SHA-256 文件哈希比对，无变动文件 0 成本跳过；
3. 热更新索引：对新增/修改的文件执行条款解析、FTS5 全文写入与向量更新；
4. 零停机热加载：直接提交 SQLite/PostgreSQL 事务，下一毫秒检索即可命中最新版本。

用法：
  python tools/sync_wecom_wedrive.py --local-dir "C:/Users/.../微盘同步目录"  # 本地微盘目录同步
  python tools/sync_wecom_wedrive.py --daemon --interval 300                 # 后台守护进程（每5分钟自动同步）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from contract_kb import config  # noqa: E402
from contract_kb.storage import Storage, create_storage  # noqa: E402
from contract_kb.ingest import import_template_file, security_check_file  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("WeDriveSync")


class WeDriveSyncManager:
    """企业微信微盘同步管理器。"""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = db_path
        self.storage = Storage(db_path) if db_path is not None else create_storage()

    def get_existing_hashes(self) -> dict[str, str]:
        """从知识库读取所有已激活母本的 SHA-256 与文件路径。"""
        return self.storage.list_active_document_hashes()

    def sync_from_directory(self, watch_dir: str | Path) -> dict:
        """从微盘本地同步目录执行增量比对与热更新。"""
        watch_path = Path(watch_dir)
        if not watch_path.exists():
            logger.error(f"指定的微盘目录不存在: {watch_path}")
            return {"error": "directory_not_found"}

        logger.info(f"开始扫描微盘目录: {watch_path}")
        existing_hashes = self.get_existing_hashes()

        # 扫描所有 .docx 文件
        docx_files = [
            f
            for f in watch_path.rglob("*.docx")
            if not f.name.startswith("~$") and not f.name.startswith(".")
        ]

        logger.info(f"扫描到 {len(docx_files)} 份 DOCX 模板文件")

        stats = {
            "scanned": len(docx_files),
            "skipped_identical": 0,
            "updated": 0,
            "inserted": 0,
            "failed": 0,
            "details": [],
        }

        for file_path in docx_files:
            try:
                # 1. 快速计算 SHA-256 文件指纹
                file_bytes = file_path.read_bytes()
                sha256 = hashlib.sha256(file_bytes).hexdigest()

                # 2. 如果指纹完全相同，直接秒级跳过（无变动，零开销）
                if sha256 in existing_hashes:
                    stats["skipped_identical"] += 1
                    continue

                logger.info(
                    f"⚡ 检测到微盘模板更新: {file_path.name} (SHA: {sha256[:10]}...)"
                )

                # 3. 安全检查与解析
                sec = security_check_file(file_path)
                if not sec.passed:
                    logger.warning(f"文件安全检查未通过，跳过: {file_path.name}")
                    stats["failed"] += 1
                    continue

                rec, err = import_template_file(file_path)
                if rec is None:
                    logger.error(f"解析失败: {file_path.name} - {err}")
                    stats["failed"] += 1
                    continue

                # 4. 执行在线热更新（自动更新文档、条款、FTS5 全文索引与向量表）
                doc_dict = rec.to_dict()
                doc_id, is_new = self.storage.upsert_document(doc_dict)

                action = "新模板入库" if is_new else "旧模板版本迭代热替换"
                logger.info(f"✅ [RAG热生效] {action}: [{doc_id}] {file_path.name}")

                if is_new:
                    stats["inserted"] += 1
                else:
                    stats["updated"] += 1

                stats["details"].append(
                    {
                        "file": file_path.name,
                        "document_id": doc_id,
                        "action": action,
                        "clause_count": len(rec.clauses or []),
                        "timestamp": time.time(),
                    }
                )

            except Exception as e:
                logger.error(f"处理文件异常: {file_path.name} - {e}", exc_info=True)
                stats["failed"] += 1

        logger.info(
            f"微盘同步完成：扫描 {stats['scanned']} 份，未变动跳过 {stats['skipped_identical']} 份，"
            f"新增/更新入库 {stats['inserted'] + stats['updated']} 份，失败 {stats['failed']} 份。"
        )
        return stats

    def run_daemon(self, watch_dir: str | Path, interval_seconds: int = 300):
        """后台轻量守护进程模式：定时增量轮询微盘变动。"""
        logger.info(f"启动企微微盘同步守护进程，轮询间隔: {interval_seconds} 秒")
        while True:
            try:
                self.sync_from_directory(watch_dir)
            except Exception as e:
                logger.error(f"守护进程轮询出错: {e}")
            time.sleep(interval_seconds)


def main():
    parser = argparse.ArgumentParser(
        description="企业微信微盘 RAG 增量热更新自动化工具"
    )
    parser.add_argument(
        "--local-dir", default=str(config.TEMPLATE_DIR), help="微盘本地映射目录路径"
    )
    parser.add_argument(
        "--daemon", action="store_true", help="以常驻守护进程模式运行定时轮询"
    )
    parser.add_argument(
        "--interval", type=int, default=300, help="轮询间隔秒数（默认 300 秒）"
    )
    args = parser.parse_args()

    manager = WeDriveSyncManager()
    if args.daemon:
        manager.run_daemon(args.local_dir, args.interval)
    else:
        res = manager.sync_from_directory(args.local_dir)
        print("\n同步摘要:", json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
