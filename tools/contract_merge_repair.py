"""Source-preserving repair of the local two-database contract merge.

Never guesses a file namespace from an ID and never substitutes request counts
for opinions. Writes a new directory, leaving every input unchanged.
"""

import argparse
import csv
import hashlib
import html
import json
import os
import pickle
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image
from tender_case_quality import merge_oa_logs, valid_file

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MD_LINK = re.compile(r"(!?\[[^\]\n]*\])\((<[^>]*>|(?:[^()\n]|\([^()\n]*\))*)\)")


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def norm(s):
    return re.sub(r"\s+", "", str(s or "")).upper()


def group_sources(contracts, relations):
    groups = {}
    by_id = defaultdict(list)
    for c in contracts:
        key = c["合同编号"]
        groups[key] = {"legacy": c, "relations": []}
        for aid in c.get("tender_apply_ids", "").split(";"):
            if aid:
                by_id[aid].append(key)
    for r in relations:
        keys = by_id.get(str(r["apply_id"])) or [
            norm(r.get("contract_no")) or "UNNUMBERED_APPLY_" + str(r["apply_id"])
        ]
        for key in keys:
            groups.setdefault(key, {"legacy": {}, "relations": []})["relations"].append(
                r
            )
    return groups


def safe_name(key):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", key).strip(" .")[:100] or "unnamed"
    return name + "_" + hashlib.sha256(key.encode()).hexdigest()[:10]


class ResourceStore:
    def __init__(self, out):
        self.out = Path(out)
        self.refs, self.gaps = [], []
        self.cache = {}
        self.content_paths = {}

    def gap(self, case, source, reason, original=""):
        self.gaps.append(
            {"case": case, "source": source, "reason": reason, "original": original}
        )

    def add(self, path, case, source, image=False, evidence="source_reference"):
        path = Path(path).resolve()
        ref = {
            "case": case,
            "source": source,
            "original": str(path),
            "kind": "image" if image else "attachment",
            "evidence": evidence,
        }
        self.refs.append(ref)
        if not valid_file(path):
            ref.update(status="missing", local="")
            self.gap(case, source, "资源未取得或内容无效", str(path))
            return ""
        ck = (str(path), image)
        try:
            if ck not in self.cache:
                suffix = path.suffix.lower()
                if image:
                    with Image.open(path) as im:
                        im.verify()
                    with Image.open(path) as im:
                        fmt = im.format
                    if fmt != "PNG":
                        import io

                        buf = io.BytesIO()
                        with Image.open(path) as im:
                            im.convert(
                                "RGBA" if im.mode in ("RGBA", "P", "LA") else "RGB"
                            ).save(buf, format="PNG")
                        payload = buf.getvalue()
                        digest = hashlib.sha256(payload).hexdigest()
                    else:
                        payload = None
                        with path.open("rb") as f:
                            digest = hashlib.file_digest(f, "sha256").hexdigest()
                    suffix = ".png"
                else:
                    payload = None
                    with path.open("rb") as f:
                        digest = hashlib.file_digest(f, "sha256").hexdigest()
                content_key = (image, digest)
                relative = (
                    self.content_paths.get(content_key)
                    or ("albums/" if image else "attachments/") + digest + suffix
                )
                target = self.out / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    if payload is not None:
                        target.write_bytes(payload)
                    else:
                        try:
                            os.link(path, target)
                        except OSError:
                            shutil.copy2(path, target)
                self.cache[ck] = (relative, digest, target.stat().st_size)
                self.content_paths[content_key] = relative
            local, digest, size = self.cache[ck]
            ref.update(status="local", local=local, sha256=digest, size=size)
            return local
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            ref.update(status="invalid", local="")
            self.gap(case, source, "资源校验失败: " + str(exc), str(path))
            return ""


