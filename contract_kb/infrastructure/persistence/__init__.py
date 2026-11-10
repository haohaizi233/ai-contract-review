# -*- coding: utf-8 -*-
"""持久化与仓储适配器（Persistence Infrastructure）。"""

from .errors import (
    EmbeddingConfigMismatch,
    OptimisticLockConflictError,
    StorageConfigError,
)
from .postgres_storage import MIGRATIONS_DIR, PgVectorStorage, StoragePG
from .sqlite_storage import SCHEMA_SQL, Storage, create_storage
from .uow import PostgresUnitOfWork, SqliteUnitOfWork

__all__ = [
    "EmbeddingConfigMismatch",
    "MIGRATIONS_DIR",
    "OptimisticLockConflictError",
    "PgVectorStorage",
    "PostgresUnitOfWork",
    "SCHEMA_SQL",
    "SqliteUnitOfWork",
    "Storage",
    "StorageConfigError",
    "StoragePG",
    "create_storage",
]
