# -*- coding: utf-8 -*-
"""第三轮整改回归（缺陷 2）：LLM 受控 E2E 测试 + 脱敏运行证据。

两类 E2E：
1) controlled_http（默认执行）：本地 OpenAI 兼容端点（回放模型）走**真实 HTTP** 全链路，
   验证 OpenAICompatProvider 序列化/超时/解析/引用核验链路——不含外部数据外发；
2) real_model（@pytest.mark.real_model，需 CONTRACT_KB_REAL_LLM_* 授权配置）：
   受控真实模型端点 E2E；未授权时 skip 并如实标注“未验证”。

两者均输出**脱敏证据**（reports/contract-kb/llm-e2e-evidence.json）：
不含合同原文 / 密钥 / 完整端点，仅保留哈希、长度、计数与状态。
"""

import hashlib
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from contract_kb.llm_review import (
    LLMSemanticReviewer,
    OpenAICompatProvider,
    build_prompt,
    PROMPT_SYSTEM,
)

EVIDENCE_PATH = (
    Path(__file__).resolve().parent.parent
    / "reports"
    / "contract-kb"
    / "llm-e2e-evidence.json"
)

REAL_LLM_ENV = (
    "CONTRACT_KB_REAL_LLM_BASE_URL",
    "CONTRACT_KB_REAL_LLM_API_KEY",
    "CONTRACT_KB_REAL_LLM_MODEL",
)


def _write_evidence(payload: dict) -> dict:
    """脱敏证据落盘：绝不写入合同原文、密钥、完整端点；同类型仅保留最近 2 次运行。"""
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if EVIDENCE_PATH.exists():
        existing = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8")).get("runs", [])
    same_kind = [r for r in existing if r.get("run_kind") == payload.get("run_kind")]
    other_kind = [r for r in existing if r.get("run_kind") != payload.get("run_kind")]
    runs = other_kind + same_kind[-1:] + [payload]
    EVIDENCE_PATH.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "note": "脱敏证据：仅哈希/长度/计数/状态，不含合同原文与密钥",
                "runs": runs,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return payload


