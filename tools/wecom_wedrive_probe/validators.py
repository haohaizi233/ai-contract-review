# -*- coding: utf-8 -*-
"""企业微信微盘探测器 - 验证器与错误诊断模块。

提供敏感信息脱敏、企业微信错误码归类诊断、网络与出口 IP 探测、文件完整性校验等核心能力。
"""

from __future__ import annotations

import hashlib
import re
import socket
import time
from enum import Enum
from pathlib import Path
from typing import Any

import requests


class ErrorCategory(str, Enum):
    """企业微信微盘 API 错误分类。"""
    SUCCESS = "SUCCESS"
    TRUSTED_IP_ERROR = "TRUSTED_IP_ERROR"
    API_UNAUTHORIZED = "API_UNAUTHORIZED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    SPACE_NO_PERMISSION = "SPACE_NO_PERMISSION"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    PARAMETER_ERROR = "PARAMETER_ERROR"
    DOWNLOAD_DENIED = "DOWNLOAD_DENIED"
    NETWORK_ERROR = "NETWORK_ERROR"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


def mask_secret(value: str | None) -> str:
    """脱敏密钥字符串，仅展示头尾字符，防止敏感信息外泄。"""
    if not value:
        return "<EMPTY>"
    length = len(value)
    if length <= 8:
        return "*" * length
    return f"{value[:4]}{'*' * min(length - 8, 8)}{value[-4:]}"


def mask_token(token: str | None) -> str:
    """脱敏 access_token。"""
    if not token:
        return "<EMPTY>"
    if len(token) <= 12:
        return "***"
    return f"{token[:6]}...{token[-6:]}"


def mask_cookie_or_url(url_or_cookie: str | None) -> str:
    """脱敏下载签名 URL 或 Cookie 凭证。"""
    if not url_or_cookie:
        return "<EMPTY>"
    if len(url_or_cookie) <= 16:
        return "***"
    return f"{url_or_cookie[:8]}...[MASKED]...{url_or_cookie[-4:]}"


