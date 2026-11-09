# -*- coding: utf-8 -*-
"""企业微信微盘 API 客户端封装。

遵循官方文档契约，提供 access_token 管理、微盘空间信息、目录列表、文件信息及安全流式下载功能。
实现严格的只读语义、TLS 证书校验、超时重试以及敏感凭据脱敏机制。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


logger = logging.getLogger("WeDriveClient")


class WeComAPIError(Exception):
    """企业微信 API 业务或网络异常。"""

    def __init__(
        self, errcode: int, errmsg: str, http_status: int = 200, raw: dict | None = None
    ):
        super().__init__(f"[{errcode}] {errmsg} (HTTP {http_status})")
        self.errcode = errcode
        self.errmsg = errmsg
        self.http_status = http_status
        self.raw = raw or {}


class WeComWeDriveClient:
    """企业微信微盘官方 API 只读客户端。"""

    BASE_URL = "https://qyapi.weixin.qq.com"

    def __init__(
        self,
        corp_id: str,
        app_secret: str,
        timeout: int = 20,
        proxy: str | None = None,
        disable_system_proxy: bool = False,
    ):
        if not corp_id:
            raise ValueError("corp_id 不能为空")
        if not app_secret:
            raise ValueError("app_secret 不能为空")

        self.corp_id = corp_id.strip()
        self.app_secret = app_secret.strip()
        self.timeout = timeout
        self.proxy = proxy.strip() if proxy else None
        self.disable_system_proxy = disable_system_proxy

        self._token: str | None = None
        self._token_expires_at: float = 0.0

        # 初始化安全 HTTP Session
        self.session = requests.Session()
        if self.disable_system_proxy:
            self.session.trust_env = False
        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}

        # 配置有限重试策略 (仅对网络中断或 5xx 状态重试，不对 4xx 或企业微信业务错误重试)
        retries = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update(
            {
                "User-Agent": "WeComWeDriveProbe/1.0 (Python3.11+)",
                "Accept": "application/json",
            }
        )

    def _get_api_url(self, path: str) -> str:
        return f"{self.BASE_URL}{path}"

    def get_token(self, force_refresh: bool = False) -> tuple[dict[str, Any], float]:
        """获取企业微信应用 access_token。

        官方接口: GET /cgi-bin/gettoken?corpid=ID&corpsecret=SECRET
        """
        now = time.time()
        if not force_refresh and self._token and now < self._token_expires_at:
            return {
                "errcode": 0,
                "errmsg": "ok",
                "access_token": self._token,
                "expires_in": int(self._token_expires_at - now),
                "from_cache": True,
            }, 0.0

        url = self._get_api_url("/cgi-bin/gettoken")
        params = {
            "corpid": self.corp_id,
            "corpsecret": self.app_secret,
        }

        t0 = time.perf_counter()
        # 强制 TLS 验证
        resp = self.session.get(url, params=params, timeout=self.timeout, verify=True)
        duration_ms = round((time.perf_counter() - t0) * 1000, 2)

        try:
            data = resp.json()
        except Exception:
            data = {
                "errcode": -1,
                "errmsg": f"接口返回非JSON响应 (HTTP {resp.status_code}): {resp.text[:100]}",
            }
        data["_http_status"] = resp.status_code

        errcode = data.get("errcode", 0)
        errmsg = data.get("errmsg", "ok")

        if errcode == 0 and "access_token" in data:
            self._token = data["access_token"]
            expires_in = data.get("expires_in", 7200)
            # 提前 5 分钟刷新
            self._token_expires_at = now + max(expires_in - 300, 60)
            logger.info(
                f"成功获取 access_token (有效秒数: {expires_in}, 耗时: {duration_ms}ms)"
            )
        else:
            logger.warning(f"获取 access_token 失败: [{errcode}] {errmsg}")

        return data, duration_ms

    def ensure_token(self) -> str:
        """确保具有可用的 access_token，若无则获取。"""
        if not self._token or time.time() >= self._token_expires_at:
            data, _ = self.get_token(force_refresh=True)
            if data.get("errcode") != 0 or not self._token:
                raise WeComAPIError(
                    errcode=data.get("errcode", -1),
                    errmsg=data.get("errmsg", "无法获取 access_token"),
                    raw=data,
                )
        return self._token

    def _post_api(
        self, path: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], float]:
        """通用的微盘 POST API 请求封装，支持安全解析与 HTTP 状态保留。"""
        token = self.ensure_token()
        url = self._get_api_url(path)

        t0 = time.perf_counter()
        resp = self.session.post(
            url,
            params={"access_token": token},
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
            verify=True,
        )
        duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        try:
            data = resp.json()
        except Exception:
            data = {
                "errcode": -1,
                "errmsg": f"接口返回非JSON响应 (HTTP {resp.status_code}): {resp.text[:100]}",
            }
        data["_http_status"] = resp.status_code
        return data, duration_ms

    def get_space_info(self, spaceid: str) -> tuple[dict[str, Any], float]:
        """获取指定微盘共享空间信息与权限设置。

        官方接口: POST /cgi-bin/wedrive/space_info?access_token=TOKEN
        Body: {"spaceid": "..."}
        """
        return self._post_api("/cgi-bin/wedrive/space_info", {"spaceid": spaceid})

    def list_files(
        self,
        spaceid: str,
        fatherid: str | None = None,
        sort_type: int = 1,
        start: int = 0,
        limit: int = 100,
    ) -> tuple[dict[str, Any], float]:
        """获取指定空间及目录下文件与子目录列表（支持分页）。

        官方接口: POST /cgi-bin/wedrive/file_list?access_token=TOKEN
        Body: {"spaceid": "...", "fatherid": "...", "sort_type": 1, "start": 0, "limit": 100}
        说明: 若读取根目录，fatherid 填写 spaceid。
        """
        payload = {
            "spaceid": spaceid,
            "fatherid": fatherid or spaceid,
            "sort_type": sort_type,
            "start": start,
            "limit": min(limit, 1000),
        }
        return self._post_api("/cgi-bin/wedrive/file_list", payload)

    def get_file_info(self, fileid: str) -> tuple[dict[str, Any], float]:
        """获取微盘文件详细元数据。

        官方接口: POST /cgi-bin/wedrive/file_info?access_token=TOKEN
        Body: {"fileid": "..."}
        """
        return self._post_api("/cgi-bin/wedrive/file_info", {"fileid": fileid})

    def get_file_download_ticket(self, fileid: str) -> tuple[dict[str, Any], float]:
        """获取微盘文件下载临时 URL 与 Cookie 鉴权凭证。

        官方接口: POST /cgi-bin/wedrive/file_download?access_token=TOKEN
        Body: {"fileid": "..."}
        返回结构: {"errcode": 0, "errmsg": "ok", "download_url": "...", "cookie_name": "...", "cookie_value": "..."}
        """
        return self._post_api("/cgi-bin/wedrive/file_download", {"fileid": fileid})

    def download_file_stream(
        self,
        download_url: str,
        cookie_name: str,
        cookie_value: str,
        target_path: Path,
        max_bytes: int = 10 * 1024 * 1024,
    ) -> tuple[dict[str, Any], float]:
        """使用下载凭证安全流式下载文件至本地临时文件，严格执行大小超限熔断保护。"""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        headers = {
            "Cookie": f"{cookie_name}={cookie_value}",
            "User-Agent": "WeComWeDriveProbe/1.0",
        }

        t0 = time.perf_counter()
        # 强制开启 TLS 校验，使用 stream 模式防内存暴涨
        resp = self.session.get(
            download_url,
            headers=headers,
            stream=True,
            timeout=self.timeout,
            verify=True,
        )
        if resp.status_code != 200:
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            return {
                "success": False,
                "status_code": resp.status_code,
                "error": f"下载响应 HTTP {resp.status_code}",
            }, duration_ms

        downloaded_bytes = 0
        tmp_target = target_path.with_suffix(f"{target_path.suffix}.tmp")

        try:
            with open(tmp_target, "wb") as f:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    downloaded_bytes += len(chunk)
                    if downloaded_bytes > max_bytes:
                        raise ValueError(
                            f"下载文件大小超过最大允许限制 ({max_bytes} 字节)"
                        )
                    f.write(chunk)

            # 下载完成原子重命名
            if tmp_target.exists():
                if target_path.exists():
                    target_path.unlink()
                tmp_target.rename(target_path)

            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            return {
                "success": True,
                "status_code": 200,
                "downloaded_bytes": downloaded_bytes,
                "saved_path": str(target_path),
            }, duration_ms

        except Exception as exc:
            if tmp_target.exists():
                tmp_target.unlink()
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            return {
                "success": False,
                "status_code": resp.status_code,
                "error": str(exc),
            }, duration_ms
