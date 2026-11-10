# -*- coding: utf-8 -*-
"""全库端到端文档解析与比对渲染验证测试（需受控真实语料）。"""

from __future__ import annotations

import json

import pytest

from contract_kb.ingest import import_template_file
from tools.contract_diff_web import execute_current_diff
from tools.material_catalog import (
    DEFAULT_MERGED_DIR,
    DEFAULT_REF_DIR,
    get_material_manager,
    get_ref_templates_catalog,
)

pytestmark = [pytest.mark.real_corpus]


def test_e2e_ref_templates_full_library_parse():
    """验证【参考/】模板库中全部 106 份文件（54 .docx + 52 .doc）100% 成功解析且提取出正文条款。"""
    if not DEFAULT_REF_DIR.exists():
        pytest.skip("本地未配置受控参考模板库（参考/）")
    all_files = sorted(
        list(DEFAULT_REF_DIR.glob("**/*.docx")) + list(DEFAULT_REF_DIR.glob("**/*.doc"))
    )
    assert len(all_files) == 106, f"期望 106 份模板文件，实际发现: {len(all_files)}"

    for p in all_files:
        rec, err = import_template_file(p)
        assert rec is not None, f"模板文件解析失败: {p.name}, error: {err}"
        assert len(rec.clauses or []) > 0, f"模板未能提取出条款: {p.name}"
        assert rec.category, f"模板业务大类未识别: {p.name}"
        assert rec.ast and len(rec.ast.get("nodes", [])) > 0, (
            f"AST 语法树节点为空: {p.name}"
        )


def test_e2e_ref_templates_catalog_self_diff_perfect_match():
    """验证【参考/】目录 54 套业务母本自检比对全部达到 100% MATCH（0 批注，0 误报）。"""
    if not DEFAULT_REF_DIR.exists():
        pytest.skip("本地未配置受控参考模板库（参考/）")
    catalog = get_ref_templates_catalog()
    assert len(catalog) == 54

    for t in catalog:
        p = DEFAULT_REF_DIR / t["rel_path"]
        assert p.exists(), f"模板物理文件丢失: {t['rel_path']}"
        res = execute_current_diff(p, forced_template_id=None, doc_name=t["name"])
        assert "aligned_blocks" in res
        assert len(res["aligned_blocks"]) > 0, f"对齐语义块为空: {t['name']}"
        assert res["stats"]["diff_blocks"] == 0, f"自测比对存在虚假差异块: {t['name']}"
        assert res["stats"]["total_annotations"] == 0, (
            f"自测比对存在误报批注: {t['name']}"
        )


def test_e2e_material_library_contract_selection_and_parsing():
    """验证素材库 5 大业务板块真实案卷合同附件提取、多格式解析与条款提取。"""
    if not (DEFAULT_MERGED_DIR / "attachments_merged.csv").exists():
        pytest.skip("本地未配置受控素材库（data/contract_merged）")
    mgr = get_material_manager()
    mgr.ensure_loaded()

    # 验证对图纸与技术模型的智能过滤：优先提取法律合同而非 Visio/CAD
    p, att = mgr.get_contract_attachment_file("CIP2024总委00111")
    assert p is not None
    assert "绘图" not in att.get("original", "")
    assert ".vsd" not in att.get("original", "").lower()
    rec, err = import_template_file(p)
    assert rec is not None
    assert len(rec.clauses) > 30

    # 验证五大板块各抽取代表案卷，全部能完成条款提取与 diff 生成
    for cat in ["CIP", "TRADEMARK", "LITIGATION", "FRAMEWORK", "OTHER"]:
        cases = mgr.search_cases(category=cat, has_word_only=True, page=1, page_size=2)[
            "cases"
        ]
        for c in cases:
            cp, catt = mgr.get_contract_attachment_file(c["no"])
            assert cp and cp.exists(), f"附件不存在: {c['no']}"
            crec, cerr = import_template_file(cp)
            assert crec is not None, f"解析失败: {c['no']} -> {cerr}"
            assert len(crec.clauses) > 0, f"无条款: {c['no']}"

            # 执行微观比对与 Web 序列化
            res = execute_current_diff(cp, doc_name=c["name"])
            j_str = json.dumps(res, ensure_ascii=False)
            assert len(j_str) > 100
            assert len(res.get("aligned_blocks", [])) > 0
            assert res.get("display_doc_name")
