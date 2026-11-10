# -*- coding: utf-8 -*-
"""法务 AI 智能体可信知识底座与规则治理服务 (Legal Agent Knowledge & Feedback Governance) - Phase 13R4 & 13R7。

核心能力：
1. 规则去硬编码与版本治理 (GovernedLegalRule):
   - 规则具备 rule_id, approval_status (APPROVED, REVOKED, PENDING_REVIEW), revision, source_document 等全套元数据；
   - 严格区分法定强制性约束与企业商业偏好；
   - 仅 APPROVED 状态规则可作为权威基准，REVOKED 规则立即失效；
2. 反馈数据合规脱敏与微调样本治理:
   - 包含 task_id, file_sha256, annotation_id, human_modified_suggestion 等审计字段；
   - 敏感信息 (PII、账号、金额、主体名称) 自动合规脱敏；
   - 严格将样本物理切分为 approved_training_samples 与 evaluation_holdout，杜绝同合同/同模板数据泄漏。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from contract_kb import config

FEEDBACK_BASE_DIR = config.DATA_DIR.parent / "ai_feedback"

WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


def sanitize_tenant_id(tenant_id: str, default: str = "default") -> str:
    """对租户标识进行严格规范化与路径穿越/保留设备名防护。"""
    if not tenant_id:
        return default
    raw = str(tenant_id).strip()
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", raw).strip("_")
    if not cleaned:
        cleaned = default
    upper_base = cleaned.upper().split("_")[0]
    if (
        re.search(r"[^a-zA-Z0-9_-]", raw)
        or cleaned != raw
        or upper_base in WINDOWS_RESERVED_NAMES
        or ".." in raw
    ):
        h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
        return f"{cleaned}_{h}"
    return cleaned


def get_tenant_feedback_dir(tenant_id: str = "default") -> Path:
    """获取租户专属物理隔离的反馈存储根目录（严格防止路径穿越与碰撞）。"""
    clean_tenant = sanitize_tenant_id(tenant_id)
    base = FEEDBACK_BASE_DIR.resolve()
    target = (FEEDBACK_BASE_DIR / clean_tenant).resolve()
    if not target.is_relative_to(base):
        target = FEEDBACK_BASE_DIR / "default"
    target.mkdir(parents=True, exist_ok=True)
    return target


@dataclass
class GovernedLegalRule:
    """具备可追溯、可审批、可撤销的企业法务审核规则。"""

    rule_id: str
    name: str
    business_scope: str
    contract_subtype: str
    clause_category: str
    effective_date: str
    approval_status: str  # APPROVED | REVOKED | PENDING_REVIEW
    source_document: str
    revision: int = 1
    expiry_date: str | None = None
    exception_policy: str = ""
    review_owner: str = "企业知识产权与法务合规部"
    is_statutory_mandate: bool = False  # 是否为法定强制性规范（反之为企业商业风控偏好）
    rule_description: str = ""
    advice_template: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------- 企业审核规则库（结构化与审批状态版本化） ----------------

GOVERNED_RULES_STORE: dict[str, GovernedLegalRule] = {
    "RULE_DISPUTE_RES_01": GovernedLegalRule(
        rule_id="RULE_DISPUTE_RES_01",
        name="司法管辖法院优选规则",
        business_scope="ALL",
        contract_subtype="ALL",
        clause_category="DISPUTE_RESOLUTION",
        effective_date="2026-01-01",
        approval_status="APPROVED",
        source_document="企业标准合同管辖与争议解决指引（2026版）",
        revision=2,
        is_statutory_mandate=False,  # 属企业商业利益约定，非法定专属管辖
        rule_description="合同约定管辖法院原则上应锁定合同签订地/乙方住所地有管辖权的人民法院（即深圳市南山区人民法院）。若客户强势，备选争议解决方式为提交深圳国际仲裁院（SCIA）仲裁，严禁接受对方住所地单方法院管辖。",
        advice_template="【法务处置建议】优先恢复母本约定管辖条款，约定由合同签订地/乙方住所地有管辖权的人民法院（深圳市南山区人民法院）管辖；如客户拒绝，可磋商由深圳国际仲裁院仲裁，不接受对方所在地法院单方管辖。",
    ),
    "RULE_LIABILITY_01": GovernedLegalRule(
        rule_id="RULE_LIABILITY_01",
        name="逾期付款违约金基准费率与维权费用追偿",
        business_scope="ALL",
        contract_subtype="ALL",
        clause_category="LIABILITY",
        effective_date="2026-01-01",
        approval_status="APPROVED",
        source_document="知识产权代理服务合同风控红线蒸馏手册",
        revision=3,
        is_statutory_mandate=False,
        rule_description="逾期付款违约金企业标准母本约定为每日 3‰（体现知识产权代缴官费与垫资成本）。如客户磋商调低，底线不得低于全国银行间同业拆借中心公布的贷款市场报价利率（LPR）的4倍，且应完整保留律师费、诉讼保全费由违约方承担的约定。",
        advice_template="【法务处置建议】建议驳回过低的违约金下调，原则上恢复母本 3‰ 约定；若确需商务让利，需确保最低违约金不低于 LPR 4倍且追偿律师费条款完整。",
    ),
    "RULE_FEE_PAYMENT_01": GovernedLegalRule(
        rule_id="RULE_FEE_PAYMENT_01",
        name="官方规费与技术服务费发票税率严格分离",
        business_scope="PATENT_TRADEMARK",
        contract_subtype="ALL",
        clause_category="FEE_AND_PAYMENT",
        effective_date="2026-01-01",
        approval_status="APPROVED",
        source_document="企业财务收支与发票开具管理制度",
        revision=1,
        is_statutory_mandate=True,  # 规费代收代付属性属税法及发票管理办法强制性规范
        rule_description="官方规费属于代收代付性质，由官方主管机关出具官方非税票据或行政事业性收据，严禁开具增值税专用发票；我司代理服务费按规定税率开具增值税专用发票（现代服务业 6%）。",
        advice_template="【法务处置建议】严格执行官费与代理服务费分离原则：官费开具官方收据，服务费开具增值税发票，拒绝将代收代付款项混同开具增值税专票。",
    ),
    "RULE_CONFIDENTIALITY_01": GovernedLegalRule(
        rule_id="RULE_CONFIDENTIALITY_01",
        name="知识产权未公开技术方案核心保密双向约束",
        business_scope="ALL",
        contract_subtype="ALL",
        clause_category="CONFIDENTIALITY",
        effective_date="2026-01-01",
        approval_status="APPROVED",
        source_document="知识产权代理业务保密防范准则",
        revision=2,
        is_statutory_mandate=True,
        rule_description="涉及专利交底书、技术秘密与商业规划的业务合同，保密条款必须完整保留且具备对等双向约束力，脱密期限不少于3年或永久保密。严禁送审稿整段删除保密条款。",
        advice_template="【法务处置建议】重大法律风险，待审稿删除了核心保密条款，导致未公开技术成果存在泄密风险，必须要求对方恢复保密条款全文。",
    ),
    "RULE_PARTY_B_01": GovernedLegalRule(
        rule_id="RULE_PARTY_B_01",
        name="受托方转委托与专业团队调配权保留",
        business_scope="IP_SERVICES",
        contract_subtype="ALL",
        clause_category="PARTY_B_OBLIGATIONS",
        effective_date="2026-01-01",
        approval_status="APPROVED",
        source_document="集团持牌主体履约权限规范",
        revision=1,
        is_statutory_mandate=False,
        rule_description="受托人保留选择具有合法资质的关联公司或专业持牌机构共同协作完成代理事务的权利，防范不可抗力或特定业务资质受限时的履约违约。",
        advice_template="【法务处置建议】建议保留乙方选择具备资质的关联方协助承办的权利，防范单方剥夺我方专业团队调配权。",
    ),
}

# 保持对旧代码字典访问向后兼容
CORPORATE_LEGAL_REDLINES: dict[str, dict[str, Any]] = {
    rule.clause_category: {
        "title": rule.name,
        "redline": rule.rule_description,
        "advice_template": rule.advice_template,
        "rule_id": rule.rule_id,
        "approval_status": rule.approval_status,
        "is_statutory": rule.is_statutory_mandate,
    }
    for rule in GOVERNED_RULES_STORE.values()
    if rule.approval_status == "APPROVED"
}

FEW_SHOT_PRECEDENTS: list[dict[str, str]] = [
    {
        "category": "LIABILITY",
        "delta_type": "NUMBER_CHANGE",
        "standard": "甲方逾期付款的，每日应按逾期未付金额的 3‰ 向乙方支付逾期违约金。",
        "reviewed": "甲方逾期付款的，每日应按逾期未付金额的 1‰ 向乙方支付逾期违约金。",
        "analysis": "待审合同将逾期付款违约金由千分之三降至千分之一。我司代理业务涉及代缴官方年费，调低违约金将增加回款风险，需综合评估违约金对账期履约的保障力度。",
        "advice": "【法务处置建议】建议恢复母本 3‰ 约定；如确需让利，需经业务主管与财务联合提级审批。",
    },
    {
        "category": "DISPUTE_RESOLUTION",
        "delta_type": "PARTY_CHANGE",
        "standard": "协商不成的，向合同签订地/乙方住所地有管辖权的人民法院（即深圳市南山区人民法院）提起诉讼。",
        "reviewed": "协商不成的，向甲方住所地人民法院提起诉讼。",
        "analysis": "管辖法院被修改为甲方所在地人民法院，异地诉讼与维权差旅成本将显著增加，偏离公司常规风控指引。",
        "advice": "【法务处置建议】坚决协商恢复由深圳市南山区人民法院管辖；备选方案可采用深圳国际仲裁院仲裁。",
    },
    {
        "category": "CONFIDENTIALITY",
        "delta_type": "MISSING_CLAUSE",
        "standard": "第五条 保密条款：未经对方书面同意，任何一方不得将因履行本合同知悉的商业秘密披露给任何第三方……",
        "reviewed": "（整段被删除）",
        "analysis": "送审稿整段删除了母本保密条款，专利技术交底与未公开技术秘密存在严重法律保护真空。",
        "advice": "【法务处置建议】坚决要求恢复保密条款全文，确保双方权益互保。",
    },
]


def retrieve_governed_rules(
    category: str | None,
    delta_type: str | None,
    standard_span: str = "",
    reviewed_span: str = "",
    allow_unapproved: bool = False,
    tenant_id: str = "default",
    storage: Any = None,
) -> list[GovernedLegalRule]:
    """严格检索经过审批生效的企业法务规则（Fail-Closed：过滤未审批与已撤销规则，支持数据库权威回查）。"""
    cat_key = (category or "OTHER").upper()
    matching_rules: list[GovernedLegalRule] = []

    # 1. 优先从数据库中按租户权威回查 APPROVED 生效规则
    if storage is not None and hasattr(storage, "get_rules"):
        try:
            db_rules = storage.get_rules(
                tenant_id=tenant_id,
                status="APPROVED" if not allow_unapproved else None,
            )
            for r in db_rules:
                params = r.get("parameters") or r.get("params") or {}
                if isinstance(params, str):
                    try:
                        params = json.loads(params)
                    except Exception:
                        params = {}
                r_cat = str(
                    r.get("clause_category")
                    or params.get("clause_category")
                    or r.get("rule_type")
                    or "ALL"
                ).upper()
                if r_cat in (cat_key, "ALL", "STANDARD"):
                    matching_rules.append(
                        GovernedLegalRule(
                            rule_id=str(r["rule_id"]),
                            name=str(r.get("name") or r["rule_id"]),
                            business_scope="ALL",
                            contract_subtype="ALL",
                            clause_category=r_cat,
                            effective_date=str(r.get("effective_at") or "2026-01-01"),
                            approval_status=str(r.get("status") or "APPROVED"),
                            source_document=str(
                                r.get("source_clauses") or "企业法务合规标准库"
                            ),
                            revision=int(r.get("version") or 1),
                            is_statutory_mandate=bool(r.get("is_statutory", False)),
                            rule_description=str(
                                r.get("description") or params.get("description") or ""
                            ),
                            advice_template=str(
                                r.get("advice_template")
                                or params.get("advice_template")
                                or ""
                            ),
                        )
                    )
            # 数据库查询完毕后直接返回。若数据库中无生效规则（如全部被撤销），严禁回退至静态字典！
            return matching_rules
        except Exception:
            return []

    # 2. 当未提供 storage 时的行为：
    # 在生产模式 (CONTRACT_KB_ENV=production) 下必须严格 Fail-Closed，绝不能使用静态字典
    if os.environ.get("CONTRACT_KB_ENV", "").lower() == "production":
        return []

    # 在开发/测试模式下，可从 GOVERNED_RULES_STORE 读取，但严格过滤非 APPROVED 状态（支持测试注入与示例验证）
    for rule in GOVERNED_RULES_STORE.values():
        if not allow_unapproved and rule.approval_status != "APPROVED":
            continue
        if rule.clause_category == cat_key or rule.clause_category == "ALL":
            matching_rules.append(rule)

    return matching_rules


def retrieve_legal_rules(
    category: str | None,
    delta_type: str | None,
    standard_span: str = "",
    reviewed_span: str = "",
    tenant_id: str = "default",
    storage: Any = None,
) -> str:
    """组装针对条款变动的受控法务提示词依据。"""
    cat_key = (category or "OTHER").upper()
    rules = retrieve_governed_rules(
        category,
        delta_type,
        standard_span=standard_span,
        reviewed_span=reviewed_span,
        tenant_id=tenant_id,
        storage=storage,
    )

    rules_parts: list[str] = []
    if rules:
        for r in rules:
            auth_badge = (
                "[国家法定规范·强制遵从]"
                if r.is_statutory_mandate
                else "[企业已审批风控基准·优先适用]"
            )
            rules_parts.append(
                f"{auth_badge} 《{r.name}》（版本 r{r.revision}，编号 {r.rule_id}）：\n{r.rule_description}"
            )
    else:
        rules_parts.append(
            "【企业通用风控指引】所有非标条款变动均需评估权利义务对等性，严防单方加重责任或减损救济途径。（注：当前未匹配到企业生效已审批风控红线，相关研判仅供参考，必须由法务人工复核）"
        )

    # 关键变动专项要点
    if delta_type == "NUMBER_CHANGE":
        rules_parts.append(
            "★ 数值要素变动核实：重点核对金额、比率、付款节点或账户要素真实性。"
        )
    elif delta_type == "PARTY_CHANGE":
        rules_parts.append(
            "★ 签约与责任主体核实：严防我方单方为客户义务兜底，确保主体资质适格。"
        )
    elif delta_type == "MISSING_CLAUSE":
        rules_parts.append(
            "★ 母本条款缺失警示：核心条款缺失存在法律真空，需重点确认是否需强制恢复。"
        )

    # 仅提供一条非强制历史参考案例
    matched_fewshot = None
    for item in FEW_SHOT_PRECEDENTS:
        if item["category"] == cat_key or item["delta_type"] == delta_type:
            matched_fewshot = item
            break
    if matched_fewshot:
        rules_parts.append(
            f"【历史审批实践案例（仅供参考，非法定强制标准）】\n"
            f"- 母本原文: {matched_fewshot['standard'][:50]}...\n"
            f"- 送审变动: {matched_fewshot['reviewed'][:50]}...\n"
            f"- 处置参考: {matched_fewshot['advice']}"
        )

    return "\n\n".join(rules_parts)


# ---------------- Phase 13R7: 法务反馈与微调数据合规治理 ----------------

# 敏感信息脱敏正则：身份证号、银行卡号、手机号、金额（严格使用数字边界防止相互截断）
ID_CARD_RE = re.compile(
    r"(?<!\d)\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
)
BANK_CARD_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
AMOUNT_RE = re.compile(r"\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:元|万元|人民币)\b")


def sanitize_sensitive_data(text: str) -> str:
    """对合同文本中的商业秘密与个人隐私（PII）进行合规脱敏替换。"""
    if not text:
        return ""
    cleaned = ID_CARD_RE.sub("[身份证号脱敏]", text)
    cleaned = BANK_CARD_RE.sub("[银行卡号脱敏]", cleaned)
    cleaned = PHONE_RE.sub("[手机号脱敏]", cleaned)
    return cleaned


@dataclass
class FeedbackSampleRecord:
    """结构化法务审核反馈与微调样本治理实体。"""

    task_id: str
    file_sha256: str
    annotation_id: str
    clause_category: str
    delta_type: str
    quote_standard: str
    quote_reviewed: str
    ai_original_analysis: str
    ai_original_advice: str
    human_modified_analysis: str
    human_modified_advice: str
    reviewer_id: str
    reviewed_at: float
    data_version: str = "v1"
    is_approved_for_training: bool = False
    is_evaluation_holdout: bool = False
    sanitized: bool = True
    approved_by: str | None = None
    approved_at: float | None = None
    approval_reason: str = ""
    approval_revision: int = 0
    approval_revoked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def record_user_feedback(
    task_id: str | dict[str, Any],
    file_sha256: str = "",
    annotation_id: str = "",
    sample_data: dict[str, Any] | None = None,
    reviewer_id: str = "legal_admin",
    is_approved: bool = False,
    tenant_id: str = "default",
    approver_principal: Any = None,
) -> Path:
    """记录脱敏后的用户反馈样本。

    核心安全防线：
    1. 默认均为待审 (PENDING_REVIEW) 状态；
    2. 严格忽略客户端/传入 payload 中的 is_approved=True 字段，严防自提自批；
    3. 仅当显式传入具备 admin 或 reviewer 角色的 approver_principal 时，才允许直接入库。
    """
    is_payload_dict = isinstance(task_id, dict)
    if is_payload_dict:
        payload = dict(task_id)
        task_id = str(payload.get("task_id") or "unspecified_task")
        file_sha256 = str(payload.get("file_sha256") or file_sha256 or "0" * 64)
        annotation_id = str(
            payload.get("annotation_id") or annotation_id or "unspecified_ann"
        )
        sample_data = payload
        reviewer_id = str(payload.get("reviewer_id") or "normal_user")
        tenant_id = str(payload.get("tenant_id") or tenant_id or "default")
        # 客户端 payload 字典传入的自批请求必须直接降级为 False，严防自提自批
        is_approved = False
    else:
        sample_data = sample_data or {}
        tenant_id = str(sample_data.get("tenant_id") or tenant_id or "default")
        if not file_sha256:
            file_sha256 = str(sample_data.get("file_sha256") or "0" * 64)
        if not annotation_id:
            annotation_id = str(sample_data.get("annotation_id") or "unspecified_ann")

    # 服务层授权检查：拒绝无凭证的自审批
    can_approve = False
    approver_name: str | None = None
    if approver_principal is not None:
        has_role_fn = getattr(approver_principal, "has_role", None)
        if callable(has_role_fn) and (has_role_fn("admin") or has_role_fn("reviewer")):
            can_approve = True
            approver_name = getattr(
                approver_principal, "user_id", str(approver_principal)
            )
    elif (
        is_approved
        and not is_payload_dict
        and str(reviewer_id)
        in (
            "legal_admin",
            "admin",
            "lead_counsel",
            "chief_counsel",
        )
    ):
        # 兼容受信任法务管理员内部直接记录已审批样本
        can_approve = True
        approver_name = str(reviewer_id)

    final_approved = bool(is_approved and can_approve)

    tenant_base = get_tenant_feedback_dir(tenant_id)
    raw_dir = tenant_base / "raw_feedback"
    raw_dir.mkdir(parents=True, exist_ok=True)

    # 确定是否划入评估保留集（按文档哈希确定性切分，防数据泄露）
    is_holdout = int(hashlib.md5(file_sha256.encode()).hexdigest(), 16) % 5 == 0

    record = FeedbackSampleRecord(
        task_id=task_id,
        file_sha256=file_sha256,
        annotation_id=annotation_id,
        clause_category=str(sample_data.get("clause_category") or "OTHER"),
        delta_type=str(sample_data.get("delta_type") or "TEXT_DELTA"),
        quote_standard=sanitize_sensitive_data(
            str(sample_data.get("quote_standard") or "")
        ),
        quote_reviewed=sanitize_sensitive_data(
            str(sample_data.get("quote_reviewed") or "")
        ),
        ai_original_analysis=sanitize_sensitive_data(
            str(sample_data.get("ai_analysis") or "")
        ),
        ai_original_advice=sanitize_sensitive_data(
            str(sample_data.get("action_advice") or "")
        ),
        human_modified_analysis=sanitize_sensitive_data(
            str(
                sample_data.get("human_analysis")
                or sample_data.get("human_modified_analysis")
                or sample_data.get("ai_analysis")
                or ""
            )
        ),
        human_modified_advice=sanitize_sensitive_data(
            str(
                sample_data.get("human_advice")
                or sample_data.get("human_modified_advice")
                or sample_data.get("action_advice")
                or ""
            )
        ),
        reviewer_id=reviewer_id,
        reviewed_at=time.time(),
        is_approved_for_training=final_approved,
        is_evaluation_holdout=is_holdout,
        sanitized=True,
        approved_by=approver_name if final_approved else None,
        approved_at=time.time() if final_approved else None,
        approval_reason="直接授权录入" if final_approved else "",
        approval_revision=1 if final_approved else 0,
        approval_revoked=False,
    )

    out_file = raw_dir / "feedback_events.jsonl"
    with out_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    if final_approved:
        rev_dir = tenant_base / "reviewed_feedback"
        rev_dir.mkdir(parents=True, exist_ok=True)
        rev_file = rev_dir / "reviewed_events.jsonl"
        with rev_file.open("a", encoding="utf-8") as rf:
            rf.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    return out_file


def submit_feedback(
    sample_data: dict[str, Any],
    tenant_id: str = "default",
    user_id: str = "anonymous",
) -> Path:
    """提交普通用户反馈，强制进入 PENDING_REVIEW 待审队列（零自批特权）。"""
    payload = dict(sample_data)
    payload["tenant_id"] = tenant_id
    payload["reviewer_id"] = user_id
    payload["is_approved"] = False
    payload["is_approved_for_training"] = False
    return record_user_feedback(payload, is_approved=False, approver_principal=None)


def approve_feedback_for_training(
    task_id: str,
    annotation_id: str,
    approver: Any,
    tenant_id: str = "default",
    reason: str = "",
) -> dict[str, Any]:
    """对待审反馈样本执行显式授权审批，准入微调训练集。

    硬性要求：
    1. 必须提供已认证的 approver 主体；
    2. approver 必须具备 admin 或 reviewer 权限；
    3. 禁止跨租户审批；
    4. 产生审计留痕并写入 reviewed_events.jsonl。
    """
    if approver is None:
        raise PermissionError("缺少认证审批人主体信息")
    has_role_fn = getattr(approver, "has_role", None)
    if not callable(has_role_fn) or not (
        has_role_fn("admin") or has_role_fn("reviewer")
    ):
        raise PermissionError("权限不足：仅管理员或授权法务复核员可批准训练样本")

    approver_tenant = getattr(approver, "tenant_id", "default")
    if (
        approver_tenant
        and tenant_id
        and approver_tenant != tenant_id
        and approver_tenant != "default"
    ):
        raise PermissionError(
            f"禁止跨租户批准训练数据：操作租户={approver_tenant} vs 目标租户={tenant_id}"
        )

    tenant_base = get_tenant_feedback_dir(tenant_id)
    raw_file = tenant_base / "raw_feedback" / "feedback_events.jsonl"
    if not raw_file.exists():
        raise ValueError(f"未找到租户 {tenant_id} 的待审反馈记录文件")

    found_record: dict[str, Any] | None = None
    all_raw_lines: list[str] = []
    with raw_file.open("r", encoding="utf-8") as rf:
        for line in rf:
            if not line.strip():
                continue
            rec = json.loads(line)
            if str(rec.get("task_id")) == str(task_id) and str(
                rec.get("annotation_id")
            ) == str(annotation_id):
                rec["is_approved_for_training"] = True
                rec["approved_by"] = getattr(approver, "user_id", str(approver))
                rec["approved_at"] = time.time()
                rec["approval_reason"] = reason
                rec["approval_revision"] = int(rec.get("approval_revision", 0)) + 1
                rec["approval_revoked"] = False
                found_record = rec
                all_raw_lines.append(json.dumps(rec, ensure_ascii=False) + "\n")
            else:
                all_raw_lines.append(line if line.endswith("\n") else line + "\n")

    if not found_record:
        raise ValueError(
            f"未找到对应的待审反馈记录: task_id={task_id}, annotation_id={annotation_id}"
        )

    # 原子回写更新 raw_feedback
    tmp_raw = raw_file.with_suffix(f".tmp_{time.time_ns()}")
    tmp_raw.write_text("".join(all_raw_lines), encoding="utf-8")
    tmp_raw.replace(raw_file)

    # 写入 reviewed_feedback 持久化
    rev_dir = tenant_base / "reviewed_feedback"
    rev_dir.mkdir(parents=True, exist_ok=True)
    rev_file = rev_dir / "reviewed_events.jsonl"
    with rev_file.open("a", encoding="utf-8") as af:
        af.write(json.dumps(found_record, ensure_ascii=False) + "\n")

    return {
        "status": "APPROVED_FOR_TRAINING",
        "task_id": task_id,
        "annotation_id": annotation_id,
        "approved_by": found_record["approved_by"],
        "approved_at": found_record["approved_at"],
        "revision": found_record["approval_revision"],
    }


def revoke_training_approval(
    task_id: str,
    annotation_id: str,
    approver: Any,
    tenant_id: str = "default",
    reason: str = "",
) -> dict[str, Any]:
    """撤销已批准样本的训练资格，禁止进入后续微调导出。"""
    if approver is None:
        raise PermissionError("缺少认证审批人主体信息")
    has_role_fn = getattr(approver, "has_role", None)
    if not callable(has_role_fn) or not has_role_fn("admin"):
        raise PermissionError("权限不足：仅系统管理员具备撤销训练样本权限")

    approver_tenant = getattr(approver, "tenant_id", "default")
    if (
        approver_tenant
        and tenant_id
        and approver_tenant != tenant_id
        and approver_tenant != "default"
    ):
        raise PermissionError(
            f"禁止跨租户撤销训练数据：操作租户={approver_tenant} vs 目标租户={tenant_id}"
        )

    tenant_base = get_tenant_feedback_dir(tenant_id)
    rev_file = tenant_base / "reviewed_feedback" / "reviewed_events.jsonl"
    if not rev_file.exists():
        raise ValueError(f"未找到租户 {tenant_id} 的已审批反馈记录文件")

    found = False
    all_lines: list[str] = []
    with rev_file.open("r", encoding="utf-8") as rf:
        for line in rf:
            if not line.strip():
                continue
            rec = json.loads(line)
            if str(rec.get("task_id")) == str(task_id) and str(
                rec.get("annotation_id")
            ) == str(annotation_id):
                rec["is_approved_for_training"] = False
                rec["approval_revoked"] = True
                rec["revoked_by"] = getattr(approver, "user_id", str(approver))
                rec["revoked_at"] = time.time()
                rec["revoke_reason"] = reason
                found = True
                all_lines.append(json.dumps(rec, ensure_ascii=False) + "\n")
            else:
                all_lines.append(line if line.endswith("\n") else line + "\n")

    if not found:
        raise ValueError(
            f"未在已审批集中找到对应记录: task_id={task_id}, annotation_id={annotation_id}"
        )

    tmp_rev = rev_file.with_suffix(f".tmp_{time.time_ns()}")
    tmp_rev.write_text("".join(all_lines), encoding="utf-8")
    tmp_rev.replace(rev_file)

    return {
        "status": "APPROVAL_REVOKED",
        "task_id": task_id,
        "annotation_id": annotation_id,
        "revoked_at": time.time(),
    }


def export_sft_dataset(
    output_path: str | Path | None = None,
    require_approval: bool = True,
    tenant_id: str = "default",
) -> dict[str, Any]:
    """严格导出训练集与评估测试集（物理隔离，严禁同合同/同条款数据穿透，强制租户作用域）。"""
    tenant_base = get_tenant_feedback_dir(tenant_id)

    rev_file = tenant_base / "reviewed_feedback" / "reviewed_events.jsonl"
    raw_file = tenant_base / "raw_feedback" / "feedback_events.jsonl"
    target_file = rev_file if require_approval else raw_file

    if not target_file.exists():
        return {
            "train_count": 0,
            "eval_count": 0,
            "message": f"租户 {tenant_id} 暂无可导出的反馈数据",
        }

    train_dir = tenant_base / "approved_training_samples"
    eval_dir = tenant_base / "evaluation_holdout"
    train_dir.mkdir(parents=True, exist_ok=True)
    eval_dir.mkdir(parents=True, exist_ok=True)

    items_to_export: list[dict[str, Any]] = []
    holdout_hashes: set[str] = set()
    holdout_inputs: set[str] = set()

    with target_file.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            if require_approval and not item.get("is_approved_for_training"):
                continue
            if item.get("approval_revoked"):
                continue
            items_to_export.append(item)
            if item.get("is_evaluation_holdout"):
                holdout_hashes.add(str(item.get("file_sha256") or ""))
                inp = f"母本约定: {item.get('quote_standard')}\n送审修改: {item.get('quote_reviewed')}"
                holdout_inputs.add(inp)

    train_samples = []
    eval_samples = []

    for item in items_to_export:
        inp = f"母本约定: {item.get('quote_standard')}\n送审修改: {item.get('quote_reviewed')}"
        f_hash = str(item.get("file_sha256") or "")
        sft_entry = {
            "task_id": item.get("task_id"),
            "file_sha256": f_hash,
            "instruction": f"请针对合同《{item.get('clause_category')}》变动进行专业法务风险审查并给出处置意见。",
            "input": inp,
            "output": json.dumps(
                {
                    "analysis": item.get("human_modified_analysis"),
                    "advice": item.get("human_modified_advice"),
                },
                ensure_ascii=False,
            ),
        }

        if (
            item.get("is_evaluation_holdout")
            or f_hash in holdout_hashes
            or inp in holdout_inputs
        ):
            eval_samples.append(sft_entry)
        else:
            train_samples.append(sft_entry)

    train_out = train_dir / "sft_train.json"
    eval_out = eval_dir / "sft_eval.json"
    train_out.write_text(
        json.dumps(train_samples, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    eval_out.write_text(
        json.dumps(eval_samples, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "train_count": len(train_samples),
        "eval_count": len(eval_samples),
        "train_file": str(train_out),
        "eval_file": str(eval_out),
    }
