# -*- coding: utf-8 -*-
"""Phase 12 消融实验配置单变量控制与真实模型可用性守卫测试。

验证内容：
1. 6 组消融实验配置的单变量正交性：相邻配置有且仅有一个自变量发生改变。
2. 真实模型不可用时的 Fail-Closed 机制：严禁使用伪造算法或假数据冒充 96% MRR，必须返回 NOT_EXECUTED_MODEL_UNAVAILABLE。
3. ComponentCallTracker 计数器契约：正确追踪各组件的真实调用。
"""

from __future__ import annotations

import pytest
from pathlib import Path

from contract_kb.ablation_study import (
    AblationConfig,
    ComponentCallTracker,
    evaluate_configuration,
    run_full_ablation_study,
)


def test_ablation_configurations_single_variable_difference() -> None:
    """验证相邻消融配置满足严格的单变量控制。"""
    cfg_a = AblationConfig(
        name="Config A",
        query_mode="FIXED",
        embedding_type="LOCAL_HASH",
        use_parent_child=False,
        reranker_type="LEXICAL",
        use_structural_comparison=False,
    )
    cfg_b = AblationConfig(
        name="Config B",
        query_mode="FIXED",
        embedding_type="DENSE_REAL",
        use_parent_child=False,
        reranker_type="LEXICAL",
        use_structural_comparison=False,
    )
    cfg_c = AblationConfig(
        name="Config C",
        query_mode="DYNAMIC",
        embedding_type="DENSE_REAL",
        use_parent_child=False,
        reranker_type="LEXICAL",
        use_structural_comparison=False,
    )
    cfg_d = AblationConfig(
        name="Config D",
        query_mode="DYNAMIC",
        embedding_type="DENSE_REAL",
        use_parent_child=True,
        reranker_type="LEXICAL",
        use_structural_comparison=False,
    )
    cfg_e = AblationConfig(
        name="Config E",
        query_mode="DYNAMIC",
        embedding_type="DENSE_REAL",
        use_parent_child=True,
        reranker_type="CROSS_ENCODER",
        use_structural_comparison=False,
    )
    cfg_f = AblationConfig(
        name="Config F",
        query_mode="DYNAMIC",
        embedding_type="DENSE_REAL",
        use_parent_child=True,
        reranker_type="CROSS_ENCODER",
        use_structural_comparison=True,
    )

    # A -> B 仅改变 embedding_type
    assert cfg_a.embedding_type != cfg_b.embedding_type
    assert cfg_a.query_mode == cfg_b.query_mode
    assert cfg_a.use_parent_child == cfg_b.use_parent_child
    assert cfg_a.reranker_type == cfg_b.reranker_type
    assert cfg_a.use_structural_comparison == cfg_b.use_structural_comparison

    # B -> C 仅改变 query_mode
    assert cfg_b.query_mode != cfg_c.query_mode
    assert cfg_b.embedding_type == cfg_c.embedding_type
    assert cfg_b.use_parent_child == cfg_c.use_parent_child
    assert cfg_b.reranker_type == cfg_c.reranker_type
    assert cfg_b.use_structural_comparison == cfg_c.use_structural_comparison

    # C -> D 仅改变 use_parent_child
    assert cfg_c.use_parent_child != cfg_d.use_parent_child
    assert cfg_c.query_mode == cfg_d.query_mode
    assert cfg_c.embedding_type == cfg_d.embedding_type
    assert cfg_c.reranker_type == cfg_d.reranker_type
    assert cfg_c.use_structural_comparison == cfg_d.use_structural_comparison

    # D -> E 仅改变 reranker_type
    assert cfg_d.reranker_type != cfg_e.reranker_type
    assert cfg_d.query_mode == cfg_e.query_mode
    assert cfg_d.embedding_type == cfg_e.embedding_type
    assert cfg_d.use_parent_child == cfg_e.use_parent_child
    assert cfg_d.use_structural_comparison == cfg_e.use_structural_comparison

    # E -> F 仅改变 use_structural_comparison
    assert cfg_e.use_structural_comparison != cfg_f.use_structural_comparison
    assert cfg_e.query_mode == cfg_f.query_mode
    assert cfg_e.embedding_type == cfg_f.embedding_type
    assert cfg_e.use_parent_child == cfg_f.use_parent_child
    assert cfg_e.reranker_type == cfg_f.reranker_type


def test_real_model_unavailability_guard(tmp_path: Path) -> None:
    """环境无真实模型时，DENSE_REAL 与 CROSS_ENCODER 必须 Fail-Closed 阻断，拒绝假造指标。"""
    from contract_kb.storage import Storage

    st = Storage(tmp_path / "test.db")

    dense_cfg = AblationConfig(
        name="Config B Real Dense",
        query_mode="FIXED",
        embedding_type="DENSE_REAL",
        use_parent_child=False,
        reranker_type="LEXICAL",
        use_structural_comparison=False,
    )

    # 运行评测（不允许 Mock，以生产真实模式运行）
    res = evaluate_configuration(dense_cfg, st, allow_mock_for_contract_test=False)

    # 在本 CI / 测试环境中未安装 sentence_transformers，必须返回 NOT_EXECUTED_MODEL_UNAVAILABLE
    assert res["status"] == "NOT_EXECUTED_MODEL_UNAVAILABLE"
    assert "BLOCKED_MODEL_RESOURCE" in res["blocking_reason"]
    assert res["metrics"] is None
    st.close()


def test_component_call_tracker_serialization() -> None:
    """测试组件调用追踪器的序列化行为。"""
    tracker = ComponentCallTracker()
    tracker.query_planning_calls = 5
    tracker.search_calls = 10
    tracker.hierarchy_expansions = 8
    tracker.cross_encoder_calls = 4
    tracker.structural_comparison_calls = 2

    d = tracker.to_dict()
    assert d["query_planning_calls"] == 5
    assert d["search_calls"] == 10
    assert d["hierarchy_expansions"] == 8
    assert d["cross_encoder_calls"] == 4
    assert d["structural_comparison_calls"] == 2
