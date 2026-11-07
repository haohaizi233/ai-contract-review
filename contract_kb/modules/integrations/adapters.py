# -*- coding: utf-8 -*-
"""企业外部异构系统防腐适配器（Anti-Corruption Adapters）。

封装泛微 e9 SQL Server、新业务系统 tender MySQL、MinIO 对象存储及企微微盘只读同步，
全部支持依赖注入模拟（Mockable `connection_factory` / `http_opener`）、参数化只读查询、
增量水位线（Watermark）推进与 `dry_run` 无副作用预演。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from ..cases.reconciliation import (
    SafeJsonCache,
    reconcile_source_records,
)
from ...domain.models.canonical_contract import MergeOutcome, SourceRecordMapping
from ...application.integrations.sync_service import SyncBatchConflictError


@dataclass
class SyncWatermark:
    """增量同步水位线状态。"""

    source_name: str
    last_cursor: str = ""
    synced_count: int = 0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SyncWatermarkStore:
    """基于 SafeJsonCache 的增量同步水位线持久化器。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def get(self, source_name: str) -> SyncWatermark:
        data = SafeJsonCache.load(self.path) or {}
        entry = data.get(source_name) or {}
        return SyncWatermark(
            source_name=source_name,
            last_cursor=str(entry.get("last_cursor") or ""),
            synced_count=int(entry.get("synced_count") or 0),
            updated_at=float(entry.get("updated_at") or 0.0),
        )

    def advance(
        self,
        source_name: str,
        new_cursor: str,
        batch_count: int,
        *,
        dry_run: bool = False,
    ) -> SyncWatermark:
        current = self.get(source_name)
        nxt = SyncWatermark(
            source_name=source_name,
            last_cursor=str(new_cursor),
            synced_count=current.synced_count + int(batch_count),
            updated_at=time.time(),
        )
        if not dry_run:
            data = SafeJsonCache.load(self.path) or {}
            data[source_name] = nxt.to_dict()
            SafeJsonCache.save(self.path, data)
        return nxt


class E9SqlServerAdapter:
    """泛微 e9 SQL Server 只读防腐适配器（通过参数化游标抽取，零拼接注入风险）。"""

    source_name = "weaver_e9"

    def __init__(self, connection_factory: Callable[[], Any]):
        self._conn_factory = connection_factory

    def fetch_contracts_since(
        self, last_request_id: int = 0, limit: int = 500
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT TOP (%s) r.requestid, r.requestmark AS contract_no, "
            "r.requestname AS title, r.createdate "
            "FROM workflow_requestbase r "
            "WHERE r.requestid > %s AND r.requestmark IS NOT NULL AND r.requestmark <> '' "
            "ORDER BY r.requestid ASC"
        )
        with self._conn_factory() as conn:
            cur = conn.cursor()
            cur.execute(sql, (int(limit), int(last_request_id)))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


class TenderMysqlAdapter:
    """新业务系统 tender MySQL 只读防腐适配器。"""

    source_name = "tender_mysql"

    def __init__(self, connection_factory: Callable[[], Any]):
        self._conn_factory = connection_factory

    def fetch_contracts_since(
        self, last_id: int = 0, limit: int = 500
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT id, contract_no, contract_name AS title, "
            "customer_name AS party_a, company_name AS party_b, "
            "total_amount, update_time "
            "FROM crm_contract "
            "WHERE id > %s AND is_deleted = 0 "
            "ORDER BY id ASC LIMIT %s"
        )
        with self._conn_factory() as conn:
            cur = conn.cursor()
            cur.execute(sql, (int(last_id), int(limit)))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