def rewrite_body(body, source_md, store, case, source):
    def rewrite(label, dest):
        dest = html.unescape(dest.strip().strip("<>"))
        if urlsplit(dest).scheme or dest.startswith("#"):
            if label.startswith("!"):
                store.gap(case, source, "正文图片仍为远程地址，未证明已本地化", dest)
            return f"{label}(<{dest}>)"
        p = (source_md.parent / unquote(dest)).resolve()
        # e9_form_attach_export writes attachments/... relative to the export
        # root, while the MD itself is sharded one directory below that root.
        if (
            source.startswith("e9:")
            and dest.startswith("attachments/")
            and not p.is_file()
        ):
            root_relative = (source_md.parent.parent / unquote(dest)).resolve()
            if root_relative.is_file():
                p = root_relative
        if p.suffix.lower() == ".md":
            # Provenance links remain explicit links to original evidence.
            if p.exists():
                return f"{label}(<{os.path.relpath(p, store.out / 'cases').replace(os.sep, '/')}>)"
            store.gap(case, source, "来源文档链接不存在", dest)
            return f"{label}（来源文档未取得：`{dest}`）"
        local = store.add(p, case, source, image=label.startswith("!"))
        return f"{label}(<../{local}>)" if local else f"{label}（未取得：`{dest}`）"

    body = MD_LINK.sub(lambda m: rewrite(m[1], m[2]), body)
    body = re.sub(r"<script\b[^>]*>.*?</script>", "", body, flags=re.I | re.S)

    def html_img(m):
        return rewrite("![意见图片]", m[2])

    body = re.sub(
        r'<img\b[^>]*?\bsrc\s*=\s*([\'"])(.*?)\1[^>]*>',
        html_img,
        body,
        flags=re.I | re.S,
    )
    return body


def _load_safe_cache(json_path: Path, legacy_pkl_path: Path):
    if json_path.exists():
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        if "logs" in raw:
            raw["logs"] = {int(k): v for k, v in raw["logs"].items()}
        for sk in ("covered", "covered_reqs", "covered_approvals"):
            if sk in raw:
                raw[sk] = set(raw[sk])
        return raw
    if legacy_pkl_path.exists():
        if os.environ.get("ALLOW_UNSAFE_PICKLE_CACHE", "0") != "1":
            raise PermissionError(
                f"Blocked unsafe pickle cache load: {legacy_pkl_path}"
            )
        with legacy_pkl_path.open("rb") as f:
            return pickle.load(f)
    return None


