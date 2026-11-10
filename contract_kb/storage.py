# -*- coding: utf-8 -*-
"""向后兼容门面（Compatibility Facade）：SQLite 存储实现与工厂已迁移至 `contract_kb.infrastructure.persistence.sqlite_storage`。"""

from __future__ import annotations

from .infrastructure.persistence.errors import (
    EmbeddingConfigMismatch,
    OptimisticLockConflictError,
    StorageConfigError,
)
from .infrastructure.persistence.sqlite_storage import *  # noqa: F401, F403
from .infrastructure.persistence.sqlite_storage import (  # noqa: F401
    SCHEMA_SQL,
    Storage,
    create_storage,
)

__all__ = [
    "EmbeddingConfigMismatch",
    "OptimisticLockConflictError",
    "SCHEMA_SQL",
    "Storage",
    "StorageConfigError",
    "create_storage",
]
