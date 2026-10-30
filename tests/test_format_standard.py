# -*- coding: utf-8 -*-
"""第三轮整改回归（缺陷 6）：公司格式标准可配置 / 可审批 / 可版本化。

覆盖：
- 标准版本化：v1/v2 独立留痕，新版本审批后旧版 SUPERSEDED；
- 审批流：仅 DRAFT 可审批；未审批（示例基线）标准**不得产生正式企业违规结论**；
- 审核引擎：未审批标准 → FORMAT_UNVERIFIED_STANDARD（NEEDS_MANUAL_REVIEW），
  审批后 → FORMAT_NON_COMPLIANT（正式结论），并留痕标准版本；
- API：创建/审批需 admin，身份来自认证层。
"""

import pytest

from contract_kb.format_auditor import audit_format
from contract_kb.review_engine import ReviewEngine
from contract_kb.storage import Storage

ADMIN = {"X-User": "admin_f", "X-Roles": "admin", "X-Tenant": "t1"}
REVIEWER = {"X-User": "rev_f", "X-Roles": "reviewer", "X-Tenant": "t1"}

STRICT_PARAMS = {
    "body_font_size_pt": 12,
    "require_contract_number_first_n_paras": 3,
    "require_placeholders": ["合同编号", "合同审批单编号"],  # 第二项为测试用严格项
}


class _FakeNode:
    def __init__(self, text, order=0, style=None):
        self.node_id = f"n{order}"
        self.node_type = "paragraph"
        self.order = order
        self.text = text
        self.section = None
        self.clause_number = None
        self.table_index = None
        self.row_index = None
        self.column_index = None
        self.style = style or {"runs": [{"text": "t", "bold": True}]}
        self.source_locator = {}


class _FakeDoc:
    def __init__(self):
        self.document_id = "fmt_doc"
        self.nodes = [
            _FakeNode("合同编号:SYN-1", 0),
            _FakeNode("第一条 合同事项", 1),
            _FakeNode("正文内容", 2),
        ]
        self.table_dims = []
        self.headers = ["页眉"]
        self.footers = ["页脚"]
        self.page_margins_cm = {"top": 2.5}


@pytest.fixture()
def fmt_storage(tmp_path):
    st = Storage(tmp_path / "fmt.db")
    yield st
    st.close()


# ---------------- 标准治理（版本化 + 审批） ----------------


def test_unapproved_standard_not_active(fmt_storage):
    rec = fmt_storage.save_format_standard("company_format", STRICT_PARAMS, "admin_f")
    assert rec["status"] == "DRAFT"
    assert fmt_storage.get_active_format_standard() is None, "未审批标准不得生效"


def test_standard_versioning_and_approval(fmt_storage):
    v1 = fmt_storage.save_format_standard("company_format", STRICT_PARAMS, "admin_f")
    assert fmt_storage.approve_format_standard(
        "company_format", v1["version"], "legal_f"
    )
    active = fmt_storage.get_active_format_standard()
    assert active["version"] == v1["version"] and active["approved_by"] == "legal_f"

    v2 = fmt_storage.save_format_standard(
        "company_format", {**STRICT_PARAMS, "line_spacing_max": 2.0}, "admin_f"
    )
    assert v2["version"] == v1["version"] + 1
    assert fmt_storage.approve_format_standard(
        "company_format", v2["version"], "legal_f"
    )
    active2 = fmt_storage.get_active_format_standard()
    assert active2["version"] == v2["version"]
    old = fmt_storage.get_format_standard("company_format", v1["version"])
    assert old["status"] == "SUPERSEDED", "旧版本必须留痕并置 SUPERSEDED"


def test_only_draft_can_be_approved(fmt_storage):
    v1 = fmt_storage.save_format_standard("company_format", STRICT_PARAMS, "admin_f")
    assert fmt_storage.approve_format_standard(
        "company_format", v1["version"], "legal_f"
    )
    assert not fmt_storage.approve_format_standard(
        "company_format", v1["version"], "legal_f"
    )
    assert not fmt_storage.approve_format_standard("company_format", 999, "legal_f")


# ---------------- 未审批标准不得产生正式违规结论 ----------------


def test_audit_unapproved_standard_not_binding():
    out = audit_format(_FakeDoc(), std=STRICT_PARAMS)
    assert out["binding"] is False
    assert out["standard"]["status"] == "EXAMPLE_UNAPPROVED"
    assert any(i["issue_type"] == "NON_COMPLIANT" for i in out["issues"]), (
        "检测逻辑保留"
    )


