# -*- coding: utf-8 -*-
"""存储层公共异常定义（无内部依赖叶子模块，消除 storage <-> storage_pg 循环依赖）。"""

from __future__ import annotations


class EmbeddingConfigMismatch(RuntimeError):
    """嵌入索引配置（模型提供方或向量维度）不一致异常（fail-closed）。"""


class StorageConfigError(RuntimeError):
    """存储后端配置缺失或非法异常（fail-closed，禁止静默回退到 SQLite）。"""


class OptimisticLockConflictError(RuntimeError):
    """知识版本或审批状态并发冲突异常 (CAS Mismatch)。"""
