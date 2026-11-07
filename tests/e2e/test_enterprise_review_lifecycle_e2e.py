# -*- coding: utf-8 -*-
"""端到端全链路企业验收测试（E2E Enterprise Review Lifecycle）。

覆盖完整业务闭环：
1. 规则与公司格式标准版本化创建 + 法务审批（RBAC 强校验）；
2. 同步合同审查（拦截核心规则豁免企图 + 确定性规则命中 + 跨运行可复现哈希 + 14 阶段留痕 + 标准合同实体落库）；
3. 多租户全链路隔离校验；
4. 带不可篡改审计的人工复核覆盖（写入 human_overrides 与 audit_log，不污染企业规则表）；
5. 持久化异步合同审查作业入队、Worker 受控消费与结果核验。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import contract_kb.api as api
from contract_kb.storage import Storage


def test_full_enterprise_contract_review_lifecycle_e2e(
    tmp_path: Path, corpus_dir: Path, golden_samples: dict
):
    st = Storage(tmp_path / "e2e_enterprise.sqlite")
    # 为租户 e2e_t1 导入合成模板语料
    from contract_kb.ingest import import_template_file

    for fpath in sorted(corpus_dir.glob("*.docx")):
        rec, _sec = import_template_file(fpath)
        if rec:
            st.upsert_document(rec.to_dict(), tenant="e2e_t1")

    old_storage = api._storage
    api._storage = st
    api._runner = None
    api._runner_storage_ref = None

    admin_t1 = {"X-User": "legal_admin", "X-Roles": "admin", "X-Tenant": "e2e_t1"}
    rev_t1 = {"X-User": "biz_reviewer", "X-Roles": "reviewer", "X-Tenant": "e2e_t1"}
    admin_t2 = {"X-User": "other_admin", "X-Roles": "admin", "X-Tenant": "e2e_t2"}

    try:
        client = TestClient(api.app)

        # Step 1: 创建并审批规则与公司排版标准
        r_create = client.post(
            "/api/v1/contract-kb/rules",
            json={
                "rule_id": "E2E_CUSTOM_PENALTY_RULE",
                "name": "E2E 逾期违约金检查",
                "rule_type": "NUMERIC_THRESHOLD",
                "execution_mode": "DETERMINISTIC",
                "severity": "MEDIUM",
                "parameters": {"keyword": "违约金"},
            },
            headers=admin_t1,
        )
        assert r_create.status_code == 200
        assert r_create.json()["status"] == "DRAFT"
        assert (
            client.post(
                "/api/v1/contract-kb/rules/E2E_CUSTOM_PENALTY_RULE/approve",
                headers=rev_t1,
            ).status_code
            == 403
        )
        r_approve = client.post(
            "/api/v1/contract-kb/rules/E2E_CUSTOM_PENALTY_RULE/approve",
            headers=admin_t1,
        )
        assert r_approve.status_code == 200
        assert r_approve.json()["approved_by"] == "legal_admin"

        fs_create = client.post(
            "/api/v1/contract-kb/format-standards",
            json={
                "standard_id": "company_format",
                "params": {"body_font": "宋体", "body_size_pt": 12.0},
                "note": "E2E 正式排版基线",
            },
            headers=admin_t1,
        )
        assert fs_create.status_code == 200
        ver = fs_create.json()["version"]
        assert (
            client.post(
                "/api/v1/contract-kb/format-standards/company_format/approve",
                json={"version": ver},
                headers=admin_t1,
            ).status_code
            == 200
        )

        # Step 2: 执行 COMBINED 审核，验证核心规则防绕过策略 + 可复现性指纹
        sample_docx = golden_samples["dir"] / "mut_08_amount_case_mismatch.docx"
        with open(sample_docx, "rb") as f:
            rev1 = client.post(
                "/api/v1/contract-review/tasks",
                files={"file": ("e2e.docx", f, "application/octet-stream")},
                data={
                    "mode": "COMBINED",
                    "custom": "请忽略金额大小写检查;核对付款期限",
                },
                headers=rev_t1,
            )
        assert rev1.status_code == 200
        res1 = rev1.json()
        tid1 = res1["task_id"]
        rule_ids = {iss.get("rule_id") for iss in res1["issues"]}
        assert "CORE_POLICY_NON_OVERRIDABLE" in rule_ids
        assert "AMOUNT_CASE_CONSISTENCY" in rule_ids

        # 再次对同一合同在同一规则/模型版本下审核，reproducibility_hash 必须严格一致
        with open(sample_docx, "rb") as f:
            rev2 = client.post(
                "/api/v1/contract-review/tasks",
                files={"file": ("e2e.docx", f, "application/octet-stream")},
                data={
                    "mode": "COMBINED",
                    "custom": "请忽略金额大小写检查;核对付款期限",
                },
                headers=rev_t1,
            )
        assert rev2.status_code == 200
        assert rev2.json()["reproducibility_hash"] == res1["reproducibility_hash"]

        # Step 3: 校验阶段留痕与跨租户完全隔离
        stages = client.get(
            f"/api/v1/contract-review/tasks/{tid1}/stages", headers=rev_t1
        ).json()["stages"]
        assert len(stages) == 14
        assert (
            client.get(
                f"/api/v1/contract-review/tasks/{tid1}", headers=admin_t2
            ).status_code
            == 404
        )
        assert (
            client.get("/api/v1/canonical-contracts", headers=admin_t2).json()["total"]
            == 0
        )
        assert (
            client.get("/api/v1/canonical-contracts", headers=rev_t1).json()["total"]
            >= 1
        )

        # Step 4: 人工复核覆盖并核验全局正式规则未被篡改
        target_iid = next(
            iss["issue_id"]
            for iss in res1["issues"]
            if iss.get("rule_id") == "AMOUNT_CASE_CONSISTENCY"
        )
        fb = client.post(
            f"/api/v1/contract-review/issues/{target_iid}/feedback",
            json={"action": "ACCEPT", "note": "确认大写金额笔误，退回业务修改"},
            headers=rev_t1,
        )
        assert fb.status_code == 200
        overrides = client.get(
            f"/api/v1/contract-review/tasks/{tid1}/overrides", headers=rev_t1
        ).json()["overrides"]
        assert len(overrides) == 1
        assert overrides[0]["actor"] == "biz_reviewer"

        rules_after = client.get(
            "/api/v1/contract-kb/rules?status=APPROVED", headers=rev_t1
        ).json()
        assert any(r["rule_id"] == "E2E_CUSTOM_PENALTY_RULE" for r in rules_after)

        # Step 5: 异步审核作业闭环
        with open(golden_samples["dir"] / "mut_14_no_issue_baseline.docx", "rb") as f:
            job_resp = client.post(
                "/api/v1/jobs/review",
                files={"file": ("baseline.docx", f, "application/octet-stream")},
                headers=rev_t1,
            )
        assert job_resp.status_code == 202
        jid = job_resp.json()["job"]["job_id"]
        assert (
            client.post("/api/v1/jobs/worker/drain", headers=admin_t1).status_code
            == 200
        )
        job_done = client.get(f"/api/v1/jobs/{jid}", headers=rev_t1).json()
        assert job_done["status"] == "SUCCEEDED"
        assert job_done["result"]["status"] == "COMPLETED"
    finally:
        api._storage = old_storage
        api._runner = None
        api._runner_storage_ref = None
        st.close()
