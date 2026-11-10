# -*- coding: utf-8 -*-
"""Phase 16-S-R1: 防重放 Nonce 存储显式依赖注入、持久化与 Fail-Closed 测试集。"""

from __future__ import annotations

import os
import sys
import time
from unittest.mock import MagicMock

import pytest

from contract_kb.infrastructure.persistence.sqlite_storage import create_storage
from contract_kb.interfaces.http.auth import InMemoryAndDbNonceStore


def test_nonce_store_production_without_implicit_sys_modules(tmp_path):
    """生产环境下 NonceStore 显式注入存储，绝不依赖隐式 sys.modules['contract_kb.api']。"""
    old_env = os.environ.get("CONTRACT_KB_ENV")
    os.environ["CONTRACT_KB_ENV"] = "production"
    try:
        db_file = tmp_path / "test_nonce.db"
        real_storage = create_storage(db_path=db_file)
        store = InMemoryAndDbNonceStore(storage=real_storage)

        # 彻底移除 contract_kb.api 模拟独立 Web 运行时
        old_mod = sys.modules.pop("contract_kb.api", None)
        try:
            now = time.time()
            ok = store.record_and_check(
                nonce=f"nonce_prod_{time.time_ns()}",
                tenant="corp_alpha",
                user="alice",
                expires_at=now + 60,
            )
            assert ok is True
        finally:
            if old_mod is not None:
                sys.modules["contract_kb.api"] = old_mod
    finally:
        if old_env is not None:
            os.environ["CONTRACT_KB_ENV"] = old_env
        else:
            os.environ.pop("CONTRACT_KB_ENV", None)


def test_nonce_store_in_memory_replay_rejected():
    """同一个 Nonce 在有效期内二次提交，必须被内存快速判定并拦截重放。"""
    store = InMemoryAndDbNonceStore()
    nonce = f"nonce_replay_{time.time_ns()}"
    exp = time.time() + 60

    # 首次使用 -> True
    ok1 = store.record_and_check(
        nonce=nonce, tenant="corp_beta", user="bob", expires_at=exp
    )
    assert ok1 is True

    # 再次重放 -> False
    ok2 = store.record_and_check(
        nonce=nonce, tenant="corp_beta", user="bob", expires_at=exp
    )
    assert ok2 is False


def test_nonce_store_cross_restart_persistence(tmp_path):
    """跨进程/跨服务重启防重放：新实例接入相同数据库存储，能够准确拦截前一进程已消费的 Nonce。"""
    db_file = tmp_path / "persistent_nonce.db"
    storage_inst1 = create_storage(db_path=db_file)
    store1 = InMemoryAndDbNonceStore(storage=storage_inst1)

    nonce = f"nonce_cross_restart_{time.time_ns()}"
    exp = time.time() + 120

    # 进程 1 消费 nonce
    ok1 = store1.record_and_check(
        nonce=nonce, tenant="corp_gamma", user="charlie", expires_at=exp
    )
    assert ok1 is True

    # 模拟服务重启：全新的内存存储实例，但挂载相同的持久化数据库
    storage_inst2 = create_storage(db_path=db_file)
    store2 = InMemoryAndDbNonceStore(storage=storage_inst2)

    # 进程 2 接收到相同的重放请求 -> 必须被数据库层拦截
    ok2 = store2.record_and_check(
        nonce=nonce, tenant="corp_gamma", user="charlie", expires_at=exp
    )
    assert ok2 is False


def test_nonce_store_db_failure_fails_closed():
    """持久化 Nonce 存储遇到数据库故障时，必须 Fail-Closed 抛出 PermissionError，严禁降级放行。"""
    failing_storage = MagicMock()
    failing_storage.record_and_check_nonce.side_effect = RuntimeError(
        "Disk I/O error on Nonce table"
    )

    store = InMemoryAndDbNonceStore(storage=failing_storage)
    nonce = f"nonce_db_err_{time.time_ns()}"

    with pytest.raises(PermissionError) as exc_info:
        store.record_and_check(
            nonce=nonce, tenant="corp_delta", user="david", expires_at=time.time() + 60
        )
    assert "安全降级阻断" in str(exc_info.value) or "异常" in str(exc_info.value)


def test_nonce_store_production_without_storage_fails_closed():
    """生产模式下若持久化存储不可用且无法构造，必须 Fail-Closed 阻断。"""
    old_env = os.environ.get("CONTRACT_KB_ENV")
    os.environ["CONTRACT_KB_ENV"] = "production"
    try:
        # 强制将存储设为不支持 record_and_check_nonce 的无效对象
        store = InMemoryAndDbNonceStore(storage=object())
        with pytest.raises(PermissionError) as exc_info:
            store.record_and_check(
                nonce=f"nonce_no_st_{time.time_ns()}",
                tenant="corp_fail",
                user="eve",
                expires_at=time.time() + 60,
            )
        assert "生产环境下持久化 Nonce 存储不可用" in str(exc_info.value)
    finally:
        if old_env is not None:
            os.environ["CONTRACT_KB_ENV"] = old_env
        else:
            os.environ.pop("CONTRACT_KB_ENV", None)
