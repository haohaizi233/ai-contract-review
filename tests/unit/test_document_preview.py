# -*- coding: utf-8 -*-
"""送审与模板原格式在线高保真预览路由与文件定位单元测试（Document Preview Tests）。"""

import io
from pathlib import Path
from unittest.mock import Mock, patch
import urllib.parse

import pytest

from contract_kb import config
from contract_kb.interfaces.http.auth import ROLE_REVIEWER, ROLE_VIEWER
from contract_kb.interfaces.web import app
from contract_kb.interfaces.web.routes.preview import (
    handle_get_preview_file,
    handle_post_open_local,
    resolve_preview_file,
)


@pytest.fixture
def test_session_id():
    return "test_preview_sess_001"


def test_resolve_preview_file_reviewed(test_session_id, tmp_path, monkeypatch):
    """测试送审文档定位：成功定位会话文件并提取格式与哈希。"""
    s_file = tmp_path / "contract.docx"
    s_file.write_bytes(b"PK\x03\x04test docx content")

    monkeypatch.setattr(
        "contract_kb.interfaces.web.routes.preview.get_session_file",
        lambda *a, **k: s_file,
    )
    monkeypatch.setattr(
        "contract_kb.interfaces.web.routes.preview.get_session_meta",
        lambda *a, **k: {"doc_name": "《测试专利代理合同》送审稿"},
    )

    path, meta = resolve_preview_file(session_id=test_session_id, target="reviewed")
    assert path is not None
    assert path.name == "contract.docx"
    assert meta["target"] == "reviewed"
    assert meta["ext"] == ".docx"
    assert meta["display_title"] == "《测试专利代理合同》送审稿"
    assert meta["file_name"] == "测试专利代理合同-送审稿.docx"
    assert meta["size"] == len(b"PK\x03\x04test docx content")
    assert len(meta["sha256"]) == 64


def test_resolve_preview_file_reviewed_hides_technical_name(test_session_id, tmp_path, monkeypatch):
    """内部/测试占位名（mut_*、contract.*）不得面向用户展示。"""
    s_file = tmp_path / "contract.docx"
    s_file.write_bytes(b"PK\x03\x04x")
    monkeypatch.setattr(
        "contract_kb.interfaces.web.routes.preview.get_session_file",
        lambda *a, **k: s_file,
    )
    monkeypatch.setattr(
        "contract_kb.interfaces.web.routes.preview.get_session_meta",
        lambda *a, **k: {
            "doc_name": "《国内专利服务合同（年费缴纳）》送审修改稿",
            "original_filename": "mut_11_late_fee_change.docx",
        },
    )
    _, meta = resolve_preview_file(session_id=test_session_id, target="reviewed")
    assert not meta["file_name"].startswith("mut_")
    assert meta["file_name"] == "国内专利服务合同（年费缴纳）-送审修改稿.docx"


def test_resolve_preview_file_reviewed_with_original_filename(test_session_id, tmp_path, monkeypatch):
    """测试送审文档定位：优先使用元数据中保存的 original_filename。"""
    s_file = tmp_path / "contract.docx"
    s_file.write_bytes(b"PK\x03\x04test docx content")

    monkeypatch.setattr(
        "contract_kb.interfaces.web.routes.preview.get_session_file",
        lambda *a, **k: s_file,
    )
    monkeypatch.setattr(
        "contract_kb.interfaces.web.routes.preview.get_session_meta",
        lambda *a, **k: {
            "doc_name": "《测试专利代理合同》送审稿",
            "original_filename": "我的送审稿_v2.docx",
        },
    )

    path, meta = resolve_preview_file(session_id=test_session_id, target="reviewed")
    assert path is not None
    assert meta["file_name"] == "我的送审稿_v2.docx"
    assert meta["original_filename"] == "我的送审稿_v2.docx"


def test_resolve_preview_file_template(test_session_id, tmp_path, monkeypatch):
    """测试基准母本定位：正确关联母本库中的物理文件。"""
    tpl_file = tmp_path / "国内专利服务合同（年费缴纳）-事务所.docx"
    tpl_file.write_bytes(b"PK\x03\x04sample template docx")

    monkeypatch.setattr(config, "TEMPLATE_DIR", tmp_path)
    monkeypatch.setattr(
        "contract_kb.interfaces.web.routes.preview.get_session_meta",
        lambda *a, **k: {"forced_template_id": "MB20251205013"},
    )

    mock_storage = Mock()
    mock_storage.get_document.return_value = {
        "document_id": "MB20251205013",
        "source_file": "国内专利服务合同（年费缴纳）-事务所.docx",
        "name": "国内专利服务合同（年费缴纳）",
    }
    monkeypatch.setattr(
        "contract_kb.interfaces.web.routes.preview.create_storage",
        lambda: mock_storage,
    )

    path, meta = resolve_preview_file(session_id=test_session_id, target="template")
    assert path is not None
    assert path.name == "国内专利服务合同（年费缴纳）-事务所.docx"
    assert meta["target"] == "template"
    assert meta["template_id"] == "MB20251205013"
    assert "年费缴纳" in meta["display_title"]


