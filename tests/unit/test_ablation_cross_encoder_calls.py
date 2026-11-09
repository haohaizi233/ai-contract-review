# -*- coding: utf-8 -*-
"""回归测试：消融实验真实组件调用追踪与 Cross-Encoder 正确路由。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import pytest

from contract_kb.ablation_study import (
    AblationConfig,
    ComponentCallTracker,
    MockCrossEncoderReranker,
    evaluate_configuration,
    setup_ablation_storage,
)
from contract_kb.embeddings import LocalHashEmbedding


def test_mock_cross_encoder_constructor_error_is_raised(tmp_path: Path):
    """
    验证：当 MockCrossEncoderReranker 构造函数抛出异常时，
    以 MOCK_CROSS_ENCODER_TEST 运行的消融实验不能静默完成，必须真实体现异常。
    （旧实现因只识别 CROSS_ENCODER 分支，导致 Mock 根本未被实例化，因而静默完成）。
    """
    st = setup_ablation_storage(tmp_path / "kb", LocalHashEmbedding())
    cfg_e = AblationConfig(
        name="Config E Mock CE",
        query_mode="DYNAMIC",
        embedding_type="MOCK_DENSE_TEST",
        use_parent_child=True,
        reranker_type="MOCK_CROSS_ENCODER_TEST",
        use_structural_comparison=False,
        is_contract_test=True,
    )

    class BrokenMockCrossEncoder(MockCrossEncoderReranker):
        def __init__(self):
            raise RuntimeError("MockCrossEncoderReranker 故意失败")

    with patch(
        "contract_kb.ablation_study.MockCrossEncoderReranker", BrokenMockCrossEncoder
    ):
        with pytest.raises(RuntimeError, match="MockCrossEncoderReranker 故意失败"):
            evaluate_configuration(cfg_e, st, allow_mock_for_contract_test=True)

    st.close()


def test_lexical_reranker_has_zero_cross_encoder_calls(tmp_path: Path):
    """验证：词法重排组的 cross_encoder_calls 必须为 0，不能被错误计数。"""
    st = setup_ablation_storage(tmp_path / "kb", LocalHashEmbedding())
    cfg_d = AblationConfig(
        name="Config D Lexical",
        query_mode="DYNAMIC",
        embedding_type="MOCK_DENSE_TEST",
        use_parent_child=True,
        reranker_type="LEXICAL",
        use_structural_comparison=False,
        is_contract_test=True,
    )
    tracker = ComponentCallTracker()
    res = evaluate_configuration(
        cfg_d, st, tracker=tracker, allow_mock_for_contract_test=True
    )
    assert res["status"] == "EXECUTED"
    assert tracker.cross_encoder_calls == 0
    assert res["component_trace"]["cross_encoder_calls"] == 0
    st.close()


def test_cross_encoder_real_calls_tracked(tmp_path: Path):
    """验证：E 组在契约模式下真实调用了 MockCrossEncoderReranker.rerank，且调用次数被准确追踪。"""
    st = setup_ablation_storage(tmp_path / "kb", LocalHashEmbedding())
    cfg_e = AblationConfig(
        name="Config E Mock CE",
        query_mode="DYNAMIC",
        embedding_type="MOCK_DENSE_TEST",
        use_parent_child=True,
        reranker_type="MOCK_CROSS_ENCODER_TEST",
        use_structural_comparison=False,
        is_contract_test=True,
    )

    calls_made = 0
    orig_rerank = MockCrossEncoderReranker.rerank

    def spy_rerank(self, query, hits, top_k=8):
        nonlocal calls_made
        calls_made += 1
        return orig_rerank(self, query, hits, top_k=top_k)

    tracker = ComponentCallTracker()
    with patch.object(MockCrossEncoderReranker, "rerank", spy_rerank):
        res = evaluate_configuration(
            cfg_e, st, tracker=tracker, allow_mock_for_contract_test=True
        )

    assert res["status"] == "EXECUTED"
    assert calls_made > 0, "MockCrossEncoderReranker.rerank 未被实际调用！"
    assert tracker.cross_encoder_calls == calls_made, "调用追踪器与实际调用次数不一致！"
    st.close()
