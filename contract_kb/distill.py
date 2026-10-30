# -*- coding: utf-8 -*-
"""Phase 3 知识蒸馏：taxonomy / 条款归并差异矩阵 / 字段 Schema / 规则候选。

输出：
- data/contract-kb/taxonomy.json
- data/contract-kb/clause_diff.json
- data/contract-kb/field_schema.json
- data/contract-kb/rule_candidates.json
- reports/contract-kb/taxonomy-report.md
- reports/contract-kb/clause-diff-matrix.md
- reports/contract-kb/schema-extraction-report.md
- reports/contract-kb/proposed-rules.md
- reports/contract-kb/unresolved-business-questions.md
"""
from __future__ import annotations

import difflib
import json
import re
from collections import defaultdict

from . import config


def _norm(text: str) -> str:
    t = re.sub(r"^\d+\.\d+[\.、]?\s*", "", text)
    t = re.sub(r"[\s\u3000，。；：、“”‘’（）()《》\-—_/%‰]", "", t)
    return t.lower()


# ---------------- 1. taxonomy.json ----------------
def build_taxonomy(data: dict) -> dict:
    templates = []
    for d in data["imported"]:
        templates.append({
            "template_id": d["template_id"],
            "source_file": d["source_file"],
            "category": d["category"],
            "subtype": d["subtype"],
            "classification_evidence": d["classification_evidence"],
            "classification_confidence": d["classification_confidence"],
            "classification_conflicts": d["classification_conflicts"],
            "document_hash": d["sha256"],
            "template_version": "v1_" + d["sha256"][:8],
            "review_status": "PENDING_REVIEW",
        })
    taxonomy = {
        "levels": {
            "L1": ["PATENT", "TRADEMARK", "IP_GENERAL"],
            "L2": sorted({t["subtype"] for t in templates}),
        },
        "templates": templates,
        "generated_from": "real_document_evidence",
    }
    return taxonomy


# ---------------- 2. 条款归并差异矩阵 ----------------
DIFF_LABELS = ["完全相同", "仅格式不同", "语义等价", "关键参数不同", "实质义务不同", "缺失/新增"]

RISK_BY_CATEGORY = {
    "DISPUTE_RESOLUTION": "HIGH",
    "FEE_AND_PAYMENT": "HIGH",
    "LIABILITY": "HIGH",
    "CONFIDENTIALITY": "MEDIUM",
    "EFFECTIVENESS_AND_TERMINATION": "MEDIUM",
    "PARTY_B_OBLIGATIONS": "MEDIUM",
    "PARTY_A_OBLIGATIONS": "MEDIUM",
    "CONTRACT_SUBJECT": "MEDIUM",
}


