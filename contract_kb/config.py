# -*- coding: utf-8 -*-
"""全局配置：路径解析基于工作区相对位置，不写死绝对路径；支持环境变量覆盖与安全开关。"""

from __future__ import annotations

import os
from pathlib import Path

# 项目根目录 = 本文件上两级
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 模板源目录（默认工作区 参考/ 目录）
TEMPLATE_DIR = Path(os.environ.get("CONTRACT_KB_TEMPLATE_DIR", PROJECT_ROOT / "参考"))
# 辅助资料目录
AUX_DIR = PROJECT_ROOT

DATA_DIR = Path(
    os.environ.get("CONTRACT_KB_DATA_DIR", PROJECT_ROOT / "data" / "contract-kb")
)
REPORT_DIR = Path(
    os.environ.get("CONTRACT_KB_REPORT_DIR", PROJECT_ROOT / "reports" / "contract-kb")
)
GOLDEN_DIR = DATA_DIR / "golden"
DB_PATH = Path(os.environ.get("CONTRACT_KB_DB_PATH", DATA_DIR / "contract_kb.db"))

# 持久化后端配置：sqlite（默认本地开发/测试）| postgres（生产级，须配置 CONTRACT_KB_PG_DSN，禁止静默降级）
STORAGE_BACKEND = (
    os.environ.get("CONTRACT_KB_STORAGE_BACKEND", "sqlite").strip().lower()
)
PG_DSN = os.environ.get("CONTRACT_KB_PG_DSN", "").strip()

# 上传文件安全边界与临时文件保留策略（0 = 同步审核完成后立即清理临时文件）
MAX_DOCX_BYTES = 20 * 1024 * 1024  # 单文件大小上限 20MB
MAX_REQUEST_BODY_BYTES = int(
    os.environ.get(
        "CONTRACT_KB_MAX_REQUEST_BODY_BYTES",
        str(MAX_DOCX_BYTES + 5 * 1024 * 1024),
    )
)  # 请求正文上限（单文件 20MB + 5MB multipart 冗余开销）
MAX_ZIP_ENTRIES = 500  # 压缩包条目上限
MAX_ZIP_RATIO = 200  # 压缩比上限（压缩炸弹防护）
ALLOWED_EXT = {".docx"}
UPLOAD_RETENTION_SECONDS = int(
    os.environ.get("CONTRACT_KB_UPLOAD_RETENTION_SECONDS", "0")
)

# Embedding 配置：默认本地离线实现，云服务商需显式授权后开启
EMBEDDING_PROVIDER = os.environ.get(
    "CONTRACT_KB_EMBEDDING", "local_hash"
)  # local_hash | st:<model> | cloud:<name>
EMBEDDING_DIM = int(os.environ.get("CONTRACT_KB_EMBEDDING_DIM", "256"))

# LLM 与外部模型出境控制：未显式配置时审核引擎降级为确定性审查并标 NOT_CHECKED
LLM_PROVIDER = os.environ.get("CONTRACT_KB_LLM", "none")
ALLOW_EXTERNAL_LLM = os.environ.get(
    "CONTRACT_KB_ALLOW_EXTERNAL_LLM", "false"
).strip().lower() in ("1", "true", "yes")

# 认证与重放窗口配置
TENANT_ID = os.environ.get("CONTRACT_KB_TENANT", "default")
AUTH_MAX_SKEW_SECONDS = int(os.environ.get("CONTRACT_KB_AUTH_MAX_SKEW_SECONDS", "300"))
REQUIRE_AUTH_TIMESTAMP = os.environ.get(
    "CONTRACT_KB_REQUIRE_AUTH_TIMESTAMP", "false"
).strip().lower() in ("1", "true", "yes")

ROLE_ADMIN = "admin"
ROLE_REVIEWER = "reviewer"
ROLE_VIEWER = "viewer"


def ensure_dirs() -> None:
    for p in (DATA_DIR, REPORT_DIR, GOLDEN_DIR):
        p.mkdir(parents=True, exist_ok=True)
