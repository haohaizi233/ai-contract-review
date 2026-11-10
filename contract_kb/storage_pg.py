# -*- coding: utf-8 -*-
"""向后兼容门面（Compatibility Facade）：PostgreSQL + pgvector 存储实现已迁移至 `contract_kb.infrastructure.persistence.postgres_storage`。"""

from __future__ import annotations

from .infrastructure.persistence.postgres_storage import *  # noqa: F401, F403
from .infrastructure.persistence.postgres_storage import (  # noqa: F401
    MIGRATION_FILE,
    MIGRATIONS_DIR,
    PgVectorStorage,
    StoragePG,
    _base_template_id,
    _norm_text,
)

__all__ = [
    "MIGRATIONS_DIR",
    "MIGRATION_FILE",
    "PgVectorStorage",
    "StoragePG",
    "_base_template_id",
    "_norm_text",
]