def test_handle_get_preview_file_info_mode(test_session_id, monkeypatch):
    """测试 info=1 模式：输出 JSON 元数据而不吐出二进制大包。"""
    handler = Mock()
    handler._get_request_identity.return_value = (test_session_id, "default", "u1")
    handler._send_json = Mock()

    fake_path = Path("fake.docx")
    fake_meta = {
        "target": "reviewed",
        "file_name": "fake.docx",
        "display_title": "测试合同",
        "size": 1024,
        "sha256": "abcdef123456",
        "ext": ".docx",
    }

    monkeypatch.setattr(
        "contract_kb.interfaces.web.routes.preview.resolve_preview_file",
        lambda *a, **k: (fake_path, fake_meta),
    )
    monkeypatch.setattr(Path, "exists", lambda self: True)

    parsed = urllib.parse.urlparse("/api/diff/preview_file?target=reviewed&info=1")
    handle_get_preview_file(handler, parsed)

    handler._send_json.assert_called_once()
    status_code, data = handler._send_json.call_args.args
    assert status_code == 200
    assert data["success"] is True
    assert data["file_name"] == "fake.docx"


def test_handle_get_preview_file_binary_stream(test_session_id, tmp_path, monkeypatch):
    """测试二进制流直接预览：带 Content-Disposition inline 与正确 MIME 类型。"""
    sample_file = tmp_path / "test.docx"
    content = b"PK\x03\x04test_docx_binary_bytes"
    sample_file.write_bytes(content)

    handler = Mock()
    handler._get_request_identity.return_value = (test_session_id, "default", "u1")
    wfile = io.BytesIO()
    handler.wfile = wfile
    headers_sent = {}
    handler.send_header = lambda k, v: headers_sent.update({k: v})

    monkeypatch.setattr(
        "contract_kb.interfaces.web.routes.preview.resolve_preview_file",
        lambda *a, **k: (sample_file, {"target": "reviewed"}),
    )

    parsed = urllib.parse.urlparse("/api/diff/preview_file?target=reviewed")
    handle_get_preview_file(handler, parsed)

    handler.send_response.assert_called_with(200)
    assert headers_sent.get("Content-Type") == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert "inline" in headers_sent.get("Content-Disposition", "")
    assert headers_sent.get("Content-Length") == str(len(content))
    assert wfile.getvalue() == content


def test_handle_post_open_local_remote_client_forbidden(test_session_id, monkeypatch):
    """安全边界检查：远程非本机请求禁止唤起操作系统进程。"""
    handler = Mock()
    handler._get_request_identity.return_value = (test_session_id, "default", "u1")
    handler.client_address = ("192.168.1.100", 54321)  # 远程IP
    handler._send_json = Mock()

    handle_post_open_local(handler)
    handler._send_json.assert_called_once()
    status_code, data = handler._send_json.call_args.args
    assert status_code == 400
    assert data["error"] == "remote_not_supported"


def test_handle_post_open_local_loopback_client_allowed(test_session_id, tmp_path, monkeypatch):
    """安全边界检查：本机 loopback 环境下允许解析本地文件。"""
    sample_file = tmp_path / "test.docx"
    sample_file.write_bytes(b"content")

    handler = Mock()
    handler._get_request_identity.return_value = (test_session_id, "default", "u1")
    handler.client_address = ("127.0.0.1", 54321)  # 本机 loopback
    handler.get_request_body.return_value = b'{"target":"reviewed"}'
    handler._send_json = Mock()

    monkeypatch.setattr(
        "contract_kb.interfaces.web.routes.preview.resolve_preview_file",
        lambda *a, **k: (sample_file, {"target": "reviewed"}),
    )

    with patch("os.startfile", create=True):
        handle_post_open_local(handler)
        handler._send_json.assert_called_once()
        status_code, data = handler._send_json.call_args.args
        assert status_code == 200
        assert data["success"] is True


def test_preview_routes_rbac_permissions():
    """验证 RBAC 路由权限矩阵配置。"""
    assert app.WEB_ROUTE_ROLES["GET /api/diff/preview_file"] == ROLE_VIEWER
    assert app.WEB_ROUTE_ROLES["POST /api/diff/open_local"] == ROLE_REVIEWER
