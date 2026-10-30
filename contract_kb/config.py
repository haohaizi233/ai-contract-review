# -*- coding: utf-8 -*-
"""全局配置：路径解析基于工作区相对位置，不写死绝对路径。"""
from __future__ import annotations
import os
from pathlib import Path

# 项目根目录 = 本文件上两级
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 模板源目录（实际工作区：参考/ 目录存放 26 份 DOCX）
TEMPLATE_DIR = Path(os.environ.get("CONTRACT_KB_TEMPLATE_DIR", PROJECT_ROOT / "参考"))
# 辅助资料目录（审批注意事项、电子印章接入材料）
AUX_DIR = PROJECT_ROOT

DATA_DIR = Path(os.environ.get("CONTRACT_KB_DATA_DIR", PROJECT_ROOT / "data" / "contract-kb"))
REPORT_DIR = Path(os.environ.get("CONTRACT_KB_REPORT_DIR", PROJECT_ROOT / "reports" / "contract-kb"))
GOLDEN_DIR = DATA_DIR / "golden"
DB_PATH = Path(os.environ.get("CONTRACT_KB_DB_PATH", DATA_DIR / "contract_kb.db"))

MAX_DOCX_BYTES = 20 * 1024 * 1024        # 单文件大小上限
MAX_ZIP_ENTRIES = 500                    # 压缩包条目上限
MAX_ZIP_RATIO = 200                      # 压缩比上限（压缩炸弹防护）
ALLOWED_EXT = {".docx"}

# Embedding 配置：默认本地离线实现，云服务商需显式授权后开启
EMBEDDING_PROVIDER = os.environ.get("CONTRACT_KB_EMBEDDING", "local_hash")  # local_hash | cloud:<name>
EMBEDDING_DIM = 256

# LLM 配置：未显式配置时审核引擎降级为确定性/规则语义审查
LLM_PROVIDER = os.environ.get("CONTRACT_KB_LLM", "none")

TENANT_ID = os.environ.get("CONTRACT_KB_TENANT", "default")
ROLE_ADMIN = "admin"
ROLE_REVIEWER = "reviewer"
ROLE_VIEWER = "viewer"


def ensure_dirs() -> None:
    for p in (DATA_DIR, REPORT_DIR, GOLDEN_DIR):
        p.mkdir(parents=True, exist_ok=True)
