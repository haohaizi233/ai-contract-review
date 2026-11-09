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
    RATE_LIMIT_ERROR = "RATE_LIMIT_ERROR"
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


def classify_wecom_error(
    errcode: int, errmsg: str = "", http_status: int = 200
) -> dict[str, Any]:
    """统一解析企业微信官方业务错误码并给出归类、中文原因与修复建议。

    严格遵循企业微信官方错误码契约:
    - https://developer.work.weixin.qq.com/document/path/90313 (全局错误码)
    - https://open.work.weixin.qq.com/devtool/query?e={errcode} (官方排障查询)
    """
    errmsg_lower = errmsg.lower()

    if errcode == 0:
        return {
            "category": ErrorCategory.SUCCESS.value,
            "http_status": http_status,
            "errcode": 0,
            "errmsg": errmsg or "ok",
            "description": "接口调用成功",
            "suggestion": "无",
            "official_doc": "https://developer.work.weixin.qq.com/document/path/90313",
        }

    # 1. 60020: not allow to access from your ip
    if errcode == 60020 or "not allow to access from your ip" in errmsg_lower:
        ip_match = re.search(r"from ip:\s*([0-9a-fA-F.:]+)", errmsg)
        caller_ip = ip_match.group(1) if ip_match else "当前公网出口IP"
        return {
            "category": ErrorCategory.TRUSTED_IP_ERROR.value,
            "http_status": http_status,
            "errcode": errcode,
            "errmsg": errmsg,
            "caller_ip": caller_ip,
            "description": f"企业可信 IP 限制 (errcode=60020): 当前执行环境出口 IP ({caller_ip}) 未包含在企业微信管理后台自建应用的【企业可信 IP】白名单中。",
            "suggestion": (
                f"1. 请企业微信管理员登录后台 (work.weixin.qq.com) ->【应用管理】-> 选中当前自建应用 ->【企业可信IP】；\n"
                f"2. 将当前出口 IP '{caller_ip}' 追加到可信 IP 列表中（若配置了多台服务器/网关，需依次录入）；\n"
                f"3. 保存后通常 1-2 分钟在微信服务端生效，即可重新测试；\n"
                f"4. 若企业使用中转代理网关节点，请在 .env 中配置 WECOM_PROXY 走指定代理出口。"
            ),
            "official_doc": "https://open.work.weixin.qq.com/devtool/query?e=60020",
        }

    # 2. 48002 / 640017: 微盘 API 未开通/未授权
    if (
        errcode in (48002, 640017)
        or "api not allow" in errmsg_lower
        or "api forbidden" in errmsg_lower
    ):
        desc = (
            "企业未开启微盘 API 功能或管理端未开通权限 (errcode=640017)"
            if errcode == 640017
            else "微盘 API 未授权给当前应用 (errcode=48002)"
        )
        return {
            "category": ErrorCategory.API_UNAUTHORIZED.value,
            "http_status": http_status,
            "errcode": errcode,
            "errmsg": errmsg,
            "description": desc,
            "suggestion": (
                "1. 请企业管理员进入后台【协作】->【微盘】->【API】；\n"
                "2. 确认已开启 API 接口同步，并将该自建应用添加到【可调用应用】名单中；\n"
                "3. 为该应用勾选至少【只读】权限。"
            ),
            "official_doc": f"https://open.work.weixin.qq.com/devtool/query?e={errcode}",
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
            "http_status": http_status,
            "errcode": errcode,
            "errmsg": errmsg,
            "description": desc,
            "suggestion": sugg,
            "official_doc": f"https://open.work.weixin.qq.com/devtool/query?e={errcode}",
        }

    # 4. 参数错误
    if errcode in (40058, 41001, 40035):
        return {
            "category": ErrorCategory.PARAMETER_ERROR.value,
            "http_status": http_status,
            "errcode": errcode,
            "errmsg": errmsg,
            "description": f"请求参数错误: {errmsg}",
            "suggestion": "核对请求体 JSON 字段名称与数据类型（例如 spaceid、fatherid、start、limit）。",
            "official_doc": f"https://open.work.weixin.qq.com/devtool/query?e={errcode}",
        }

    # 5. 空间或资源权限不足 (640004=无权限访问, 640005=成员不在空间内, 640008=没有空间权限)
    if errcode in (2000003, 93001, 301036, 640004, 640005, 640008):
        if errcode == 640004:
            desc = f"无权限访问对应微盘资源 (errcode=640004: {errmsg})"
        elif errcode == 640005:
            desc = f"成员不在空间内 (errcode=640005: {errmsg})"
        elif errcode == 640008:
            desc = f"微盘空间无访问权限 (errcode=640008: {errmsg})：调用方缺少空间访问/协同权限。"
        else:
            desc = f"微盘空间/资源权限不足 (errcode={errcode}: {errmsg})"

        return {
            "category": ErrorCategory.SPACE_NO_PERMISSION.value,
            "http_status": http_status,
            "errcode": errcode,
            "errmsg": errmsg,
            "description": desc,
            "suggestion": (
                "1. 【微盘 API 全局授权检查】：请企业微信管理员登录后台 (work.weixin.qq.com) ->【协作】->【微盘】->【API】，确认应用已加入可调用列表并开启权限；\n"
                "2. 【空间协同成员检查】：确认当前调用应用或关联管理员对目标微盘空间具有协同权限；\n"
                "3. 【替代方案】：由自建应用通过 API space_create 创建专属空间，或通过微盘客户端本地同步盘读取。"
            ),
            "official_doc": f"https://open.work.weixin.qq.com/devtool/query?e={errcode}",
        }

    # 6. 资源不存在 (640001=空间不存在, 640002=文件不存在)
    if errcode in (2000002, 2000004, 93000, 93002, 640001, 640002):
        if errcode == 640001:
            desc = f"微盘不存在当前空间 (errcode=640001: {errmsg})"
        elif errcode == 640002:
            desc = f"微盘文件不存在 (errcode=640002: {errmsg})"
        else:
            desc = f"微盘空间或目标文件不存在 (errcode={errcode}): 可能已被删除或 ID 错误。"

        return {
            "category": ErrorCategory.RESOURCE_NOT_FOUND.value,
            "http_status": http_status,
            "errcode": errcode,
            "errmsg": errmsg,
            "description": desc,
            "suggestion": "检查配置中的 space_id 或 file_id 是否与微盘中的实际对象一致。",
            "official_doc": f"https://open.work.weixin.qq.com/devtool/query?e={errcode}",
        }

    # 7. 接口调用频率超限 (45009, 45029)
    if errcode in (45009, 45029):
        return {
            "category": ErrorCategory.RATE_LIMIT_ERROR.value,
            "http_status": http_status,
            "errcode": errcode,
            "errmsg": errmsg,
            "description": f"接口调用频率超限 (errcode={errcode}): {errmsg}",
            "suggestion": "请降低接口并发或增加请求延迟，遵守企业微信官方频控限制。",
            "official_doc": f"https://open.work.weixin.qq.com/devtool/query?e={errcode}",
        }

    # 8. 下载受限
    if errcode in (2000005, 93003):
        return {
            "category": ErrorCategory.DOWNLOAD_DENIED.value,
            "http_status": http_status,
            "errcode": errcode,
            "errmsg": errmsg,
            "description": "文件下载受限：文件设置了仅在线查看、空间水印保护或应用缺少下载权限。",
            "suggestion": "检查微盘空间的安全水印配置及成员下载权限限制。",
            "official_doc": f"https://open.work.weixin.qq.com/devtool/query?e={errcode}",
        }

    # 默认兜底：保留原始信息，不强行归类
    return {
        "category": ErrorCategory.UNKNOWN_ERROR.value,
        "http_status": http_status,
        "errcode": errcode,
        "errmsg": errmsg,
        "description": f"未收录的企业微信业务错误 (errcode={errcode}): {errmsg}",
        "suggestion": f"请查阅企业微信官方开发者文档或根据 hint 访问错误查询工具: https://open.work.weixin.qq.com/devtool/query?e={errcode}",
        "official_doc": f"https://open.work.weixin.qq.com/devtool/query?e={errcode}",
    }


def check_dns(
    host: str = "qyapi.weixin.qq.com", timeout: float = 5.0
) -> dict[str, Any]:
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


def check_egress_ip(
    session: requests.Session | None = None, timeout: float = 5.0
) -> dict[str, Any]:
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
                        return {
                            "success": True,
                            "ip": ip.split(",")[0].strip(),
                            "source": ep,
                            "raw": text,
                        }
                except Exception:
                    pass
                # 匹配 IP
                ip_match = re.search(r"((?:\d{1,3}\.){3}\d{1,3})", text)
                if ip_match:
                    return {
                        "success": True,
                        "ip": ip_match.group(1),
                        "source": ep,
                        "raw": text,
                    }
        except Exception:
            continue

    return {
        "success": False,
        "ip": None,
        "source": None,
        "error": "无法探测公网出口 IP",
    }


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
        size_match = actual_size == expected_size

    sha_match = True
    if expected_sha:
        expected_clean = expected_sha.strip().lower()
        # 企业微信返回的 sha 字段通常为 sha1 或 sha256
        sha_match = expected_clean in (actual_sha1, actual_sha256)

    md5_match = True
    if expected_md5:
        expected_clean = expected_md5.strip().lower()
        md5_match = expected_clean == actual_md5

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
