# -*- coding: utf-8 -*-
"""Security and lifecycle retention tests for temporary upload files."""

from __future__ import annotations

import time
from pathlib import Path

from contract_kb.infrastructure.file_storage.retention import (
    collect_expired_uploads,
)


def test_collect_expired_uploads_dry_run_preserves_files(tmp_path: Path):
    """Verify dry_run identifies candidates without unlinking them."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    old_file = upload_dir / "old_contract.docx"
    old_file.write_text("old content", encoding="utf-8")
    # Simulate file from 2 days ago
    old_time = time.time() - (48 * 3600)
    import os
    os.utime(old_file, (old_time, old_time))

    now = time.time()
    result = collect_expired_uploads(upload_dir, now=now, retention_seconds=86400, dry_run=True)

    assert result["dry_run"] is True
    assert result["scanned_count"] == 1
    assert result["expired_count"] == 1
    assert result["deleted_count"] == 0
    assert old_file.exists(), "Dry-run must not delete expired files"


def test_collect_expired_uploads_live_purges_past_retention(tmp_path: Path):
    """Verify live run removes expired files and retains recent files."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    old_file = upload_dir / "old_doc.docx"
    old_file.write_text("expired", encoding="utf-8")
    old_time = time.time() - (48 * 3600)
    import os
    os.utime(old_file, (old_time, old_time))

    new_file = upload_dir / "recent_doc.docx"
    new_file.write_text("recent", encoding="utf-8")

    now = time.time()
    result = collect_expired_uploads(upload_dir, now=now, retention_seconds=86400, dry_run=False)

    assert result["dry_run"] is False
    assert result["scanned_count"] == 2
    assert result["expired_count"] == 1
    assert result["deleted_count"] == 1
    assert not old_file.exists(), "Expired file must be deleted"
    assert new_file.exists(), "Recent file must be preserved"


def test_active_leases_and_checkpoints_protected_from_cleanup(tmp_path: Path):
    """Verify files currently leased or referenced by recoverable checkpoints are preserved."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    leased_file = upload_dir / "in_flight_review.docx"
    leased_file.write_text("leased contract", encoding="utf-8")
    old_time = time.time() - (48 * 3600)
    import os
    os.utime(leased_file, (old_time, old_time))

    now = time.time()
    result = collect_expired_uploads(
        upload_dir,
        now=now,
        retention_seconds=86400,
        active_file_names={"in_flight_review.docx"},
        dry_run=False,
    )

    assert result["expired_count"] == 0
    assert result["retained_count"] == 1
    assert leased_file.exists(), "Active leased file must not be expired or deleted"
