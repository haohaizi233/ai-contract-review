import re
import csv
import json
import time
from pathlib import Path
from collections import Counter

MD_LINK = re.compile(r"(!?\[[^\]\n]*\])\((<[^>]*>|(?:[^()\n]|\([^()\n]*\))*)\)")


def rewrite_relative_links(body: str) -> str:
    def repl(m):
        link_prefix = m.group(1)
        dest = m.group(2)
        is_angle = dest.startswith("<") and dest.endswith(">")
        raw = dest[1:-1] if is_angle else dest
        # Relative paths starting with '../' need an additional '../' since files are moved 1 directory deeper
        if raw.startswith("../"):
            new_raw = "../" + raw
            new_dest = f"<{new_raw}>" if is_angle else new_raw
            return f"{link_prefix}({new_dest})"
        return m.group(0)

    return MD_LINK.sub(repl, body)


def main():
    start_time = time.time()
    base_dir = Path("data/contract_merged").resolve()
    cases_dir = base_dir / "cases"
    csv_path = base_dir / "contracts_classification.csv"
    jsonl_path = base_dir / "contracts_merged.jsonl"

    print("Step 1: Reading classification ledger...")
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        csv_rows = list(reader)
        fieldnames = list(reader.fieldnames)

    counts = Counter(r["业务分类代字"] for r in csv_rows)
    gt50_set = {k for k, v in counts.items() if v > 50}
    print(
        f"Total cases: {len(csv_rows)}, Categories > 50: {len(gt50_set)}, Categories <= 50: {len(counts) - len(gt50_set)}"
    )

    # Map each case to target folder
    case_folder_map = {}
    for r in csv_rows:
        prefix = r["业务分类代字"]
        folder = prefix if prefix in gt50_set else "其他业务"
        # Group key / contract no
        rel_md = r["案卷MD文件"]  # e.g. cases/xxx.md
        filename = Path(rel_md).name
        case_folder_map[filename] = folder

    # Step 2: Ensure target directories exist
    target_folders = set(case_folder_map.values())
    print(f"\nStep 2: Creating {len(target_folders)} target directories...")
    for folder in target_folders:
        (cases_dir / folder).mkdir(parents=True, exist_ok=True)

    # Step 3: Move and rewrite markdown files
    print("\nStep 3: Moving and rewriting relative links in markdown files...")
    processed_count = 0

    for filename, folder in case_folder_map.items():
        src_file = cases_dir / filename
        tgt_file = cases_dir / folder / filename

        if not src_file.exists():
            if tgt_file.exists():
                # Already moved in a previous run
                processed_count += 1
                continue
            else:
                raise FileNotFoundError(f"Source file not found: {src_file}")

        body = src_file.read_text(encoding="utf-8")
        new_body = rewrite_relative_links(body)

        tgt_file.write_text(new_body, encoding="utf-8")
        src_file.unlink()
        processed_count += 1

        if processed_count % 5000 == 0 or processed_count == len(case_folder_map):
            print(f"  Processed {processed_count}/{len(case_folder_map)} files...")

    # Step 4: Update contracts_merged.jsonl
    print("\nStep 4: Updating contracts_merged.jsonl file paths...")
    with open(jsonl_path, "r", encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]

    for rec in records:
        old_file = rec.get("文件", "")  # e.g. cases/xxx.md
        fname = Path(old_file).name
        folder = case_folder_map.get(fname, "其他业务")
        rec["文件"] = f"cases/{folder}/{fname}"

    with open(jsonl_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"Updated {len(records)} records in contracts_merged.jsonl.")

    # Step 5: Update contracts_classification.csv
    print("\nStep 5: Updating contracts_classification.csv...")
    if "归档文件夹" not in fieldnames:
        # Insert '归档文件夹' right after '业务分类名称'
        cat_idx = fieldnames.index("业务分类名称")
        fieldnames.insert(cat_idx + 1, "归档文件夹")

    for r in csv_rows:
        fname = Path(r["案卷MD文件"]).name
        folder = case_folder_map.get(fname, "其他业务")
        r["归档文件夹"] = folder
        r["案卷MD文件"] = f"cases/{folder}/{fname}"

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)
    print(
        "Updated contracts_classification.csv with new file paths and 归档文件夹 column."
    )

    elapsed = time.time() - start_time
    print(f"\nMigration completed successfully in {elapsed:.2f} seconds!")


if __name__ == "__main__":
    main()