def test_audit_approved_standard_binding(fmt_storage):
    rec = fmt_storage.save_format_standard("company_format", STRICT_PARAMS, "admin_f")
    fmt_storage.approve_format_standard("company_format", rec["version"], "legal_f")
    active = fmt_storage.get_active_format_standard()
    out = audit_format(_FakeDoc(), std=active["params"], standard_record=active)
    assert out["binding"] is True
    assert out["standard"]["approved_by"] == "legal_f"


def test_engine_unapproved_standard_no_formal_violation(fmt_storage, golden_samples):
    """DRAFT（未审批）严格标准作提示依据：差异必须降级，禁止正式违规结论。"""
    fmt_storage.save_format_standard(
        "company_format", STRICT_PARAMS, "admin_f"
    )  # DRAFT
    assert fmt_storage.get_active_format_standard() is None
    engine = ReviewEngine(fmt_storage)
    r = engine.review(
        golden_samples["dir"] / "mut_14_no_issue_baseline.docx", mode="COMBINED"
    )
    assert not any(i["category"] == "FORMAT_NON_COMPLIANT" for i in r["issues"]), (
        "未经审批的示例标准禁止产生正式企业违规结论"
    )
    unverified = [
        i for i in r["issues"] if i["category"] == "FORMAT_UNVERIFIED_STANDARD"
    ]
    assert unverified, "DRAFT 标准的差异应作为非约束性提示输出"
    for i in unverified:
        assert "不构成正式企业违规结论" in i["description"]
        assert i["check_status"] == "NEEDS_MANUAL_REVIEW"
        assert i["requires_human_review"] is True


def test_engine_approved_standard_produces_binding_findings(
    fmt_storage, golden_samples
):
    rec = fmt_storage.save_format_standard("company_format", STRICT_PARAMS, "admin_f")
    fmt_storage.approve_format_standard("company_format", rec["version"], "legal_f")
    engine = ReviewEngine(fmt_storage)
    r = engine.review(
        golden_samples["dir"] / "mut_14_no_issue_baseline.docx", mode="COMBINED"
    )
    binding = [i for i in r["issues"] if i["category"] == "FORMAT_NON_COMPLIANT"]
    assert binding, "审批后的严格标准应产出正式结论（缺失『合同审批单编号』占位）"
    for i in binding:
        assert f"v{rec['version']}" in i["description"], "正式结论必须留痕标准版本"
    assert not any(i["category"] == "FORMAT_UNVERIFIED_STANDARD" for i in r["issues"])


# ---------------- API（RBAC + 审批身份） ----------------


@pytest.fixture()
def api_client(fmt_storage):
    import contract_kb.api as api

    api._storage = fmt_storage
    from fastapi.testclient import TestClient

    return TestClient(api.app)


def test_api_format_standard_requires_admin(api_client):
    resp = api_client.post(
        "/api/contract-kb/format-standards",
        json={"standard_id": "company_format", "params": STRICT_PARAMS},
        headers=REVIEWER,
    )
    assert resp.status_code == 403
    ok = api_client.post(
        "/api/contract-kb/format-standards",
        json={"standard_id": "company_format", "params": STRICT_PARAMS},
        headers=ADMIN,
    )
    assert ok.status_code == 200 and ok.json()["status"] == "DRAFT"


def test_api_format_standard_approve_identity_from_auth_layer(api_client):
    created = api_client.post(
        "/api/contract-kb/format-standards",
        json={"standard_id": "company_format", "params": STRICT_PARAMS},
        headers=ADMIN,
    ).json()
    resp = api_client.post(
        "/api/contract-kb/format-standards/company_format/approve",
        json={"version": created["version"]},
        headers=REVIEWER,
    )
    assert resp.status_code == 403
    ok = api_client.post(
        "/api/contract-kb/format-standards/company_format/approve",
        json={"version": created["version"]},
        headers=ADMIN,
    )
    assert ok.status_code == 200
    assert ok.json()["approved_by"] == "admin_f", "审批身份必须来自认证层"
    listing = api_client.get(
        "/api/contract-kb/format-standards", headers=REVIEWER
    ).json()
    assert listing["active"]["version"] == created["version"]
    assert listing["active"]["status"] == "ACTIVE"
