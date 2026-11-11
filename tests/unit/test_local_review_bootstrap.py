"""The local bootstrap and browser requests must own the same review session."""

from unittest.mock import Mock

import pytest

from contract_kb.interfaces.web import app
from contract_kb.interfaces.http.auth import authenticate_web_request
from contract_kb.application.agents.review_sessions import AgentReviewManager
from contract_kb.interfaces.web.routes import materials


def test_local_bootstrap_uses_request_identity(monkeypatch, tmp_path):
    monkeypatch.delenv("DIFF_WEB_MODE", raising=False)
    monkeypatch.delenv("CONTRACT_KB_AUTH_SHARED_KEY", raising=False)
    manager = AgentReviewManager()
    # Avoid background LLM work; exercise the real ownership checks.
    monkeypatch.setattr(
        "contract_kb.application.agents.review_sessions.AgentReviewSession.start_review",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(app, "get_agent_manager", lambda: manager)
    monkeypatch.setattr(app, "execute_current_diff", lambda path: {"annotations": []})
    app.start_local_default_review()
    principal = authenticate_web_request(
        method="POST",
        path="/api/materials/load_contract",
        query="",
        headers={},
        raw_body=b"",
        storage=None,
    )
    attachment = tmp_path / "source.docx"
    attachment.write_bytes(b"test attachment")
    catalog = Mock()
    catalog.cases_by_no = {"case-1": {"name": "test contract"}}
    catalog.get_contract_attachment_file.return_value = (attachment, {})
    monkeypatch.setattr(materials, "get_material_manager", lambda: catalog)
    monkeypatch.setattr(materials, "get_agent_manager", lambda: manager)
    monkeypatch.setattr(
        materials, "prepare_session_file", lambda *a, **k: tmp_path / "session.docx"
    )
    monkeypatch.setattr(materials, "save_session_meta", Mock())
    monkeypatch.setattr(
        materials, "execute_current_diff", lambda *a, **k: {"annotations": []}
    )
    handler = Mock()
    handler._get_request_identity.return_value = (
        "default_session",
        principal.tenant_id,
        principal.user_id,
    )
    handler.get_request_body.return_value = b'{"contract_no":"case-1"}'
    # Switching historical contracts repeatedly reuses the local session.
    for _ in range(2):
        materials.handle_post_materials_load_contract(handler)
        assert handler._send_json.call_args.args[0] == 200
    session = manager.get_or_create_session(
        "default_session", user_id=principal.user_id
    )
    assert session.user_id == principal.user_id == "local_dev_user"
    with pytest.raises(PermissionError):
        manager.get_or_create_session("default_session", user_id="another_user")
    with pytest.raises(PermissionError):
        manager.get_or_create_session("default_session", tenant_id="another_tenant")


@pytest.mark.parametrize("env", ["DIFF_WEB_MODE", "CONTRACT_KB_AUTH_SHARED_KEY"])
def test_enterprise_does_not_bootstrap_local_session(monkeypatch, env):
    monkeypatch.delenv("DIFF_WEB_MODE", raising=False)
    monkeypatch.delenv("CONTRACT_KB_AUTH_SHARED_KEY", raising=False)
    monkeypatch.setenv(env, "ENTERPRISE" if env == "DIFF_WEB_MODE" else "test-key")
    manager = Mock()
    diff = Mock()
    monkeypatch.setattr(app, "get_agent_manager", lambda: manager)
    monkeypatch.setattr(app, "execute_current_diff", diff)
    app.start_local_default_review()
    manager.start_document_review.assert_not_called()
    diff.assert_not_called()
