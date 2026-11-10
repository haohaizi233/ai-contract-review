# -*- coding: utf-8 -*-
"""Web 可视化工作台与模板库/素材库 API 全量端到端测试。"""

from __future__ import annotations

import json
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from tools.contract_diff_web import (
    DiffVisualizerHandler,
    generate_rendered_html,
    export_standalone_html,
)
from tools.material_catalog import DEFAULT_REF_DIR


@pytest.fixture(scope="module")
def web_server():
    """启动本地瞬态 HTTP 测试服务。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), DiffVisualizerHandler)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base_url = f"http://127.0.0.1:{port}"
    yield base_url
    server.shutdown()


def test_get_root_page(web_server):
    """测试根页面正常渲染 HTML。"""
    req = urllib.request.Request(f"{web_server}/")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        html = resp.read().decode("utf-8")
        assert "AI 合同审查" in html
        assert "materialModal" in html
        assert "refTemplateModal" in html


def test_api_ref_templates_catalog(web_server):
    """测试模板库清单 API。"""
    req = urllib.request.Request(f"{web_server}/api/ref_templates/catalog")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data.get("success") is True
        templates = data.get("templates", [])
        assert len(templates) == 54


def test_api_materials_stats(web_server):
    """测试素材库资产统计 API。"""
    req = urllib.request.Request(f"{web_server}/api/materials/stats")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        stats = json.loads(resp.read().decode("utf-8"))
        assert stats["total_cases"] >= 21000
        assert stats["total_word_contracts"] >= 4500
        assert "categories" in stats


def test_api_materials_cases_filter(web_server):
    """测试素材库分类筛选与分页 API。"""
    req = urllib.request.Request(f"{web_server}/api/materials/cases?category=CIP&page=1&page_size=10")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        res = json.loads(resp.read().decode("utf-8"))
        assert res["page"] == 1
        assert len(res["cases"]) == 10
        assert res["total"] > 100


def test_api_materials_case_detail(web_server):
    """测试案卷详情与审批元数据提取 API。"""
    req = urllib.request.Request(f"{web_server}/api/materials/case_detail?contract_no=BJI260500006")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        detail = json.loads(resp.read().decode("utf-8"))
        assert detail["case"]["no"] == "BJI260500006"
        assert len(detail["attachments"]) >= 1


def test_api_materials_load_contract(web_server):
    """测试从素材库载入真实 Word 合同并执行原格式比对。"""
    payload = json.dumps({"contract_no": "BJI260500006"}).encode("utf-8")
    req = urllib.request.Request(
        f"{web_server}/api/materials/load_contract",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        diff_res = json.loads(resp.read().decode("utf-8"))
        assert "aligned_blocks" in diff_res
        assert len(diff_res["aligned_blocks"]) > 0
        assert diff_res["display_doc_name"]


def test_api_ref_templates_load_self_diff(web_server):
    """测试官方模板 100% 自身回归自测比对（零残存差异）。"""
    payload = json.dumps({
        "rel_path": "商标/商标咨询服务合同-事务所.docx",
        "mode": "self_diff"
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{web_server}/api/ref_templates/load",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        res = json.loads(resp.read().decode("utf-8"))
        assert res["stats"]["diff_blocks"] == 0
        assert res["stats"]["total_annotations"] == 0


def test_api_ref_templates_directory_traversal_defense(web_server):
    """测试目录穿越安全防护。"""
    payload = json.dumps({
        "rel_path": "../../../etc/passwd",
        "mode": "reviewed"
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{web_server}/api/ref_templates/load",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code in (400, 404)


def test_api_materials_load_contract_traversal_defense(web_server):
    """测试素材库加载合同的路径穿越与越界防护。"""
    payload = json.dumps({
        "contract_no": "BJI260500006",
        "attachment_path": "../../contract_mapping_dictionary.json"
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{web_server}/api/materials/load_contract",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code in (400, 404)


def test_api_materials_case_detail_not_found(web_server):
    """测试不存在案卷号时优雅返回 404。"""
    req = urllib.request.Request(f"{web_server}/api/materials/case_detail?contract_no=NON_EXISTENT_CASE_999")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 404


def test_generate_rendered_html_and_export():
    """测试静态 HTML 生成与导出无未替换占位符。"""
    html = generate_rendered_html()
    assert "__INITIAL_DATA_PLACEHOLDER__" not in html
    assert "__ALL_TEMPLATES_PLACEHOLDER__" not in html
    assert "__REF_TEMPLATES_PLACEHOLDER__" not in html
    assert "__MATERIAL_DATA_PLACEHOLDER__" not in html
    assert len(html) > 100000

    export_standalone_html()
    out1 = Path("data/contract_merged/合同模板条款比对与Diff可视化.html")
    out2 = DEFAULT_REF_DIR / "合同模板条款比对与Diff可视化.html"
    assert out1.exists()
    assert out2.exists()
    assert out1.stat().st_size > 100000
    assert out2.stat().st_size > 100000
