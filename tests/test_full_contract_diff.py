# -*- coding: utf-8 -*-
"""测试整版合同双栏对照与法务批注生成引擎 (Full Diff & Annotation)。"""

import pytest

from contract_kb import config
from contract_kb.full_diff import build_full_contract_diff


def test_full_contract_diff_on_sample(golden_samples):
    sample = golden_samples["dir"] / "mut_11_late_fee_change.docx"
    assert sample.exists(), f"测试样本不存在: {sample}"

    res = build_full_contract_diff(sample)
    assert res is not None
    assert "aligned_blocks" in res
    assert "annotations" in res
    assert "stats" in res

    # 验证整版对齐块
    blocks = res["aligned_blocks"]
    assert len(blocks) > 0

    # 验证包含各种状态
    statuses = {b["status"] for b in blocks}
    assert "MATCH" in statuses
    assert "DIFF" in statuses
    assert "MISSING_IN_REVIEWED" in statuses

    # 验证批注
    anns = res["annotations"]
    assert len(anns) > 0
    # 确认存在高危数值篡改批注与处置建议
    has_high = any(a["risk_level"] == "HIGH" for a in anns)
    assert has_high
    assert all("ai_analysis" in a and "action_advice" in a for a in anns)


def test_full_contract_diff_100_percent_match():
    # 找一份标准模板自身对比
    tpls = list(config.TEMPLATE_DIR.glob("**/*驳回复审*.docx"))
    if not tpls:
        pytest.skip("未找到驳回复审模板")

    res = build_full_contract_diff(tpls[0])
    assert res["stats"]["high_risk"] == 0
    assert res["stats"]["diff_blocks"] == 0
    assert res["stats"]["matched_blocks"] == len(res["aligned_blocks"])
