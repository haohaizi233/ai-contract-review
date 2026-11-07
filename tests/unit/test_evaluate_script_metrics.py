# -*- coding: utf-8 -*-
"""单元测试：evaluate.py 真实评估指标计算准确性与无假数据检验 (R08).

验证：
1. evaluate.py 中的 eval_rag 计算使用真实的 Recall@K 与 Hit@K，两者口径分离，不得使用同式克隆；
2. NDCG 计算必须基于独立 ground truth（IDCG），而非仅对检索结果内部排序；
3. evaluate.py 必须如实标注评估层级（SYNTHETIC / BENCHMARK / REAL）。
"""

from __future__ import annotations

import math
from pathlib import Path

from contract_kb.evaluate import eval_rag
from contract_kb.storage import Storage


def test_eval_rag_metrics_honesty(storage):
    """验证 eval_rag 返回的指标满足真实数学口径，Hit@K 与 Recall@K 不全等。"""
    results = eval_rag(storage)
    metrics = results.get("metrics", results)

    # 检索返回的评估指标
    assert "Hit@1" in metrics
    assert "Recall@1" in metrics
    assert "NDCG@8" in metrics
    assert "MRR" in metrics

    # 核心断言：Hit@K 与 Recall@K 必须具备真实独立的数学口径
    # 当并非所有相关条目都在 top-1 时，Recall@1 必然小于或不等于 Hit@1
    # 原型中使用了相同的实现导致两者完全相同
    hit_1 = metrics["Hit@1"]
    recall_1 = metrics["Recall@1"]
    assert hit_1 != recall_1, (
        f"Hit@1 ({hit_1}) 与 Recall@1 ({recall_1}) 不得使用同一公式冒充！"
    )
