# -*- coding: utf-8 -*-
"""模板库与素材库资产索引、检索与解析单元测试。"""

from __future__ import annotations

import pytest

from tools.material_catalog import (
    DEFAULT_MERGED_DIR,
    DEFAULT_REF_DIR,
    clean_template_title,
    get_material_manager,
    get_ref_templates_catalog,
)


def test_clean_template_title():
    assert (
        clean_template_title("WDCLI_123456_国内专利服务合同（年费缴纳）-事务所.docx")
        == "国内专利服务合同（年费缴纳）"
    )
    assert (
        clean_template_title("MB20251205014_V13.0_国内专利服务合同（申请）.docx")
        == "国内专利服务合同（申请）"
    )
    assert clean_template_title("保密协议-事务所.doc") == "保密协议"


@pytest.mark.real_corpus
def test_ref_templates_catalog_completeness():
    if not DEFAULT_REF_DIR.exists():
        pytest.skip("本地未配置受控参考模板库（参考/）")
    catalog = get_ref_templates_catalog()
    assert len(catalog) == 54, f"期望 54 份标准模板，实测: {len(catalog)}"

    folders = {t["folder"] for t in catalog}
    expected_folders = {
        "专利",
        "商标",
        "诉讼",
        "国际专利",
        "国际商标",
        "双软",
        "综合业务",
        "著作权",
        "集成电路布图设计登记",
        "保密协议",
    }
    assert expected_folders.issubset(folders), f"缺失板块: {expected_folders - folders}"

    # 验证关键模板均存在且文件大小合法
    for t in catalog:
        assert t["size"] > 1000, f"模板文件异常过小: {t}"
        assert t["name"], "模板标题不得为空"


@pytest.mark.real_corpus
def test_material_library_manager_stats_and_indexing():
    if not (DEFAULT_MERGED_DIR / "attachments_merged.csv").exists():
        pytest.skip("本地未配置受控素材库（data/contract_merged）")
    mgr = get_material_manager()
    stats = mgr.get_stats()
    assert stats["total_cases"] >= 21000, f"案卷数异常: {stats['total_cases']}"
    assert stats["total_word_contracts"] >= 4500, (
        f"Word 合同数异常: {stats['total_word_contracts']}"
    )
    assert "CIP" in stats["categories"]
    assert "TRADEMARK" in stats["categories"]


@pytest.mark.real_corpus
def test_material_library_search_and_pagination():
    if not (DEFAULT_MERGED_DIR / "attachments_merged.csv").exists():
        pytest.skip("本地未配置受控素材库（data/contract_merged）")
    mgr = get_material_manager()
    res = mgr.search_cases(
        category="CIP", search="保密", has_word_only=True, page=1, page_size=10
    )
    assert res["total"] > 0
    assert len(res["cases"]) <= 10
    assert res["total_pages"] >= 1
    for item in res["cases"]:
        assert item["has_word"] is True
        assert item["no"]


@pytest.mark.real_corpus
def test_material_library_contract_attachment_fetch():
    if not (DEFAULT_MERGED_DIR / "attachments_merged.csv").exists():
        pytest.skip("本地未配置受控素材库（data/contract_merged）")
    mgr = get_material_manager()
    # 查找首个有 Word 合同的案卷
    res = mgr.search_cases(category="ALL", has_word_only=True, page=1, page_size=1)
    assert len(res["cases"]) == 1
    c = res["cases"][0]

    fpath, fmeta = mgr.get_contract_attachment_file(c["no"])
    assert fpath is not None, f"未找到案卷合同文件: {c['no']}"
    assert fpath.exists(), f"文件不存在: {fpath}"
    assert fpath.suffix.lower() in (".docx", ".doc", ".pdf")


@pytest.mark.real_corpus
def test_material_library_case_detail():
    if not (DEFAULT_MERGED_DIR / "attachments_merged.csv").exists():
        pytest.skip("本地未配置受控素材库（data/contract_merged）")
    mgr = get_material_manager()
    # 获取任意已知案卷
    res = mgr.search_cases(category="CIP", page=1, page_size=1)
    c = res["cases"][0]
    detail = mgr.get_case_detail(c["no"])
    assert detail is not None
    assert detail["case"]["no"] == c["no"]
    assert "attachments" in detail
