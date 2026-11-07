# -*- coding: utf-8 -*-
"""Phase 12 消融实验集成测试：验证组件调用追踪与全矩阵端到端执行。"""

from __future__ import annotations

from pathlib import Path
import pytest

from contract_kb.ablation_study import run_full_ablation_study


def test_ablation_study_full_matrix_contract_execution(tmp_path: Path) -> None:
    """验证在 allow_mock_for_contract_test 模式下，全矩阵 6 组消融实验能够完整执行并精确追踪调用。"""
    result = run_full_ablation_study(tmp_path, allow_mock_for_contract_test=True)
    res_list = result["ablation_results"]
    assert len(res_list) == 6

    cfg_a = res_list[0]
    cfg_b = res_list[1]
    cfg_c = res_list[2]
    cfg_d = res_list[3]
    cfg_e = res_list[4]
    cfg_f = res_list[5]

    for item in res_list:
        assert item["status"] == "EXECUTED"
        assert item["metrics"] is not None
        assert "MRR" in item["metrics"]
        assert "NDCG@8" in item["metrics"]

    # 验证组件调用追踪（单变量技术组件接线真实性）
    # Config A: 无动态规划、无层级扩展、无 Cross-Encoder、无结构化比对
    assert cfg_a["component_trace"]["query_planning_calls"] == 0
    assert cfg_a["component_trace"]["hierarchy_expansions"] == 0
    assert cfg_a["component_trace"]["cross_encoder_calls"] == 0
    assert cfg_a["component_trace"]["structural_comparison_calls"] == 0
    assert cfg_a["component_trace"]["search_calls"] > 0

    # Config B: 依然是 Fixed Query
    assert cfg_b["component_trace"]["query_planning_calls"] == 0

    # Config C: 启用了动态查询规划
    assert cfg_c["component_trace"]["query_planning_calls"] > 0
    assert cfg_c["component_trace"]["hierarchy_expansions"] == 0

    # Config D: 启用了 Parent-Child 层级扩展
    assert cfg_d["component_trace"]["query_planning_calls"] > 0
    assert cfg_d["component_trace"]["hierarchy_expansions"] > 0
    assert cfg_d["component_trace"]["cross_encoder_calls"] == 0

    # Config E: 启用了 Cross-Encoder 重排
    assert cfg_e["component_trace"]["hierarchy_expansions"] > 0
    assert cfg_e["component_trace"]["cross_encoder_calls"] > 0
    assert cfg_e["component_trace"]["structural_comparison_calls"] == 0

    # Config F: 启用了 Structural Comparison 比对引擎
    assert cfg_f["component_trace"]["structural_comparison_calls"] > 0


def test_ablation_study_honest_blocking_when_real_model_missing(tmp_path: Path) -> None:
    """验证在不使用 Mock 的真实生产环境下，真实模型缺失时必须诚实标记 BLOCKED，不能伪造指标。"""
    result = run_full_ablation_study(tmp_path, allow_mock_for_contract_test=False)
    res_list = result["ablation_results"]
    assert len(res_list) == 6

    cfg_a = res_list[0]
    # Config A 是纯词法 + LocalHash 基线，必须能真实执行并给出真实实测指标
    assert cfg_a["status"] == "EXECUTED"
    assert cfg_a["metrics"] is not None

    # Config B ~ F 要求真实的 Dense 模型或 Cross-Encoder，在无 torch/sentence-transformers 时阻断
    for item in res_list[1:]:
        assert item["status"] == "NOT_EXECUTED_MODEL_UNAVAILABLE"
        assert "BLOCKED_MODEL_RESOURCE" in item["blocking_reason"]
        assert item["metrics"] is None