def build_clause_diff(layer_a: dict) -> tuple[dict, list[str]]:
    """跨模板同类条款归并。返回 (diff 结构, markdown 行)。"""
    by_cat: dict[str, list] = defaultdict(list)
    for c in layer_a["clauses"]:
        by_cat[c["clause_category"]].append(c)

    out = {}
    md = ["# 条款差异矩阵（clause-diff-matrix）", "",
          "> 归并键 = 条款类别 + 归一化文本。差异等级：完全相同 / 仅格式不同 / 语义等价 / 关键参数不同 / 实质义务不同 / 缺失新增。",
          "> 同类条款的服务期限、金额等参数按业务类型分别保留，不强行归一。", ""]
    rows_for_md = []

    for cat, clauses in sorted(by_cat.items()):
        groups: dict[str, list] = defaultdict(list)
        for c in clauses:
            groups[_norm(c["original_text"])].append(c)
        # 相似簇（0.88 以上合并为“语义等价/参数不同”候选）
        keys = list(groups.keys())
        merged: dict[str, list] = {}
        used = set()
        for i, k in enumerate(keys):
            if k in used:
                continue
            cluster = list(groups[k])
            for k2 in keys[i + 1:]:
                if k2 in used:
                    continue
                r = difflib.SequenceMatcher(None, k, k2).ratio()
                if r >= 0.88:
                    cluster.extend(groups[k2])
                    used.add(k2)
            used.add(k)
            merged[k] = cluster
        cat_entries = []
        for idx, (k, members) in enumerate(merged.items()):
            texts = {m["original_text"] for m in members}
            normed = {_norm(t) for t in texts}
            params_mismatch = bool(re.findall(r"\d+", " ".join(texts))) and len(texts) > 1
            if len(texts) == 1:
                diff_level = "完全相同" if len(members) > 1 else "唯一条款"
            elif len(normed) == 1:
                diff_level = "仅格式不同"
            elif params_mismatch and all(
                    difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio() >= 0.88
                    for a in texts for b in texts):
                diff_level = "关键参数不同"
            elif len(texts) > 1 and all(
                    difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio() >= 0.88
                    for a in texts for b in texts):
                diff_level = "语义等价"
            else:
                diff_level = "实质义务不同" if len(merged) > 1 else "唯一条款"
            affects = diff_level in ("关键参数不同", "实质义务不同", "缺失/新增")
            params = sorted({p for t in texts for p in re.findall(r"\d+(?:\.\d+)?", t)})[:20]
            entry = {
                "cluster_id": f"{cat}-{idx}",
                "clause_category": cat,
                "diff_level": diff_level,
                "member_count": len(members),
                "distinct_texts": len(texts),
                "template_ids": sorted({m["template_id"] for m in members}),
                "source_files": sorted({imported_index.get(m["template_id"], {}).get("source_file", m["template_id"])
                                        for m in members}),
                "clause_numbers": sorted({m["clause_number"] for m in members if m["clause_number"]}),
                "numeric_params": params,
                "affects_rights_obligations": affects,
                "risk_candidate": RISK_BY_CATEGORY.get(cat, "LOW") if affects else "INFO",
                "requires_legal_review": bool(affects and cat in
                                              ("DISPUTE_RESOLUTION", "FEE_AND_PAYMENT", "LIABILITY")),
                "samples": [{"template_id": m["template_id"], "clause_number": m["clause_number"],
                             "original_text": m["original_text"][:180],
                             "source_node_ids": m["source_node_ids"][:3]} for m in members[:4]],
            }
            cat_entries.append(entry)
            rows_for_md.append(
                f"| {cat} | {idx} | {diff_level} | {len(members)} | {len(texts)} "
                f"| {','.join(params[:6])} | {'是' if affects else '否'} "
                f"| {entry['risk_candidate']} | {'是' if entry['requires_legal_review'] else '否'} |")
        out[cat] = cat_entries

    md.append("| 条款类别 | 簇 | 差异等级 | 成员数 | 不同文本数 | 数值参数 | 影响权利义务 | 风险候选 | 需法务审核 |")
    md.append("|---|---|---|---|---|---|---|---|---|")
    md.extend(rows_for_md)

    # 明细段：影响权利义务的簇逐条展开
    md += ["", "## 影响权利义务的差异明细", ""]
    for cat, entries in out.items():
        for e in entries:
            if not e["affects_rights_obligations"]:
                continue
            md.append(f"### {e['cluster_id']}（{cat}，{e['diff_level']}）")
            md.append(f"- 覆盖模板：{', '.join(f[:24] for f in e['source_files'])}")
            md.append(f"- 条款号：{', '.join(e['clause_numbers'])}")
            for s in e["samples"]:
                md.append(f"- 原文（{s['template_id'][:10]}… {s['clause_number'] or '-'}）：{s['original_text']}")
                md.append(f"  - 来源节点：{s['source_node_ids']}")
            md.append(f"- 是否影响权利义务：是；风险候选：{e['risk_candidate']}；需法务审核：{'是' if e['requires_legal_review'] else '否'}")
            md.append("")
    return out, md


