# -*- coding: utf-8 -*-
"""法务 AI 智能体本地知识库与先例检索底座（Legal Agent Knowledge Base & Precedents）。

核心职责：
1. 沉淀企业 78 套标准模板中提炼的 10 大硬性法务红线与 13 大条款分类基准（基于《知识库_合同模板蒸馏_深度版.md》）；
2. 汇集泛微 OA 历史真实法务审批意见（data/e9-opinion-export）与先例规则；
3. 为大模型 Agent 提供针对具体条款类别的精准规则检索（RAG / Rule-Injection）；
4. 提供用户反馈样本归档与微调（SFT）训练集导出能力，支持持续迭代。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contract_kb import config

# 10 大企业宪法级审核红线与原则（从 78 套母本深度蒸馏）
CORPORATE_LEGAL_REDLINES: dict[str, dict[str, Any]] = {
    "DISPUTE_RESOLUTION": {
        "title": "争议解决与司法管辖",
        "redline": "必须约定‘深圳市南山区人民法院’管辖！严禁接受约定甲方所在地或第三方所在地管辖（重特大专利诉讼可约定深圳市中级人民法院知识产权法庭）。",
        "advice_template": "【法务处置建议】坚决恢复母本标准管辖条款，约定由合同签订地/乙方住所地有管辖权的人民法院（即深圳市南山区人民法院）管辖，拒绝任何对方所在地管辖条款。",
    },
    "LIABILITY": {
        "title": "违约责任与赔偿追偿",
        "redline": "① 逾期付款违约金日利率统一标准为 3‰，若被削弱为 1‰ 或更低，极大削弱履约保障，属高危篡改；② 甲方违约必须赔偿乙方全部损失（含直接损失、预期利益及律师费、诉讼费、保全费、调查取证费等），维权成本严禁被划扣；③ 非乙方过错甲方单方解约的，已收费用一律不予退还。",
        "advice_template": "【法务处置建议】坚决驳回违约金降低修改，恢复母本 3‰ 逾期付款日利率；确保律师费、诉讼保全费等追偿范围完整保留。",
    },
    "FEE_AND_PAYMENT": {
        "title": "费用、税率与发票开具",
        "redline": "① 我方收取的技术代理服务费开具增值税专用发票（税率 6%）；② 官方规费开具官方收据（代收代付性质，严禁开具增值税专用发票）；③ 付款模式默认为合同生效后 3 个工作日内一次性付清，先款后办；若被拉长宽限期或改为先办后付，需评估垫资与欠费风险。",
        "advice_template": "【法务处置建议】严守发票与税率常量（服务费专票 6%、官费收据）；核实付款节点是否符合先款后办制度，付款期限拉长须经业务与财务联合提级审批。",
    },
    "CONFIDENTIALITY": {
        "title": "商业秘密与保密条款",
        "redline": "保密条款为知识产权代理服务的生命线，严禁送审稿整段删除或单方免除对方保密义务。必须包含双方互保、脱密期（不少于3年或永久保密）及泄密惩罚。",
        "advice_template": "【法务处置建议】重大法律风险，待审稿删除了核心保密条款，导致我方技术成果与商业秘密处于裸奔状态，必须坚决要求对方无条件恢复母本保密条款。",
    },
    "PARTY_B_OBLIGATIONS": {
        "title": "乙方权利与义务（我方权利）",
        "redline": "① 必须完整保留乙方转委托权（‘有权选择满足资质的关联公司或具备合法资质的第三方共同或协助完成’）；② 严防强义务动词被单方强化为不可抗力责任，保留合理补正期限与免责事由。",
        "advice_template": "【法务处置建议】坚守乙方转委托与关联公司承办权，拒绝单方剥夺我方调配专业团队的合法权利。",
    },
    "PARTY_A_OBLIGATIONS": {
        "title": "甲方义务与配合",
        "redline": "甲方必须保证提供资料的真实性、合法性与完整性；严防责任倒置（例如将甲方材料不实引发的官方驳回责任转嫁给我方）。",
        "advice_template": "【法务处置建议】坚持保留甲方对申报材料真实性、合法性承担完全责任的约定，严禁责任倒置。",
    },
    "FRONT_MATTER": {
        "title": "签约主体与合同标的",
        "redline": "签约主体必须符合集团持牌主体资质（恒程创新、广东恒程、合伙专利所、创誉、广利恒成）；委托标的与合同标题必须与正文服务范围、收费项目完全闭环对应。",
        "advice_template": "【法务处置建议】核实签约主体经营范围与持牌资质；确认合同标题与委托标的同正文收费、服务范围严密吻合。",
    },
    "EFFECTIVENESS_AND_TERMINATION": {
        "title": "合同生效、变更与送达",
        "redline": "① 未尽事宜或修改必须以‘书面补充协议’形式签订，严禁口头约定生效；② 送达规则统一锁定为特快专递‘自发出之日起第四日视为送达’。",
        "advice_template": "【法务处置建议】坚持书面补充协议效力约定，锁定第四日送达推定规则，防范送达迟延法律风险。",
    },
}

# 真实 OA 审批意见精选案例（Few-Shot 示例）
FEW_SHOT_PRECEDENTS: list[dict[str, str]] = [
    {
        "category": "LIABILITY",
        "delta_type": "NUMBER_CHANGE",
        "standard": "甲方逾期付款的，每日应按逾期未付金额的 3‰ 向乙方支付逾期违约金。",
        "reviewed": "甲方逾期付款的，每日应按逾期未付金额的 1‰ 向乙方支付逾期违约金。",
        "analysis": "待审合同将逾期付款违约金日利率由千分之三单方降低至千分之一。我司作为专业法律服务与知识产权代理机构，按期回款直接影响官方官费缴纳与团队工时成本，大幅调低违约金将导致客户拖欠成本极低，实质弱化违约威慑。",
        "advice": "【法务处置建议】坚决驳回并恢复千分之三标准母本约定；如客户坚持下调，最低底线不得低于中国人民银行同期 LPR 的 4 倍，且必须由业务主管与财务负责人联批。",
    },
    {
        "category": "DISPUTE_RESOLUTION",
        "delta_type": "PARTY_CHANGE",
        "standard": "协商不成的，向合同签订地/乙方住所地有管辖权的人民法院（即深圳市南山区人民法院）提起诉讼。",
        "reviewed": "协商不成的，向甲方住所地人民法院提起诉讼。",
        "analysis": "管辖法院被对方擅自修改为甲方所在地人民法院。此修改将导致我方后续异地应诉、差旅、调查取证成本剧增，且面临地方保护主义壁垒，严重违背公司风控红线。",
        "advice": "【法务处置建议】一票否决项。立即要求对方纠正管辖约定，恢复‘深圳市南山区人民法院’；若对方极为强势，备选方案为‘提交深圳国际仲裁院（SCIA）仲裁’，绝不接受对方所在地法院管辖。",
    },
    {
        "category": "CONFIDENTIALITY",
        "delta_type": "MISSING_CLAUSE",
        "standard": "第五条 保密条款：未经对方书面同意，任何一方不得将因履行本合同知悉的商业秘密披露给任何第三方……",
        "reviewed": "（整段被删除）",
        "analysis": "送审稿整段删除了母本中的核心保密条款。知识产权代理涉及客户未公开的交底书、技术方案与商业战略，同时我方亦向客户交付了独创性撰写文本，保密条款缺失将引发不可估量的泄密责任争议与权益真空。",
        "advice": "【法务处置建议】坚决要求对方恢复第五条保密条款全文，保密义务具有双向保护属性，若对方仍拒绝，建议暂停签署。",
    },
]


def retrieve_legal_rules(
    category: str | None,
    delta_type: str | None,
    standard_span: str = "",
    reviewed_span: str = "",
) -> str:
    """根据条款类别与变动类型，检索最匹配的企业风控红线与指导意见。"""
    cat_key = (category or "OTHER").upper()
    redline_info = CORPORATE_LEGAL_REDLINES.get(cat_key)

    rules_parts: list[str] = []
    if redline_info:
        rules_parts.append(
            f"【权威企业风控红线 · {redline_info['title']}】\n{redline_info['redline']}"
        )
    else:
        rules_parts.append(
            "【权威企业风控原则】所有非标修改均需评估对等性，严防单方加重我方责任、免除对方义务或削弱追偿权。"
        )

    # 针对数值、期限、主体等关键要素追加专项提示
    if delta_type == "NUMBER_CHANGE":
        rules_parts.append(
            "★ 数值变动专项注意：核实是否涉及违约金费率调低、付款金额减少或账号变更，关乎财务与资金安全。"
        )
    elif delta_type == "DEADLINE_CHANGE":
        rules_parts.append(
            "★ 期限变动专项注意：核实付款与交付宽限期是否被单方拉长，避免我方承担过长垫资与逾期违约风险。"
        )
    elif delta_type == "PARTY_CHANGE":
        rules_parts.append(
            "★ 主体错位专项注意：核实权利与义务是否发生倒置，坚决杜绝我方为客户单方义务背书。"
        )
    elif delta_type == "MISSING_CLAUSE":
        rules_parts.append(
            "★ 条款缺失专项注意：母本条款被整段删除属于高危信号，必须查明原因并力求恢复。"
        )
    elif delta_type == "NEW_CLAUSE":
        rules_parts.append(
            "★ 客户新增非标专项注意：非标条款常附带惩罚性排他或无条件退费，须严格审查合理性与对等反制。"
        )

    # 附带最相关的 1 条真实法务先例
    matched_fewshot = None
    for item in FEW_SHOT_PRECEDENTS:
        if item["category"] == cat_key or item["delta_type"] == delta_type:
            matched_fewshot = item
            break
    if not matched_fewshot and FEW_SHOT_PRECEDENTS:
        matched_fewshot = FEW_SHOT_PRECEDENTS[0]

    if matched_fewshot:
        rules_parts.append(
            f"【公司历史真实审批先例参考】\n"
            f"- 母本原文: {matched_fewshot['standard'][:60]}...\n"
            f"- 送审变动: {matched_fewshot['reviewed'][:60]}...\n"
            f"- 法务剖析参考: {matched_fewshot['analysis'][:120]}...\n"
            f"- 处置建议参考: {matched_fewshot['advice']}"
        )

    return "\n\n".join(rules_parts)


def record_user_feedback(sample: dict[str, Any]) -> Path:
    """持久化记录法务人员在前端的操作与修改，沉淀为高质量微调训练数据。"""
    feedback_dir = config.DATA_DIR.parent / "ai_feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)
    file_path = feedback_dir / "contract_audit_training_samples.jsonl"

    with file_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(sample, ensure_ascii=False) + "\n")

    return file_path


def export_sft_dataset(output_path: str | Path | None = None) -> dict[str, Any]:
    """将沉淀的标注数据转换为用于大模型微调（SFT / LoRA）的标准 JSON 格式。"""
    feedback_dir = config.DATA_DIR.parent / "ai_feedback"
    src_file = feedback_dir / "contract_audit_training_samples.jsonl"
    if not output_path:
        output_path = feedback_dir / "qwen_sft_dataset.json"
    else:
        output_path = Path(output_path)

    if not src_file.exists():
        return {"count": 0, "path": str(output_path), "message": "暂无反馈沉淀样本"}

    dataset = []
    with src_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                # 构造成 OpenAI / Qwen SFT 对话格式
                sft_item = {
                    "messages": [
                        {
                            "role": "system",
                            "content": "你是一位精通中国合同法与知识产权代理实务的资深法务专家智能体。",
                        },
                        {
                            "role": "user",
                            "content": (
                                f"请对以下合同条款变动进行深度法律风险审查，并给出处置建议：\n"
                                f"【条款类别】{item.get('clause_category', '')}\n"
                                f"【条款标题】{item.get('clause_title', '')}\n"
                                f"【标准母本约定】{item.get('standard_span', '')}\n"
                                f"【送审修改内容】{item.get('reviewed_span', '')}\n"
                            ),
                        },
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "risk_level": item.get("risk_level", "MEDIUM"),
                                    "risk_label": item.get("risk_label", ""),
                                    "ai_analysis": item.get("ai_analysis", ""),
                                    "action_advice": item.get("action_advice", ""),
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
                dataset.append(sft_item)
            except Exception:
                continue

    output_path.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "count": len(dataset),
        "path": str(output_path),
        "message": f"成功导出 {len(dataset)} 条高价值微调样本",
    }
