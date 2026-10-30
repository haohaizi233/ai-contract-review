# -*- coding: utf-8 -*-
"""审核引擎：解析 → 分类 → 模板匹配 → 字段抽取 → 确定性规则 → RAG → 语义审核（降级） → 合并去重 → 证据校验。

关键约束：
- 金额一律 Decimal，禁止 float 参与财务计算；
- 证据不足时返回 INSUFFICIENT_EVIDENCE / NEEDS_MANUAL_REVIEW / NOT_CHECKED，不推定违规；
- source_reference 必须指向真实节点，不允许伪造位置；
- CUSTOM 模式不允许覆盖系统强制核心规则（CORE_RULE_IDS）；
- LLM 未配置时语义审核降级为确定性模式，如实标注 NOT_CHECKED。
"""
from __future__ import annotations

import re
import uuid
import json
from dataclasses import dataclass, field, asdict
from decimal import Decimal, InvalidOperation

from .ingest import import_template_file, security_check_file
from .storage import Storage


class _ParsedView:
    """把 ImportedDoc.ast dict 适配成 format_auditor 所需的对象视图。"""
    def __init__(self, ast: dict, document_id: str):
        self.document_id = document_id
        self.nodes = [_NodeView(n) for n in ast.get("nodes", [])]
        self.table_dims = [
            {"table_index": i, "rows": sum(1 for n in self.nodes
                                           if n.node_type == "table_cell" and n.table_index == i
                                           and n.row_index == max((x.row_index for x in self.nodes
                                                                  if x.node_type == "table_cell"
                                                                  and x.table_index == i), default=-1)) + 1,
             "cols": max((n.column_index for n in self.nodes
                          if n.node_type == "table_cell" and n.table_index == i), default=-1) + 1}
            for i in sorted({n.table_index for n in self.nodes
                             if n.node_type == "table_cell" and n.table_index is not None})]
        self.headers = ast.get("headers", [])
        self.footers = ast.get("footers", [])
        self.page_margins_cm = None   # 解析层暂未提取 pgMar → FORMAT_UNREADABLE 如实标注


class _NodeView:
    def __init__(self, d: dict):
        self.__dict__.update({k: d.get(k) for k in
                              ("node_id", "node_type", "order", "text", "section",
                               "clause_number", "table_index", "row_index", "column_index",
                               "style", "source_locator")})


def audit_format_from_rec(rec) -> dict:
    from .format_auditor import audit_format
    view = _ParsedView(rec.ast or {}, rec.template_id)
    return audit_format(view)

# 系统强制核心规则：CUSTOM 用户要求不得覆盖
CORE_RULE_IDS = {
    "AMOUNT_CASE_CONSISTENCY", "CONTRACT_NUMBER_BACKFILL", "PARTY_ATTACHMENT_CONSISTENCY",
    "DISPUTE_JURISDICTION_STANDARD", "CLAUSE_REQUIRED_CONFIDENTIALITY", "CLAUSE_REQUIRED_LIABILITY",
}

SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}


@dataclass
class SourceReference:
    document_id: str
    node_ids: list = field(default_factory=list)
    clause_number: str | None = None
    original_text: str = ""


@dataclass
class Issue:
    issue_id: str
    rule_id: str | None
    severity: str
    category: str
    title: str
    description: str
    expected_value: object = None
    actual_value: object = None
    source_reference: SourceReference | None = None
    knowledge_references: list = field(default_factory=list)
    suggestion: str = ""
    execution_mode: str = "DETERMINISTIC"
    requires_human_review: bool = False
    confidence: float | None = None
    check_status: str = "CHECKED"   # CHECKED | INSUFFICIENT_EVIDENCE | NEEDS_MANUAL_REVIEW | NOT_CHECKED

    def to_dict(self):
        d = asdict(self)
        return d


def _dec(s: str) -> Decimal | None:
    try:
        return Decimal(s.replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        return None


def _cn_num_to_int(s: str) -> int | None:
    """中文大写金额 → 整数（支持 到 仟万位 + 零/整）。"""
    digits = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
              "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5, "陆": 6, "柒": 7, "捌": 8, "玖": 9}
    units = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000,
             "万": 10000, "萬": 10000, "亿": 100000000}
    s = re.sub(r"[圆圆整元\s]", "", s)
    if not s:
        return None
    total, section, num = 0, 0, 0
    ok = False
    for ch in s:
        if ch in digits:
            num = digits[ch]
            ok = True
        elif ch in units:
            u = units[ch]
            if u == 10000 or u == 100000000:
                section = (section + num) * u
                total += section
                section, num = 0, 0
            else:
                section += (num if num else 1) * u
                num = 0
            ok = True
        else:
            return None
    total += section + num
    return total if ok else None


