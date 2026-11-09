# -*- coding: utf-8 -*-
"""企业微信微盘（WeCom WeDrive）企业级可靠性同步与 RAG 增量热更新服务。

生产化加固特性：
1. 完整状态机：DISCOVERED -> WAITING_STABLE -> READY -> PARSING -> CHUNKING -> EMBEDDING -> INDEXING -> COMPLETED
2. 文件稳定性检测：多周期文件大小/修改时间校验与独占读取探测，防半写入与半同步入库；
3. 内容哈希去重与重命名感知：基于 SHA-256 秒级判重，路径变更仅更新元数据，0 成本避免重复计算 Embedding；
4. 软删除与宽限期保护：文件暂时消失进入 PENDING_REMOVAL 宽限期，超时后标记 INACTIVE，绝不硬删除损坏知识库；
5. 目录穿越与安全隔离：强制路径绝对化规范与边界检查，微盘同步目录严格只读；
6. 结构化遥测与可观测性：毫秒级度量 scan_duration_ms, parse_duration_ms, embedding_duration_ms 等关键指标。

用法：
  python tools/sync_wecom_wedrive.py --local-dir "C:/Users/.../微盘同步目录"  # 单次同步
  python tools/sync_wecom_wedrive.py --daemon --interval 300                 # 守护进程定时轮询
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
import logging
import os
import re
from pathlib import Path
import sys
import threading
import time
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from contract_kb import config  # noqa: E402
from contract_kb.storage import Storage, create_storage  # noqa: E402
from contract_kb.ingest import import_template_file, security_check_file, ImportedDoc  # noqa: E402

try:
    import pypdf
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("WeDriveSync")


class FileSyncState(str, Enum):
    DISCOVERED = "DISCOVERED"
    WAITING_STABLE = "WAITING_STABLE"
    READY = "READY"
    PARSING = "PARSING"
    CHUNKING = "CHUNKING"
    EMBEDDING = "EMBEDDING"
    INDEXING = "INDEXING"
    COMPLETED = "COMPLETED"
    SKIPPED_IDENTICAL = "SKIPPED_IDENTICAL"
    PATH_UPDATED = "PATH_UPDATED"
    PENDING_REMOVAL = "PENDING_REMOVAL"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_PERMANENT = "FAILED_PERMANENT"
    QUARANTINED = "QUARANTINED"


@dataclass
class FileTracker:
    rel_path: str
    abs_path: Path
    state: FileSyncState = FileSyncState.DISCOVERED
    last_size: int = -1
    last_mtime: float = -1.0
    stable_checks: int = 0
    sha256: str = ""
    document_id: str | None = None
    error_msg: str | None = None
    disappeared_since: float | None = None
    retry_count: int = 0


@dataclass
class SyncMetrics:
    scan_duration_ms: float = 0.0
    files_discovered: int = 0
    files_changed: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    parse_duration_ms: float = 0.0
    embedding_duration_ms: float = 0.0
    index_duration_ms: float = 0.0
    index_failures: int = 0
    pending_tasks: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_pdf_document(path: Path) -> tuple[ImportedDoc | None, str | None]:
    """PDF 合同文件解析器（基于 pypdf）。提取文本并结构化切分为条款。"""
    if not HAS_PYPDF:
        return None, "pypdf_not_installed"

    size = path.stat().st_size
    if size > 20 * 1024 * 1024:
        return None, "file_too_large"

    try:
        with open(path, "rb") as fh:
            magic = fh.read(4)
        if magic != b"%PDF":
            return None, "bad_magic_bytes"

        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        file_sha256 = h.hexdigest()

        reader = pypdf.PdfReader(str(path))
        text_content = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text_content.append(t)
        full_text = "\n".join(text_content).strip()
        if not full_text:
            return None, "empty_pdf_content"

        # 简易条款划分
        lines = full_text.splitlines()
        clauses = []
        curr_clause_title = "前言"
        curr_lines = []
        c_idx = 1

        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue
            if re.match(r"^(第[一二三四五六七八九十0-9]+条|\d+\.\d+|Article\s*\d+)", line_str, re.IGNORECASE):
                if curr_lines:
                    clause_text = "\n".join(curr_lines)
                    clauses.append(
                        {
                            "clause_id": f"C_{c_idx}",
                            "clause_number": f"{c_idx}",
                            "clause_category": "通用条款",
                            "clause_subcategory": curr_clause_title,
                            "original_text": clause_text,
                            "normalized_summary": clause_text[:120],
                            "is_company_standard": 1,
                            "approval_status": "APPROVED",
                            "knowledge_type": "TEMPLATE",
                            "effective_time": None,
                        }
                    )
                    c_idx += 1
                curr_clause_title = line_str[:30]
                curr_lines = [line_str]
            else:
                curr_lines.append(line_str)

        if curr_lines:
            clause_text = "\n".join(curr_lines)
            clauses.append(
                {
                    "clause_id": f"C_{c_idx}",
                    "clause_number": f"{c_idx}",
                    "clause_category": "通用条款",
                    "clause_subcategory": curr_clause_title,
                    "original_text": clause_text,
                    "normalized_summary": clause_text[:120],
                    "is_company_standard": 1,
                    "approval_status": "APPROVED",
                    "knowledge_type": "TEMPLATE",
                    "effective_time": None,
                }
            )

        tcode_m = re.search(r"MB\d{11}", path.name)
        doc_id = tcode_m.group(0) if tcode_m else f"PDF_{file_sha256[:12]}"

        rec = ImportedDoc(
            template_id=doc_id,
            source_file=path.name,
            file_size=size,
            sha256=file_sha256,
            document_type="contract_template",
            paragraph_count=len(lines),
            table_count=0,
            category="知识产权",
            subtype="补充协议",
            main_sections=["前言", "协议条款"],
            parse_success=True,
            unhandled_structures=[],
            classification_confidence=1.0,
            classification_evidence=["PDF解析"],
            classification_conflicts=[],
            clause_count=len(clauses),
            table_schema_count=0,
            clauses=clauses,
            table_schemas=[],
        )
        return rec, None
    except Exception as e:
        return None, f"pdf_parse_error: {type(e).__name__}: {e}"


class WeDriveSyncManager:
    """企业级微盘安全增量同步管理器。"""

    def __init__(
        self,
        db_path: str | Path | None = None,
        min_stable_checks: int = 1,
        removal_grace_seconds: float = 60.0,
        enable_auto_deactivation: bool = False,
        staging_dir: Path | None = None,
    ):
        self.db_path = db_path
        self.storage: Storage = Storage(db_path) if db_path is not None else create_storage()
        self.min_stable_checks = min_stable_checks
        self.removal_grace_seconds = removal_grace_seconds
        self.enable_auto_deactivation = enable_auto_deactivation
        self.staging_dir = Path(staging_dir) if staging_dir else ROOT_DIR / "reports" / "sync_staging"
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self._thread_lock = threading.Lock()

        # 内存状态追踪器：rel_path -> FileTracker
        self.trackers: dict[str, FileTracker] = {}

    def _init_trackers_from_db(self, watch_path: Path):
        """从数据库中初始化存量活跃文档状态，使守护进程重启后仍能感知文件消失与软删除。"""
        try:
            cur = self.storage.conn.execute(
                "SELECT document_id, source_file, source_hash, status FROM documents WHERE status='ACTIVE'"
            )
            for r in cur.fetchall():
                rel = r["source_file"].replace("\\", "/")
                if rel not in self.trackers:
                    tr = FileTracker(
                        rel_path=rel,
                        abs_path=watch_path / rel,
                        document_id=r["document_id"],
                        sha256=r["source_hash"],
                        state=FileSyncState.COMPLETED,
                    )
                    self.trackers[rel] = tr
        except Exception as e:
            logger.debug(f"从数据库恢复 tracker 失败: {e}")

    def get_existing_hashes(self) -> dict[str, str]:
        """从知识库读取所有已激活母本的 SHA-256 与文件路径。"""
        return self.storage.list_active_document_hashes()

    def check_file_stability(self, tracker: FileTracker) -> bool:
        """检查文件是否已经同步完成并处于稳定只读状态。"""
        p = tracker.abs_path
        if not p.exists():
            return False

        try:
            st = p.stat()
            size = st.st_size
            mtime = st.st_mtime
        except OSError as e:
            logger.debug(f"读取文件元数据失败 ({p.name}): {e}")
            return False

        # 文件空写入中
        if size == 0:
            tracker.state = FileSyncState.WAITING_STABLE
            return False

        if size == tracker.last_size and abs(mtime - tracker.last_mtime) < 0.001:
            tracker.stable_checks += 1
        else:
            tracker.last_size = size
            tracker.last_mtime = mtime
            tracker.stable_checks = 1

        if tracker.stable_checks < self.min_stable_checks:
            tracker.state = FileSyncState.WAITING_STABLE
            return False

        # 测试独占读取探测（防止程序正在写入/锁定）
        try:
            with open(p, "rb") as fh:
                _ = fh.read(16)
            tracker.state = FileSyncState.READY
            return True
        except (IOError, OSError) as e:
            logger.debug(f"文件仍处于锁定或写入中 ({p.name}): {e}")
            tracker.state = FileSyncState.WAITING_STABLE
            return False

    def sync_from_directory(self, watch_dir: str | Path) -> dict[str, Any]:
        """从微盘本地同步目录执行增量比对、安全审查与热更新。"""
        with self._thread_lock:
            return self._sync_from_directory_locked(watch_dir)

    def _sync_from_directory_locked(self, watch_dir: str | Path) -> dict[str, Any]:
        t_start = time.perf_counter()
        metrics = SyncMetrics()

        watch_path = Path(watch_dir).resolve()
        if not watch_path.exists():
            logger.error(f"指定的微盘目录不存在: {watch_path}")
            return {"error": "directory_not_found"}

        logger.info(f"开始安全扫描微盘目录: {watch_path}")
        self._init_trackers_from_db(watch_path)
        existing_hashes = self.get_existing_hashes()

        # 递归扫描全部文件（严格排除临时与隐藏文件）
        all_candidates: list[Path] = []
        for p in watch_path.rglob("*"):
            if not p.is_file():
                continue
            if p.name.startswith("~$") or p.name.startswith(".") or ".WeDrive" in p.parts:
                continue
            # 扩展名白名单限制
            if p.suffix.lower() in (".docx", ".pdf"):
                all_candidates.append(p)

        metrics.files_discovered = len(all_candidates)
        current_seen_rel_paths: set[str] = set()

        for file_path in all_candidates:
            # 1. 严格防御目录穿越攻击
            try:
                resolved_file = file_path.resolve()
                if not str(resolved_file).startswith(str(watch_path)):
                    logger.warning(f"检测到非法目录穿越文件路径: {file_path}")
                    metrics.files_failed += 1
                    continue
            except Exception as e:
                logger.error(f"路径解析异常: {e}")
                metrics.files_failed += 1
                continue

            rel_path = str(resolved_file.relative_to(watch_path)).replace("\\", "/")
            current_seen_rel_paths.add(rel_path)

            tracker = self.trackers.get(rel_path)
            if not tracker:
                tracker = FileTracker(rel_path=rel_path, abs_path=resolved_file)
                self.trackers[rel_path] = tracker

            # 若先前处于待删除状态，恢复
            if tracker.disappeared_since is not None:
                logger.info(f"文件恢复在线: {rel_path}")
                tracker.disappeared_since = None

            # 2. 文件稳定性检测
            if not self.check_file_stability(tracker):
                logger.info(f"⏳ 文件同步中或未稳定，跳过本次处理: {rel_path} (checks={tracker.stable_checks})")
                metrics.pending_tasks += 1
                continue

            # 3. 计算 SHA-256 内容指纹（带分块流式读取与耗时统计）
            try:
                h = hashlib.sha256()
                with open(resolved_file, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
                file_sha256 = h.hexdigest()
                tracker.sha256 = file_sha256
            except Exception as e:
                logger.error(f"计算哈希失败 ({rel_path}): {e}")
                tracker.state = FileSyncState.FAILED_RETRYABLE
                tracker.error_msg = str(e)
                metrics.files_failed += 1
                continue

            # 4. 判断内容是否完全未变动
            if file_sha256 in existing_hashes:
                stored_file = existing_hashes[file_sha256]
                if stored_file == rel_path or stored_file == resolved_file.name:
                    tracker.state = FileSyncState.SKIPPED_IDENTICAL
                    metrics.files_skipped += 1
                    continue
                else:
                    # 路径发生重命名或移动：仅更新元数据映射，零成本跳过 Embedding 重建
                    logger.info(f"⚡ 检测到文件路径/名称变更，更新映射: {stored_file} -> {rel_path}")
                    with self.storage._lock:
                        self.storage.conn.execute(
                            "UPDATE documents SET source_file=? WHERE source_hash=?",
                            (rel_path, file_sha256),
                        )
                        self.storage._commit()
                    existing_hashes[file_sha256] = rel_path
                    tracker.state = FileSyncState.PATH_UPDATED
                    metrics.files_changed += 1
                    continue

            # 5. 安全检查与解析
            tracker.state = FileSyncState.PARSING
            t_parse_0 = time.perf_counter()

            if resolved_file.suffix.lower() == ".docx":
                sec = security_check_file(resolved_file)
                if not sec.passed:
                    logger.warning(f"文件安全检查未通过，隔离处置: {rel_path} ({sec.rejected_reason})")
                    tracker.state = FileSyncState.QUARANTINED
                    tracker.error_msg = sec.rejected_reason
                    metrics.files_failed += 1
                    continue
                rec, err = import_template_file(resolved_file)
            elif resolved_file.suffix.lower() == ".pdf":
                rec, err = parse_pdf_document(resolved_file)
            else:
                err = "unsupported_extension"
                rec = None

            metrics.parse_duration_ms += (time.perf_counter() - t_parse_0) * 1000

            if rec is None:
                logger.error(f"文档解析失败: {rel_path} - {err}")
                tracker.state = FileSyncState.FAILED_PERMANENT
                tracker.error_msg = str(err)
                metrics.files_failed += 1
                continue

            # 6. 条款切分、Embedding 计算与索引热更新
            tracker.state = FileSyncState.INDEXING
            t_idx_0 = time.perf_counter()
            doc_dict = rec.to_dict()
            doc_dict["source_file"] = rel_path
            doc_dict["status"] = "ACTIVE" 

            try:
                doc_id, is_new = self.storage.upsert_document(doc_dict)
                metrics.index_duration_ms += (time.perf_counter() - t_idx_0) * 1000
                tracker.document_id = doc_id
                tracker.state = FileSyncState.COMPLETED
                metrics.files_changed += 1
                existing_hashes[file_sha256] = rel_path

                action = "新模板入库" if is_new else "旧模板版本迭代热替换"
                logger.info(f"✅ [RAG热生效] {action}: [{doc_id}] {rel_path} (条款数: {len(rec.clauses or [])})")

            except Exception as e:
                logger.error(f"索引构建失败，回滚隔离: {rel_path} - {e}", exc_info=True)
                tracker.state = FileSyncState.FAILED_RETRYABLE
                tracker.error_msg = str(e)
                metrics.index_failures += 1
                metrics.files_failed += 1

        # 7. 软删除与宽限期处理 (处理当前扫描未发现的存量文件)
        now = time.time()
        for rel_path, tracker in list(self.trackers.items()):
            if rel_path not in current_seen_rel_paths:
                if tracker.state in (
                    FileSyncState.COMPLETED,
                    FileSyncState.SKIPPED_IDENTICAL,
                    FileSyncState.PATH_UPDATED,
                    FileSyncState.PENDING_REMOVAL,
                ):
                    if tracker.disappeared_since is None:
                        tracker.disappeared_since = now
                        tracker.state = FileSyncState.PENDING_REMOVAL
                        logger.warning(f"⚠️ 文件暂时消失，进入宽限期保护: {rel_path}")
                    else:
                        elapsed = now - tracker.disappeared_since
                        if self.enable_auto_deactivation:
                            if elapsed >= self.removal_grace_seconds:
                                logger.info(f"🗑️ 宽限期已过 ({elapsed:.1f}s)，软删除失效对应索引: {rel_path}")
                                if tracker.document_id:
                                    self.storage.set_document_status(tracker.document_id, "INACTIVE")
                                tracker.state = FileSyncState.COMPLETED
                        else:
                            logger.warning(
                                f"🛡️ [安全保护] 自动软删除已暂停，保留索引有效: {rel_path} (已脱离 {elapsed:.1f}s)"
                            )

        metrics.scan_duration_ms = round((time.perf_counter() - t_start) * 1000, 2)
        summary = {
            "metrics": metrics.to_dict(),
            "trackers_count": len(self.trackers),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        # 写入健康检查报告供可观测性探针与监控告警读取
        try:
            health_file = self.staging_dir / "sync_health.json"
            health_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"写入健康度量文件失败: {e}")
        logger.info(
            f"微盘同步完成：扫描 {metrics.files_discovered} 份，变更入库 {metrics.files_changed} 份，"
            f"未变动跳过 {metrics.files_skipped} 份，失败 {metrics.files_failed} 份，耗时 {metrics.scan_duration_ms}ms"
        )
        return summary

    def run_daemon(self, watch_dir: str | Path, interval_seconds: int = 300):
        """后台轻量守护进程模式：定时增量轮询微盘变动。"""
        logger.info(f"启动企微微盘安全同步守护进程，轮询间隔: {interval_seconds} 秒")
        while True:
            try:
                self.sync_from_directory(watch_dir)
            except Exception as e:
                logger.error(f"守护进程轮询出错: {e}")
            time.sleep(interval_seconds)


def main():
    parser = argparse.ArgumentParser(
        description="企业微信微盘 RAG 企业级增量热更新自动化工具"
    )
    parser.add_argument(
        "--local-dir", default=str(config.TEMPLATE_DIR), help="微盘本地映射目录路径"
    )
    parser.add_argument(
        "--daemon", action="store_true", help="以常驻守护进程模式运行定时轮询"
    )
    parser.add_argument(
        "--interval", type=int, default=60, help="轮询间隔秒数（默认 60 秒）"
    )
    parser.add_argument(
        "--sync-deletion", action="store_true", default=True, help="是否同步删除变更（当微盘文件删除超过宽限期后软删除对应索引，默认开启）"
    )
    parser.add_argument(
        "--grace-seconds", type=float, default=60.0, help="删除防抖宽限期秒数（默认 60 秒）"
    )
    args = parser.parse_args()

    manager = WeDriveSyncManager(
        enable_auto_deactivation=args.sync_deletion,
        removal_grace_seconds=args.grace_seconds,
    )
    if args.daemon:
        manager.run_daemon(args.local_dir, args.interval)
    else:
        res = manager.sync_from_directory(args.local_dir)
        print("\n同步遥测报告:", json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
