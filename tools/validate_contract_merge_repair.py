"""Validate generated contract repair without trusting its completion counters."""

import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contract_merge_repair import MD_LINK


def validate(base):
    root = Path(__file__).resolve().parents[1]
    records = [
        json.loads(line)
        for line in (base / "contracts_merged.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    originals = [
        json.loads(line)
        for line in (root / "data/tender-case-export/_relations.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    supplement = [
        json.loads(line)
        for line in (root / "data/contract_merge_supplement/_relations.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    expected = {str(r["apply_id"]) for r in originals + supplement}
    actual = {x for r in records for x in r["申请ID"].split(";") if x}
    result = {
        "groups": len(records),
        "md_disk": len(list((base / "cases").rglob("*.md"))),
        "missing_application_ids": sorted(expected - actual),
        "unexpected_application_ids": sorted(actual - expected),
        "missing_md": [],
        "broken_links": [],
        "non_png": [],
        "invalid_images": [],
        "empty_application_sections": [],
        "link_references": 0,
        "source_md_inclusions": sum(r["来源MD数"] for r in records),
    }
    for r in records:
        p = base / r["文件"]
        if not p.exists():
            result["missing_md"].append(str(p))
            continue
        body = p.read_text(encoding="utf-8")
        for aid in filter(None, r["申请ID"].split(";")):
            if f"## 申请 {aid}：原始资料、业务明细与审批历史" not in body:
                result["empty_application_sections"].append(
                    {"case": r["分组键"], "id": aid}
                )
        for m in MD_LINK.finditer(body):
            dest = m[2].strip("<>")
            if urlsplit(dest).scheme or dest.startswith("#"):
                continue
            result["link_references"] += 1
            if not (p.parent / unquote(dest)).is_file():
                result["broken_links"].append({"md": r["文件"], "dest": dest})
    images = list((base / "albums").glob("*"))
    result["unique_images"] = len(images)
    for p in images:
        try:
            with Image.open(p) as im:
                if im.format != "PNG":
                    result["non_png"].append(p.name)
                im.verify()
        except Exception as e:
            result["invalid_images"].append({"path": p.name, "error": str(e)})
    result["unique_attachments"] = len(list((base / "attachments").glob("*")))
    for r in records:
        if r["合同编号"] == "BQ2025BM00285":
            body = (base / r["文件"]).read_text(encoding="utf-8")
            result["sample_has_old_history"] = (
                "旧审批日志（workflow_requestLog + workflow_approvelog" in body
            )
            result["sample_has_form"] = "## 审批表单" in body
    assert result["groups"] == result["md_disk"], result
    for key in [
        "missing_application_ids",
        "unexpected_application_ids",
        "missing_md",
        "broken_links",
        "non_png",
        "invalid_images",
        "empty_application_sections",
    ]:
        assert not result[key], (key, result[key][:5])
    assert result["sample_has_old_history"] and result["sample_has_form"]
    (base / "_validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    validate(Path(sys.argv[1]).resolve())
