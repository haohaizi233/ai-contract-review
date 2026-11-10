# -*- coding: utf-8 -*-
"""Phase 3 知识蒸馏：覆盖全量 78 套标准合同模板与五级分类体系。

输出：
- data/contract-kb/taxonomy.json (78 模板全量五级知识体系、603 产品绑定与 12 组排他规则)
- data/contract-kb/clause_diff.json (条款跨模板归并与差异矩阵)
- data/contract-kb/field_schema.json (动态占位符 + 表格字段全景 Schema)
- data/contract-kb/rule_candidates.json (审批红线、财务合规、12 组排他决策树候选规则；一律为 DRAFT 待审批)
- reports/contract-kb/taxonomy-report.md
- reports/contract-kb/clause-diff-matrix.md
- reports/contract-kb/schema-extraction-report.md
- reports/contract-kb/proposed-rules.md
- reports/contract-kb/unresolved-business-questions.md
- reports/contract-kb/template-structure-analysis.md
"""

from __future__ import annotations

import difflib
import itertools
import json
import re
from collections import defaultdict
from typing import Any

from contract_kb import config
from ..contract_comparison.clause_diff import diff_clauses
from .catalog_data import (
    DISAMBIGUATION_GROUPS,
    TEMPLATE_5TH_LAYER_RULES,
    get_template_leaf_map,
)

_TPL_LEAF_MAP = get_template_leaf_map()
imported_index: dict[str, dict] = {}


def _norm(text: str) -> str:
    t = re.sub(r"^\d+\.\d+[\.、]?\s*", "", text)
    t = re.sub(r"[\s\u3000，。；：、“”‘’（）()《》\-—_/%‰]", "", t)
    return t.lower()


# ---------------- 1. taxonomy.json ----------------
def build_taxonomy(data: dict) -> dict:
    templates = []
    l1_set = set()
    l2_set = set()
    l3_set = set()

    for d in data["imported"]:
        tid = d["template_id"]
        rule = TEMPLATE_5TH_LAYER_RULES.get(tid, {})
        leaf = _TPL_LEAF_MAP.get(tid, {})

        l1_name = leaf.get("l1", d["category"])
        l2_name = leaf.get("l2", d["subtype"])
        l3_name = leaf.get("l3", rule.get("sub_scene", ""))

        l1_set.add(d["category"])
        l2_set.add(d["subtype"])
        if l3_name:
            l3_set.add(l3_name)

        tpl_entry = {
            "template_id": tid,
            "template_code": tid,
            "source_file": d["source_file"],
            "category": d["category"],
            "subtype": d["subtype"],
            "l1_sector": l1_name,
            "l2_matter": l2_name,
            "l3_business_type": l3_name,
            "sub_scene": rule.get("sub_scene", ""),
            "fee_mode": rule.get("fee_mode", ""),
            "target_constraint": rule.get("target_constraint", ""),
            "disambiguation_rule": rule.get("disambiguation_rule", ""),
            "new_products": rule.get("new_products", []),
            "new_product_count": len(rule.get("new_products", [])),
            "erp_products": rule.get("erp_products", []),
            "historical_contract_count": leaf.get("hist_cnt", 0),
            "classification_evidence": d["classification_evidence"],
            "classification_confidence": d["classification_confidence"],
            "classification_conflicts": d["classification_conflicts"],
            "document_hash": d["sha256"],
            "template_version": leaf.get("version", "v1_" + d["sha256"][:8]),
            "review_status": "APPROVED",
        }
        templates.append(tpl_entry)

    total_prods = sum(t["new_product_count"] for t in templates)

    return {
        "version": "2.0_78_templates",
        "levels": {
            "L1_categories": sorted(l1_set),
            "L2_subtypes": sorted(l2_set),
            "L3_business_classes": sorted(l3_set),
            "L4_scene_constraints_count": len(templates),
            "L5_templates_count": len(templates),
        },
        "statistics": {
            "total_templates": len(templates),
            "total_bound_products": total_prods,
            "disambiguation_groups_count": len(DISAMBIGUATION_GROUPS),
        },
        "disambiguation_groups": DISAMBIGUATION_GROUPS,
        "templates": templates,
        "generated_from": "real_78_docx_templates_and_5th_layer_rules",
    }