def load_oa(contracts, refresh):
    oa = _load_safe_cache(
        DATA / "tender-case-export/_cache/oa.json",
        DATA / "tender-case-export/_cache/oa.pkl",
    ) or {"logs": {}, "covered_reqs": set(), "covered_approvals": set()}
    extra_json = DATA / "contract_merge_oa_extra.json"
    extra = _load_safe_cache(extra_json, DATA / "contract_merge_oa_extra.pkl") or {
        "logs": {},
        "covered": set(),
    }
    ids = {
        int(r)
        for c in contracts
        for r in c.get("e9_requestids", "").split(";")
        if r.isdigit()
    }
    covered = set(oa.get("covered_reqs", set())) & set(
        oa.get("covered_approvals", set())
    )
    todo = sorted(ids - covered - extra["covered"])
    if refresh and todo:
        from live_db_merge import load_env
        import pytds

        env = load_env()
        # Read only; stage both sources before marking a chunk covered.
        with pytds.connect(
            dsn=env["DB_HOST"],
            port=int(env["DB_PORT"]),
            user=env["DB_USER"],
            password=env["DB_PWD_READONLY"],
            database="ecologyE9",
            timeout=180,
            login_timeout=30,
        ) as conn:
            cur = conn.cursor()
            for start in range(0, len(todo), 400):
                chunk = todo[start : start + 400]
                ph = ",".join(map(str, chunk))
                staged = defaultdict(list)
                queries = [
                    (
                        "workflow_requestLog",
                        f"SELECT requestid,nodeid,operator,operatedate,operatetime,LOGID,logtype,annexdocids,CAST(remark AS nvarchar(max)) AS remark FROM workflow_requestLog WHERE requestid IN ({ph})",
                    ),
                    (
                        "workflow_approvelog",
                        f"SELECT requestid,nodeid,operator,logdate AS operatedate,logtime AS operatetime,CAST(remark AS nvarchar(max)) AS remark FROM workflow_approvelog WHERE requestid IN ({ph})",
                    ),
                ]
                for table, query in queries:
                    cur.execute(query)
                    columns = [x[0] for x in cur.description]
                    for values in cur.fetchall():
                        row = dict(zip(columns, values))
                        row["source"] = table
                        if "LOGID" not in row:
                            row["LOGID"] = hashlib.sha256(
                                repr(values).encode()
                            ).hexdigest()
                            row["logtype"] = "审批意见"
                        staged[int(row["requestid"])].append(row)
                for rid in chunk:
                    extra["logs"][rid] = staged[rid]
                extra["covered"].update(chunk)
                temp = extra_json.with_suffix(".tmp")
                temp.write_text(
                    json.dumps(
                        {
                            "logs": {str(k): v for k, v in extra["logs"].items()},
                            "covered": sorted(extra["covered"]),
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                temp.replace(extra_json)
                print(
                    f"旧意见补齐 {min(start + 400, len(todo))}/{len(todo)}", flush=True
                )
    oa["logs"].update(extra["logs"])
    oa["available"] = covered | extra["covered"]
    return oa


def write_csv(path, records, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(records)


def render_gaps(gaps):
    def literal(value):
        return (
            html.escape(str(value))
            .replace("[", "&#91;")
            .replace("]", "&#93;")
            .replace("`", "&#96;")
        )

    return [
        "- "
        + literal(g["source"])
        + "："
        + literal(g["reason"])
        + ("；<code>" + literal(g["original"]) + "</code>" if g["original"] else "")
        for g in gaps
    ] or ["- 本轮未新增缺口；不据此推断源系统所有历史均已完整。"]


def build(out, refresh=False, legacy=None):
    if legacy is None:
        baseline = DATA / "contract_merged_baseline_20261105"
        legacy = baseline if baseline.exists() else DATA / "contract_merged"
    legacy = Path(legacy).resolve()
    contracts = read_csv(legacy / "contracts_merged.csv")
    src = DATA / "tender-case-export"
    relations = [
        json.loads(line_str)
        for line_str in (src / "_relations.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line_str.strip()
    ]
    supplement = DATA / "contract_merge_supplement"
    if (supplement / "_relations.jsonl").exists():
        additional = [
            json.loads(line_str)
            for line_str in (supplement / "_relations.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line_str.strip()
        ]
        original_ids = {str(r["apply_id"]) for r in relations}
        for r in additional:
            if str(r["apply_id"]) not in original_ids:
                r["_source_root"] = str(supplement)
                relations.append(r)
    groups = group_sources(contracts, relations)
    if out.exists():
        raise RuntimeError("输出目录已存在，拒绝覆盖: " + str(out))
    oa = load_oa(contracts, refresh)
    e9 = DATA / "e9-opinion-export"
    e9_index = {r["requestid"]: r for r in read_csv(e9 / "_index.csv")}
    candidates = defaultdict(list)
    for filename, kind in [
        ("attachments_merged.csv", "attachment"),
        ("albums_merged.csv", "image"),
    ]:
        for r in read_csv(legacy / filename):
            candidates[r["合同编号"]].append((r, kind))
    errors = defaultdict(list)
    for r in read_csv(src / "_errors.csv"):
        errors[r["申请ID"]].append(r)
    if (supplement / "_errors.csv").exists():
        for r in read_csv(supplement / "_errors.csv"):
            errors[r["申请ID"]].append(r)
    (out / "cases").mkdir(parents=True)
    store = ResourceStore(out)
    results = []
    source_records = []
    for count, (key, group) in enumerate(groups.items(), 1):
        c = group["legacy"]
        rs = group["relations"]
        start_gap = len(store.gaps)
        start_ref = len(store.refs)
        lines = [
            f"# 合同资料合并：{key}",
            "",
            "> 按来源保留原始资料、附件引用和审批正文。合同编号为汇总分组，申请 ID / requestid 为溯源主键；缺口不等于无数据。",
            "",
        ]
        if c:
            lines += [
                "## 主数据索引",
                "",
                f"- 合同名称：{c.get('合同名称', '')}",
                f"- 两库覆盖：{c.get('覆盖类别', '')}",
                "- 状态以以下各原始记录为准；不同申请的状态不自动合并。",
                "",
            ]
        requestids = {x for x in c.get("e9_requestids", "").split(";") if x}
        embedded_requests = set()
        included = []
        known_ids = {str(r["apply_id"]) for r in rs}
        for aid in filter(None, c.get("tender_apply_ids", "").split(";")):
            if aid not in known_ids:
                store.gap(
                    key,
                    "申请 " + aid,
                    "本地逐申请导出中无对应记录；保留申请 ID，原始资料待补取",
                )
        for r in rs:
            aid = str(r["apply_id"])
            source_md = Path(r.get("_source_root", src)) / r["md"]
            requestids.update(map(str, r.get("oa_requestids", [])))
            if source_md.exists():
                body = source_md.read_text(encoding="utf-8")
                lines += [
                    f"## 申请 {aid}：原始资料、业务明细与审批历史",
                    "",
                    rewrite_body(body, source_md, store, key, "apply:" + aid),
                    "",
                ]
                included.append(str(source_md.relative_to(ROOT)))
                embedded_requests.update(
                    str(x["requestid"]) for x in r.get("old_comments", [])
                )
            else:
                store.gap(key, "apply:" + aid, "逐申请 MD 不存在", str(source_md))
            for error in errors[aid]:
                store.gap(key, "apply:" + aid + ":" + error["缺口类型"], error["说明"])
            source_records.append({"case": key, "apply_id": aid, "relation": r})

        # Old forms complement application data; avoid re-inserting filtered
        # opinions when the dual-table history already appears in an application.
        for rid in sorted(requestids):
            index = e9_index.get(rid)
            if index:
                source_md = e9 / index["文件"]
                if source_md.exists():
                    body = source_md.read_text(encoding="utf-8")
                    if rid in embedded_requests:
                        body = body.split("## 审批意见（按时间顺序）")[0]
                    lines += [
                        f"## 旧 OA 请求 {rid}：原表单及补充资料",
                        "",
                        rewrite_body(body, source_md, store, key, "e9:" + rid),
                        "",
                    ]
                    included.append(str(source_md.relative_to(ROOT)))
                else:
                    store.gap(key, "e9:" + rid, "旧表单 MD 不存在")
            else:
                store.gap(key, "e9:" + rid, "旧表单未导出，当前仅有索引或审批日志")
            if rid in embedded_requests:
                continue
            n = int(rid) if rid.isdigit() else -1
            if n not in oa["available"]:
                store.gap(key, "e9:" + rid, "双表审批日志尚未完整取得，不能判断无意见")
                continue
            logs = merge_oa_logs(oa["logs"].get(n, []))
            lines += [
                f"## 旧请求 {rid}：双来源审批日志（{len(logs)} 条记录，含事件）",
                "",
            ]
            for row in logs:
                node = oa["nodes"].get(int(row.get("nodeid") or 0), row.get("nodeid"))
                person = oa["names"].get(
                    int(row.get("operator") or 0), row.get("operator")
                )
                lines += [
                    f"### {row.get('operatedate', '')} {row.get('operatetime', '')} · {node} · {person}",
                    "",
                    "- 来源：`" + json.dumps(row["sources"], ensure_ascii=False) + "`",
                    "- 操作类型（原值）：" + str(row.get("logtype", "")),
                    "",
                ]
                from tender_case_export import html_to_md

                body = html_to_md(str(row.get("remark") or ""))
                lines += [
                    rewrite_body(
                        body, e9 / "cases" / "_raw.md", store, key, "log:" + rid
                    ),
                    "",
                ]
                if row.get("annexdocids"):
                    store.gap(
                        key,
                        "log:" + rid,
                        "意见附件 docid 未展开",
                        str(row["annexdocids"]),
                    )
            source_records.append({"case": key, "requestid": rid, "old_logs": logs})

        # Keep all old merge assets available, but never call a numeric-ID-only
        # association verified. Canonical source references are above.
        assets = candidates.get(key, [])
        if assets:
            lines += [
                "## 前版资源候选（保留检索，关联证据待核验）",
                "",
                "本区沿用前版分组，仅证明文件存在；正文来源引用及其缺口见上文。同号匹配未自动认定为正确关联。",
                "",
            ]
            seen = set()
            for candidate, kind in assets:
                local = store.add(
                    legacy / candidate["统一相对路径"],
                    key,
                    "legacy:" + candidate.get("来源库", candidate.get("来源", "")),
                    image=kind == "image",
                    evidence="legacy_candidate_unverified",
                )
                if local and local not in seen:
                    seen.add(local)
                    label = (
                        str(candidate.get("文件名", Path(local).name))
                        .replace("[", "")
                        .replace("]", "")
                    )
                    lines.append(f"- [{label}](<../{local}>) · {kind} · 关联待核验")
            store.gap(
                key, "legacy", "前版资源候选未逐条证实来源关联，不计为业务附件完整"
            )
        gaps = store.gaps[start_gap:]
        lines += ["", "## 合并缺口与待核验", ""]
        lines += render_gaps(gaps)
        md = "cases/" + safe_name(key) + ".md"
        (out / md).write_text("\n".join(lines), encoding="utf-8")
        refs = store.refs[start_ref:]
        result = {
            "合同编号": c.get("合同编号", "")
            or (rs[0].get("contract_no", "") if rs else ""),
            "分组键": key,
            "覆盖类别": c.get("覆盖类别", "tender_only"),
            "申请ID": ";".join(
                sorted(
                    known_ids
                    | set(filter(None, c.get("tender_apply_ids", "").split(";")))
                )
            ),
            "旧requestid": ";".join(sorted(requestids)),
            "来源MD数": len(included),
            "来源MD": included,
            "缺口数": len(gaps),
            "本地资源引用": sum(x["status"] == "local" for x in refs),
            "文件": md,
        }
        results.append(result)
        if count % 1000 == 0:
            print(
                f"合并 {count}/{len(groups)}；资源引用 {len(store.refs)}；缺口 {len(store.gaps)}",
                flush=True,
            )

    for filename, records in [
        ("contracts_merged.jsonl", results),
        ("_relations.jsonl", source_records),
        ("resources.jsonl", store.refs),
        ("_gaps.jsonl", store.gaps),
    ]:
        with (out / filename).open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    write_csv(
        out / "contracts_merged.csv",
        results,
        [
            "合同编号",
            "分组键",
            "覆盖类别",
            "申请ID",
            "旧requestid",
            "来源MD数",
            "缺口数",
            "本地资源引用",
            "文件",
        ],
    )
    write_csv(out / "_errors.csv", store.gaps, ["case", "source", "reason", "original"])
    for name, kind in [
        ("attachments_merged.csv", "attachment"),
        ("albums_merged.csv", "image"),
    ]:
        write_csv(
            out / name,
            [r for r in store.refs if r["kind"] == kind],
            [
                "case",
                "source",
                "original",
                "status",
                "evidence",
                "local",
                "sha256",
                "size",
            ],
        )
    summary = {
        "groups": len(results),
        "source_applications": len(relations),
        "covered_application_ids": len(
            {aid for r in results for aid in r["申请ID"].split(";") if aid}
        ),
        "source_md_inclusions": sum(r["来源MD数"] for r in results),
        "resource_references": len(store.refs),
        "resource_status": dict(Counter(r["status"] for r in store.refs)),
        "gaps": len(store.gaps),
        "gap_reasons": dict(Counter(g["reason"] for g in store.gaps)),
    }
    (out / "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k != "gap_reasons"}, ensure_ascii=False
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DATA / "contract_merged_v2")
    parser.add_argument("--refresh-oa", action="store_true")
    parser.add_argument(
        "--legacy", type=Path, help="前版合并库，切换后默认使用保留的 baseline 目录"
    )
    args = parser.parse_args()
    build(args.out.resolve(), args.refresh_oa, args.legacy)