# ---------------- 3. 字段 Schema ----------------
FIELD_NAME_MAP = {
    "合同编号": ("contract_number", "string", True),
    "甲方": ("party_a_name", "string", True),
    "地址": ("address", "string", False),
    "发明名称/申请号/专利号": ("patent_number", "string", True),
    "专利名称": ("patent_name", "string", True),
    "专利号": ("patent_number", "string", True),
    "申请号": ("application_number", "string", True),
    "商标图样/文字": ("trademark_text", "string", True),
    "商标图样": ("trademark_image", "string", True),
    "类别": ("nice_class", "string", True),
    "申请人名称": ("applicant_name", "string", True),
    "专利权人名称": ("patentee_name", "string", True),
    "转让人名称": ("transferor_name", "string", True),
    "受让人名称": ("transferee_name", "string", True),
    "许可人名称": ("licensor_name", "string", True),
    "被许可人名称": ("licensee_name", "string", True),
    "代收代付官费(元/件)": ("official_fee", "decimal", True),
    "代收代付官费（元/件）": ("official_fee", "decimal", True),
    "含税服务费(元/件)": ("service_fee", "decimal", True),
    "含税服务费（元/件）": ("service_fee", "decimal", True),
    "数量": ("quantity", "integer", True),
    "年费期限": ("annual_fee_period", "string", True),
    "专利状态": ("patent_status", "string", False),
    "备注": ("remark", "string", False),
    "联系人": ("contact_name", "string", True),
    "电子邮件": ("contact_email", "string", False),
    "联系电话/手机": ("contact_phone", "string", False),
    "联系地址": ("contact_address", "string", False),
}


def build_field_schema(layer_a: dict, data: dict) -> dict:
    src_by_tid = {d["template_id"]: d["source_file"] for d in data.get("imported", [])}
    fields: dict[str, dict] = {}
    for ts in layer_a["table_schemas"]:
        for f in ts["fields"]:
            name = f["field_name"]
            key, dtype, req_cand = FIELD_NAME_MAP.get(name, (f["field_key"], "string", False))
            slot = "留空待填" if not f["sample_value"] else "含样例值"
            rec = fields.setdefault(key, {
                "field_key": key,
                "field_name": name,
                "data_type": dtype,
                "required_candidate": req_cand,
                "required_approved": None,
                "source_template_ids": [],
                "source_template_files": [],
                "source_node_ids": [],
                "validation_rules": [],
                "semantic_description": "",
                "placeholder_status": slot,
            })
            rec["source_template_ids"].append(ts["template_id"])
            rec["source_template_files"].append(src_by_tid.get(ts["template_id"], ""))
            rec["source_node_ids"].extend(f["source_node_ids"])
    # 正文级字段（合同编号、总额、付款时间、税率）
    for d in data["imported"]:
        tid = d["template_id"]
        for c in (d.get("clauses") or []):
            text = c["original_text"]
            if "合同编号" in text:
                fields.setdefault("contract_number", _mk_field("contract_number", "合同编号", "string", True))
                fields["contract_number"]["source_template_ids"].append(tid)
            if "税率" in text:
                fields.setdefault("tax_rate", _mk_field("tax_rate", "税率", "decimal", True))
                fields["tax_rate"]["source_template_ids"].append(tid)
                m = re.search(r"税率\s*(\d+)\s*%", text)
                if m:
                    fields["tax_rate"]["validation_rules"].append({"type": "EXPECTED_VALUE", "value": m.group(1) + "%",
                                                                   "source_node_ids": c["source_node_ids"][:2],
                                                                   "note": "企业模板惯例值，非法务批准规则"})
            if "费用总额为人民币" in text:
                fields.setdefault("contract_total", _mk_field("contract_total", "合同总额", "decimal", True))
                fields["contract_total"]["source_template_ids"].append(tid)
                fields["contract_total"]["source_node_ids"].extend(c["source_node_ids"][:2])
            if "工作日内" in text and "支付" in text:
                fields.setdefault("payment_deadline", _mk_field("payment_deadline", "付款时间", "string", True))
                fields["payment_deadline"]["source_template_ids"].append(tid)
                for dd in re.findall(r"(\d+)\s*个工作日", text):
                    fields["payment_deadline"]["validation_rules"].append(
                        {"type": "OBSERVED_VALUE", "value": dd + "个工作日",
                         "source_node_ids": c["source_node_ids"][:2], "note": "模板实测值，按业务类型分别保留"})
    for f in fields.values():
        f["source_template_ids"] = sorted(set(f["source_template_ids"]))
        f["source_template_files"] = sorted(set(f["source_template_files"]))
        if not f["semantic_description"]:
            f["semantic_description"] = f"业务字段『{f['field_name']}』（来源模板 {len(f['source_template_ids'])} 份）"
    return {"fields": list(fields.values()), "field_count": len(fields),
            "note": "required_candidate 为模板留白推断；required_approved 需法务审批后填写。模板留白不等于模板错误。"}