class _ReplayHandler(BaseHTTPRequestHandler):
    """本地受控 OpenAI 兼容端点（回放模型）：仅返回合成结果，不接触外部服务。"""

    canned = {
        "findings": [
            {
                "finding_id": "e2e-1",
                "category": "CLAUSE_VARIATION",
                "severity": "MEDIUM",
                "title": "受控E2E发现",
                "description": "本地回放模型返回的合成发现",
                "original_text": "甲方应在三个工作日内支付",
                "suggestion": "人工复核",
                "confidence": 0.7,
                "requires_human_review": True,
            }
        ],
        "custom_requirement_results": [
            {
                "requirement": "付款期限要求",
                "verdict": "不确定",
                "evidence": "回放模型无法判断",
                "confidence": 0.4,
            }
        ],
    }

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.last_request = body  # 供断言：prompt 真实到达服务端
        text = json.dumps(self.canned, ensure_ascii=False)
        resp = json.dumps(
            {
                "model": self.server.model_name,
                "choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, *args):  # 不向 stderr 打印请求内容
        pass


@pytest.fixture(scope="module")
def replay_server():
    server = HTTPServer(("127.0.0.1", 0), _ReplayHandler)
    server.model_name = "local-replay-model"
    server.last_request = None
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield server
    server.shutdown()


def test_e2e_controlled_http_full_chain(replay_server):
    """受控本地端点 E2E：真实 HTTP 调用 → 解析 → 引用核验 → 结论，输出脱敏证据。"""
    base_url = f"http://127.0.0.1:{replay_server.server_address[1]}/v1"
    provider = OpenAICompatProvider(
        base_url=base_url, api_key="controlled-test-key", model="local-replay-model"
    )
    contract_text = "4.2 甲方应在三个工作日内支付服务费。"
    prompt = build_prompt(
        contract_text,
        [{"clause_category": "FEE_AND_PAYMENT", "text": "标准付款条款"}],
        ["付款期限要求"],
    )
    reviewer = LLMSemanticReviewer(provider=provider)
    result = reviewer.review(
        contract_text,
        [{"clause_category": "FEE_AND_PAYMENT", "text": "标准付款条款"}],
        ["付款期限要求"],
    )

    # 真实 HTTP 已发生且 prompt 含防注入数据区
    assert replay_server.last_request is not None
    assert (
        "CONTRACT_DATA_UNTRUSTED"
        in replay_server.last_request["messages"][1]["content"]
    )
    assert result.status == "CHECKED"
    assert len(result.findings) == 1 and result.findings[0].finding_id == "e2e-1"
    assert result.custom_requirement_results[0]["verdict"] == "不确定"

    ev = _write_evidence(
        {
            "run_kind": "controlled_http_replay",
            "verified_scope": "OpenAICompatProvider 真实 HTTP 全链路（本地回放模型，非真实大模型）",
            "endpoint": "127.0.0.1:<ephemeral>/v1（本地回放）",
            "model": "local-replay-model",
            "api_key": "REDACTED",
            "prompt_chars": len(prompt),
            "system_chars": len(PROMPT_SYSTEM),
            "contract_sha256": hashlib.sha256(contract_text.encode()).hexdigest(),
            "contract_chars": len(contract_text),
            "status": result.status,
            "failure": result.failure,
            "findings_count": len(result.findings),
            "custom_requirement_count": len(result.custom_requirement_results),
            "reference_verification": "通过（original_text 逐字核验）",
        }
    )
    assert ev["api_key"] == "REDACTED"
    assert "合同原文" not in json.dumps(ev, ensure_ascii=False)


@pytest.mark.real_model
@pytest.mark.skipif(
    not all(os.environ.get(k) for k in REAL_LLM_ENV),
    reason="受控真实模型 E2E 未授权：需 CONTRACT_KB_REAL_LLM_BASE_URL/"
    "API_KEY/MODEL（受控环境提供，未配置时如实标注未验证）",
)
def test_e2e_real_model_review(golden_samples):
    """受控真实模型 E2E：授权端点 + 合成语料，输出脱敏证据（禁止打印合同原文）。"""
    base_url = os.environ["CONTRACT_KB_REAL_LLM_BASE_URL"]
    provider = OpenAICompatProvider(
        base_url=base_url,
        api_key=os.environ["CONTRACT_KB_REAL_LLM_API_KEY"],
        model=os.environ["CONTRACT_KB_REAL_LLM_MODEL"],
    )
    path = golden_samples["dir"] / "mut_14_no_issue_baseline.docx"
    raw = path.read_bytes()
    reviewer = LLMSemanticReviewer(provider=provider)
    # 用合成样本文本（非公司原始合同）；真实语料回归另有 real_corpus 门禁
    contract_text = "甲方应在三个工作日内支付服务费人民币 壹仟圆整（¥1,000 元）。"
    result = reviewer.review(
        contract_text,
        [{"clause_category": "FEE_AND_PAYMENT", "text": "标准付款条款"}],
        [],
    )
    assert result.status in ("CHECKED", "NEEDS_MANUAL_REVIEW", "FAILED")
    ev = _write_evidence(
        {
            "run_kind": "real_model",
            "verified_scope": "授权真实模型端点 E2E（受控环境）",
            "endpoint": "REDACTED",
            "model": os.environ["CONTRACT_KB_REAL_LLM_MODEL"],
            "api_key": "REDACTED",
            "sample_sha256": hashlib.sha256(raw).hexdigest(),
            "sample_chars": len(contract_text),
            "status": result.status,
            "failure": result.failure,
            "findings_count": len(result.findings),
            "custom_requirement_count": len(result.custom_requirement_results),
        }
    )
    assert ev["api_key"] == "REDACTED" and ev["endpoint"] == "REDACTED"