class MinioFileAdapter:
    """受控对象存储流式下载与 SHA-256 完整性校验适配器。"""

    def __init__(
        self,
        max_bytes: int = 50 * 1024 * 1024,
        opener: Callable[[str, float], Any] | None = None,
    ):
        self.max_bytes = max_bytes
        self._opener = opener

    def download_verified(
        self,
        url: str,
        dest_path: str | Path,
        *,
        expected_sha256: str | None = None,
        timeout: float = 30.0,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if dry_run:
            return {"status": "DRY_RUN", "url": url, "bytes": 0, "sha256": None}

        import urllib.request

        open_fn = self._opener or (lambda u, t: urllib.request.urlopen(u, timeout=t))
        target = Path(dest_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        hasher = hashlib.sha256()
        total = 0

        try:
            with open_fn(url, timeout) as resp, tmp.open("wb") as out:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.max_bytes:
                        raise ValueError(
                            f"远端附件体积超过安全上限（{total} > {self.max_bytes}）"
                        )
                    hasher.update(chunk)
                    out.write(chunk)

            digest = hasher.hexdigest()
            if expected_sha256 and digest.lower() != expected_sha256.lower():
                raise ValueError(
                    f"附件 SHA-256 校验不匹配：expected={expected_sha256}, actual={digest}"
                )
            tmp.replace(target)
            return {
                "status": "DOWNLOADED",
                "bytes": total,
                "sha256": digest,
                "path": str(target),
            }
        except Exception:
            if tmp.exists():
                tmp.unlink()
            raise


class WeDriveReadOnlyAdapter:
    """企业微信微盘只读受控适配器（严禁暴露任何上传/删除/改名/权限变更方法）。"""

    ALLOWED_READONLY_MODES = frozenset({"DRY_RUN", "INCREMENTAL_SYNC"})

    def __init__(
        self,
        list_files_fn: Callable[[str], list[dict[str, Any]]],
        mode: str = "DRY_RUN",
    ):
        if mode not in self.ALLOWED_READONLY_MODES:
            raise ValueError(
                f"企微微盘适配器仅允许只读模式：{self.ALLOWED_READONLY_MODES}"
            )
        self._list_files_fn = list_files_fn
        self.mode = mode

    def probe_docx_inventory(self, space_id: str) -> list[dict[str, Any]]:
        if not space_id or not space_id.strip():
            raise ValueError("space_id 不能为空")
        items = self._list_files_fn(space_id)
        return [
            {
                "fileid": str(it.get("fileid") or ""),
                "file_name": str(it.get("file_name") or ""),
                "file_size": int(it.get("file_size") or 0),
                "mtime": int(it.get("mtime") or 0),
                "sha": str(it.get("sha") or ""),
                "sync_mode": self.mode,
            }
            for it in items
            if str(it.get("file_name") or "").lower().endswith(".docx")
        ]


class ContractIntegrationOrchestrator:
    """增量抽取 + 交叉对账 + 标准合同入库编排器（支持 dry_run 零副作用预演）。"""

    def __init__(
        self,
        storage: Any,
        watermark_store: SyncWatermarkStore,
        e9_adapter: E9SqlServerAdapter | None = None,
        tender_adapter: TenderMysqlAdapter | None = None,
    ):
        self.storage = storage
        self.watermark_store = watermark_store
        self.e9_adapter = e9_adapter
        self.tender_adapter = tender_adapter

    def sync_incremental_batch(
        self,
        *,
        tenant_id: str = "default",
        batch_size: int = 200,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        e9_wm = self.watermark_store.get("weaver_e9")
        td_wm = self.watermark_store.get("tender_mysql")

        e9_rows = (
            self.e9_adapter.fetch_contracts_since(
                int(e9_wm.last_cursor or 0), limit=batch_size
            )
            if self.e9_adapter
            else []
        )
        td_rows = (
            self.tender_adapter.fetch_contracts_since(
                int(td_wm.last_cursor or 0), limit=batch_size
            )
            if self.tender_adapter
            else []
        )

        canonical_items, diff_report = reconcile_source_records(
            e9_rows, td_rows, tenant_id=tenant_id, dry_run=dry_run
        )

        persisted = 0
        if not dry_run and canonical_items:
            with self.storage.unit_of_work():
                for cc in canonical_items:
                    cno_str = cc.contract_number.raw if cc.contract_number else None
                    existing_dict = None
                    find_by_cno = getattr(
                        self.storage, "find_canonical_by_contract_number", None
                    )
                    if find_by_cno and cno_str:
                        existing_dict = find_by_cno(tenant_id, cno_str)
                    if not existing_dict:
                        for m in cc.source_mappings:
                            existing_dict = self.storage.find_canonical_by_source(
                                tenant_id, m.source_system, m.external_id
                            )
                            if existing_dict:
                                break

                    if existing_dict:
                        existing_mappings = [
                            SourceRecordMapping(**m)
                            for m in existing_dict.get("source_mappings", [])
                        ]
                        existing_source_keys = {
                            (m.source_system, m.external_id): m
                            for m in existing_mappings
                        }
                        for new_m in cc.source_mappings:
                            existing_source_keys[
                                (new_m.source_system, new_m.external_id)
                            ] = new_m

                        cc.contract_id = existing_dict["contract_id"]
                        cc.source_mappings = list(existing_source_keys.values())
                        expected_ver = int(existing_dict.get("version", 1))
                        cc.version = expected_ver + 1
                        outcome = MergeOutcome(
                            contract=cc, is_new=False, version_incremented=True
                        )
                        saved = self.storage.save_canonical_version(
                            outcome, expected_version=expected_ver
                        )
                        if not saved:
                            raise SyncBatchConflictError(
                                f"增量合流并发版本冲突：合同 {cno_str} 期望版本 {expected_ver}"
                            )
                    else:
                        outcome = MergeOutcome(
                            contract=cc, is_new=True, version_incremented=False
                        )
                        saved = self.storage.save_canonical_version(
                            outcome, expected_version=None
                        )
                        if not saved:
                            raise SyncBatchConflictError(
                                f"增量合流并发版本冲突：新建合同 {cno_str} 失败"
                            )
                    persisted += 1

        if e9_rows:
            max_req = max(int(r.get("requestid") or 0) for r in e9_rows)
            self.watermark_store.advance(
                "weaver_e9", str(max_req), len(e9_rows), dry_run=dry_run
            )
        if td_rows:
            max_id = max(int(r.get("id") or 0) for r in td_rows)
            self.watermark_store.advance(
                "tender_mysql", str(max_id), len(td_rows), dry_run=dry_run
            )

        return {
            "dry_run": dry_run,
            "persisted_canonical_count": persisted,
            "reconciliation": diff_report.to_dict(),
        }
