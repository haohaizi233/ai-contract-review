# -*- coding: utf-8 -*-
"""tender_unify_merge.py — 两库统一拼接：把 tender 新库数据追加到 e9 导出数据集上。

以「合同编号」为主键（规范化后精确匹配，复合编号按分隔符拆分建索引），
合并四路来源：
  ① e9 侧    data/e9-opinion-export/_index.csv（案号/requestid/流程/日期/意见条数/md）
  ② 新库主数据 tender_contract.contract_apply + contract_archive（oa_id/oa_source 回链）
  ③ 新库意见  tender_bpm.act_hi_comment × act_hi_procinst（BUSINESS_KEY_ = contract_apply.id）
  ④ 文件解析  data/tender_sync/manifest.csv（infra_file id → 文件名/本地路径）；
             迁移件 require_file_ids 里的 e9 imagefileid 解析不到 → 标记悬空

输出 data/contract_unified/:
  contracts_unified.csv    每合同一行（覆盖来源/两侧主键/意见数/文件数）
  contracts_unified.jsonl  每合同完整明细（含文件逐条解析）
  merge_report.md          覆盖率统计

用法: python tools/tender_unify_merge.py
"""
import csv
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENVF = os.path.join(ROOT, "data", ".db_env")
E9_INDEX = os.path.join(ROOT, "data", "e9-opinion-export", "_index.csv")
MANIFEST = os.path.join(ROOT, "data", "tender_sync", "manifest.csv")
FILES_DIR = os.path.join(ROOT, "data", "tender_files")
OUT_DIR = os.path.join(ROOT, "data", "contract_unified")

SEP_RE = re.compile(r"[,，;；/、\s]+")


def load_env():
    env = {}
    if os.path.exists(ENVF):
        for line in open(ENVF, encoding="utf-8"):
            line = line.strip()
            if line.startswith("export ") and "=" in line:
                k, v = line[len("export "):].split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    return env


