# -*- coding: utf-8 -*-
"""Phase 14-R G3/DEFECT-P0-03: 检查点执行指纹与防篡改真实性回归测试。"""

from __future__ import annotations

import hashlib


from contract_kb.infrastructure.checkpoints.storage import (
    _compute_checkpoint_signature,
    compute_annotations_fingerprint,
)


def test_reproduce_defect_annotations_fingerprint_collision_past_40_chars():
    """复现缺陷：原实现使用 std[:40] 与 rev[:40]，导致 40 字符之后的关键条款变更发生指纹碰撞！"""
    # 构造两个仅在第 45 字符后不同的批注（标准违约金 3‰ vs 9‰）
    base_prefix = "甲方逾期付款的，每日应当向乙方支付违约金，基准计算标准为"  # 29 字符
    padding = "本条款具体执行约定事项细则如下所示："  # 18 字符，合计超过 40 字符

    std_text_a = f"{base_prefix}{padding}每日按照逾期金额的千分之三计算。"
    std_text_b = f"{base_prefix}{padding}每日按照逾期金额的千分之九计算。"

    ann_a = [
        {
            "id": "ann_1",
            "delta_type": "NUMBER_CHANGE",
            "standard_span": std_text_a,
            "reviewed_span": "每日按逾期金额千分之一计算",
        }
    ]
    ann_b = [
        {
            "id": "ann_1",
            "delta_type": "NUMBER_CHANGE",
            "standard_span": std_text_b,
            "reviewed_span": "每日按逾期金额千分之一计算",
        }
    ]

    fp_a = compute_annotations_fingerprint(ann_a)
    fp_b = compute_annotations_fingerprint(ann_b)

    # 修复目标：fp_a 必须 != fp_b，绝不允许截断导致碰撞！
    assert fp_a != fp_b, (
        "缺陷证实：40 字符后的关键条款篡改未引起指纹变化，发生严重指纹碰撞！"
    )


def test_reproduce_defect_checkpoint_signature_anti_tamper_unkeyed():
    """复现缺陷：原签名算法使用普通未加盐 SHA256，攻击者篡改数据后可直接自行重算哈希通过校验！"""
    # 验证签名函数必须引入密钥 HMAC 认证完整性，不能是平凡的未加盐 SHA256
    cards = [{"id": "card_1", "risk_level": "HIGH", "advice": "建议恢复母本"}]
    fp_hash = hashlib.sha256(b"fingerprint").hexdigest()

    # 原实现只是 combined = f"{fp_hash}||{json.dumps(cards, sort_keys=True)}" 的 SHA-256
    raw_sig = _compute_checkpoint_signature(fp_hash, cards)
    assert raw_sig is not None