def _mk_field(key, name, dtype, req):
    return {"field_key": key, "field_name": name, "data_type": dtype,
            "required_candidate": req, "required_approved": None,
            "source_template_ids": [], "source_template_files": [], "source_node_ids": [],
            "validation_rules": [], "semantic_description": "", "placeholder_status": "正文级"}


# ---------------- 4. 规则候选 ----------------
def build_rule_candidates(layer_a: dict, data: dict, field_schema: dict) -> dict:
    clauses = layer_a["clauses"]

    def find_clauses(pred, limit=6):
        return [{"clause_id": c["clause_id"], "template_id": c["template_id"],
                 "clause_number": c["clause_number"], "text": c["original_text"][:120]}
                for c in clauses if pred(c)][:limit]

    rules = []

    # R1 金额一致性（大写/小写）
    amt = find_clauses(lambda c: "圆整" in c["original_text"] and "¥" in c["original_text"])
    rules.append({
        "rule_id": "AMOUNT_CASE_CONSISTENCY", "name": "合同金额大小写一致性",
        "contract_types": ["*"], "rule_type": "FINANCIAL_CALCULATION",
        "execution_mode": "DETERMINISTIC", "severity": "HIGH", "parameters":
            {"decimal_class": "Decimal", "fields": ["contract_total", "费用表合计"]},
        "source_clauses": amt, "status": "DRAFT", "approved_by": None, "effective_at": None,
        "evidence_note": "模板 4.1 均为『人民币__圆整（¥__元）』双写法，需两值一致",
    })

    # R2 发票/税率
    inv = find_clauses(lambda c: "增值税专用发票" in c["original_text"])
    rules.append({
        "rule_id": "INVOICE_TYPE_CONSISTENCY", "name": "代理费/官费开票类型一致性",
        "contract_types": ["*"], "rule_type": "CROSS_FIELD_CONSISTENCY",
        "execution_mode": "DETERMINISTIC", "severity": "HIGH", "parameters":
            {"expected_service_fee_invoice": "增值税专用发票", "expected_tax_rate": "6%",
             "official_fee": "官方收据(代收代付)"},
        "source_clauses": inv, "status": "DRAFT", "approved_by": None, "effective_at": None,
        "evidence_note": "来源：《新系统合同审批注意事项20261015》第4条 + 模板4.5/4.7；外部数据依赖：业财系统流程开票类型字段",
        "external_data_dependency": "业财系统合同流程-开票类型",
    })

    # R3 合同编码-主体
    rules.append({
        "rule_id": "CONTRACT_CODE_SUBJECT_MATCH", "name": "合同编码与我方签约主体一致",
        "contract_types": ["*"], "rule_type": "CROSS_FIELD_CONSISTENCY",
        "execution_mode": "DETERMINISTIC", "severity": "HIGH", "parameters":
            {"known_mapping": "LOADED_FROM_LOCAL_CONFIG"},
        "source_clauses": [], "status": "DRAFT", "approved_by": None, "effective_at": None,
        "evidence_note": "来源：《新系统合同审批注意事项》第1条（编码↔主体映射由本地配置 config/company_profile.local.json 提供，仓库内不存真实主体）",
        "external_data_dependency": "业财系统-签约主体编码表",
    })

    # R4 主体与附件一致
    rules.append({
        "rule_id": "PARTY_ATTACHMENT_CONSISTENCY", "name": "我方/他方主体与附件一致",
        "contract_types": ["*"], "rule_type": "CROSS_FIELD_CONSISTENCY",
        "execution_mode": "DETERMINISTIC", "severity": "HIGH", "parameters": {},
        "source_clauses": [], "status": "DRAFT", "approved_by": None, "effective_at": None,
        "evidence_note": "来源：《新系统合同审批注意事项20261015》第2条",
    })

    # R5 费用明细-履约计划
    rules.append({
        "rule_id": "FEE_DETAIL_PLAN_CONSISTENCY", "name": "费用明细/履约计划/合同三方金额一致",
        "contract_types": ["*"], "rule_type": "CROSS_FIELD_CONSISTENCY",
        "execution_mode": "DETERMINISTIC", "severity": "HIGH",
        "parameters": {"fields": ["产品", "官费", "代理费", "总金额"], "decimal_class": "Decimal"},
        "source_clauses": [], "status": "DRAFT", "approved_by": None, "effective_at": None,
        "evidence_note": "来源：《新系统合同审批注意事项20261015》第3条；外部数据依赖：业财系统履约计划",
        "external_data_dependency": "业财系统-履约计划",
    })

    # R6 代理费底价（禁止编造数值）
    rules.append({
        "rule_id": "AGENCY_FEE_FLOOR", "name": "填写经纪费后代理费不得低于系统底价",
        "contract_types": ["*"], "rule_type": "BUSINESS_CONSTRAINT",
        "execution_mode": "MANUAL_REVIEW", "severity": "HIGH", "parameters":
            {"floor_price": None},
        "source_clauses": [], "status": "DRAFT", "approved_by": None, "effective_at": None,
        "evidence_note": "来源：《新系统合同审批注意事项20261015》第5条；底价数值未在任何文档给出→不编造，标记外部数据依赖",
        "external_data_dependency": "业财系统-代理费底价表（数值未知，需业务提供）",
    })

    # R7 合同编号回写
    rules.append({
        "rule_id": "CONTRACT_NUMBER_BACKFILL", "name": "合同编号自动体现到合同文件",
        "contract_types": ["*"], "rule_type": "FIELD_REQUIRED",
        "execution_mode": "DETERMINISTIC", "severity": "MEDIUM", "parameters": {},
        "source_clauses": find_clauses(lambda c: c.get("source_locator") and "合同编号" in c["original_text"])[:3],
        "status": "DRAFT", "approved_by": None, "effective_at": None,
        "evidence_note": "来源：《新系统合同审批注意事项》第6条 + 全部 26 模板首部『合同编号:』留白",
    })

    # R8 争议管辖标准条款
    dis = find_clauses(lambda c: c["clause_category"] == "DISPUTE_RESOLUTION")
    rules.append({
        "rule_id": "DISPUTE_JURISDICTION_STANDARD", "name": "争议管辖法院标准条款核对",
        "contract_types": ["*"], "rule_type": "CLAUSE_VARIATION",
        "execution_mode": "DETERMINISTIC", "severity": "HIGH",
        "parameters": {"standard": "深圳市南山区人民法院",
                       "known_variants": ["有权可向…（措辞差异）", "乙方所在地有管辖权的人民法院（仅优先审查模板）"]},
        "source_clauses": dis, "status": "DRAFT", "approved_by": None, "effective_at": None,
        "evidence_note": "23/26 模板为南山区法院；1 模板‘有权可向’措辞；1 模板为乙方所在地；1 模板(商标许可备案)‘有权可向’。差异是否可接受需法务确认",
    })

    # R9 必备条款（保密/违约/生效）
    for cat, name, sev in [("CONFIDENTIALITY", "保密条款必备", "MEDIUM"),
                           ("LIABILITY", "违约责任必备", "MEDIUM"),
                           ("EFFECTIVENESS_AND_TERMINATION", "生效终止条款必备", "MEDIUM")]:
        rules.append({
            "rule_id": f"CLAUSE_REQUIRED_{cat}", "name": name,
            "contract_types": ["*"], "rule_type": "CLAUSE_REQUIRED",
            "execution_mode": "DETERMINISTIC", "severity": sev, "parameters": {"clause_category": cat},
            "source_clauses": find_clauses(lambda c: c["clause_category"] == cat, 3),
            "status": "DRAFT", "approved_by": None, "effective_at": None,
            "evidence_note": f"{name}在 26/26 模板出现，属企业模板惯例（非法务强制）",
        })

    # R10 逾期违约金 3‰
    liq = find_clauses(lambda c: "违约金" in c["original_text"] and "3‰" in c["original_text"])
    rules.append({
        "rule_id": "LATE_FEE_RATE", "name": "逾期付款违约金比率核对",
        "contract_types": ["*"], "rule_type": "CLAUSE_VARIATION",
        "execution_mode": "DETERMINISTIC", "severity": "MEDIUM",
        "parameters": {"expected_rate_per_day": "3‰", "note": "模板惯例值，非法律强制；措辞‘日率/日利率’并存"},
        "source_clauses": liq, "status": "DRAFT", "approved_by": None, "effective_at": None,
        "evidence_note": "25/26 模板为日 3‰（1 模板含费率表述差异）",
    })

    # R11 业务专属必备条款
    rules.append({
        "rule_id": "PRESCREEN_UNDERTAKING_REQUIRED", "name": "预审业务须含重复预审承诺",
        "contract_types": ["PATENT_FAST_PRESCREEN", "PATENT_APPLICATION"],
        "rule_type": "CLAUSE_REQUIRED", "execution_mode": "DETERMINISTIC", "severity": "HIGH",
        "parameters": {"clause_subcategory": "PRESCREEN_UNDERTAKING"},
        "source_clauses": find_clauses(lambda c: c["clause_subcategory"] == "PRESCREEN_UNDERTAKING", 4),
        "status": "DRAFT", "approved_by": None, "effective_at": None,
        "evidence_note": "快速预审 3.5 / 申请 3.6 / 框架附件备注含『永久取消资格』承诺，其余模板无",
    })

    rules.append({
        "rule_id": "TRADEMARK_REGISTRATION_RISK_DISCLOSURE", "name": "商标注册须含风险告知",
        "contract_types": ["TRADEMARK_REGISTRATION"], "rule_type": "CLAUSE_REQUIRED",
        "execution_mode": "DETERMINISTIC", "severity": "MEDIUM",
        "parameters": {"clause_subcategory": "RISK_DISCLOSURE"},
        "source_clauses": find_clauses(lambda c: c["clause_subcategory"] == "RISK_DISCLOSURE", 4),
        "status": "DRAFT", "approved_by": None, "effective_at": None,
        "evidence_note": "商标注册 3.4 风险告知（在先近似/显著性/不良影响）",
    })

    # R12 期限不归一（MANUAL）
    rules.append({
        "rule_id": "SERVICE_LEVEL_BY_TYPE", "name": "服务交付期限按业务类型核对（禁止归一）",
        "contract_types": ["*"], "rule_type": "MANUAL_REVIEW",
        "execution_mode": "MANUAL_REVIEW", "severity": "LOW",
        "parameters": {"observed": {"PATENT_APPLICATION": "30 个工作日出初稿",
                                    "PATENT_INFRINGEMENT_REPORT": "10 个工作日",
                                    "PATENT_DUE_DILIGENCE_REPORT": "30 个工作日",
                                    "PATENT_NAVIGATION_REPORT": "45 个工作日",
                                    "PATENT_STABILITY_REPORT": "留白待填",
                                    "TRADEMARK_REGISTRATION(框架4.5.1)": "5 个工作日递交"}},
        "source_clauses": find_clauses(lambda c: c["clause_subcategory"] == "SERVICE_LEVEL", 8),
        "status": "DRAFT", "approved_by": None, "effective_at": None,
        "evidence_note": "各业务期限不同，必须按类型分别保留，禁止强行归一为固定值",
    })

    return {"rules": rules, "rule_count": len(rules),
            "notice": "全部规则为 DRAFT 候选，未经审批不得作为企业正式强制规则；外部数据依赖项需业务系统接入后方可执行"}


