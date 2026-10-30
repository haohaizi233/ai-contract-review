# -*- coding: utf-8 -*-
"""第三轮整改回归（缺陷 2）：ReviewEngine 真实 LLM Provider 注入链路。

覆盖：
- 工厂 get_llm_provider 按授权配置初始化：none / mock / openai_compat；
- **正常配置下绝不默认为 None**：配置了授权端点必须得到 Provider；
  配置不完整时 fail-closed 抛错（LLMConfigError），不得静默回退 None；
- ReviewEngine 启动注入：结果留痕 llm.provider / llm.configured；
- Mock 集成：LLM 发现与自定义要求结论进入 issues；
- 不可用（未配置）时如实标记 NOT_CHECKED。
"""

import pytest

from contract_kb.llm_review import (
    LLMConfigError,
    LLMNotConfigured,
    LLMSemanticReviewer,
    MockLLMProvider,
    OpenAICompatProvider,
    get_llm_provider,
)
from contract_kb.review_engine import ReviewEngine

FULL_ENV = {
    "CONTRACT_KB_LLM": "openai_compat",
    "CONTRACT_KB_LLM_BASE_URL": "http://127.0.0.1:9/v1",
    "CONTRACT_KB_LLM_API_KEY": "controlled-key",
    "CONTRACT_KB_LLM_MODEL": "enterprise-llm-v1",
}


# ---------------- Provider 工厂 ----------------


def test_factory_none_returns_none():
    assert get_llm_provider(env={}) is None
    assert get_llm_provider(spec="none", env={}) is None


def test_factory_mock_provider():
    p = get_llm_provider(env={"CONTRACT_KB_LLM": "mock"})
    assert isinstance(p, MockLLMProvider)


def test_factory_openai_compat_full_config_never_none():
    """已授权配置存在时 Provider 不得为 None（原缺陷：默认 None 导致无声跳过）。"""
    p = get_llm_provider(env=dict(FULL_ENV))
    assert p is not None
    assert isinstance(p, OpenAICompatProvider)
    assert p.model == "enterprise-llm-v1"


def test_factory_openai_compat_missing_config_fail_closed():
    """配置不完整必须抛错（fail-closed），不得返回 None 假装未配置。"""
    with pytest.raises(LLMConfigError):
        get_llm_provider(
            env={
                "CONTRACT_KB_LLM": "openai_compat",
                "CONTRACT_KB_LLM_BASE_URL": "http://x",
            }
        )
    with pytest.raises(LLMNotConfigured):
        get_llm_provider(env={"CONTRACT_KB_LLM": "openai_compat"})


def test_factory_unknown_spec_rejected():
    with pytest.raises(LLMConfigError):
        get_llm_provider(env={"CONTRACT_KB_LLM": "gpt99"})


# ---------------- ReviewEngine 启动注入 ----------------


def test_engine_injects_configured_provider(monkeypatch):
    monkeypatch.setenv("CONTRACT_KB_LLM", "mock")
    engine = ReviewEngine(storage=None)
    assert engine._llm_provider is not None, "正常配置下不允许默认 None"
    assert engine.llm_status() == {"provider": "mock", "configured": True}


def test_engine_unconfigured_marks_not_checked(monkeypatch):
    monkeypatch.delenv("CONTRACT_KB_LLM", raising=False)
    engine = ReviewEngine(storage=None)
    assert engine._llm_provider is None
    assert engine.llm_status() == {"provider": "none", "configured": False}


def test_engine_broken_config_raises_at_startup(monkeypatch):
    """启动时配置不完整 → 立即抛错，不允许运行期静默降级。"""
    monkeypatch.setenv("CONTRACT_KB_LLM", "openai_compat")
    monkeypatch.delenv("CONTRACT_KB_LLM_API_KEY", raising=False)
    with pytest.raises(LLMConfigError):
        ReviewEngine(storage=None)


# ---------------- Mock 集成（全链路） ----------------


def test_engine_mock_llm_integration(storage, golden_samples, monkeypatch):
    """Mock LLM 全链路：发现进入 issues、自定义要求有结论、不标 NOT_CHECKED。"""
    monkeypatch.setenv("CONTRACT_KB_LLM", "mock")
    canned = MockLLMProvider(
        {
            "result": {
                "findings": [
                    {
                        "finding_id": "f1",
                        "category": "SEMANTIC_RISK",
                        "severity": "MEDIUM",
                        "title": "Mock发现",
                        "description": "Mock 描述",
                        "original_text": "工作日",
                        "suggestion": "核实",
                        "confidence": 0.6,
                        "requires_human_review": True,
                    }
                ],
                "custom_requirement_results": [
                    {
                        "requirement": "付款期限不超过5个工作日",
                        "verdict": "不确定",
                        "evidence": "需业务确认",
                        "confidence": 0.5,
                    }
                ],
            }
        }
    )
    engine = ReviewEngine(storage, llm_provider=canned)
    r = engine.review(
        golden_samples["dir"] / "mut_14_no_issue_baseline.docx",
        mode="COMBINED",
        custom_requirements=["付款期限不超过5个工作日"],
        tenant_id="t1",
        created_by="tester",
    )
    assert canned.calls, "LLM Provider 必须被真实调用"
    assert r["llm"] == {"provider": "mock", "configured": True}
    cats = {i["category"] for i in r["issues"]}
    assert "SEMANTIC_RISK" in cats
    assert "CUSTOM_REQUIREMENT_RESULT" in cats
    assert not any(
        i["rule_id"] == "LLM_SEMANTIC_REVIEW" and i["check_status"] == "NOT_CHECKED"
        for i in r["issues"]
    ), "配置 LLM 后不得再标 NOT_CHECKED"
    # 任务留痕带租户/创建者
    task_row = storage.get_task(r["task_id"])
    assert task_row["tenant_id"] == "t1" and task_row["created_by"] == "tester"


def test_engine_unconfigured_llm_marked_not_checked(
    storage, golden_samples, monkeypatch
):
    monkeypatch.delenv("CONTRACT_KB_LLM", raising=False)
    engine = ReviewEngine(storage)
    r = engine.review(golden_samples["dir"] / "mut_14_no_issue_baseline.docx")
    llm_issues = [i for i in r["issues"] if i["rule_id"] == "LLM_SEMANTIC_REVIEW"]
    assert llm_issues and llm_issues[0]["check_status"] == "NOT_CHECKED"
    assert r["llm"]["configured"] is False


def test_engine_openai_compat_provider_recorded(monkeypatch):
    monkeypatch.setenv("CONTRACT_KB_LLM", "openai_compat")
    monkeypatch.setenv("CONTRACT_KB_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("CONTRACT_KB_LLM_API_KEY", "k")
    monkeypatch.setenv("CONTRACT_KB_LLM_MODEL", "m1")
    engine = ReviewEngine(storage=None)
    assert isinstance(engine._llm_provider, OpenAICompatProvider)
    assert engine.llm_status()["provider"] == "openai_compat"


def test_llm_reviewer_wrapper_still_supported(storage, golden_samples):
    """显式注入 llm_reviewer（既有用法）保持可用。"""
    prov = MockLLMProvider(
        {"result": {"findings": [], "custom_requirement_results": []}}
    )
    engine = ReviewEngine(storage, llm_reviewer=LLMSemanticReviewer(provider=prov))
    r = engine.review(golden_samples["dir"] / "mut_14_no_issue_baseline.docx")
    assert r["status"] == "COMPLETED"
    assert r["llm"]["configured"] is True
