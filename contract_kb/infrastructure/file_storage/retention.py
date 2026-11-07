# -*- coding: utf-8 -*-
"""Controlled temporary upload file lifecycle and safe retention management."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class PathTraversalSecurityError(RuntimeError):
    """Raised when an inspected upload path escapes the controlled storage root."""


def collect_expired_uploads(
    upload_dir: str | Path,
    now: float,
    *,
    retention_seconds: float = 86400.0,
    active_file_names: set[str] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Scan controlled upload directory and collect/purge files past retention horizon.

    - Verifies strict absolute path boundaries to prevent traversal attacks.
    - Preserves files currently referenced by active leases or resumable checkpoints.
    - Respects dry_run to safely audit candidates before purging.
    """
    root = Path(upload_dir).resolve()
    if not root.exists() or not root.is_dir():
        return {
            "dry_run": dry_run,
            "scanned_count": 0,
            "expired_count": 0,
            "retained_count": 0,
            "deleted_count": 0,
            "expired_files": [],
        }

    active_set = active_file_names or set()
    scanned = 0
    expired_files: list[dict[str, Any]] = []
    deleted_count = 0
    retained_count = 0

    for entry in root.rglob("*"):
        if not entry.is_file():
            continue

        resolved_entry = entry.resolve()
        # Path boundary containment check
        try:
            resolved_entry.relative_to(root)
        except ValueError:
            raise PathTraversalSecurityError(
                f"Path traversal detected: {resolved_entry} is outside controlled root {root}"
            )

        scanned += 1
        stat = entry.stat()
        mtime = stat.st_mtime
        age = now - mtime

        # Check if protected by active lease or recent checkpoint
        is_active = (entry.name in active_set) or (str(entry.relative_to(root)) in active_set)

        if is_active or age < retention_seconds:
            retained_count += 1
            continue

        # Candidate for expiration
        file_info = {
            "filename": entry.name,
            "path": str(resolved_entry),
            "relative_path": str(entry.relative_to(root)).replace("\\", "/"),
            "size": stat.st_size,
            "mtime": mtime,
            "age_seconds": round(age, 2),
        }
        expired_files.append(file_info)

        if not dry_run:
            try:
                entry.unlink()
                deleted_count += 1
            except OSError:
                pass

    return {
        "dry_run": dry_run,
        "scanned_count": scanned,
        "expired_count": len(expired_files),
        "retained_count": retained_count,
        "deleted_count": deleted_count,
        "expired_files": expired_files,
    }
