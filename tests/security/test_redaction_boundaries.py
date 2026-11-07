# -*- coding: utf-8 -*-
"""安全测试：统一递归脱敏与审计入口边界保护（T03 / F07）。

测试目标：
1. 递归脱敏：无论多层嵌套的 dict、list、tuple、set，均能穿透并对敏感键值脱敏；
2. 边界保护：循环引用不发生栈溢出（RecursionError），超深嵌套安全截断；
3. 文本与 JSON 识别：JSON 字符串与正则敏感变体（DSN、Token、Cookie、Password）均能清洗；
4. 审计白名单：safe_audit_detail 保证仅白名单字段进入审计日志；
5. 存储层 fail-closed：存储层 log_audit 在入库前强制结构化脱敏，不信任调用方。
"""

from __future__ import annotations

from pathlib import Path

from contract_kb.infrastructure.observability.redaction import (
    redact_dict,
    redact_sensitive_text,
    redact_value,
    safe_audit_detail,
)
from contract_kb.storage import Storage


def test_redact_value_recursive_dict_list_and_variants():
    """验证多层嵌套字典、列表及各种变体敏感键（大小写、连字符、下划线）的彻底脱敏。"""
    payload = {
        "event": "login",
        "nested": [
            {"Api-Key": "secret_key_12345", "user": "alice"},
            {"X_Auth_Token": "token_value_xyz", "data": [{"passwd": "my_password"}]},
        ],
        "cookie": "SESSIONID=abcdef12345678; Path=/",
        "dsn": "postgresql://usr:super_secret_pw@db.corp.internal:5432/appdb",
    }
    redacted = redact_value(payload)

    # 结构保持不变
    assert redacted["event"] == "login"
    assert redacted["nested"][0]["user"] == "alice"
    # 敏感字段全部掩码
    assert redacted["nested"][0]["Api-Key"] == "***REDACTED***"
    assert redacted["nested"][1]["X_Auth_Token"] == "***REDACTED***"
    assert redacted["nested"][1]["data"][0]["passwd"] == "***REDACTED***"
    assert "super_secret_pw" not in str(redacted["dsn"])
    assert "abcdef12345678" not in str(redacted["cookie"])
    assert redact_dict(payload) == redacted


def test_redact_value_cycle_reference_safe():
    """验证循环引用对象不会导致递归栈溢出。"""
    a: dict = {"name": "node_a", "secret": "plain_secret_value"}
    b: dict = {"name": "node_b", "a_ref": a}
    a["b_ref"] = b

    cleaned = redact_value(a)
    assert cleaned["secret"] == "***REDACTED***"
    assert cleaned["name"] == "node_a"
    assert cleaned["b_ref"]["name"] == "node_b"
    assert cleaned["b_ref"]["a_ref"] == "[CIRCULAR_REFERENCE]"


def test_redact_value_deep_nesting_safe():
    """验证超过最大安全递归深度的结构被安全截断，不耗尽调用栈。"""
    curr: dict = {"level": 0, "secret": "top_secret"}
    root = curr
    for i in range(1, 40):
        next_d = {"level": i, "secret": f"secret_{i}"}
        curr["child"] = next_d
        curr = next_d

    cleaned = redact_value(root, max_depth=15)
    assert cleaned["secret"] == "***REDACTED***"
    # 深入到第 15 层后应为截断占位符
    target = cleaned
    for _ in range(15):
        target = target["child"]
    assert target == "[MAX_DEPTH_EXCEEDED]"


def test_redact_sensitive_text_json_string():
    """验证 JSON 字符串格式与 Python 字典字符串格式的敏感凭据脱敏。"""
    json_str = '{"api_key":"synthetic-secret-123", "public_id":"pub_456"}'
    cleaned = redact_sensitive_text(json_str)
    assert "synthetic-secret-123" not in cleaned
    assert "pub_456" in cleaned

    dict_repr = "{'password': 'plain_pwd', 'username': 'testuser'}"
    cleaned_repr = redact_sensitive_text(dict_repr)
    assert "plain_pwd" not in cleaned_repr
    assert "testuser" in cleaned_repr


def test_safe_audit_detail_whitelist():
    """验证审计详情构建严格受控于事件字段白名单，非白名单字段被过滤。"""
    detail = safe_audit_detail(
        "review_task",
        {
            "mode": "STANDARD",
            "bytes": 2048,
            "leak_pass": "should_be_excluded_from_audit",
            "unexpected_field": "drop_me",
        },
    )
    assert "mode=STANDARD" in detail
    assert "bytes=2048" in detail
    assert "should_be_excluded_from_audit" not in detail
    assert "unexpected_field" not in detail


def test_storage_log_audit_enforces_redaction(tmp_path: Path):
    """验证存储层 log_audit 在底层自动执行脱敏，即使调用方传入明文敏感词也绝不落库。"""
    st = Storage(tmp_path / "audit_test.sqlite")
    st.log_audit(
        actor="synthetic_actor",
        action="NOTE",
        target="target_obj",
        detail="user password=raw_super_secret_password_value and token=bearer_xyz12345",
    )
    row = st.conn.execute(
        "SELECT detail FROM audit_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    saved_detail = row["detail"]
    assert "raw_super_secret_password_value" not in saved_detail
    assert "bearer_xyz12345" not in saved_detail
    assert "***REDACTED***" in saved_detail
    st.close()
