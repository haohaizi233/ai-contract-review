# -*- coding: utf-8 -*-
"""企业微信微盘探测器 - 离线单元测试套件。

涵盖凭证脱敏、错误分类、客户端接口模拟、流式安全下载、完整性校验及报告渲染测试。
全部测试在离线 Mock 隔离环境下运行，不产生网络开销。
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from client import WeComWeDriveClient
from probe import WeDriveProbeRunner
from validators import (
    ErrorCategory,
    classify_wecom_error,
    mask_cookie_or_url,
    mask_secret,
    mask_token,
    verify_file_integrity,
)


def test_masking_functions():
    """测试敏感凭据脱敏函数。"""
    assert mask_secret("123456") == "******"
    assert mask_secret(None) == "<EMPTY>"
    secret_str = "OQ1HIYez0eJvXkuUrhzepGgvKdWcGV7Tgg71FCW_u7w"
    masked = mask_secret(secret_str)
    assert masked.startswith("OQ1H")
    assert masked.endswith("_u7w")
    assert "0eJvXkuU" not in masked

    token_str = "DRN3Dl2jRcEz4UYW4Lpkgb_E-bSR3X9tHdlFOk3FMJClm9g9qaNzISJqBeFdSijP2swd1lRV0GSI52RrFGo0"
    masked_tok = mask_token(token_str)
    assert masked_tok.startswith("DRN3Dl")
    assert masked_tok.endswith("rFGo0")
    assert "RcEz4UYW" not in masked_tok

    assert mask_cookie_or_url(None) == "<EMPTY>"
    assert mask_cookie_or_url(
        "https://example.com/download?sig=abcdef1234567890"
    ).startswith("https://")


def test_classify_wecom_error():
    """测试企业微信错误码归类与中文建议生成。"""
    # 0 成功
    c0 = classify_wecom_error(0, "ok")
    assert c0["category"] == ErrorCategory.SUCCESS.value

    # 60020 IP 限制
    c60020 = classify_wecom_error(
        60020, "not allow to access from your ip, hint: [123], from ip: 203.0.113.21"
    )
    assert c60020["category"] == ErrorCategory.TRUSTED_IP_ERROR.value
    assert c60020["caller_ip"] == "203.0.113.21"
    assert "可信" in c60020["suggestion"]
    assert "203.0.113.21" in c60020["suggestion"]

    # 48002 微盘未授权
    c48002 = classify_wecom_error(48002, "api not allow")
    assert c48002["category"] == ErrorCategory.API_UNAUTHORIZED.value

    # 40001 凭证错误
    c40001 = classify_wecom_error(40001, "invalid credential")
    assert c40001["category"] == ErrorCategory.INVALID_CREDENTIALS.value

    # 2000003 空间无权限
    c2000003 = classify_wecom_error(2000003, "no space auth")
    assert c2000003["category"] == ErrorCategory.SPACE_NO_PERMISSION.value


def test_client_init_validation():
    """测试客户端参数校验。"""
    with pytest.raises(ValueError):
        WeComWeDriveClient(corp_id="", app_secret="secret")
    with pytest.raises(ValueError):
        WeComWeDriveClient(corp_id="corp", app_secret="")

    client = WeComWeDriveClient(
        corp_id="my_corp",
        app_secret="my_sec",
        timeout=15,
        proxy="http://127.0.0.1:8888",
        disable_system_proxy=True,
    )
    assert client.timeout == 15
    assert client.session.trust_env is False
    assert client.session.proxies.get("https") == "http://127.0.0.1:8888"


@patch.object(requests.Session, "get")
def test_client_get_token(mock_get):
    """测试 Token 获取及缓存刷新。"""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "errcode": 0,
        "errmsg": "ok",
        "access_token": "mocked_token_xyz",
        "expires_in": 7200,
    }
    mock_resp.status_code = 200
    mock_get.return_value = mock_resp

    client = WeComWeDriveClient("corp", "secret")
    data, duration_ms = client.get_token(force_refresh=True)

    assert data["errcode"] == 0
    assert data["access_token"] == "mocked_token_xyz"
    assert client.ensure_token() == "mocked_token_xyz"

    # 再次调用命中缓存
    cached_data, _ = client.get_token(force_refresh=False)
    assert cached_data.get("from_cache") is True
    assert mock_get.call_count == 1


@patch.object(requests.Session, "post")
def test_client_get_space_info(mock_post):
    """测试微盘 space_info 调用。"""
    client = WeComWeDriveClient("corp", "secret")
    client._token = "valid_tok"
    client._token_expires_at = 9999999999.0

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "errcode": 0,
        "errmsg": "ok",
        "space_info": {
            "spaceid": "s.12345",
            "space_name": "法律事务部",
            "space_sub_type": 0,
        },
    }
    mock_post.return_value = mock_resp

    data, duration_ms = client.get_space_info("s.12345")
    assert data["errcode"] == 0
    assert data["space_info"]["space_name"] == "法律事务部"
    assert mock_post.call_count == 1


@patch.object(requests.Session, "post")
def test_client_list_files(mock_post):
    """测试微盘 file_list 调用。"""
    client = WeComWeDriveClient("corp", "secret")
    client._token = "valid_tok"
    client._token_expires_at = 9999999999.0

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "errcode": 0,
        "errmsg": "ok",
        "has_more": False,
        "file_list": {
            "item": [
                {
                    "fileid": "f_01",
                    "file_name": "合同.docx",
                    "file_type": 2,
                    "file_size": 1024,
                },
                {"fileid": "d_01", "file_name": "2026模板", "file_type": 1},
            ]
        },
    }
    mock_post.return_value = mock_resp

    data, duration_ms = client.list_files("s.12345")
    assert data["errcode"] == 0
    assert len(data["file_list"]["item"]) == 2


def test_download_and_verify_file_integrity():
    """测试流式下载与文件哈希完整性校验。"""
    content = b"Hello Antigravity WeDrive Test File Stream Content 1234567890"
    expected_size = len(content)
    expected_md5 = hashlib.md5(content).hexdigest()
    expected_sha1 = hashlib.sha1(content).hexdigest()
    expected_sha256 = hashlib.sha256(content).hexdigest()

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "test_contract.docx"

        # 模拟流式响应下载
        client = WeComWeDriveClient("corp", "secret")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content = MagicMock(return_value=[content[:10], content[10:]])

        with patch.object(client.session, "get", return_value=mock_resp):
            dl_res, dur = client.download_file_stream(
                download_url="https://dl.weixin.qq.com/mock",
                cookie_name="WW_DOWN",
                cookie_value="mock_cookie_val",
                target_path=test_file,
                max_bytes=1024 * 1024,
            )

        assert dl_res["success"] is True
        assert test_file.exists()
        assert test_file.stat().st_size == expected_size

        # 验证完整性
        v_res = verify_file_integrity(
            test_file,
            expected_size=expected_size,
            expected_sha=expected_sha256,
            expected_md5=expected_md5,
        )
        assert v_res["valid"] is True
        assert v_res["size_match"] is True
        assert v_res["md5_match"] is True
        assert v_res["sha_match"] is True
        assert v_res["sha1"] == expected_sha1


def test_download_file_size_exceeded():
    """测试流式下载超过最大大小限制时自动熔断并清理临时文件。"""
    large_content = b"X" * 1024 * 1024 * 2  # 2MB

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "too_large.docx"
        client = WeComWeDriveClient("corp", "secret")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content = MagicMock(return_value=[large_content])

        with patch.object(client.session, "get", return_value=mock_resp):
            dl_res, _ = client.download_file_stream(
                download_url="https://dl.weixin.qq.com/mock",
                cookie_name="WW_DOWN",
                cookie_value="val",
                target_path=test_file,
                max_bytes=1024 * 1024,  # 限 1MB
            )

        assert dl_res["success"] is False
        assert "超过最大允许限制" in dl_res["error"]
        assert not test_file.exists()


def test_probe_runner_mock_happy_path(monkeypatch):
    """测试探测器完整运行并在完全可用场景下生成结论 A。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = WeDriveProbeRunner()
        runner.reports_dir = Path(tmpdir) / "reports"
        runner.downloads_dir = Path(tmpdir) / "downloads"
        runner.reports_dir.mkdir(parents=True, exist_ok=True)
        runner.downloads_dir.mkdir(parents=True, exist_ok=True)

        runner.corp_id = "test_corp"
        runner.app_secret = "test_secret"
        runner.space_id = "s.mock_space"

        client = WeComWeDriveClient("test_corp", "test_secret")
        client._token = "valid_mock_token"
        client._token_expires_at = 9999999999.0
        runner.client = client

        # Mock API responses
        monkeypatch.setattr(
            client,
            "get_token",
            lambda force_refresh=True: (
                {"errcode": 0, "access_token": "tok", "expires_in": 7200},
                10.0,
            ),
        )
        monkeypatch.setattr(
            client,
            "get_space_info",
            lambda spaceid: (
                {
                    "errcode": 0,
                    "space_info": {"space_name": "法律部", "space_sub_type": 0},
                },
                15.0,
            ),
        )
        monkeypatch.setattr(
            client,
            "list_files",
            lambda spaceid, fatherid=None, sort_type=1, start=0, limit=100: (
                {
                    "errcode": 0,
                    "has_more": False,
                    "file_list": {
                        "item": [
                            {
                                "fileid": "file_1",
                                "file_name": "测试采购合同.docx",
                                "file_type": 2,
                                "file_size": 24,
                                "mtime": 1700000000,
                            }
                        ]
                    },
                },
                20.0,
            ),
        )
        monkeypatch.setattr(
            client,
            "get_file_info",
            lambda fileid: (
                {
                    "errcode": 0,
                    "file_info": {
                        "fileid": "file_1",
                        "file_name": "测试采购合同.docx",
                        "file_size": 24,
                        "file_type": 2,
                    },
                },
                12.0,
            ),
        )
        monkeypatch.setattr(
            client,
            "get_file_download_ticket",
            lambda fileid: (
                {
                    "errcode": 0,
                    "download_url": "https://test.dl",
                    "cookie_name": "K",
                    "cookie_value": "V",
                },
                14.0,
            ),
        )

        # Mock stream download to write small file
        def mock_dl_stream(
            download_url, cookie_name, cookie_value, target_path, max_bytes
        ):
            target_path.write_bytes(b"Mock Contract Data 12345")
            return {
                "success": True,
                "status_code": 200,
                "downloaded_bytes": 24,
                "saved_path": str(target_path),
            }, 25.0

        monkeypatch.setattr(client, "download_file_stream", mock_dl_stream)

        runner.run_stage_b_api_tests()
        runner.generate_reports()

        assert runner.result_data["conclusion"]["level"] == "A"
        assert (runner.reports_dir / "probe_result.json").exists()
        assert (runner.reports_dir / "probe_report.md").exists()