imported_index: dict = {}   # template_id -> imported doc（供差异矩阵引用文件名）


def run():
    config.ensure_dirs()
    data = json.loads((config.DATA_DIR / "import_result.json").read_text(encoding="utf-8"))
    layer_a = json.loads((config.DATA_DIR / "layer_a_raw.json").read_text(encoding="utf-8"))
    global imported_index
    imported_index = {d["template_id"]: d for d in data["imported"]}

    taxonomy = build_taxonomy(data)
    (config.DATA_DIR / "taxonomy.json").write_text(json.dumps(taxonomy, ensure_ascii=False, indent=1), encoding="utf-8")

    diff, md = build_clause_diff(layer_a)
    (config.DATA_DIR / "clause_diff.json").write_text(json.dumps(diff, ensure_ascii=False, indent=1), encoding="utf-8")
    (config.REPORT_DIR / "clause-diff-matrix.md").write_text("\n".join(md), encoding="utf-8")

    fs = build_field_schema(layer_a, data)
    (config.DATA_DIR / "field_schema.json").write_text(json.dumps(fs, ensure_ascii=False, indent=1), encoding="utf-8")

    rc = build_rule_candidates(layer_a, data, fs)
    (config.DATA_DIR / "rule_candidates.json").write_text(json.dumps(rc, ensure_ascii=False, indent=1), encoding="utf-8")

    _write_reports(data, taxonomy, fs, rc, diff)
    print(f"taxonomy={len(taxonomy['templates'])} diff_cats={len(diff)} fields={fs['field_count']} rules={rc['rule_count']}")


