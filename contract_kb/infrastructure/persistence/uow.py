# -*- coding: utf-8 -*-
"""统一工作单元（Unit of Work）基础设施实现：支持可嵌套 SAVEPOINT 与真正的多操作原子回滚。"""

from __future__ import annotations

import uuid
from typing import Any

from ...application.ports.persistence import UnitOfWorkPort


class _SavepointUnitOfWorkBase(UnitOfWorkPort):
    """基于存储层原生 SAVEPOINT 原语与事务深度计数器的通用原子工作单元。"""

    def __init__(self, storage: Any):
        self._st = storage
        self._sp = f"sp_{uuid.uuid4().hex[:10]}"
        self._active = False

    def __enter__(self) -> "_SavepointUnitOfWorkBase":
        self._st._savepoint_begin(self._sp)
        self._active = True
        return self

    def commit(self) -> None:
        if self._active:
            self._st._savepoint_commit(self._sp)
            self._active = False

    def rollback(self) -> None:
        if self._active:
            self._st._savepoint_rollback(self._sp)
            self._active = False

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        try:
            if exc_type is not None:
                self.rollback()
            elif self._active:
                self.commit()
        finally:
            self._st._savepoint_exit()


class SqliteUnitOfWork(_SavepointUnitOfWorkBase):
    """基于 SQLite SAVEPOINT + 存储层事务深度计数的原子工作单元。"""


class PostgresUnitOfWork(_SavepointUnitOfWorkBase):
    """基于 PostgreSQL SAVEPOINT + 存储层事务深度计数的原子工作单元。"""