def test_probe_runner_trusted_ip_blocked(monkeypatch):
    """测试探测器遇到 60020 IP 白名单拦截时准确定位结论 C。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = WeDriveProbeRunner()
        runner.reports_dir = Path(tmpdir) / "reports"
        runner.downloads_dir = Path(tmpdir) / "downloads"
        runner.reports_dir.mkdir(parents=True, exist_ok=True)
        runner.downloads_dir.mkdir(parents=True, exist_ok=True)

        runner.corp_id = "test_corp"
        runner.app_secret = "test_secret"
        runner.space_id = "s.mock_space"

        client = WeComWeDriveClient("test_corp", "test_secret")
        client._token = "valid_mock_token"
        client._token_expires_at = 9999999999.0
        runner.client = client

        monkeypatch.setattr(
            client,
            "get_token",
            lambda force_refresh=True: (
                {"errcode": 0, "access_token": "tok", "expires_in": 7200},
                10.0,
            ),
        )
        monkeypatch.setattr(
            client,
            "get_space_info",
            lambda spaceid: (
                {
                    "errcode": 60020,
                    "errmsg": "not allow to access from your ip, hint: [179401], from ip: 203.0.113.21",
                },
                15.0,
            ),
        )
        monkeypatch.setattr(
            client,
            "list_files",
            lambda spaceid, fatherid=None, sort_type=1, start=0, limit=100: (
                {
                    "errcode": 60020,
                    "errmsg": "not allow to access from your ip, from ip: 203.0.113.21",
                },
                15.0,
            ),
        )

        runner.run_stage_b_api_tests()
        runner.generate_reports()

        assert runner.result_data["conclusion"]["level"] == "C"
        assert "企业可信IP" in runner.result_data["conclusion"]["title"]
        assert "203.0.113.21" in runner.result_data["conclusion"]["details"]


def test_http_200_with_640008_space_permission_denied():
    """测试 HTTP 200 但业务返回 640008 权限拒绝时的识别与分类。"""
    c = classify_wecom_error(640008, "permision deny, from ip: 183.23.152.239")
    assert c["category"] == ErrorCategory.SPACE_NO_PERMISSION.value
    assert "无访问权限" in c["description"]
    assert "640008" in c["description"]


def test_token_expiry_and_refresh_mechanism():
    """测试 Token 失效判定与强制刷新。"""
    client = WeComWeDriveClient("corp", "secret")
    client._token = "expired_token"
    client._token_expires_at = 1000.0  # 远早于当前时间

    with patch.object(requests.Session, "get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "errcode": 0,
            "errmsg": "ok",
            "access_token": "fresh_token_123",
            "expires_in": 7200,
        }
        mock_get.return_value = mock_resp

        tok = client.ensure_token()
        assert tok == "fresh_token_123"
        assert client._token == "fresh_token_123"
        assert mock_get.call_count == 1


def test_network_timeout_handling():
    """测试网络超时被安全捕获。"""
    client = WeComWeDriveClient("corp", "secret", timeout=1)
    client._token = "valid_tok"
    client._token_expires_at = 9999999999.0

    with patch.object(requests.Session, "post") as mock_post:
        mock_post.side_effect = requests.exceptions.Timeout("Connection timed out")
        with pytest.raises(requests.exceptions.Timeout):
            client.get_space_info("s.test")


def test_rate_limit_and_space_not_exist_classification():
    """测试接口限流 (45009) 与空间不存在 (640001) 的错误分类。"""
    # 接口限流
    c_rate = classify_wecom_error(45009, "api freq out of limit")
    assert "超限" in c_rate["description"] or "45009" in str(c_rate)

    # 空间不存在
    c_not_exist = classify_wecom_error(640001, "space not exist")
    assert "不存在" in c_not_exist["description"] or "640001" in str(c_not_exist)


def test_missing_config_detection():
    """测试环境变量缺失检查。"""
    runner = WeDriveProbeRunner()
    runner.corp_id = ""
    runner.app_secret = ""
    runner.space_id = ""
    ok = runner.run_stage_a_env_check()
    assert ok is False
    res = runner.result_data["environment_check"]
    assert res["has_corp_id"] is False
    assert res["has_app_secret"] is False
    assert res["has_space_id"] is False
    assert len(res["missing_configs"]) == 3


@pytest.mark.parametrize(
    "errcode,errmsg,expected_cat,expected_kw",
    [
        (640001, "space not exist", ErrorCategory.RESOURCE_NOT_FOUND.value, "空间"),
        (640002, "file not exist", ErrorCategory.RESOURCE_NOT_FOUND.value, "文件"),
        (640004, "access forbidden", ErrorCategory.SPACE_NO_PERMISSION.value, "无权限访问"),
        (640005, "member not in space", ErrorCategory.SPACE_NO_PERMISSION.value, "成员不在空间内"),
        (640008, "permision deny", ErrorCategory.SPACE_NO_PERMISSION.value, "微盘空间无访问权限"),
        (640017, "wedrive api disabled", ErrorCategory.API_UNAUTHORIZED.value, "未开启微盘 API"),
        (45009, "api freq limit", ErrorCategory.RATE_LIMIT_ERROR.value, "频率超限"),
        (45029, "app freq limit", ErrorCategory.RATE_LIMIT_ERROR.value, "频率超限"),
        (60020, "from ip: 183.23.152.239", ErrorCategory.TRUSTED_IP_ERROR.value, "企业可信 IP"),
        (999999, "custom unexpected error", ErrorCategory.UNKNOWN_ERROR.value, "未收录"),
    ],
)
def test_official_wecom_error_codes_parameterized(errcode, errmsg, expected_cat, expected_kw):
    """参数化严格验证企业微信官方微盘错误码分类及描述契约。"""
    res = classify_wecom_error(errcode, errmsg, http_status=200)
    assert res["category"] == expected_cat
    assert expected_kw in res["description"]
    assert res["errcode"] == errcode
    assert res["http_status"] == 200
    assert "official_doc" in res


