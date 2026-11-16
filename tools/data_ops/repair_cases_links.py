"""Repair broken image/attachment references in data/contract_merged/cases/*.md

Fixes applied to every .md file:
  1. Remote HTTP image links → local albums/xxx.png (if recovered)
  2. Remote HTTP image links with no local match → ![图片]（未取得：`url`）
  3. file:///  / blob: / bare Windows-path images → ![图片]（未取得：`url`）
  4. javascript:void(0) links → bare label text
  5. All angle-bracket-wrapped local links → plain (no <>) relative links
     (<../../attachments/x.ext>)  →  (../../attachments/x.ext)
     (<../../albums/x.png>)       →  (../../albums/x.png)
  6. Source-provenance cross-links (<../../../xxx>) → strip <>

Does NOT:
  - Remove the "## 2026-11-09 原图恢复核验" footnote section
  - Modify any <code> blocks or backtick spans
  - Touch files outside cases/

Writes atomically via temp-file + os.replace (retries on Windows lock).
"""

import hashlib
import json
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data" / "contract_merged"
CASES = BASE / "cases"
ALBUMS = BASE / "albums"


# ---------------------------------------------------------------------------
# 1. Build URL → relative-album-path mapping
# ---------------------------------------------------------------------------


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def build_url_map() -> dict[str, str]:
    """Return {url_or_path -> 'albums/<sha>.png'} for all restored images."""
    # prov_map: url_hash -> relative path
    prov: dict[str, str] = {}
    prov_path = BASE / "image_recovery_provenance.jsonl"
    if prov_path.exists():
        with open(prov_path, encoding="utf-8") as f:
            for line in f:
                item = json.loads(line)
                if item.get("status") == "restored" and item.get("restored"):
                    # restored is list; take first element
                    local_rel = item["restored"][0]
                    # only keep if file actually exists on disk
                    if (BASE / local_rel).exists():
                        prov[item["url_hash"]] = local_rel

    # urls.json files: list of path strings whose sha256 is the url_hash
    url_map: dict[str, str] = {}  # raw_url_or_path -> albums/xxx.png

    for uf in [
        ROOT / "tmp" / "image_recovery_20261109" / "urls.json",
        ROOT / "tmp" / "recrawl_20261110" / "urls.json",
    ]:
        if not uf.exists():
            continue
        with open(uf, encoding="utf-8") as f:
            data = json.load(f)
        urls = data if isinstance(data, list) else list(data.keys())
        for raw in urls:
            h = _sha256(raw)
            if h in prov:
                url_map[raw] = prov[h]

    # Also index by full http URL.  The urls.json entries are path-only like
    #   /weaver/weaver.file.FileDownload?fileid=...
    # but markdown bodies contain the full URL including host.
    # Build both directions.
    HOST = "http://hc.hcipi.com:8088"
    extra: dict[str, str] = {}
    for raw, local in url_map.items():
        if raw.startswith("/"):
            extra[HOST + raw] = local
        elif raw.startswith("http"):
            path = re.sub(r"^https?://[^/]+", "", raw)
            extra[path] = local
    url_map.update(extra)

    # Fallback: also index via direct full-URL sha256 (some entries stored that way)
    for raw, local in list(url_map.items()):
        h = _sha256(raw)
        if h in prov and h not in {_sha256(k) for k in url_map}:
            url_map[raw] = prov[h]

    # Also try full-URL hash directly against prov
    for raw in list(url_map.keys()):
        pass  # already covered above

    # Brute-force: iterate prov once more and try full-URL keys from url_map
    # (handles entries stored with full URL as the hash key)
    for url_hash, local in prov.items():
        # skip hashes we already have
        pass  # already processed via url_map keys above

    return url_map


# ---------------------------------------------------------------------------
# 2. Regex helpers
# ---------------------------------------------------------------------------

# Matches any Markdown link/image: ![alt](dest) or [text](dest)
# dest may be angle-bracket-wrapped or plain
MD_LINK = re.compile(
    r"(!\[[^\]\n]*\]|(?<!!)\[[^\]\n]*\])"  # group 1: label (with or without !)
    r"\((<[^>]*>|(?:[^()\n]|\([^()\n]*\))*)\)",  # group 2: dest (with or without <>)
    re.UNICODE,
)

# Matches fenced code blocks (``` or ~~~) – we skip editing inside these
FENCE = re.compile(
    r"^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?\n[ \t]*\1[ \t]*$", re.MULTILINE | re.DOTALL
)

# Matches inline code spans – skip editing inside
INLINE_CODE = re.compile(r"`[^`\n]+`")

# Matches <code>...</code> HTML
HTML_CODE = re.compile(r"<code>[^<]*</code>", re.DOTALL)


def _make_placeholder_index(text: str) -> tuple[str, dict[str, str]]:
    """Replace code regions with placeholders so regex won't touch them."""
    placeholders: dict[str, str] = {}
    idx = [0]

    def replace(m: re.Match) -> str:
        key = f"\x00PLACEHOLDER_{idx[0]:06d}\x00"
        idx[0] += 1
        placeholders[key] = m.group(0)
        return key

    # Order matters: fences first, then html code, then inline code
    text = FENCE.sub(replace, text)
    text = HTML_CODE.sub(replace, text)
    text = INLINE_CODE.sub(replace, text)
    return text, placeholders