def classify_wecom_error(errcode: int, errmsg: str = "") -> dict[str, Any]:
    """统一解析企业微信官方业务错误码并给出归类、中文原因与修复建议。"""
    errmsg_lower = errmsg.lower()

    if errcode == 0:
        return {
            "category": ErrorCategory.SUCCESS.value,
            "errcode": 0,
            "errmsg": errmsg or "ok",
            "description": "接口调用成功",
            "suggestion": "无",
        }

    # 1. 60020: not allow to access from your ip
    if errcode == 60020 or "not allow to access from your ip" in errmsg_lower:
        ip_match = re.search(r"from ip:\s*([0-9a-fA-F.:]+)", errmsg)
        caller_ip = ip_match.group(1) if ip_match else "当前公网出口IP"
        return {
            "category": ErrorCategory.TRUSTED_IP_ERROR.value,
            "errcode": errcode,
            "errmsg": errmsg,
            "caller_ip": caller_ip,
            "description": f"可信 IP 限制：当前执行环境的公网出口 IP ({caller_ip}) 未包含在企业微信管理后台配置的【企业可信 IP】白名单中。",
            "suggestion": (
                f"1. 请企业微信管理员登录后台 (work.weixin.qq.com) ->【应用管理】-> 选中当前自建应用 ->【企业可信IP】；\n"
                f"2. 将当前出口 IP '{caller_ip}' 追加到可信 IP 列表中（若配置了多台服务器/网关，需依次录入）；\n"
                f"3. 保存后通常 1-2 分钟在微信服务端生效，即可重新测试；\n"
                f"4. 若企业使用中转代理服务器（如南山 Caddy/FRP 转发节点），请在 .env 中配置 WECOM_PROXY 走指定代理出口。"
            ),
        }

    # 2. 48002: api not authorized
    if errcode == 48002 or "api not allow" in errmsg_lower or "api forbidden" in errmsg_lower:
        return {
            "category": ErrorCategory.API_UNAUTHORIZED.value,
            "errcode": errcode,
            "errmsg": errmsg,
            "description": "微盘 API 未授权：当前自建应用未获得调用企业微信微盘接口的权限。",
            "suggestion": (
                "1. 请企业管理员进入后台【管理工具】/【协作】->【微盘】->【API】；\n"
                "2. 确认已开启 API 接口同步，并将该自建应用添加到【可调用应用】名单中；\n"
                "3. 为该应用勾选至少【只读】权限。"
            ),
        }

    # 3. 凭证类错误
    if errcode in (40001, 40014, 42001, 40013):
        desc = "企业微信凭证异常"
        sugg = "检查 CorpID 与 AppSecret 是否匹配且有效。"
        if errcode == 40001:
            desc = "Secret 错误或 access_token 无效"
            sugg = "请核对自建应用 Secret，并在管理后台确认应用未被停用或重置 Secret。"
        elif errcode == 40013:
            desc = "不合法的 CorpID"
            sugg = "请核对企业微信 CorpID 是否输入正确。"
        elif errcode in (40014, 42001):
            desc = "access_token 已过期或不合法"
            sugg = "系统应主动重新获取 access_token 并重试。"

        return {
            "category": ErrorCategory.INVALID_CREDENTIALS.value,
            "errcode": errcode,
            "errmsg": errmsg,
            "description": desc,
            "suggestion": sugg,
        }

    # 4. 参数错误
    if errcode in (40058, 41001, 40035):
        return {
            "category": ErrorCategory.PARAMETER_ERROR.value,
            "errcode": errcode,
            "errmsg": errmsg,
            "description": f"请求参数错误: {errmsg}",
            "suggestion": "核对请求体 JSON 字段名称与数据类型（例如 spaceid、fatherid、start、limit）。",
        }

    # 5. 空间或文件无权限
    if errcode in (2000003, 93001, 301036, 640008):
        return {
            "category": ErrorCategory.SPACE_NO_PERMISSION.value,
            "errcode": errcode,
            "errmsg": errmsg,
            "description": f"微盘空间无访问权限 (errcode={errcode}: {errmsg})：当前自建应用未获得该微盘空间（法律事务部）的访问权限。",
            "suggestion": (
                "1. 【排查项 A - 检查微盘 API 可调用授权】：\n"
                "   请企业微信管理员登录后台 (work.weixin.qq.com) ->【协作】->【微盘】->【API】；\n"
                "   确认已开启 API 接口同步，且在【可调用接口的应用】列表中已添加当前自建应用（AgentId: 1000077），并分配了‘只读’权限。\n"
                "2. 【排查项 B - 检查目标空间访问权限】：\n"
                "   当前空间‘法律事务部’由用户毛世梅手动创建（agent_id=0），受企业微信应用权限隔离保护。\n"
                "   请在企业微信管理后台【协作】->【微盘】->【空间管理】中检查该空间的权限设置；或由空间管理员在企业微信微盘中将自建应用/操作账号加入空间成员；\n"
                "3. 【替代方案 - 应用专属空间同步】：\n"
                "   若企业微信安全机制限制应用访问普通用户手动创建的既有空间，可在后台将需要同步的母本合同归集至由自建应用通过 API 创建的专属业务空间中。"
            ),
        }

    # 6. 资源不存在
    if errcode in (2000002, 2000004, 93000, 93002):
        return {
            "category": ErrorCategory.RESOURCE_NOT_FOUND.value,
            "errcode": errcode,
            "errmsg": errmsg,
            "description": "微盘空间或目标文件不存在（可能已被删除或 ID 错误）。",
            "suggestion": "检查配置中的 space_id 或 file_id 是否与微盘中的实际对象一致。",
        }

    # 7. 下载无权限
    if errcode in (2000005, 93003):
        return {
            "category": ErrorCategory.DOWNLOAD_DENIED.value,
            "errcode": errcode,
            "errmsg": errmsg,
            "description": "文件下载受限：文件设置了仅在线查看、空间水印保护或应用缺少下载权限。",
            "suggestion": "检查微盘空间的安全水印配置及成员下载权限限制。",
        }

    # 默认兜底
    return {
        "category": ErrorCategory.UNKNOWN_ERROR.value,
        "errcode": errcode,
        "errmsg": errmsg,
        "description": f"未收录的企业微信业务错误 (errcode={errcode}): {errmsg}",
        "suggestion": f"请查阅企业微信官方开发者文档或根据 hint 访问错误查询工具: https://open.work.weixin.qq.com/devtool/query?e={errcode}",
    }