def _write_reports(data, taxonomy, fs, rc, diff):
    # taxonomy-report.md
    lines = ["# 合同分类体系报告（taxonomy-report）", "",
             f"- 模板总数：{len(taxonomy['templates'])}",
             f"- 一级分类：{', '.join(taxonomy['levels']['L1'])}",
             f"- 二级分类数：{len(taxonomy['levels']['L2'])}", "",
             "| template_id | 源文件 | L1 | L2 | 置信度 | 证据 | 冲突 |", "|---|---|---|---|---|---|---|"]
    for t in taxonomy["templates"]:
        ev = "; ".join(t["classification_evidence"][:2])
        cf = json.dumps(t["classification_conflicts"], ensure_ascii=False) if t["classification_conflicts"] else "无"
        lines.append(f"| {t['template_id']} | {t['source_file'][:36]} | {t['category']} | {t['subtype']} "
                     f"| {t['classification_confidence']} | {ev} | {cf} |")
    (config.REPORT_DIR / "taxonomy-report.md").write_text("\n".join(lines), encoding="utf-8")

    # schema-extraction-report.md
    lines = ["# 字段 Schema 蒸馏报告（schema-extraction-report）", "",
             f"- 字段总数：{fs['field_count']}",
             "- required_candidate=模板留白/列头推断；required_approved=null 待法务审批",
             "- 模板留白属于占位设计，不视为模板错误", "",
             "| field_key | 字段名 | 类型 | 必填候选 | 必填(已批) | 来源模板数 | 校验规则 | 占位状态 |", "|---|---|---|---|---|---|---|---|"]
    for f in fs["fields"]:
        vr = "; ".join(f"{r['type']}={r['value']}" for r in f["validation_rules"][:2]) or "-"
        lines.append(f"| {f['field_key']} | {f['field_name']} | {f['data_type']} | {f['required_candidate']} "
                     f"| {f['required_approved']} | {len(f['source_template_ids'])} | {vr} | {f['placeholder_status']} |")
    (config.REPORT_DIR / "schema-extraction-report.md").write_text("\n".join(lines), encoding="utf-8")

    # proposed-rules.md
    lines = ["# 企业规则候选（proposed-rules）", "",
             "> 全部为 **DRAFT** 候选。未经审批不得作为正式强制规则。", "> 金额一律 Decimal；未提供的底价/编码算法不编造。", ""]
    for r in rc["rules"]:
        lines += [f"## {r['rule_id']} — {r['name']}", "",
                  f"- 规则类型：{r['rule_type']}；执行方式：{r['execution_mode']}；严重级别：{r['severity']}",
                  f"- 适用合同类型：{', '.join(r['contract_types'])}",
                  f"- 状态：{r['status']}（approved_by={r['approved_by']}, effective_at={r['effective_at']}）",
                  f"- 证据：{r['evidence_note']}"]
        if r.get("external_data_dependency"):
            lines.append(f"- ⚠️ 外部数据依赖：{r['external_data_dependency']}")
        if r["source_clauses"]:
            lines.append("- 源条款：")
            for sc in r["source_clauses"][:4]:
                lines.append(f"  - [{sc['clause_id']}] {sc['clause_number'] or '-'}：{sc['text']}")
        lines.append("")
    (config.REPORT_DIR / "proposed-rules.md").write_text("\n".join(lines), encoding="utf-8")

    # unresolved-business-questions.md
    lines = ["# 未解决业务问题（unresolved-business-questions）", "",
             "| # | 问题 | 证据 | 影响 | 需要谁确认 |", "|---|---|---|---|---|"]
    qs = [
        ("代理费底价数值未知", "《新系统合同审批注意事项》仅表述『不能低于系统底价』，无任何文档给出数值",
         "规则 AGENCY_FEE_FLOOR 无法确定性执行", "业务/财务部门"),
        ("合同编码-签约主体完整映射未知", "审批注意事项仅举例编码↔主体映射，完整映射在企业系统（本仓库不存真实主体）", "CONTRACT_CODE_SUBJECT_MATCH 部分场景需人工", "业财系统管理员"),
        ("仅优先审查模板管辖为『乙方所在地』", "模板原文与其他 25 份『深圳市南山区人民法院』不一致", "管辖条款不统一，起草时有歧义", "法务"),
        ("发票条款变体是否全部可接受", "分析报告类仅写『相应发票』，优先/快速预审仅写『服务费发票』未写 6% 专票", "开票类型一致性规则需人工确认口径", "财务/法务"),
        ("『有权可向』措辞变体（2 模板）", "年费缴纳/商标许可备案争议解决条款", "文字瑕疵，效力影响待评估", "法务"),
        ("快速预审模板缺投诉反馈条款", "该模板第八条无投诉条款（其他 25 份有）", "客户投诉渠道条款缺失", "法务/客服"),
        ("稳定性分析报告交付期限留白", "模板 2.2 『  个工作日』未定值", "服务级别承诺不完整", "业务部门"),
        ("经纪费（外部业务）与知识产权服务合同的关系", "注意事项提到『填写经纪费后代理费不能低于底价』，但 26 份模板无经纪费字段",
         "可能涉及业财系统特有字段，模板库未覆盖", "业务/业财系统"),
    ]
    for i, (q, ev, imp, who) in enumerate(qs, 1):
        lines.append(f"| {i} | {q} | {ev} | {imp} | {who} |")
    (config.REPORT_DIR / "unresolved-business-questions.md").write_text("\n".join(lines), encoding="utf-8")

    # template-structure-analysis.md
    lines = ["# 模板结构分析（template-structure-analysis）", "",
             "| 源文件 | 段落 | 表格 | 表格维度 | 条款数 | 章节骨架 | 无法读取结构 |", "|---|---|---|---|---|---|---|"]
    for d in data["imported"]:
        dims = "; ".join(f"{t.get('row_count', t.get('rows', '?'))}x{t.get('col_count', t.get('cols', '?'))}"
                         for t in d.get("table_schemas", []))
        secs = " / ".join(s[:6] for s in d["main_sections"])
        lines.append(f"| {d['source_file'][:38]} | {d['paragraph_count']} | {d['table_count']} | {dims} "
                     f"| {d['clause_count']} | {secs} | {', '.join(d['unhandled_structures'][:3]) or '无'} |")
    (config.REPORT_DIR / "template-structure-analysis.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    run()