def sql_query(sql, env):
    cmd = [
        "mysql", "-h" + env["MYSQL_HOST"], "-P" + env["MYSQL_PORT"],
        "-u" + env["MYSQL_USER"], "-p" + env["MYSQL_PWD"],
        "-N", "-B", "--default-character-set=utf8mb4", "-e", sql,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise RuntimeError("mysql 查询失败: " + out.stderr.strip()[:300])
    return [line.split("\t") for line in out.stdout.splitlines()]


def norm_no(s):
    return re.sub(r"\s+", "", (s or "").strip()).upper()


def keys_of(raw):
    """合同编号规范化；复合编号（HT001、HT002）拆分建多个索引键。"""
    n = norm_no(raw)
    if not n:
        return []
    parts = [p for p in SEP_RE.split(n) if len(p) >= 4]
    return list(dict.fromkeys(parts or [n]))


# ---------------------------------------------------------------- ① e9 侧

def load_e9():
    e9 = {}
    with open(E9_INDEX, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            k = norm_no(row.get("案号", ""))
            if not k:
                continue
            rec = e9.setdefault(k, {"requestids": [], "titles": [], "flows": set(),
                                    "dates": set(), "opinions": 0, "mds": []})
            if row.get("requestid"):
                rec["requestids"].append(row["requestid"])
            if row.get("标题"):
                rec["titles"].append(row["标题"])
            if row.get("流程"):
                rec["flows"].add(row["流程"])
            if row.get("发起日期"):
                rec["dates"].add(row["发起日期"][:10])
            try:
                rec["opinions"] += int(row.get("意见条数") or 0)
            except ValueError:
                pass
            if row.get("文件"):
                rec["mds"].append(row["文件"])
    return e9


# ---------------------------------------------------------------- ②③④ 新库侧

def load_tender(env):
    apply_rows = sql_query(
        "SELECT contract_no, id, IFNULL(oa_id,''), IFNULL(oa_source,''), IFNULL(contract_name,''), "
        "IFNULL(contract_status,''), IFNULL(applicant_name,''), IFNULL(applicant_dept_name,''), "
        "IFNULL(my_main_body,''), IFNULL(other_main_body,''), IFNULL(create_time,''), IFNULL(start_time,''), "
        "IFNULL(contract_archive_id,''), IFNULL(require_fill_file_id,''), IFNULL(require_file_ids,''), "
        "IFNULL(no_require_file_ids,''), IFNULL(change_agreement_file_ids,'') "
        "FROM tender_contract.contract_apply WHERE deleted = 0 "
        "AND contract_no IS NOT NULL AND contract_no <> ''", env)
    arch_rows = sql_query(
        "SELECT contract_no, id, IFNULL(contract_name,''), IFNULL(contract_status,''), "
        "IFNULL(applicant_name,''), IFNULL(create_time,''), "
        "IFNULL(require_fill_file_id,''), IFNULL(require_file_ids,''), "
        "IFNULL(no_require_file_ids,''), IFNULL(change_agreement_file_ids,'') "
        "FROM tender_contract.contract_archive WHERE deleted = 0 "
        "AND contract_no IS NOT NULL AND contract_no <> ''", env)
    attach_rows = sql_query(
        "SELECT source_id, IFNULL(source_type,''), IFNULL(doc_name,''), IFNULL(oss_id,'') "
        "FROM tender_contract.contract_attachment WHERE deleted = 0", env)
    opin_rows = sql_query(
        "SELECT p.BUSINESS_KEY_, COUNT(*) FROM tender_bpm.act_hi_comment c "
        "JOIN tender_bpm.act_hi_procinst p ON c.PROC_INST_ID_ = p.PROC_INST_ID_ "
        "WHERE IFNULL(p.BUSINESS_KEY_,'') <> '' GROUP BY p.BUSINESS_KEY_", env)

    applies = defaultdict(list)   # key -> [row...]
    for r in apply_rows:
        (no, rid, oa_id, oa_src, name, status, applicant, dept, mine, other,
         ctime, stime, arch_id, fill_id, req_ids, noreq_ids, change_ids) = r
        item = {"id": rid, "oa_id": oa_id, "oa_source": oa_src, "name": name,
                "status": status, "applicant": applicant, "dept": dept,
                "my_main_body": mine, "other_main_body": other,
                "create_time": ctime, "start_time": stime, "archive_id": arch_id,
                "file_fields": [fill_id, req_ids, noreq_ids, change_ids]}
        for k in keys_of(no):
            applies[k].append(item)
    archives = defaultdict(list)
    for r in arch_rows:
        item = {"id": r[1], "name": r[2], "status": r[3], "applicant": r[4],
                "create_time": r[5], "file_fields": [r[6], r[7], r[8], r[9]],
                "dept": "", "my_main_body": "", "other_main_body": "",
                "oa_id": "", "oa_source": "", "start_time": "", "archive_id": ""}
        for k in keys_of(r[0]):
            archives[k].append(item)
    attachments = defaultdict(list)  # source_id -> [...]
    for src_id, src_type, doc_name, oss_id in attach_rows:
        attachments[src_id].append({"source_type": src_type, "doc_name": doc_name, "oss_id": oss_id})
    opinions = {}
    for bk, cnt in opin_rows:
        opinions[bk] = int(cnt or 0)
    return applies, archives, attachments, opinions


def load_manifest():
    m = {}
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                m[row["id"]] = {"name": row.get("name", ""), "type": row.get("type", ""),
                                "size": row.get("size", ""), "dest": row.get("dest", "")}
    return m


def resolve_files(items, attach_list, manifest):
    """解析一单所有来源（apply + archive 四个关联字段 + contract_attachment.oss_id）
    → (resolved[], dangling[])，按 id 去重。"""
    resolved, dangling = [], []
    seen = set()
    for item in items:
        for field_val in item.get("file_fields", []):
            for fid in SEP_RE.split(field_val or ""):
                fid = fid.strip()
                if not fid or fid in seen:
                    continue
                seen.add(fid)
                info = manifest.get(fid)
                if info:
                    dest = os.path.join(FILES_DIR, info["dest"])
                    resolved.append({"id": fid, "name": info["name"], "type": info["type"],
                                     "size": info["size"], "local_path": info["dest"],
                                     "downloaded": os.path.exists(dest)})
                else:
                    dangling.append({"id": fid, "note": "e9 imagefileid 或已删除，新库 infra_file 无此行"})
    for att in attach_list:
        fid = (att.get("oss_id") or "").strip()
        if not fid or fid in seen:
            continue
        seen.add(fid)
        info = manifest.get(fid)
        if info:
            dest = os.path.join(FILES_DIR, info["dest"])
            resolved.append({"id": fid, "name": info["name"] or att.get("doc_name", ""),
                             "type": info["type"], "size": info["size"],
                             "local_path": info["dest"], "downloaded": os.path.exists(dest),
                             "via": "contract_attachment"})
        else:
            dangling.append({"id": fid, "note": "contract_attachment.oss_id 指向的文件不在 infra_file",
                             "doc_name": att.get("doc_name", "")})
    return resolved, dangling


# ---------------------------------------------------------------- 合并输出

def main():
    env = load_env()
    if "MYSQL_HOST" not in env:
        sys.exit("data/.db_env 缺少 MYSQL_* 凭据")
    os.makedirs(OUT_DIR, exist_ok=True)

    print("[1/4] 读 e9 索引 ...")
    e9 = load_e9()
    print("      e9 单据 %d（去重后合同编号 %d）" % (sum(len(v["mds"]) for v in e9.values()), len(e9)))

    print("[2/4] 拉新库 contract_apply / contract_archive / 附件 / 新审批意见 ...")
    applies, archives, attachments, opinions = load_tender(env)
    print("      新库 apply %d 编号 / archive %d 编号 / 附件 %d 条 / 意见关联 %d 个业务键"
          % (len(applies), len(archives), sum(len(v) for v in attachments.values()), len(opinions)))

    print("[3/4] 解析文件清单 ...")
    manifest = load_manifest()
    print("      manifest %d 条" % len(manifest))

    all_keys = sorted(set(e9) | set(applies) | set(archives))
    csv_path = os.path.join(OUT_DIR, "contracts_unified.csv")
    jsonl_path = os.path.join(OUT_DIR, "contracts_unified.jsonl")
    stats = {"both": 0, "e9_only": 0, "tender_only": 0,
             "files_resolved": 0, "files_dangling": 0, "files_downloaded": 0,
             "e9_opinions": 0, "new_opinions": 0}

    cols = ["合同编号", "覆盖来源", "e9_requestids", "e9_流程", "e9_发起日期", "e9_意见条数",
            "e9_md数", "e9_md示例", "tender_apply_ids", "tender_oa_ids", "tender_oa_source",
            "tender_合同名称", "tender_状态", "tender_申请人", "tender_部门",
            "tender_我方主体", "tender_对方主体", "tender_创建时间", "tender_archive_ids",
            "新审批意见数", "关联文件数", "文件已解析数", "文件悬空数", "本地已下载数", "文件摘要"]

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fc, \
         open(jsonl_path, "w", encoding="utf-8") as fj:
        w = csv.writer(fc)
        w.writerow(cols)
        for k in all_keys:
            e = e9.get(k)
            aps = applies.get(k, [])
            ars = archives.get(k, [])
            has_tender = bool(aps or ars)
            src = "both" if (e and has_tender) else ("e9_only" if e else "tender_only")
            stats[src] += 1

            aps_sorted = sorted(aps, key=lambda x: x["create_time"], reverse=True)
            ars_sorted = sorted(ars, key=lambda x: x["create_time"], reverse=True)
            primary = aps_sorted[0] if aps_sorted else (ars_sorted[0] if ars_sorted else None)
            attach_list = []
            for a in aps_sorted + ars_sorted:
                attach_list.extend(attachments.get(a["id"], []))
            resolved, dangling = resolve_files(aps_sorted + ars_sorted, attach_list, manifest)
            new_op = sum(opinions.get(a["id"], 0) for a in aps)
            dl = sum(1 for f in resolved if f["downloaded"])
            stats["files_resolved"] += len(resolved)
            stats["files_dangling"] += len(dangling)
            stats["files_downloaded"] += dl
            stats["e9_opinions"] += (e["opinions"] if e else 0)
            stats["new_opinions"] += new_op

            rec = {
                "合同编号": k,
                "覆盖来源": src,
                "e9": None if not e else {
                    "requestids": e["requestids"], "流程": sorted(e["flows"]),
                    "发起日期": sorted(e["dates"]), "意见条数": e["opinions"],
                    "md": e["mds"], "标题": e["titles"][:3],
                },
                "tender": None if not has_tender else {
                    "apply_ids": [a["id"] for a in aps_sorted],
                    "oa_ids": [a["oa_id"] for a in aps_sorted],
                    "oa_source": [a["oa_source"] for a in aps_sorted],
                    "合同名称": [a["name"] for a in aps_sorted if a["name"]][:3],
                    "状态": primary["status"] if primary else "",
                    "申请人": primary["applicant"] if primary else "",
                    "部门": primary["dept"] if primary else "",
                    "我方主体": primary["my_main_body"] if primary else "",
                    "对方主体": primary["other_main_body"] if primary else "",
                    "创建时间": primary["create_time"] if primary else "",
                    "archive_ids": [a["id"] for a in ars],
                    "新审批意见数": new_op,
                },
                "文件": {"已解析": resolved, "悬空": dangling},
            }
            fj.write(json.dumps(rec, ensure_ascii=False) + "\n")

            w.writerow([
                k, src,
                ";".join(e["requestids"]) if e else "",
                ";".join(sorted(e["flows"])) if e else "",
                ";".join(sorted(e["dates"])) if e else "",
                e["opinions"] if e else 0,
                len(e["mds"]) if e else 0,
                e["mds"][0] if e else "",
                ";".join(a["id"] for a in aps_sorted),
                ";".join(a["oa_id"] for a in aps_sorted if a["oa_id"]),
                ";".join(sorted({a["oa_source"] for a in aps_sorted if a["oa_source"]})),
                (primary["name"] if primary else "") or (";".join(a["name"] for a in ars[:1])),
                primary["status"] if primary else "",
                primary["applicant"] if primary else "",
                primary["dept"] if primary else "",
                primary["my_main_body"] if primary else "",
                primary["other_main_body"] if primary else "",
                primary["create_time"] if primary else "",
                ";".join(a["id"] for a in ars),
                new_op, len(resolved) + len(dangling),
                len(resolved), len(dangling), dl,
                ";".join(f["name"] for f in resolved[:3]),
            ])

    print("[4/4] 写报告 ...")
    total = len(all_keys)
    report = os.path.join(OUT_DIR, "merge_report.md")
    with open(report, "w", encoding="utf-8") as f:
        f.write("# 两库统一拼接报告（e9 OA × tender 新库）\n\n")
        f.write("生成时间：%s ｜ 主键：合同编号（规范化精确匹配）\n\n" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        f.write("## 覆盖面\n\n| 覆盖来源 | 合同数 | 占比 |\n|---|---:|---:|\n")
        for k_, label in [("both", "两边都有"), ("e9_only", "仅 e9"), ("tender_only", "仅新库")]:
            f.write("| %s | %d | %.1f%% |\n" % (label, stats[k_], stats[k_] * 100.0 / max(total, 1)))
        f.write("| **合计（去重合同编号）** | **%d** | 100%% |\n\n" % total)
        f.write("## 数据量\n\n")
        f.write("- e9 历史审批意见：%d 条（来自 e9 导出索引）\n" % stats["e9_opinions"])
        f.write("- 新库新审批意见：%d 条（act_hi_comment × act_hi_procinst）\n" % stats["new_opinions"])
        f.write("- 文件关联：已解析 %d（本地已下载 %d）｜悬空 %d（e9 imagefileid，历史附件未迁入）\n\n"
                % (stats["files_resolved"], stats["files_downloaded"], stats["files_dangling"]))
        f.write("## 产物\n\n")
        f.write("- `contracts_unified.csv` — 每合同一行，可直接进 Excel/BI\n")
        f.write("- `contracts_unified.jsonl` — 每合同完整明细（文件逐条含本地路径与下载状态）\n")
        f.write("- 下载状态为生成时刻的快照；文件全量同步完成后重跑 `python tools/tender_unify_merge.py` 刷新\n\n")
        f.write("## 说明\n\n")
        f.write("- `覆盖来源=both`：同一合同编号在 e9 与新库都有记录（新库多为 oa_source=1/2 迁移件）\n")
        f.write("- `仅 e9`：新库未收录（多为非合同域流程单或未迁移单）\n")
        f.write("- `仅新库`：新系统原生/其他来源合同，e9 导出集里没有\n")
        f.write("- 悬空文件 = 迁移件 require_file_ids 指向 e9 imagefileid，实体在 e9 侧（需高权限取）\n")
    print("完成: %d 个合同编号 | CSV: %s" % (total, csv_path))
    print("覆盖: both=%d e9_only=%d tender_only=%d | 文件 resolved=%d dangling=%d downloaded=%d"
          % (stats["both"], stats["e9_only"], stats["tender_only"],
             stats["files_resolved"], stats["files_dangling"], stats["files_downloaded"]))
    print("意见: e9=%d 新库=%d" % (stats["e9_opinions"], stats["new_opinions"]))


if __name__ == "__main__":
    main()