def check_dns(host: str = "qyapi.weixin.qq.com", timeout: float = 5.0) -> dict[str, Any]:
    """检测企业微信接口域名的 DNS 解析是否正常。"""
    t0 = time.perf_counter()
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        addr_info = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        ips = sorted(list({info[4][0] for info in addr_info}))
        duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        return {
            "host": host,
            "resolved": True,
            "ips": ips,
            "duration_ms": duration_ms,
            "error": None,
        }
    except Exception as exc:
        duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        return {
            "host": host,
            "resolved": False,
            "ips": [],
            "duration_ms": duration_ms,
            "error": str(exc),
        }
    finally:
        socket.setdefaulttimeout(old_timeout)


def check_egress_ip(session: requests.Session | None = None, timeout: float = 5.0) -> dict[str, Any]:
    """检测当前网络连接的真实公网出口 IP。"""
    req_session = session or requests.Session()
    endpoints = [
        "http://myip.ipip.net",
        "https://api.ipify.org?format=json",
        "https://httpbin.org/ip",
    ]

    for ep in endpoints:
        try:
            resp = req_session.get(ep, timeout=timeout)
            if resp.status_code == 200:
                text = resp.text.strip()
                # 尝试解析 json
                try:
                    data = resp.json()
                    ip = data.get("ip") or data.get("origin")
                    if ip:
                        return {"success": True, "ip": ip.split(",")[0].strip(), "source": ep, "raw": text}
                except Exception:
                    pass
                # 匹配 IP
                ip_match = re.search(r"((?:\d{1,3}\.){3}\d{1,3})", text)
                if ip_match:
                    return {"success": True, "ip": ip_match.group(1), "source": ep, "raw": text}
        except Exception:
            continue

    return {"success": False, "ip": None, "source": None, "error": "无法探测公网出口 IP"}


def verify_file_integrity(
    file_path: Path,
    expected_size: int | None = None,
    expected_sha: str | None = None,
    expected_md5: str | None = None,
) -> dict[str, Any]:
    """校验下载文件的完整性、格式及哈希一致性。"""
    if not file_path.exists():
        return {
            "valid": False,
            "error": f"文件不存在: {file_path}",
        }

    actual_size = file_path.stat().st_size
    if actual_size == 0:
        return {
            "valid": False,
            "actual_size": 0,
            "error": "文件为空 (0 字节)",
        }

    md5_hash = hashlib.md5()
    sha1_hash = hashlib.sha1()
    sha256_hash = hashlib.sha256()

    with open(file_path, "rb") as f:
        while chunk := f.read(64 * 1024):
            md5_hash.update(chunk)
            sha1_hash.update(chunk)
            sha256_hash.update(chunk)

    actual_md5 = md5_hash.hexdigest().lower()
    actual_sha1 = sha1_hash.hexdigest().lower()
    actual_sha256 = sha256_hash.hexdigest().lower()

    size_match = True
    if expected_size is not None and expected_size >= 0:
        size_match = (actual_size == expected_size)

    sha_match = True
    if expected_sha:
        expected_clean = expected_sha.strip().lower()
        # 企业微信返回的 sha 字段通常为 sha1 或 sha256
        sha_match = (expected_clean in (actual_sha1, actual_sha256))

    md5_match = True
    if expected_md5:
        expected_clean = expected_md5.strip().lower()
        md5_match = (expected_clean == actual_md5)

    is_valid = size_match and sha_match and md5_match

    return {
        "valid": is_valid,
        "actual_size": actual_size,
        "expected_size": expected_size,
        "size_match": size_match,
        "md5": actual_md5,
        "expected_md5": expected_md5,
        "md5_match": md5_match,
        "sha1": actual_sha1,
        "sha256": actual_sha256,
        "expected_sha": expected_sha,
        "sha_match": sha_match,
        "extension": file_path.suffix.lower(),
    }