# ---------------- 2. 条款归并差异矩阵 ----------------
DIFF_LABELS = [
    "完全相同",
    "仅格式不同",
    "措辞差异（相似度线索）",
    "关键参数不同",
    "实质义务不同",
    "缺失/新增",
]

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


def _cluster_clauses_by_similarity(clauses: list[dict]) -> dict[str, list[dict]]:
    """对同类别条款执行带长度预筛选的相似度聚类。"""
    groups: dict[str, list[dict]] = defaultdict(list)
    for c in clauses:
        groups[_norm(c["original_text"])].append(c)

    keys = list(groups.keys())
    merged: dict[str, list[dict]] = {}
    used: set[str] = set()
    for i, k in enumerate(keys):
        if k in used:
            continue
        cluster = list(groups[k])
        len_k = len(k)
        for k2 in keys[i + 1 :]:
            if k2 in used:
                continue
            if len_k > 0 and abs(len_k - len(k2)) / max(len_k, len(k2)) > 0.15:
                continue
            if difflib.SequenceMatcher(None, k, k2).ratio() >= 0.88:
                cluster.extend(groups[k2])
                used.add(k2)
        used.add(k)
        merged[k] = cluster
    return merged


def _classify_cluster_diff_level(members: list[dict]) -> tuple[str, set[str]]:
    """计算簇内条款的结构化差异等级（禁止仅凭字符串相似度断言语义等价）。"""
    texts = {m["original_text"] for m in members}
    normed = {_norm(t) for t in texts}
    if len(texts) == 1:
        return ("完全相同" if len(members) > 1 else "唯一条款"), texts
    if len(normed) == 1:
        return "仅格式不同", texts

    kinds: set[str] = set()
    sample_texts = sorted(texts)[:10]
    for a_, b_ in itertools.combinations(sample_texts, 2):
        kinds |= {d.delta_type for d in diff_clauses(a_, b_)}
    structured = kinds & {
        "NUMBER_CHANGE",
        "DEADLINE_CHANGE",
        "PARTY_CHANGE",
        "NEGATION_CHANGE",
        "OBLIGATION_CHANGE",
    }
    if not structured:
        return "措辞差异（相似度线索）", texts
    if structured & {"NEGATION_CHANGE", "OBLIGATION_CHANGE", "PARTY_CHANGE"}:
        return "实质义务不同", texts
    return "关键参数不同", texts