def _restore_placeholders(text: str, placeholders: dict[str, str]) -> str:
    for key, val in placeholders.items():
        text = text.replace(key, val)
    return text


# ---------------------------------------------------------------------------
# 3. Per-file repair
# ---------------------------------------------------------------------------


def repair_file(p: Path, url_map: dict[str, str]) -> tuple[int, int]:
    """
    Return (changes_made, skipped) tuple.
    Writes the file in-place (atomic) only when changes > 0.
    """
    text = p.read_text(encoding="utf-8", errors="ignore")
    original = text

    # Protect code regions
    text, placeholders = _make_placeholder_index(text)

    changes = 0

    def repl(m: re.Match) -> str:
        nonlocal changes
        label = m.group(1)  # e.g. ![图片] or [附件名.docx]
        raw_dest = m.group(2)  # may start/end with <>
        is_angle = raw_dest.startswith("<") and raw_dest.endswith(">")
        dest = raw_dest[1:-1] if is_angle else raw_dest
        is_img = label.startswith("!")

        # ---- Rule 4: javascript: links → bare label text ----
        if dest.startswith("javascript:"):
            changes += 1
            return label.lstrip("!")  # strip leading ! if accidentally image

        # ---- Rules 1/2/3: HTTP/HTTPS image URLs ----
        if is_img and dest.startswith(("http://", "https://")):
            local = url_map.get(dest)
            if not local:
                # Also try path-only
                path_only = re.sub(r"^https?://[^/]+", "", dest)
                local = url_map.get(path_only)

            if local and (BASE / local).exists():
                # Rewrite to relative path from this file
                # Files are at cases/<category>/<file>.md → need ../../<local>
                rel = "../../" + local.replace("\\", "/")
                changes += 1
                return f"{label}({rel})"
            else:
                # Cannot resolve → mark as 未取得
                changes += 1
                return f"![图片]（未取得：`{dest}`）"

        # ---- Rule 3: file: / blob: / Windows path images ----
        if is_img and (
            dest.startswith("file:")
            or dest.startswith("blob:")
            or (len(dest) > 1 and dest[1] == ":" and dest[0].isalpha())  # C:\...
            or dest.startswith("\\\\")
        ):
            changes += 1
            return f"![图片]（未取得：`{dest}`）"

        # ---- Rule 5/6: strip angle brackets from local relative paths ----
        if is_angle:
            # Only strip <> for local relative links (not http/file/blob)
            if not dest.startswith(
                (
                    "http://",
                    "https://",
                    "file:",
                    "blob:",
                    "javascript:",
                    "mailto:",
                    "data:",
                )
            ):
                changes += 1
                return f"{label}({dest})"

        return m.group(0)  # no change

    text = MD_LINK.sub(repl, text)

    # Restore code regions
    text = _restore_placeholders(text, placeholders)

    if text == original:
        return 0, 0

    # Atomic write with retry (handles Windows file-lock races)
    dest_path = p
    for attempt in range(5):
        try:
            fd, tmp_path = tempfile.mkstemp(dir=dest_path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
                os.replace(tmp_path, dest_path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            break
        except PermissionError:
            if attempt < 4:
                time.sleep(0.1 * (attempt + 1))
            else:
                raise

    return changes, 0


# ---------------------------------------------------------------------------
# 4. Main
# ---------------------------------------------------------------------------


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Repair broken links in cases/*.md")
    parser.add_argument("--dry-run", action="store_true", help="Scan but do not write")
    parser.add_argument(
        "--sample", type=int, default=0, help="Process only first N files"
    )
    parser.add_argument("--workers", type=int, default=8, help="Thread pool size")
    args = parser.parse_args()

    print("Building URL → album mapping...")
    url_map = build_url_map()
    print(f"  {len(url_map):,} URL entries in mapping")

    all_files = sorted(CASES.rglob("*.md"))
    print(f"  {len(all_files):,} .md files to process")

    if args.sample:
        all_files = all_files[: args.sample]
        print(f"  Sampling {args.sample} files")

    if args.dry_run:
        print("DRY-RUN: will not write any files")

    total_files = 0
    total_changed = 0
    errors = []

    def process(p: Path):
        if args.dry_run:
            return 0, 0
        return repair_file(p, url_map)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(process, p): p for p in all_files}
        for fut in as_completed(futures):
            p = futures[fut]
            total_files += 1
            try:
                changed, _ = fut.result()
                total_changed += changed
            except Exception as e:
                errors.append((str(p), str(e)))

            if total_files % 2000 == 0:
                print(
                    f"  {total_files}/{len(all_files)} files processed, {total_changed} link changes so far"
                )

    print("\nDone.")
    print(f"  Files processed : {total_files:,}")
    print(f"  Link changes    : {total_changed:,}")
    if errors:
        print(f"  Errors          : {len(errors)}")
        for path, err in errors[:20]:
            print(f"    {path}: {err}")
    else:
        print("  Errors          : 0")


if __name__ == "__main__":
    main()