class ReviewEngine:
    def __init__(self, storage: Storage, llm_provider=None, llm_reviewer=None):
        self.storage = storage
        self._llm_provider = llm_provider
        self._llm_reviewer = llm_reviewer

    # ---------- 主链路 ----------
    def review(self, docx_path, mode: str = "STANDARD",
               custom_requirements: list[str] | None = None) -> dict:
        custom_requirements = custom_requirements or []
        issues: list[Issue] = []
        task_id = uuid.uuid4().hex[:12]

        # 1. 安全 + 解析
        sec = security_check_file(docx_path)
        if not sec.passed:
            return {"task_id": task_id, "status": "REJECTED",
                    "reason": f"security_check_failed: {sec.rejected_reason}", "issues": []}
        rec, err = import_template_file(docx_path)
        if rec is None:
            return {"task_id": task_id, "status": "PARSE_FAILED", "reason": err, "issues": []}

        clauses = [{"clause_id": c["clause_id"], "clause_number": c["clause_number"],
                    "article_title": c["article_title"], "clause_category": c["clause_category"],
                    "clause_subcategory": c["clause_subcategory"], "original_text": c["original_text"],
                    "source_node_ids": c["source_node_ids"], "source_locator": c["source_locator"]}
                   for c in (rec.clauses or [])]
        # clauses 在 ImportedDoc 中是 dict
        if rec.clauses and isinstance(rec.clauses[0], dict):
            clauses = rec.clauses

        # 2. 业务类型识别
        subtype, category = rec.subtype, rec.category
        if rec.classification_confidence < 0.7 or rec.classification_conflicts:
            issues.append(Issue(
                issue_id=uuid.uuid4().hex[:10], rule_id=None, severity="LOW",
                category="CLASSIFICATION_UNCERTAIN", title="合同业务类型识别不确定",
                description=f"识别结果 {subtype} 置信度 {rec.classification_confidence}，"
                            f"冲突：{rec.classification_conflicts or '无'}，建议人工确认业务类型",
                actual_value={"subtype": subtype, "confidence": rec.classification_confidence},
                requires_human_review=True, check_status="NEEDS_MANUAL_REVIEW",
                execution_mode="DETERMINISTIC"))

        # 3. 模板匹配（按 subtype 找有效模板版本）
        matched = self._match_template(subtype)
        if matched is None:
            issues.append(Issue(
                issue_id=uuid.uuid4().hex[:10], rule_id=None, severity="MEDIUM",
                category="TEMPLATE_NOT_FOUND", title="未匹配到有效标准模板版本",
                description=f"按业务类型 {subtype} 未在知识库找到对应标准模板，"
                            "无法执行模板差异比对；其他确定性检查继续执行",
                actual_value=subtype, check_status="INSUFFICIENT_EVIDENCE",
                execution_mode="DETERMINISTIC"))

        # 4-5. 字段抽取 + 结构化校验（确定性）
        issues.extend(self._check_contract_number(rec, clauses))
        issues.extend(self._check_amount_consistency(rec, clauses))
        issues.extend(self._check_cross_amount_consistency(rec, clauses))
        issues.extend(self._check_fee_table(clauses, rec))
        issues.extend(self._check_required_clauses(clauses, subtype))
        issues.extend(self._check_dispute_jurisdiction(clauses))
        issues.extend(self._check_invoice_terms(clauses))
        issues.extend(self._check_late_fee(clauses))
        issues.extend(self._check_party_consistency(rec))
        issues.extend(self._check_payment_deadline(clauses))
        issues.extend(self._check_identifier_format(clauses))
        issues.extend(self._check_contradiction(clauses))

        # 4b. Word 格式审核（Phase 4）
        fmt = audit_format_from_rec(rec)
        for fi in fmt["issues"]:
            if fi["issue_type"] == "NON_COMPLIANT":
                sev = "MEDIUM" if fi.get("severity") == "MEDIUM" else "LOW"
                issues.append(Issue(
                    issue_id=uuid.uuid4().hex[:10], rule_id=f"FORMAT:{fi['check']}",
                    severity=sev, category="FORMAT_NON_COMPLIANT",
                    title=f"格式不符标准：{fi['check']}", description=fi["description"],
                    expected_value=fi.get("expected"), actual_value=fi.get("actual"),
                    execution_mode="DETERMINISTIC", requires_human_review=False))
            elif fi["issue_type"] == "TEMPLATE_DIFF":
                issues.append(Issue(
                    issue_id=uuid.uuid4().hex[:10], rule_id=f"FORMAT:{fi['check']}",
                    severity="LOW", category="FORMAT_TEMPLATE_DIFF",
                    title=f"格式与模板差异：{fi['check']}", description=fi["description"],
                    expected_value=fi.get("expected"), actual_value=fi.get("actual"),
                    execution_mode="DETERMINISTIC", requires_human_review=True,
                    check_status="NEEDS_MANUAL_REVIEW"))
            else:  # FORMAT_UNREADABLE
                issues.append(Issue(
                    issue_id=uuid.uuid4().hex[:10], rule_id=f"FORMAT:{fi['check']}",
                    severity="INFO", category="FORMAT_UNREADABLE",
                    title=f"格式信息不可读：{fi['check']}", description=fi["description"],
                    execution_mode="DETERMINISTIC", requires_human_review=True,
                    check_status="INSUFFICIENT_EVIDENCE"))

        # 6. 确定性规则引擎（审批通过的规则才强制执行；DRAFT 仅提示）
        issues.extend(self._run_rules(rec, clauses, subtype))

        # 7. RAG 检索标准条款（为语义审核提供上下文；无 LLM 时仅输出检索证据）
        rag_context = []
        for kw in ["费用及支付方式", "违约责任", "争议解决", "保密"]:
            for h in self.storage.search(kw, top_k=3, contract_subtype=subtype,
                                         rerank=True)[:2]:
                rag_context.append({"query": kw, "clause_id": h["clause_id"],
                                    "document_id": h["document_id"], "text": h["text"][:120]})

        # 8. LLM 语义审核（含用户自定义要求真交 LLM 分析）
        from .llm_review import LLMSemanticReviewer
        llm = getattr(self, "_llm_reviewer", None) or LLMSemanticReviewer(
            provider=getattr(self, "_llm_provider", None))
        contract_text = "\n".join(c.get("original_text", "") for c in clauses)
        custom_reqs = custom_requirements if mode in ("CUSTOM", "COMBINED") else []
        llm_result = llm.review(contract_text, rag_context, custom_reqs)
        if llm_result.status == "NOT_CHECKED":
            issues.append(Issue(
                issue_id=uuid.uuid4().hex[:10], rule_id="LLM_SEMANTIC_REVIEW",
                severity="INFO", category="NOT_CHECKED", title="LLM 语义审核未执行",
                description="当前环境未配置授权 LLM 提供商。依据数据治理要求，"
                            "未经配置不得向外部大模型传输合同内容。语义级审查标记为 NOT_CHECKED，"
                            "本次结果仅包含确定性规则检查。",
                execution_mode="LLM_SEMANTIC", check_status="NOT_CHECKED",
                requires_human_review=True))
            # 自定义要求未获 LLM 分析时如实标注（不伪称已审）
            for req in custom_reqs:
                issues.append(Issue(
                    issue_id=uuid.uuid4().hex[:10], rule_id="CUSTOM_REQUIREMENT",
                    severity="LOW", category="MANUAL_REVIEW",
                    title=f"自定义要求未分析：{req[:30]}",
                    description=f"要求『{req}』需 LLM 语义分析，当前未配置 LLM，"
                                "标记 NOT_CHECKED，需人工复核。",
                    execution_mode="MANUAL_REVIEW", requires_human_review=True,
                    check_status="NOT_CHECKED"))
        else:
            for f in llm_result.findings:
                issues.append(Issue(
                    issue_id=uuid.uuid4().hex[:10], rule_id=f"LLM:{f.category}",
                    severity=f.severity, category=f.category,
                    title=f.title, description=f.description,
                    source_reference=SourceReference(rec.template_id, [], None, f.original_text)
                    if f.original_text else None,
                    suggestion=f.suggestion, execution_mode="LLM_SEMANTIC",
                    requires_human_review=f.requires_human_review,
                    confidence=f.confidence,
                    check_status="NEEDS_MANUAL_REVIEW" if f.requires_human_review else "CHECKED"))
            for cr in llm_result.custom_requirement_results:
                verdict = cr.get("verdict", "不确定")
                issues.append(Issue(
                    issue_id=uuid.uuid4().hex[:10], rule_id="CUSTOM_REQUIREMENT",
                    severity="LOW" if verdict != "不满足" else "MEDIUM",
                    category="CUSTOM_REQUIREMENT_RESULT",
                    title=f"自定义要求结论：{verdict}",
                    description=f"要求『{cr.get('requirement','')}』→ {verdict}；"
                                f"证据：{cr.get('evidence','')}",
                    execution_mode="LLM_SEMANTIC",
                    requires_human_review=True,
                    confidence=cr.get("confidence")))
            if llm_result.failure:
                issues.append(Issue(
                    issue_id=uuid.uuid4().hex[:10], rule_id="LLM_SEMANTIC_REVIEW",
                    severity="INFO", category="NOT_CHECKED",
                    title=f"LLM 审核部分失败：{llm_result.failure}",
                    description=f"失败类型 {llm_result.failure}，已降级处理，不伪造结论。",
                    execution_mode="LLM_SEMANTIC", check_status="NEEDS_MANUAL_REVIEW",
                    requires_human_review=True))
        if mode in ("STANDARD", "COMBINED") and matched:
            issues.extend(self._diff_vs_template(rec, clauses, matched))

        # 10. 去重 + 证据校验
        issues = self._dedupe(issues)
        issues = self._verify_evidence(issues, rec)

        # 排序：严重度 → 类别
        issues.sort(key=lambda i: (SEVERITY_ORDER.get(i.severity, 9), i.category))

        task = {"task_id": task_id, "document_id": rec.template_id,
                "source_file": rec.source_file, "mode": mode,
                "category": category, "subtype": subtype,
                "classification_confidence": rec.classification_confidence,
                "matched_template_id": matched["document_id"] if matched else None,
                "status": "COMPLETED",
                "issue_count": len(issues),
                "rag_context": rag_context,
                "issues": [i.to_dict() for i in issues]}
        self.storage.save_task(task_id, rec.template_id, mode, "COMPLETED",
                               {"source_file": rec.source_file, "subtype": subtype})
        self.storage.save_issues(task_id, [i.to_dict() for i in issues])
        return task

    # ---------- 模板匹配 ----------
    def _match_template(self, subtype: str):
        rows = self.storage.conn.execute(
            "SELECT document_id, source_file, source_hash FROM documents "
            "WHERE contract_subtype=? AND status='ACTIVE'", (subtype,)).fetchone()
        return dict(rows) if rows else None

    # ---------- 确定性检查 ----------
    def _first_clause(self, clauses, pred):
        for c in clauses:
            if pred(c):
                return c
        return None

    def _check_contract_number(self, rec, clauses) -> list[Issue]:
        out = []
        node = self._first_clause(clauses, lambda c: "合同编号" in c["original_text"])
        if not node:
            out.append(Issue(
                issue_id=uuid.uuid4().hex[:10], rule_id="CONTRACT_NUMBER_BACKFILL",
                severity="MEDIUM", category="FIELD_REQUIRED", title="缺少合同编号标识",
                description="待审文档未找到『合同编号』标识行。证据不足判断是否违规："
                            "若为合同附件或摘录文档可能不含编号，需人工确认。",
                expected_value="合同编号: <编号>", actual_value=None,
                check_status="INSUFFICIENT_EVIDENCE", requires_human_review=True))
        else:
            text = node["original_text"]
            m = re.search(r"合同编号[:：]\s*(\S+)", text)
            if not m or not m.group(1).strip():
                out.append(Issue(
                    issue_id=uuid.uuid4().hex[:10], rule_id="CONTRACT_NUMBER_BACKFILL",
                    severity="MEDIUM", category="FIELD_REQUIRED", title="合同编号为空",
                    description="『合同编号』存在但未填写。来源：《新系统合同审批注意事项》"
                                "『合同编号需自动体现到合同文件里面』",
                    expected_value="已填写的合同编号", actual_value=text.strip(),
                    source_reference=SourceReference(rec.template_id, node["source_node_ids"],
                                                     node.get("clause_number"), text),
                    suggestion="在合同首部填写合同编号（编码前缀须对应我方签约主体）"))
        return out

    def _check_amount_consistency(self, rec, clauses) -> list[Issue]:
        """大小写金额一致性（Decimal）。"""
        out = []
        for c in clauses:
            t = c["original_text"]
            m = re.search(r"人民币\s*([壹贰叁肆伍陆柒捌玖拾佰仟万亿零一二三四五六七八九十百千万]+)\s*圆整[（(]¥\s*([\d,]+(?:\.\d+)?)\s*元[）)]", t)
            if not m:
                continue
            cn_raw, num_raw = m.group(1), m.group(2)
            cn_val = _cn_num_to_int(cn_raw)
            num_val = _dec(num_raw)
            if cn_val is None or num_val is None:
                out.append(Issue(
                    issue_id=uuid.uuid4().hex[:10], rule_id="AMOUNT_CASE_CONSISTENCY",
                    severity="INFO", category="INSUFFICIENT_EVIDENCE",
                    title="金额大小写无法自动解析",
                    description=f"『人民币{cn_raw}圆整（¥{num_raw}元）』中的大写或小写无法解析，"
                                "标记人工核对，不推定不一致",
                    actual_value=t[:80], check_status="INSUFFICIENT_EVIDENCE",
                    requires_human_review=True,
                    source_reference=SourceReference(c.get("document_id", ""), c["source_node_ids"],
                                                     c.get("clause_number"), t)))
                continue
            if Decimal(cn_val) != num_val:
                out.append(Issue(
                    issue_id=uuid.uuid4().hex[:10], rule_id="AMOUNT_CASE_CONSISTENCY",
                    severity="HIGH", category="AMOUNT_MISMATCH", title="合同金额大小写不一致",
                    description=f"大写『{cn_raw}』= {cn_val}，小写 ¥{num_raw} = {num_val}，两值不一致",
                    expected_value=f"¥{cn_val}", actual_value=f"¥{num_raw}",
                    suggestion="修正大写或小写金额使两值一致（金额使用 Decimal 精确比较）",
                    source_reference=SourceReference(c.get("document_id", ""), c["source_node_ids"],
                                                     c.get("clause_number"), t)))
        return out

    def _check_fee_table(self, clauses, rec) -> list[Issue]:
        """费用明细表：官费×数量 + 服务费×数量 = 合计（Decimal）。"""
        out = []
        for c in clauses:
            t = c["original_text"]
            if "合计" not in t or "圆整" not in t:
                continue
            m = re.search(r"合计[（(]?元?[）)]?\s*[|｜]?\s*人民币\s*([壹贰叁肆伍陆柒捌玖拾佰仟万亿零一二三四五六七八九十百千万]+)\s*圆整[（(]¥\s*([\d,]+(?:\.\d+)?)", t)
            if m:
                cn_val = _cn_num_to_int(m.group(1))
                num_val = _dec(m.group(2))
                if cn_val is not None and num_val is not None and Decimal(cn_val) != num_val:
                    out.append(Issue(
                        issue_id=uuid.uuid4().hex[:10], rule_id="FEE_TABLE_TOTAL",
                        severity="HIGH", category="AMOUNT_MISMATCH", title="费用表合计大小写不一致",
                        description=f"费用表合计大写= {cn_val}，小写= {num_val}",
                        expected_value=str(cn_val), actual_value=str(num_val),
                        source_reference=SourceReference(c.get("document_id", ""), c["source_node_ids"],
                                                         c.get("clause_number"), t)))
        return out

    def _check_cross_amount_consistency(self, rec, clauses) -> list[Issue]:
        """金额勾稽（Decimal）：所有『人民币X圆整（¥Y元）』表述中的金额必须一致。

        仅对含『圆整（¥』的总价类表述做交叉比对；出现多个不同数值时提示不一致。
        """
        out = []
        pair_re = re.compile(
            r"人民币\s*([壹贰叁肆伍陆柒捌玖拾佰仟万亿零一二三四五六七八九十百千万]+)\s*圆整"
            r"[（(]\s*¥\s*([\d,]+(?:\.\d+)?)\s*元?\s*[）)]")
        found: list[tuple[str, str, dict]] = []   # (大写, 小写, clause)
        for c in clauses:
            for m in pair_re.finditer(c["original_text"]):
                found.append((m.group(1), m.group(2), c))
        if len(found) < 2:
            return out
        values = {}
        for cn, num, c in found:
            d = _dec(num)
            if d is not None:
                values.setdefault(str(d), []).append((cn, num, c))
        if len(values) > 1:
            keys = list(values)
            sample = values[keys[0]][0][2]
            detail = "；".join(f"{k}（{len(v)}处）" for k, v in values.items())
            out.append(Issue(
                issue_id=uuid.uuid4().hex[:10], rule_id="AMOUNT_CASE_CONSISTENCY",
                severity="HIGH", category="AMOUNT_MISMATCH",
                title="合同内金额表述互相不一致",
                description=f"合同中出现多个不同的总价金额：{detail}。"
                            "金额以 Decimal 精确比较；请核对费用条款与合计行",
                expected_value=keys[0], actual_value=" / ".join(keys),
                suggestion="统一费用总额与合计行金额（大小写一致且各处一致）",
                execution_mode="DETERMINISTIC",
                source_reference=SourceReference(
                    sample.get("document_id", ""), sample["source_node_ids"],
                    sample.get("clause_number"), sample["original_text"])))
        return out

    def _check_party_consistency(self, rec) -> list[Issue]:
        """主体一致性：首部表格中的甲乙方名称 vs 正文出现的甲方名称。"""
        out = []
        table_names: list[str] = []
        for ts in (rec.table_schemas or []):
            if ts["table_index"] == 0:
                for h in ts["headers"]:
                    if h and ("公司" in h or "事务所" in h or "研究院" in h or "厂" in h or "中心" in h):
                        table_names.append(h.strip())
                    m = re.search(r"(?:甲方|乙方)[:：]\s*([^\s，。；、|]{2,30}?(?:公司|事务所|研究院|厂|中心))", h or "")
                    if m:
                        table_names.append(m.group(1).strip())
                for f in ts["fields"]:
                    v = f.get("sample_value") or ""
                    if v and ("公司" in v or "事务所" in v or "研究院" in v):
                        table_names.append(v.strip())
        text = "\n".join(c["original_text"] for c in (rec.clauses or [])) if rec.clauses else ""
        inline = re.findall(r"甲方[:：]\s*([^\s，。；、|（）()]{2,30}?(?:公司|事务所|研究院|厂|中心))", text)
        for name in inline:
            if not table_names:
                continue
            if not any(name in t or t in name for t in table_names):
                out.append(Issue(
                    issue_id=uuid.uuid4().hex[:10], rule_id="PARTY_ATTACHMENT_CONSISTENCY",
                    severity="HIGH", category="PARTY_MISMATCH",
                    title="甲方名称与首部/附件不一致",
                    description=f"首部表格主体为『{'、'.join(dict.fromkeys(table_names))[:60]}』，"
                                f"正文出现『{name}』，需与合同附件保持一致",
                    expected_value=table_names[0], actual_value=name,
                    suggestion="统一甲方主体名称（《新系统合同审批注意事项》：我方主体、他方主体需与合同附件一致）",
                    requires_human_review=True))
        return out

    def _check_payment_deadline(self, clauses) -> list[Issue]:
        """付款期限：模板惯例 3 个工作日；偏离仅提示（用户可另行约定）。"""
        out = []
        c = self._first_clause(clauses, lambda x: "工作日内" in x["original_text"] and "支付" in x["original_text"])
        if not c:
            return out
        m = re.search(r"(\d+|[一二三四五六七八九十]+)\s*个工作日内向乙方支付", c["original_text"])
        if not m:
            return out
        raw = m.group(1)
        days = int(raw) if raw.isdigit() else _cn_num_to_int(raw + "十") // 10 if raw == "十" else _cn_num_to_int(raw)
        if days is not None and days != 3:
            out.append(Issue(
                issue_id=uuid.uuid4().hex[:10], rule_id="PAYMENT_DEADLINE_CHECK",
                severity="LOW", category="CLAUSE_VARIATION", title="付款期限偏离模板惯例",
                description=f"模板惯例为合同生效起 3 个工作日内付款，待审文档为 {raw} 个工作日。"
                            "付款期限可另行约定，仅作偏离提示",
                expected_value="3 个工作日", actual_value=f"{raw} 个工作日",
                requires_human_review=True, check_status="NEEDS_MANUAL_REVIEW",
                source_reference=SourceReference(c.get("document_id", ""), c["source_node_ids"],
                                                 c.get("clause_number"), c["original_text"])))
        return out

    def _check_identifier_format(self, clauses) -> list[Issue]:
        """标识类字段格式：专利号/申请号（宽松校验，异常才提示）。"""
        out = []
        pat = re.compile(r"(专利号|申请号)[:：]?\s*([A-Za-z0-9\.\-]+)")
        for c in clauses:
            for m in pat.finditer(c["original_text"]):
                val = m.group(2)
                if not re.match(r"^(\d{8,14}(\.\d)?|ZL\s*\d{8,14}(\.\d)?|CN\s*\d+)$", val):
                    out.append(Issue(
                        issue_id=uuid.uuid4().hex[:10], rule_id="IDENTIFIER_FORMAT_CHECK",
                        severity="LOW", category="FIELD_FORMAT",
                        title=f"{m.group(1)}格式疑似异常",
                        description=f"『{m.group(1)}：{val}』不符合常见专利/申请号格式（纯数字 8-14 位可含校验位）。"
                                    "格式校验为宽松启发式，不推定违规，需人工核对",
                        expected_value="8-14 位数字（可含 .校验位）", actual_value=val,
                        requires_human_review=True, check_status="NEEDS_MANUAL_REVIEW",
                        source_reference=SourceReference(c.get("document_id", ""), c["source_node_ids"],
                                                         c.get("clause_number"), c["original_text"])))
        return out

    def _check_contradiction(self, clauses) -> list[Issue]:
        """矛盾条款启发式：付款条款与『无偿/无需支付』并存时提示人工复核。"""
        out = []
        pays = [c for c in clauses if "支付" in c["original_text"] and "费用" in c["original_text"]]
        frees = [c for c in clauses if "无需支付" in c["original_text"] or "无偿" in c["original_text"]]
        if pays and frees:
            p, f = pays[0], frees[0]
            out.append(Issue(
                issue_id=uuid.uuid4().hex[:10], rule_id="CONTRADICTION_CHECK",
                severity="MEDIUM", category="SEMANTIC_RISK", title="疑似矛盾条款：付费与无偿并存",
                description=f"存在付款条款『{p['original_text'][:60]}…』与无偿条款『{f['original_text'][:60]}…』。"
                            "语义矛盾判定需 LLM 或人工，本次仅做关键词级提示",
                actual_value={"payment_clause": p["original_text"][:100],
                              "free_clause": f["original_text"][:100]},
                requires_human_review=True, check_status="NEEDS_MANUAL_REVIEW",
                execution_mode="MANUAL_REVIEW",
                source_reference=SourceReference(f.get("document_id", ""), f["source_node_ids"],
                                                 f.get("clause_number"), f["original_text"])))
        return out

    def _check_required_clauses(self, clauses, subtype) -> list[Issue]:
        out = []
        cats = {c["clause_category"] for c in clauses}
        for cat, name in [("CONFIDENTIALITY", "保密条款"), ("LIABILITY", "违约责任条款"),
                          ("EFFECTIVENESS_AND_TERMINATION", "生效与终止条款")]:
            if cat not in cats:
                out.append(Issue(
                    issue_id=uuid.uuid4().hex[:10], rule_id=f"CLAUSE_REQUIRED_{cat}",
                    severity="MEDIUM", category="CLAUSE_MISSING", title=f"缺少{name}",
                    description=f"待审文档未发现『{name}』（标准模板 26/26 均含该条款）。"
                                "缺失原因可能是删除或摘录，需人工确认",
                    expected_value=name, actual_value=None,
                    suggestion=f"补充{name}（参照企业标准模板文本）",
                    requires_human_review=True, check_status="INSUFFICIENT_EVIDENCE"))
        # 业务专属：预审承诺
        if subtype in ("PATENT_FAST_PRESCREEN", "PATENT_APPLICATION"):
            if not any(c.get("clause_subcategory") == "PRESCREEN_UNDERTAKING" for c in clauses):
                out.append(Issue(
                    issue_id=uuid.uuid4().hex[:10], rule_id="PRESCREEN_UNDERTAKING_REQUIRED",
                    severity="HIGH", category="CLAUSE_MISSING", title="缺少重复预审承诺条款",
                    description=f"业务类型 {subtype} 的标准模板含『重复预审…永久取消资格』承诺条款，"
                                "待审文档未发现该条款",
                    expected_value="PRESCREEN_UNDERTAKING 承诺", actual_value=None,
                    suggestion="补充预审承诺条款（含隐瞒预审历史全额赔偿及费用不退约定）"))
        return out

    def _check_dispute_jurisdiction(self, clauses) -> list[Issue]:
        out = []
        c = self._first_clause(clauses, lambda x: x["clause_category"] == "DISPUTE_RESOLUTION")
        if not c:
            return out
        t = c["original_text"]
        if "深圳市南山区人民法院" in t:
            return out
        if "乙方所在地" in t:
            out.append(Issue(
                issue_id=uuid.uuid4().hex[:10], rule_id="DISPUTE_JURISDICTION_STANDARD",
                severity="HIGH", category="CLAUSE_VARIATION", title="争议管辖条款偏离标准",
                description="管辖约定为『乙方所在地有管辖权的人民法院』，与 23/26 模板的"
                            "『深圳市南山区人民法院』不一致，管辖法院不确定",
                expected_value="深圳市南山区人民法院",
                actual_value="乙方所在地有管辖权的人民法院",
                suggestion="修改为『深圳市南山区人民法院』（或经法务确认的管辖条款）",
                requires_human_review=True,
                source_reference=SourceReference(c.get("document_id", ""), c["source_node_ids"],
                                                 c.get("clause_number"), t)))
        else:
            out.append(Issue(
                issue_id=uuid.uuid4().hex[:10], rule_id="DISPUTE_JURISDICTION_STANDARD",
                severity="MEDIUM", category="CLAUSE_VARIATION", title="争议管辖条款与标准文本不一致",
                description=f"管辖条款未出现标准法院名称，实际文本：{t[:100]}",
                expected_value="深圳市南山区人民法院", actual_value=t[:100],
                requires_human_review=True,
                source_reference=SourceReference(c.get("document_id", ""), c["source_node_ids"],
                                                 c.get("clause_number"), t)))
        return out

    def _check_invoice_terms(self, clauses) -> list[Issue]:
        out = []
        inv = self._first_clause(clauses, lambda c: "发票" in c["original_text"])
        if not inv:
            return out
        t = inv["original_text"]
        if "增值税专用发票" in t and "6%" in t:
            return out
        out.append(Issue(
            issue_id=uuid.uuid4().hex[:10], rule_id="INVOICE_TYPE_CONSISTENCY",
            severity="MEDIUM", category="INVOICE_TERM_VARIANT", title="发票条款未明示 6% 增值税专用发票",
            description=f"发票条款为变体写法：{t[:110]}。其他模板标准写法为"
                        "『服务费增值税专用发票（税率6%）并提供官费官方收据』。"
                        "开票类型须与审批流程填报一致，需人工确认口径",
            expected_value="服务费增值税专用发票（税率6%）+ 官费官方收据",
            actual_value=t[:110], requires_human_review=True,
            check_status="NEEDS_MANUAL_REVIEW",
            source_reference=SourceReference(inv.get("document_id", ""), inv["source_node_ids"],
                                             inv.get("clause_number"), t)))
        return out

    def _check_late_fee(self, clauses) -> list[Issue]:
        out = []
        c = self._first_clause(clauses, lambda x: "违约金" in x["original_text"] and "‰" in x["original_text"])
        if not c:
            return out
        t = c["original_text"]
        m = re.search(r"(\d+)\s*‰", t)
        if m and m.group(1) != "3":
            out.append(Issue(
                issue_id=uuid.uuid4().hex[:10], rule_id="LATE_FEE_RATE",
                severity="MEDIUM", category="CLAUSE_VARIATION", title="逾期违约金比率偏离模板惯例",
                description=f"模板惯例为日 3‰，待审文档为日 {m.group(1)}‰（企业惯例值，非法务强制）",
                expected_value="3‰/日", actual_value=m.group(1) + "‰/日",
                requires_human_review=True,
                source_reference=SourceReference(c.get("document_id", ""), c["source_node_ids"],
                                                 c.get("clause_number"), t)))
        return out

    def _run_rules(self, rec, clauses, subtype) -> list[Issue]:
        """Phase 1.1：通过 RuleEngine 真正执行规则。

        - APPROVED 且生效期内的规则 → 执行器强制检查（FAIL 转 Issue）；
        - DRAFT / 过期 / 类型不适用 / 例外 → SKIP 留痕（不强制）；
        - 每次执行保留 rule_id、version、result、evidence。
        """
        from .rule_engine import RuleEngine, RuleSpec, ReviewContext
        out = []
        specs = []
        for r in self.storage.get_rules():
            try:
                specs.append(RuleSpec(
                    rule_id=r["rule_id"], version=r.get("version", 1), name=r.get("name", ""),
                    rule_type=r["rule_type"], execution_mode=r.get("execution_mode", "DETERMINISTIC"),
                    severity=r.get("severity", "MEDIUM"), priority=r.get("params", {}).get("priority", 100),
                    contract_types=r.get("contract_types", ["*"]),
                    parameters=r.get("params", {}) or {},
                    exceptions=r.get("params", {}).get("exceptions", []) or [],
                    source_clauses=r.get("source_clauses", []),
                    status=r.get("status", "DRAFT"),
                    approved_by=r.get("approved_by"),
                    effective_at=r.get("effective_at"),
                    effective_until=r.get("params", {}).get("effective_until"),
                ))
            except Exception:
                continue
        ctx = ReviewContext(document_id=rec.template_id, subtype=subtype, category=rec.category,
                            clauses=clauses)
        engine = RuleEngine()
        results = engine.execute(specs, ctx)
        # 执行留痕（内存 + 数据库）
        for res in results:
            self.storage.conn.execute(
                "INSERT OR REPLACE INTO rule_executions VALUES (?,?,?,?,?,?,?,?,?)",
                (f"{res.rule_id}:{res.rule_version}:{res.executed_at:.3f}", res.rule_id, res.rule_version,
                 res.rule_type, res.result, res.severity, res.message,
                 json.dumps(res.evidence, ensure_ascii=False), res.executed_at))
            if res.result == "FAIL":
                out.append(Issue(
                    issue_id=uuid.uuid4().hex[:10], rule_id=res.rule_id,
                    severity=res.severity, category="RULE_VIOLATION",
                    title=f"规则不通过：{res.rule_id}",
                    description=f"[v{res.rule_version}] {res.message}",
                    source_reference=SourceReference(
                        rec.template_id, res.evidence.get("node_ids", []),
                        res.evidence.get("clause_number"), "") if res.evidence.get("node_ids") else None,
                    execution_mode="DETERMINISTIC", requires_human_review=True))
            elif res.result == "MANUAL_REQUIRED":
                out.append(Issue(
                    issue_id=uuid.uuid4().hex[:10], rule_id=res.rule_id,
                    severity="LOW", category="MANUAL_REVIEW",
                    title=f"规则需人工复核：{res.rule_id}",
                    description=f"[v{res.rule_version}] {res.message}",
                    execution_mode="MANUAL_REVIEW", requires_human_review=True,
                    check_status="NEEDS_MANUAL_REVIEW"))
            elif res.result == "SKIPPED_DRAFT":
                out.append(Issue(
                    issue_id=uuid.uuid4().hex[:10], rule_id=res.rule_id,
                    severity="INFO", category="RULE_PENDING_APPROVAL",
                    title=f"规则未审批，未强制执行：{res.rule_id}",
                    description=f"[v{res.rule_version}] {res.message}",
                    execution_mode="DETERMINISTIC", check_status="NOT_CHECKED",
                    requires_human_review=False))
        self.storage.conn.commit()
        return out

    def _diff_vs_template(self, rec, clauses, matched) -> list[Issue]:
        """与匹配模板做条款级差异比对（确定性）。"""
        out = []
        tpl_clauses = self.storage.conn.execute(
            "SELECT clause_category, original_text, clause_number FROM clauses WHERE document_id=?",
            (matched["document_id"],)).fetchall()
        tpl_by_cat: dict[str, list] = {}
        for t in tpl_clauses:
            tpl_by_cat.setdefault(t["clause_category"], []).append(t)
        cat_present = {c["clause_category"] for c in clauses}
        for cat, tlist in tpl_by_cat.items():
            if cat not in cat_present and cat in ("CONFIDENTIALITY", "LIABILITY", "DISPUTE_RESOLUTION"):
                out.append(Issue(
                    issue_id=uuid.uuid4().hex[:10], rule_id="TEMPLATE_DIFF",
                    severity="MEDIUM", category="CLAUSE_MISSING",
                    title=f"相对标准模板缺少条款类别：{cat}",
                    description=f"标准模板（{matched['source_file']}）含 {cat} 类条款，待审文档未发现",
                    expected_value=cat, actual_value=None,
                    knowledge_references=[{"document_id": matched["document_id"],
                                           "text": tlist[0]["original_text"][:100]}],
                    requires_human_review=True, check_status="INSUFFICIENT_EVIDENCE"))
        return out

    # ---------- 后处理 ----------
    @staticmethod
    def _dedupe(issues: list[Issue]) -> list[Issue]:
        seen: dict[tuple, Issue] = {}
        for i in issues:
            key = (i.rule_id, i.category, i.title, str(i.actual_value)[:40])
            if key not in seen:
                seen[key] = i
        return list(seen.values())

    @staticmethod
    def _verify_evidence(issues: list[Issue], rec) -> list[Issue]:
        """证据校验：source_reference.node_ids 必须真实存在，否则降级 INSUFFICIENT_EVIDENCE。"""
        valid_ids = set()
        for c in (rec.clauses or []):
            ids = c.get("source_node_ids", []) if isinstance(c, dict) else []
            valid_ids.update(ids)
        for i in issues:
            if i.source_reference is not None:
                ids = i.source_reference.node_ids
                if ids and not set(ids) & valid_ids:
                    i.check_status = "INSUFFICIENT_EVIDENCE"
                    i.description += "（证据校验：引用节点未能在解析结果中核实，已降级为证据不足）"
                elif not ids:
                    i.source_reference = None
        return issues