def _render_diff_markdown(
    out: dict[str, list[dict]], rows_for_md: list[str]
) -> list[str]:
    md = [
        "# 条款差异矩阵（clause-diff-matrix · 全量78套模板）",
        "",
        "> 差异等级：完全相同 / 仅格式不同 / 措辞差异（相似度线索） / 关键参数不同 / 实质义务不同 / 唯一条款。",
        "> 相似度仅作聚类线索；否定词/金额/期限/责任主体/义务强度变化经专门检查后定级。",
        "",
        "| 条款类别 | 簇 | 差异等级 | 成员数 | 不同文本数 | 数值参数 | 影响权利义务 | 风险候选 | 需法务审核 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    md.extend(rows_for_md)
    md += ["", "## 影响权利义务的差异明细", ""]
    for cat, entries in out.items():
        for e in entries:
            if not e["affects_rights_obligations"]:
                continue
            md.append(f"### {e['cluster_id']}（{cat}，{e['diff_level']}）")
            md.append(
                f"- 覆盖模板数：{len(e['template_ids'])}（如：{', '.join(e['template_ids'][:6])}…）"
            )
            md.append(f"- 条款号：{', '.join(e['clause_numbers'])}")
            for s in e["samples"]:
                md.append(
                    f"- 原文（{s['template_id']} {s['clause_number'] or '-'}）：{s['original_text']}"
                )
            md.append(
                f"- 是否影响权利义务：是；风险级别：{e['risk_candidate']}；需法务审核：{'是' if e['requires_legal_review'] else '否'}"
            )
            md.append("")
    return md


def build_clause_diff(
    layer_a: dict, imported_idx: dict | None = None
) -> tuple[dict, list[str]]:
    """跨模板同类条款归并与差异定位。"""
    resolved_index = imported_idx if imported_idx is not None else imported_index
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for c in layer_a["clauses"]:
        by_cat[c["clause_category"]].append(c)

    out: dict[str, list[dict]] = {}
    rows_for_md: list[str] = []

    for cat, clauses in sorted(by_cat.items()):
        merged = _cluster_clauses_by_similarity(clauses)
        cat_entries: list[dict] = []
        for idx, members in enumerate(merged.values()):
            diff_level, texts = _classify_cluster_diff_level(members)
            affects = diff_level in ("关键参数不同", "实质义务不同", "缺失/新增")
            params = sorted(
                {p for t in texts for p in re.findall(r"\d+(?:\.\d+)?", t)}
            )[:20]
            entry = {
                "cluster_id": f"{cat}-{idx}",
                "clause_category": cat,
                "diff_level": diff_level,
                "member_count": len(members),
                "distinct_texts": len(texts),
                "template_ids": sorted({m["template_id"] for m in members}),
                "source_files": sorted(
                    {
                        resolved_index.get(m["template_id"], {}).get(
                            "source_file", m["template_id"]
                        )
                        for m in members
                    }
                ),
                "clause_numbers": sorted(
                    {m["clause_number"] for m in members if m["clause_number"]}
                ),
                "numeric_params": params,
                "affects_rights_obligations": affects,
                "risk_candidate": RISK_BY_CATEGORY.get(cat, "LOW")
                if affects
                else "INFO",
                "requires_legal_review": bool(
                    affects
                    and cat in ("DISPUTE_RESOLUTION", "FEE_AND_PAYMENT", "LIABILITY")
                ),
                "samples": [
                    {
                        "template_id": m["template_id"],
                        "clause_number": m["clause_number"],
                        "original_text": m["original_text"][:180],
                        "source_node_ids": m["source_node_ids"][:3],
                    }
                    for m in members[:4]
                ],
            }
            cat_entries.append(entry)
            rows_for_md.append(
                f"| {cat} | {idx} | {diff_level} | {len(members)} | {len(texts)} "
                f"| {','.join(params[:6])} | {'是' if affects else '否'} "
                f"| {entry['risk_candidate']} | {'是' if entry['requires_legal_review'] else '否'} |"
            )
        out[cat] = cat_entries

    return out, _render_diff_markdown(out, rows_for_md)


# ---------------- 3. 字段 Schema ----------------
FIELD_NAME_MAP = {
    # 基础签约主体
    "合同编号": ("contract_number", "string", True),
    "甲方": ("party_a_name", "string", True),
    "乙方": ("party_b_name", "string", True),
    "地址": ("address", "string", False),
    "统一社会信用代码/身份证号": ("party_id_card", "string", True),
    # 专利标的
    "发明名称/申请号/专利号": ("patent_number", "string", True),
    "专利名称": ("patent_name", "string", True),
    "专利号": ("patent_number", "string", True),
    "申请号": ("application_number", "string", True),
    "专利权人": ("patentee_name", "string", True),
    "专利权人名称": ("patentee_name", "string", True),
    "申请人名称": ("applicant_name", "string", True),
    "专利状态": ("patent_status", "string", False),
    "年费期限": ("annual_fee_period", "string", True),
    # 商标标的
    "商标图样/文字": ("trademark_text", "string", True),
    "商标图样": ("trademark_image", "string", True),
    "商标名称": ("trademark_name", "string", True),
    "注册号": ("trademark_reg_no", "string", True),
    "类别": ("nice_class", "string", True),
    "类似群": ("sub_class", "string", False),
    # 转让与许可
    "转让人名称": ("transferor_name", "string", True),
    "受让人名称": ("transferee_name", "string", True),
    "转让人": ("transferor_name", "string", True),
    "受让人": ("transferee_name", "string", True),
    "许可人名称": ("licensor_name", "string", True),
    "被许可人名称": ("licensee_name", "string", True),
    # 费用与财务
    "代收代付官费(元/件)": ("official_fee", "decimal", True),
    "代收代付官费（元/件）": ("official_fee", "decimal", True),
    "含税服务费(元/件)": ("service_fee", "decimal", True),
    "含税服务费（元/件）": ("service_fee", "decimal", True),
    "代理费": ("agency_fee", "decimal", True),
    "官费": ("official_fee", "decimal", True),
    "总金额": ("total_amount", "decimal", True),
    "费用总计": ("total_fee", "decimal", True),
    "数量": ("quantity", "integer", True),
    "件数": ("item_count", "integer", True),
    # 联系机制
    "联系人": ("contact_name", "string", True),
    "电子邮件": ("contact_email", "string", False),
    "联系电话/手机": ("contact_phone", "string", False),
    "联系地址": ("contact_address", "string", False),
    "专属联系人": ("party_b_contact", "string", True),
    "专属联系电话": ("party_b_phone", "string", True),
    "专属邮箱": ("party_b_email", "string", True),
    "总服务监督邮箱": ("supervision_email", "string", True),
}


def _mk_field(key: str, name: str, dtype: str, req: bool) -> dict:
    return {
        "field_key": key,
        "field_name": name,
        "data_type": dtype,
        "required_candidate": req,
        "required_approved": None,
        "source_template_ids": [],
        "source_template_files": [],
        "source_node_ids": [],
        "validation_rules": [],
        "semantic_description": "",
        "placeholder_status": "正文级",
    }


def build_field_schema(layer_a: dict, data: dict) -> dict:
    src_by_tid = {d["template_id"]: d["source_file"] for d in data.get("imported", [])}
    fields: dict[str, dict] = {}

    for ts in layer_a.get("table_schemas", []):
        for f in ts.get("fields", []):
            name = f["field_name"].strip()
            key, dtype, req_cand = FIELD_NAME_MAP.get(
                name, (f["field_key"], "string", False)
            )
            slot = "留空待填" if not f.get("sample_value") else "含样例值"
            rec = fields.setdefault(
                key,
                {
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
                },
            )
            rec["source_template_ids"].append(ts["template_id"])
            rec["source_template_files"].append(src_by_tid.get(ts["template_id"], ""))
            rec["source_node_ids"].extend(f.get("source_node_ids", []))

    ph_re = re.compile(r"\{\{([^{}]+)\}\}")
    for d in data.get("imported", []):
        tid = d["template_id"]
        for c in d.get("clauses") or []:
            text = c["original_text"]
            for ph in ph_re.findall(text):
                ph_clean = ph.strip()
                f_key = f"var_{ph_clean}"
                rec = fields.setdefault(
                    f_key,
                    {
                        "field_key": f_key,
                        "field_name": f"占位符: {ph_clean}",
                        "data_type": "string",
                        "required_candidate": True,
                        "required_approved": None,
                        "source_template_ids": [],
                        "source_template_files": [],
                        "source_node_ids": [],
                        "validation_rules": [
                            {
                                "type": "TEMPLATE_PLACEHOLDER",
                                "value": f"{{{{{ph_clean}}}}}",
                            }
                        ],
                        "semantic_description": f"Word标准模板动态占位符 {{{{{ph_clean}}}}}",
                        "placeholder_status": "动态插槽变量",
                    },
                )
                rec["source_template_ids"].append(tid)
                rec["source_template_files"].append(src_by_tid.get(tid, ""))
                rec["source_node_ids"].extend(c.get("source_node_ids", [])[:2])

            if "合同编号" in text:
                fields.setdefault(
                    "contract_number",
                    _mk_field("contract_number", "合同编号", "string", True),
                )
                fields["contract_number"]["source_template_ids"].append(tid)
            if "费用总额为人民币" in text or "费用总计" in text:
                fields.setdefault(
                    "contract_total",
                    _mk_field("contract_total", "合同总金额", "decimal", True),
                )
                fields["contract_total"]["source_template_ids"].append(tid)
            if "税率" in text:
                fields.setdefault(
                    "tax_rate", _mk_field("tax_rate", "税率", "decimal", True)
                )
                fields["tax_rate"]["source_template_ids"].append(tid)
                m = re.search(r"税率\s*(\d+)\s*%", text)
                if m:
                    fields["tax_rate"]["validation_rules"].append(
                        {
                            "type": "EXPECTED_VALUE",
                            "value": m.group(1) + "%",
                            "note": "企业标准发票税率",
                        }
                    )
            if "工作日内" in text and "支付" in text:
                fields.setdefault(
                    "payment_deadline",
                    _mk_field("payment_deadline", "付款时间", "string", True),
                )
                fields["payment_deadline"]["source_template_ids"].append(tid)

    for f in fields.values():
        f["source_template_ids"] = sorted(set(f["source_template_ids"]))
        f["source_template_files"] = sorted(set(f["source_template_files"]))
        if not f["semantic_description"]:
            f["semantic_description"] = (
                f"业务字段『{f['field_name']}』（来源模板 {len(f['source_template_ids'])} 份）"
            )

    return {
        "fields": list(fields.values()),
        "field_count": len(fields),
        "note": "包含表格列头字段与 Word {{...}} 动态插槽变量；required_approved 保持 None 待法务审批。",
    }


# ---------------- 4. 规则候选（治理红线：候选规则一律为 DRAFT，严禁自动置 APPROVED） ----------------
def build_rule_candidates(layer_a: dict, data: dict, field_schema: dict) -> dict:
    clauses = layer_a["clauses"]

    def find_clauses(pred, limit: int = 6) -> list[dict]:
        return [
            {
                "clause_id": c["clause_id"],
                "template_id": c["template_id"],
                "clause_number": c["clause_number"],
                "text": c["original_text"][:120],
            }
            for c in clauses
            if pred(c)
        ][:limit]

    rules: list[dict[str, Any]] = []

    # R1: 金额一致性
    amt = find_clauses(
        lambda c: "圆整" in c["original_text"] and "¥" in c["original_text"]
    )
    rules.append(
        {
            "rule_id": "AMOUNT_CASE_CONSISTENCY",
            "name": "合同金额大小写一致性",
            "contract_types": ["*"],
            "rule_type": "FINANCIAL_CALCULATION",
            "execution_mode": "DETERMINISTIC",
            "severity": "HIGH",
            "parameters": {
                "decimal_class": "Decimal",
                "fields": ["contract_total", "费用表合计"],
            },
            "source_clauses": amt,
            "status": "DRAFT",
            "approved_by": None,
            "effective_at": None,
            "evidence_note": "合同费用条款『人民币__圆整（¥__元）』双写法，大写金额与阿拉伯数字必须一致",
        }
    )

    # R2: 发票税率合规
    inv = find_clauses(lambda c: "增值税专用发票" in c["original_text"])
    rules.append(
        {
            "rule_id": "INVOICE_TYPE_CONSISTENCY",
            "name": "代理费/官费开票类型一致性",
            "contract_types": ["*"],
            "rule_type": "CROSS_FIELD_CONSISTENCY",
            "execution_mode": "DETERMINISTIC",
            "severity": "HIGH",
            "parameters": {
                "service_fee_invoice": "增值税专用发票（6%）",
                "official_fee_receipt": "官方收据（代收代付）",
            },
            "source_clauses": inv,
            "status": "DRAFT",
            "approved_by": None,
            "effective_at": None,
            "evidence_note": "服务费开具6%增值税专用发票，官费为代收代付由官方开具收据",
            "external_data_dependency": "业财系统合同流程-开票类型",
        }
    )

    # R3: 代理费底价校验（文档未给出底价数值明细，严禁编造，标记为 MANUAL_REVIEW + DRAFT）
    rules.append(
        {
            "rule_id": "AGENCY_FEE_FLOOR",
            "name": "服务费不低于系统产品底价",
            "contract_types": ["*"],
            "rule_type": "BUSINESS_CONSTRAINT",
            "execution_mode": "MANUAL_REVIEW",
            "severity": "HIGH",
            "parameters": {"floor_price": None},
            "source_clauses": amt[:2],
            "status": "DRAFT",
            "approved_by": None,
            "effective_at": None,
            "evidence_note": "依据《新系统合同审批注意事项》代理费不得低于系统设定底价；因底价明细表在业财外部库，禁止虚构底价数值",
            "external_data_dependency": "业财系统产品底价主数据表（crm_business_product）",
        }
    )

    # R4: 重复预审处罚禁令（候选态 DRAFT，需法务与质检部审批后转 APPROVED）
    prescreen_clauses = find_clauses(lambda c: "永久取消资格" in c["original_text"])
    rules.append(
        {
            "rule_id": "PRESCREEN_REPEAT_SUBMISSION_BAN",
            "name": "专利快速预审重复递交严查承诺",
            "contract_types": ["PATENT_APPLICATION", "PATENT_FAST_PRESCREEN"],
            "rule_type": "CLAUSE_VARIATION",
            "execution_mode": "DETERMINISTIC",
            "severity": "HIGH",
            "parameters": {
                "clause_category": "PARTY_A_OBLIGATIONS",
                "must_contain": ["永久取消资格"],
            },
            "source_clauses": prescreen_clauses,
            "status": "DRAFT",
            "approved_by": None,
            "effective_at": None,
            "evidence_note": "国内专利申请与预审模板第三条明确承诺不得重复在其他机构预审",
        }
    )

    # R5: 12组同分类排他决策规则候选（保持 DRAFT 待人工审批发布）
    for grp in DISAMBIGUATION_GROUPS:
        rules.append(
            {
                "rule_id": f"DISAMBIGUATION_{grp['group_id']}",
                "name": f"【第4~5级排他规则】{grp['group_name']}",
                "contract_types": [grp["group_id"]],
                "rule_type": "BUSINESS_CONSTRAINT",
                "execution_mode": "MANUAL_REVIEW",
                "severity": "HIGH",
                "parameters": {
                    "shared_db_mapping": grp["shared_db_mapping"],
                    "conflict_templates": grp["conflict_templates"].split("、"),
                    "decision_tree": grp["decision_logic"],
                    "note": f"排他决策路由校验：{grp['group_name']}",
                },
                "source_clauses": [],
                "status": "DRAFT",
                "approved_by": None,
                "effective_at": None,
                "evidence_note": f"解决 {grp['conflict_templates']} 同大类模板选型混淆：{grp['decision_logic'][:100]}…",
            }
        )

    # R6: 产品与模板强约束绑定候选
    rules.append(
        {
            "rule_id": "PRODUCT_TEMPLATE_MAPPING_LOCK",
            "name": "产品编码-标准合同模板排他绑定校验",
            "contract_types": ["*"],
            "rule_type": "BUSINESS_CONSTRAINT",
            "execution_mode": "MANUAL_REVIEW",
            "severity": "HIGH",
            "parameters": {
                "total_bound_products": 603,
                "authorized_templates_count": 78,
                "note": "核验业务系统商机产品编码与标准模板绑定表一致性",
            },
            "source_clauses": [],
            "status": "DRAFT",
            "approved_by": None,
            "effective_at": None,
            "evidence_note": "商机勾选的产品编码需与 contract_template_products 授权表吻合",
            "external_data_dependency": "tender_contract.contract_template_products",
        }
    )

    return {
        "rules": rules,
        "rule_count": len(rules),
        "note": "蒸馏生成的候选规则一律处于 DRAFT 状态，须经法务/管理员显式审批后方可强制生效。",
    }


def _write_reports(data: dict, taxonomy: dict, fs: dict, rc: dict, diff: dict) -> None:
    lines = [
        "# 合同分类体系报告（taxonomy-report · 全量78套标准模板）",
        "",
        f"- 模板总数：{len(taxonomy['templates'])}",
        f"- 一级业务板块：{', '.join(taxonomy['levels']['L1_categories'])}",
        f"- 二级业务子类数：{len(taxonomy['levels']['L2_subtypes'])}",
        f"- 绑定的新系统产品总数：{taxonomy['statistics']['total_bound_products']} 项",
        f"- 同分类易混排他决策组数：{taxonomy['statistics']['disambiguation_groups_count']} 组",
        "",
        "| 模板编号 | 模板名称 | 一级板块 | 二级业务 | 细分场景约束 | 绑定产品数 | 核心排他决策红线 |",
        "|---|---|---|---|---|---|---|",
    ]
    for t in taxonomy["templates"]:
        ev_rule = (
            t["disambiguation_rule"][:45] + "…"
            if len(t["disambiguation_rule"]) > 45
            else t["disambiguation_rule"]
        )
        scene = (
            t["sub_scene"][:28] + "…" if len(t["sub_scene"]) > 28 else t["sub_scene"]
        )
        lines.append(
            f"| `{t['template_id']}` | {t['source_file'][:30]} | {t['l1_sector']} | {t['l2_matter']} "
            f"| {scene} | **{t['new_product_count']}** | {ev_rule} |"
        )
    (config.REPORT_DIR / "taxonomy-report.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )

    lines = [
        "# 字段 Schema 蒸馏报告（schema-extraction-report · 全量78套模板）",
        "",
        f"- 抽取字段总数：{fs['field_count']}",
        "- 涵盖 Word {{...}} 动态插槽变量与业务表格数据列头",
        "",
        "| field_key | 字段名 | 类型 | 必填候选 | 来源模板数 | 占位状态 | 说明/样例 |",
        "|---|---|---|---|---|---|---|",
    ]
    for f in fs["fields"]:
        lines.append(
            f"| `{f['field_key']}` | {f['field_name']} | `{f['data_type']}` | {'是' if f['required_candidate'] else '否'} "
            f"| {len(f['source_template_ids'])} | {f['placeholder_status']} | {f['semantic_description'][:40]} |"
        )
    (config.REPORT_DIR / "schema-extraction-report.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )

    lines = [
        "# 企业合同审核与排他规则候选（proposed-rules · 全量78套模板）",
        "",
        f"> 共提炼 {rc['rule_count']} 项关键业务规则与排他决策树（全部为 DRAFT 待审批状态）。",
        "",
    ]
    for r in rc["rules"]:
        lines += [
            f"## {r['rule_id']} — {r['name']}",
            "",
            f"- **规则类型**：`{r['rule_type']}` ｜ **执行模式**：`{r['execution_mode']}` ｜ **严重等级**：`{r['severity']}`",
            f"- **适用合同范围**：{', '.join(r['contract_types'])}",
            f"- **审批状态**：`{r['status']}`",
            f"- **规则依据与证据**：{r['evidence_note']}",
        ]
        if r.get("external_data_dependency"):
            lines.append(f"- **外部系统依赖**：`{r['external_data_dependency']}`")
        if r.get("parameters", {}).get("decision_tree"):
            lines.append(
                f"- **决策树详细逻辑**：\n```text\n{r['parameters']['decision_tree']}\n```"
            )
        lines.append("")
    (config.REPORT_DIR / "proposed-rules.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )

    lines = [
        "# 未解决业务与系统对接问题（unresolved-business-questions）",
        "",
        "| # | 待明确问题 | 当前证据与现状 | 影响范围 | 责任部门 |",
        "|---|---|---|---|---|",
        "| 1 | 代理费底价各产品真实数值 | 《审批注意事项》要求不能低于系统底价，但文档未含明细表 | AGENCY_FEE_FLOOR 自动校验 | 业财/财务部 |",
        "| 2 | 老ERP合同历史导入字段对齐 | 老系统有存量合同，部分历史数据缺失 auth_item 字段 | 历史合同归档与回溯 | IT运维部 |",
        "| 3 | 涉外专利外所费实报实销汇率标准 | 分阶段费用版涉外专利模板中外所律师费与官费按外币结算 | 结算换汇与超额审批 | 财务/涉外部 |",
        "| 4 | 批量维权打假执行回款扣减口径 | 全风险代理合同中回款扣除差旅诉讼费后分成比例的确认机制 | 诉讼尾款结算与提成 | 法律服务部 |",
    ]
    (config.REPORT_DIR / "unresolved-business-questions.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )

    lines = [
        "# 模板结构与段落表格分析（template-structure-analysis · 全量78套模板）",
        "",
        "| 模板编号 | 原始文件名 | 段落数 | 表格数 | 表格规格 | 条款数 | 无法读取异常 |",
        "|---|---|---|---|---|---|---|",
    ]
    for d in data["imported"]:
        dims = "; ".join(
            f"{t.get('row_count', t.get('rows', '?'))}x{t.get('col_count', t.get('cols', '?'))}"
            for t in d.get("table_schemas", [])[:3]
        )
        unhandled = (
            ", ".join(d["unhandled_structures"][:2])
            if d["unhandled_structures"]
            else "无"
        )
        lines.append(
            f"| `{d['template_id']}` | {d['source_file'][:32]} | {d['paragraph_count']} | {d['table_count']} | {dims} "
            f"| {d['clause_count']} | {unhandled} |"
        )
    (config.REPORT_DIR / "template-structure-analysis.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def run() -> None:
    config.ensure_dirs()
    data = json.loads(
        (config.DATA_DIR / "import_result.json").read_text(encoding="utf-8")
    )
    layer_a = json.loads(
        (config.DATA_DIR / "layer_a_raw.json").read_text(encoding="utf-8")
    )
    imp_idx = {d["template_id"]: d for d in data["imported"]}

    taxonomy = build_taxonomy(data)
    (config.DATA_DIR / "taxonomy.json").write_text(
        json.dumps(taxonomy, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    diff, md = build_clause_diff(layer_a, imp_idx)
    (config.DATA_DIR / "clause_diff.json").write_text(
        json.dumps(diff, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (config.REPORT_DIR / "clause-diff-matrix.md").write_text(
        "\n".join(md), encoding="utf-8"
    )

    fs = build_field_schema(layer_a, data)
    (config.DATA_DIR / "field_schema.json").write_text(
        json.dumps(fs, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    rc = build_rule_candidates(layer_a, data, fs)
    (config.DATA_DIR / "rule_candidates.json").write_text(
        json.dumps(rc, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    _write_reports(data, taxonomy, fs, rc, diff)
    print(
        f"SUCCESS_DISTILL: taxonomy={len(taxonomy['templates'])} diff_cats={len(diff)} fields={fs['field_count']} rules={rc['rule_count']}"
    )


if __name__ == "__main__":
    run()
