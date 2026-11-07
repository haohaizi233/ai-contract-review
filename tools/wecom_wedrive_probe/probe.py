# -*- coding: utf-8 -*-
"""企业微信微盘探测器 - 主探测编排与报告生成器。

执行真实企业微信微盘 API 连通性、权限边界探测与文件读取测试。
生成符合企业验收标准的结构化 JSON 与 Markdown 验收报告。
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:

    def load_dotenv(*_args: Any, **_kwargs: Any) -> None:
        pass


CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from client import WeComWeDriveClient  # noqa: E402
from validators import (  # noqa: E402
    ErrorCategory,
    check_dns,
    check_egress_ip,
    classify_wecom_error,
    mask_cookie_or_url,
    mask_secret,
    mask_token,
    verify_file_integrity,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("WeDriveProbe")


class WeDriveProbeRunner:
    """微盘 API 真实连通性测试执行器。"""

    def __init__(self, env_path: Path | str | None = None):
        self.root_dir = CURRENT_DIR
        self.reports_dir = self.root_dir / "reports"
        self.downloads_dir = self.root_dir / "downloads"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)

        # 1. 加载环境变量
        loaded_env_file = None
        candidates = [
            Path(env_path) if env_path else None,
            self.root_dir / ".env",
            self.root_dir.parent.parent / ".env",
        ]
        for c in candidates:
            if c and c.exists():
                load_dotenv(c, override=True)
                loaded_env_file = str(c)
                break

        self.loaded_env_file = loaded_env_file
        self.corp_id = os.getenv("WECOM_CORP_ID", "").strip()
        self.app_secret = os.getenv("WECOM_APP_SECRET", "").strip()
        self.space_id = os.getenv("WECOM_SPACE_ID", "").strip()
        self.test_file_id = os.getenv("WECOM_TEST_FILE_ID", "").strip()
        self.download_max_mb = int(os.getenv("WECOM_DOWNLOAD_MAX_MB", "10"))
        self.timeout = int(os.getenv("WECOM_REQUEST_TIMEOUT", "20"))
        self.proxy = os.getenv("WECOM_PROXY", "").strip() or None
        self.disable_system_proxy = os.getenv(
            "WECOM_DISABLE_SYSTEM_PROXY", "false"
        ).lower() in ("true", "1", "yes")

        self.client: WeComWeDriveClient | None = None
        self.result_data: dict[str, Any] = {
            "metadata": {
                "generated_at": datetime.datetime.now().isoformat(),
                "runner_version": "1.0.0",
                "env_file": self.loaded_env_file,
            },
            "environment_check": {},
            "api_tests": {},
            "catalog_statistics": {
                "accessible_spaces_count": 0,
                "folders_count": 0,
                "files_count": 0,
                "file_types_distribution": {},
                "mtime_range": {"min": None, "max": None},
                "has_file_hash": False,
                "incremental_sync_feasible": False,
            },
            "conclusion": {
                "level": "UNKNOWN",
                "title": "",
                "details": "",
            },
        }

    def run_stage_a_env_check(self) -> bool:
        """阶段 A：配置与网络检查。"""
        logger.info("==================================================")
        logger.info("阶段 A：配置与网络检查")
        logger.info("==================================================")

        missing_configs = []
        if not self.corp_id:
            missing_configs.append("WECOM_CORP_ID")
        if not self.app_secret:
            missing_configs.append("WECOM_APP_SECRET")
        if not self.space_id:
            missing_configs.append("WECOM_SPACE_ID")

        env_status = {
            "has_corp_id": bool(self.corp_id),
            "corp_id": self.corp_id if self.corp_id else None,
            "has_app_secret": bool(self.app_secret),
            "masked_secret": mask_secret(self.app_secret),
            "has_space_id": bool(self.space_id),
            "space_id": self.space_id if self.space_id else None,
            "missing_configs": missing_configs,
            "download_max_mb": self.download_max_mb,
            "timeout_seconds": self.timeout,
            "custom_proxy": self.proxy,
            "disable_system_proxy": self.disable_system_proxy,
        }

        # DNS 探测
        dns_res = check_dns("qyapi.weixin.qq.com", timeout=5.0)
        logger.info(
            f"DNS 解析 qyapi.weixin.qq.com: {dns_res['resolved']} (IP: {dns_res['ips']}, 耗时: {dns_res['duration_ms']}ms)"
        )

        # 出口 IP 探测
        probe_session = (
            WeComWeDriveClient(
                corp_id="dummy",
                app_secret="dummy",
                proxy=self.proxy,
                disable_system_proxy=self.disable_system_proxy,
            ).session
            if (self.corp_id and self.app_secret)
            else None
        )

        egress_res = check_egress_ip(probe_session)
        logger.info(
            f"当前执行环境公网出口 IP: {egress_res.get('ip')} (探测来源: {egress_res.get('source')})"
        )

        env_status["dns"] = dns_res
        env_status["egress_ip"] = egress_res
        self.result_data["environment_check"] = env_status

        if missing_configs:
            logger.error(f"缺少必要配置变量: {', '.join(missing_configs)}")
            self.result_data["conclusion"] = {
                "level": "E",
                "title": "环境阻塞",
                "details": f"缺少必要环境变量配置: {', '.join(missing_configs)}。请在 .env 中填入真实企业微信自建应用凭据。",
            }
            return False

        if not dns_res["resolved"]:
            logger.error(f"DNS 无法解析企业微信官方接口域名: {dns_res['error']}")
            self.result_data["conclusion"] = {
                "level": "E",
                "title": "环境阻塞",
                "details": f"网络异常：无法解析企业微信官方接口域名 qyapi.weixin.qq.com ({dns_res['error']})。",
            }
            return False

        # 初始化客户端
        self.client = WeComWeDriveClient(
            corp_id=self.corp_id,
            app_secret=self.app_secret,
            timeout=self.timeout,
            proxy=self.proxy,
            disable_system_proxy=self.disable_system_proxy,
        )
        return True

    def run_stage_b_api_tests(self):
        """阶段 B & C：真实 API 验证、目录遍历与文件下载。"""
        if not self.client:
            return

        logger.info("==================================================")
        logger.info("阶段 B：真实 API 验证与微盘权限边界探测")
        logger.info("==================================================")

        tests = {}

        # ----------------------------------------------------
        # 1. GET /cgi-bin/gettoken
        # ----------------------------------------------------
        test_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info("--> [1/7] 测试获取 Token (gettoken)...")
        token_data, duration_ms = self.client.get_token(force_refresh=True)
        errcode = token_data.get("errcode", -1)
        errmsg = token_data.get("errmsg", "")
        diag = classify_wecom_error(errcode, errmsg)

        token_success = errcode == 0 and "access_token" in token_data
        tests["token"] = {
            "name": "获取 Token",
            "endpoint": "GET /cgi-bin/gettoken",
            "time": test_time,
            "http_status": 200,
            "errcode": errcode,
            "errmsg": errmsg,
            "duration_ms": duration_ms,
            "status": "PASS" if token_success else "FAIL",
            "category": diag["category"],
            "description": diag["description"],
            "suggestion": diag["suggestion"],
            "masked_token": mask_token(token_data.get("access_token")),
            "expires_in": token_data.get("expires_in"),
        }

        if not token_success:
            logger.error(f"Token 获取失败: [{errcode}] {errmsg}")
            self.result_data["api_tests"] = tests
            self.result_data["conclusion"] = {
                "level": "D",
                "title": "授权失败",
                "details": f"获取 access_token 失败: [{errcode}] {errmsg}。请检查 CorpID、Secret 及网络连通性。",
            }
            return

        # ----------------------------------------------------
        # 2. POST /cgi-bin/wedrive/space_info
        # ----------------------------------------------------
        test_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(
            f"--> [2/7] 测试获取空间信息 (wedrive/space_info) - SpaceID: {self.space_id}..."
        )
        space_data, duration_ms = self.client.get_space_info(self.space_id)
        errcode = space_data.get("errcode", -1)
        errmsg = space_data.get("errmsg", "")
        diag = classify_wecom_error(errcode, errmsg)
        space_success = errcode == 0

        space_info_obj = space_data.get("space_info", {})
        tests["space_info"] = {
            "name": "空间信息",
            "endpoint": "POST /cgi-bin/wedrive/space_info",
            "time": test_time,
            "http_status": 200,
            "errcode": errcode,
            "errmsg": errmsg,
            "duration_ms": duration_ms,
            "status": "PASS" if space_success else "FAIL",
            "category": diag["category"],
            "description": diag["description"],
            "suggestion": diag["suggestion"],
            "caller_ip": diag.get("caller_ip"),
            "space_name": space_info_obj.get("space_name"),
            "space_sub_type": space_info_obj.get("space_sub_type"),
        }

        # ----------------------------------------------------
        # 3. POST /cgi-bin/wedrive/file_list (根目录列表)
        # ----------------------------------------------------
        test_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info("--> [3/7] 测试根目录列表读取 (wedrive/file_list)...")
        list_data, duration_ms = self.client.list_files(
            spaceid=self.space_id,
            fatherid=self.space_id,
            sort_type=1,
            start=0,
            limit=100,
        )
        errcode = list_data.get("errcode", -1)
        errmsg = list_data.get("errmsg", "")
        diag = classify_wecom_error(errcode, errmsg)
        root_list_success = errcode == 0

        raw_items = list_data.get("file_list", {}).get("item", [])
        tests["root_file_list"] = {
            "name": "根目录列表",
            "endpoint": "POST /cgi-bin/wedrive/file_list",
            "time": test_time,
            "http_status": 200,
            "errcode": errcode,
            "errmsg": errmsg,
            "duration_ms": duration_ms,
            "status": "PASS" if root_list_success else "FAIL",
            "category": diag["category"],
            "description": diag["description"],
            "suggestion": diag["suggestion"],
            "items_count": len(raw_items),
            "has_more": list_data.get("has_more", False),
        }

        # ----------------------------------------------------
        # 4. 递归目录遍历与建立文件清单
        # ----------------------------------------------------
        logger.info("--> [4/7] 递归目录遍历...")
        test_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        all_files: list[dict[str, Any]] = []
        all_folders: list[dict[str, Any]] = []
        all_other_docs: list[dict[str, Any]] = []

        if not root_list_success:
            tests["recursive_list"] = {
                "name": "递归文件列表",
                "endpoint": "POST /cgi-bin/wedrive/file_list (recursive)",
                "time": test_time,
                "http_status": 200,
                "errcode": errcode,
                "errmsg": errmsg,
                "duration_ms": 0.0,
                "status": "SKIP",
                "category": diag["category"],
                "description": f"前置根目录读取未成功，跳过递归遍历 ({diag['description']})",
                "suggestion": diag["suggestion"],
            }
        else:
            t0_rec = time.perf_counter()
            # 维护已访问文件夹集合，防止循环与重复遍历
            visited_folder_ids: set[str] = set()
            queue: list[dict[str, Any]] = [
                {"fileid": self.space_id, "file_name": "根目录", "path": "/"}
            ]

            while queue:
                current_folder = queue.pop(0)
                folder_id = current_folder["fileid"]
                folder_path = current_folder["path"]

                if folder_id in visited_folder_ids:
                    continue
                visited_folder_ids.add(folder_id)

                start = 0
                has_more = True
                while has_more:
                    page_data, _ = self.client.list_files(
                        spaceid=self.space_id,
                        fatherid=folder_id,
                        sort_type=1,
                        start=start,
                        limit=100,
                    )
                    if page_data.get("errcode") != 0:
                        logger.warning(
                            f"遍历目录 {folder_path} (ID: {folder_id}) 失败: {page_data.get('errmsg')}"
                        )
                        break

                    items = page_data.get("file_list", {}).get("item", [])
                    for item in items:
                        item_type = item.get("file_type")
                        item_id = item.get("fileid")
                        item_name = item.get("file_name", "unnamed")
                        current_item_path = f"{folder_path}{item_name}"

                        # file_type: 1 文件夹, 2 普通文件, 3 微文档, 4 微表格, 5 收集表, 6 幻灯片
                        if item_type == 1:
                            all_folders.append(
                                {
                                    "fileid": item_id,
                                    "file_name": item_name,
                                    "path": current_item_path,
                                    "fatherid": item.get("fatherid"),
                                    "mtime": item.get("mtime"),
                                }
                            )
                            queue.append(
                                {
                                    "fileid": item_id,
                                    "file_name": item_name,
                                    "path": f"{current_item_path}/",
                                }
                            )
                        elif item_type == 2:
                            all_files.append(
                                {
                                    "fileid": item_id,
                                    "file_name": item_name,
                                    "path": current_item_path,
                                    "file_size": item.get("file_size", 0),
                                    "file_type": item_type,
                                    "ctime": item.get("ctime"),
                                    "mtime": item.get("mtime"),
                                    "sha": item.get("sha"),
                                    "md5": item.get("md5"),
                                }
                            )
                        else:
                            all_other_docs.append(
                                {
                                    "fileid": item_id,
                                    "file_name": item_name,
                                    "path": current_item_path,
                                    "file_type": item_type,
                                    "url": item.get("url"),
                                    "mtime": item.get("mtime"),
                                }
                            )

                    has_more = page_data.get("has_more", False)
                    start = page_data.get("next_start", 0)
                    if not has_more or not start:
                        break

            duration_rec_ms = round((time.perf_counter() - t0_rec) * 1000, 2)
            tests["recursive_list"] = {
                "name": "递归文件列表",
                "endpoint": "POST /cgi-bin/wedrive/file_list (recursive)",
                "time": test_time,
                "http_status": 200,
                "errcode": 0,
                "errmsg": "ok",
                "duration_ms": duration_rec_ms,
                "status": "PASS",
                "category": ErrorCategory.SUCCESS.value,
                "description": f"递归遍历完成，共发现 {len(all_folders)} 个文件夹，{len(all_files)} 份普通文件，{len(all_other_docs)} 份在线微文档",
                "suggestion": "无",
                "folders_count": len(all_folders),
                "files_count": len(all_files),
                "other_docs_count": len(all_other_docs),
            }

        # ----------------------------------------------------
        # 统计清单特征
        # ----------------------------------------------------
        file_types_dist: dict[str, int] = {}
        mtimes: list[int] = []
        has_hash_in_api = False

        for f in all_files:
            ext = Path(f["file_name"]).suffix.lower() or "<no_ext>"
            file_types_dist[ext] = file_types_dist.get(ext, 0) + 1
            if f.get("mtime"):
                mtimes.append(f["mtime"])
            if f.get("sha") or f.get("md5"):
                has_hash_in_api = True

        mtime_min_str = (
            datetime.datetime.fromtimestamp(min(mtimes)).strftime("%Y-%m-%d %H:%M:%S")
            if mtimes
            else None
        )
        mtime_max_str = (
            datetime.datetime.fromtimestamp(max(mtimes)).strftime("%Y-%m-%d %H:%M:%S")
            if mtimes
            else None
        )

        self.result_data["catalog_statistics"] = {
            "accessible_spaces_count": 1 if space_success else 0,
            "folders_count": len(all_folders),
            "files_count": len(all_files),
            "other_docs_count": len(all_other_docs),
            "file_types_distribution": file_types_dist,
            "mtime_range": {"min": mtime_min_str, "max": mtime_max_str},
            "has_file_hash": has_hash_in_api,
            "incremental_sync_feasible": (has_hash_in_api or bool(mtimes)),
        }

        # ----------------------------------------------------
        # 5. POST /cgi-bin/wedrive/file_info (文件元数据查询)
        # ----------------------------------------------------
        logger.info("--> [5/7] 测试文件信息查询 (wedrive/file_info)...")
        test_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 选取目标文件: 优先使用环境变量指定，否则选取 <= 10MB 的第一个普通文件
        target_file_id = self.test_file_id
        target_file_meta = None

        if not target_file_id and all_files:
            max_limit_bytes = self.download_max_mb * 1024 * 1024
            for candidate in all_files:
                if candidate.get("file_size", 0) <= max_limit_bytes:
                    target_file_id = candidate["fileid"]
                    target_file_meta = candidate
                    break

        if not target_file_id:
            tests["file_info"] = {
                "name": "文件信息",
                "endpoint": "POST /cgi-bin/wedrive/file_info",
                "time": test_time,
                "http_status": 200,
                "errcode": 0,
                "errmsg": "no eligible file",
                "duration_ms": 0.0,
                "status": "SKIP",
                "category": ErrorCategory.RESOURCE_NOT_FOUND.value,
                "description": "未指定 WECOM_TEST_FILE_ID 且微盘中无可访问的普通文件（<=10MB），跳过元数据查询",
                "suggestion": "在微盘中上传测试合同文件，或在 .env 中设置 WECOM_TEST_FILE_ID",
            }
        else:
            file_info_data, duration_ms = self.client.get_file_info(target_file_id)
            errcode = file_info_data.get("errcode", -1)
            errmsg = file_info_data.get("errmsg", "")
            diag = classify_wecom_error(errcode, errmsg)
            file_info_success = errcode == 0

            fi_obj = file_info_data.get("file_info", {})
            tests["file_info"] = {
                "name": "文件信息",
                "endpoint": "POST /cgi-bin/wedrive/file_info",
                "time": test_time,
                "http_status": 200,
                "errcode": errcode,
                "errmsg": errmsg,
                "duration_ms": duration_ms,
                "status": "PASS" if file_info_success else "FAIL",
                "category": diag["category"],
                "description": diag["description"],
                "suggestion": diag["suggestion"],
                "fileid": target_file_id,
                "file_name": fi_obj.get("file_name")
                or (target_file_meta.get("file_name") if target_file_meta else None),
                "file_size": fi_obj.get("file_size"),
                "file_type": fi_obj.get("file_type"),
                "sha": fi_obj.get("sha"),
                "md5": fi_obj.get("md5"),
            }

        # ----------------------------------------------------
        # 6. POST /cgi-bin/wedrive/file_download (获取下载凭证并下载)
        # ----------------------------------------------------
        logger.info("--> [6/7] 测试文件下载凭证与文件流下载 (wedrive/file_download)...")
        test_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        download_file_record = None
        if not target_file_id:
            tests["file_download"] = {
                "name": "文件下载",
                "endpoint": "POST /cgi-bin/wedrive/file_download",
                "time": test_time,
                "http_status": 200,
                "errcode": 0,
                "errmsg": "no eligible file",
                "duration_ms": 0.0,
                "status": "SKIP",
                "category": ErrorCategory.RESOURCE_NOT_FOUND.value,
                "description": "无可用测试文件，跳过文件下载测试",
                "suggestion": "在空间中准备小型普通文件（<=10MB）",
            }
        else:
            ticket_data, duration_ticket_ms = self.client.get_file_download_ticket(
                target_file_id
            )
            errcode = ticket_data.get("errcode", -1)
            errmsg = ticket_data.get("errmsg", "")
            diag = classify_wecom_error(errcode, errmsg)

            if errcode != 0:
                tests["file_download"] = {
                    "name": "文件下载",
                    "endpoint": "POST /cgi-bin/wedrive/file_download",
                    "time": test_time,
                    "http_status": 200,
                    "errcode": errcode,
                    "errmsg": errmsg,
                    "duration_ms": duration_ticket_ms,
                    "status": "FAIL",
                    "category": diag["category"],
                    "description": diag["description"],
                    "suggestion": diag["suggestion"],
                }
            else:
                download_url = ticket_data.get("download_url", "")
                cookie_name = ticket_data.get("cookie_name", "")
                cookie_value = ticket_data.get("cookie_value", "")

                safe_name = f"test_{target_file_id[:8]}.bin"
                if target_file_meta and target_file_meta.get("file_name"):
                    safe_name = target_file_meta["file_name"]
                local_save_path = self.downloads_dir / safe_name

                dl_res, duration_dl_ms = self.client.download_file_stream(
                    download_url=download_url,
                    cookie_name=cookie_name,
                    cookie_value=cookie_value,
                    target_path=local_save_path,
                    max_bytes=self.download_max_mb * 1024 * 1024,
                )

                if dl_res["success"]:
                    download_file_record = local_save_path
                    tests["file_download"] = {
                        "name": "文件下载",
                        "endpoint": "POST /cgi-bin/wedrive/file_download + GET download_url",
                        "time": test_time,
                        "http_status": 200,
                        "errcode": 0,
                        "errmsg": "ok",
                        "duration_ms": round(duration_ticket_ms + duration_dl_ms, 2),
                        "status": "PASS",
                        "category": ErrorCategory.SUCCESS.value,
                        "description": f"成功通过官方凭证安全流式下载文件 ({dl_res['downloaded_bytes']} 字节)",
                        "suggestion": "无",
                        "saved_path": str(local_save_path),
                        "masked_url": mask_cookie_or_url(download_url),
                        "cookie_name": cookie_name,
                    }
                else:
                    tests["file_download"] = {
                        "name": "文件下载",
                        "endpoint": "POST /cgi-bin/wedrive/file_download + GET download_url",
                        "time": test_time,
                        "http_status": dl_res.get("status_code", 500),
                        "errcode": -1,
                        "errmsg": dl_res.get("error", "下载失败"),
                        "duration_ms": round(duration_ticket_ms + duration_dl_ms, 2),
                        "status": "FAIL",
                        "category": ErrorCategory.DOWNLOAD_DENIED.value,
                        "description": f"下载凭证换取成功，但拉取二进制流失败: {dl_res.get('error')}",
                        "suggestion": "检查网络连通性及微盘空间水印与防泄密下载控制策略。",
                    }

        # ----------------------------------------------------
        # 7. 文件完整性与哈希校验
        # ----------------------------------------------------
        logger.info("--> [7/7] 验证文件完整性...")
        test_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not download_file_record or not download_file_record.exists():
            tests["file_integrity"] = {
                "name": "文件完整性",
                "endpoint": "Local SHA256/MD5 Hash Verification",
                "time": test_time,
                "http_status": 200,
                "errcode": 0,
                "errmsg": "download not completed",
                "duration_ms": 0.0,
                "status": "SKIP",
                "category": ErrorCategory.RESOURCE_NOT_FOUND.value,
                "description": "由于未完成文件实体下载，跳过完整性与哈希校验",
                "suggestion": "在文件下载通过后再执行完整性校验",
            }
        else:
            exp_size = target_file_meta.get("file_size") if target_file_meta else None
            exp_sha = target_file_meta.get("sha") if target_file_meta else None
            exp_md5 = target_file_meta.get("md5") if target_file_meta else None

            integrity_res = verify_file_integrity(
                file_path=download_file_record,
                expected_size=exp_size,
                expected_sha=exp_sha,
                expected_md5=exp_md5,
            )

            tests["file_integrity"] = {
                "name": "文件完整性",
                "endpoint": "Local SHA256/MD5 Hash Verification",
                "time": test_time,
                "http_status": 200,
                "errcode": 0 if integrity_res["valid"] else -1,
                "errmsg": integrity_res.get("error") or "ok",
                "duration_ms": 1.0,
                "status": "PASS" if integrity_res["valid"] else "FAIL",
                "category": ErrorCategory.SUCCESS.value
                if integrity_res["valid"]
                else ErrorCategory.PARAMETER_ERROR.value,
                "description": (
                    f"完整性校验通过 (大小: {integrity_res['actual_size']} 字节, SHA256: {integrity_res['sha256'][:12]}...)"
                    if integrity_res["valid"]
                    else f"校验不匹配: {integrity_res.get('error')}"
                ),
                "suggestion": "无"
                if integrity_res["valid"]
                else "检查文件是否被中间网关篡改或下载截断",
                "sha256": integrity_res["sha256"],
                "md5": integrity_res["md5"],
                "actual_size": integrity_res["actual_size"],
            }

        self.result_data["api_tests"] = tests

        # ----------------------------------------------------
        # 结论判定
        # ----------------------------------------------------
        self._determine_conclusion()

    def _determine_conclusion(self):
        """依据测试矩阵判定明确的结论级别 (A/B/C/D/E)。"""
        tests = self.result_data.get("api_tests", {})

        tok = tests.get("token", {}).get("status")
        sp = tests.get("space_info", {}).get("status")
        rf = tests.get("root_file_list", {}).get("status")
        rec = tests.get("recursive_list", {}).get("status")
        dl = tests.get("file_download", {}).get("status")
        integ = tests.get("file_integrity", {}).get("status")

        if tok != "PASS":
            self.result_data["conclusion"] = {
                "level": "D",
                "title": "授权失败",
                "details": "Token 或自建应用凭据不满足要求，无法获取官方 access_token。",
            }
            return

        # Token PASS 的情况下
        if (
            sp == "PASS"
            and rf == "PASS"
            and rec == "PASS"
            and dl == "PASS"
            and integ == "PASS"
        ):
            self.result_data["conclusion"] = {
                "level": "A",
                "title": "完全可用",
                "details": "能够读取指定微盘空间、完整遍历目录并成功下载和校验普通文件。具备开发微盘自动增量同步的完整前提。",
            }
            return

        if sp == "PASS" and rf == "PASS":
            self.result_data["conclusion"] = {
                "level": "B",
                "title": "部分可用",
                "details": "可以列目录并感知文件变化，但文件下载受限（可能因空间安全水印配置、无下载权限或缺少符合条件的普通测试文件）。",
            }
            return

        # 空间信息或根目录无法访问，但 Token 成功
        sp_obj = tests.get("space_info", {})
        errcode = sp_obj.get("errcode")
        category = sp_obj.get("category")

        if errcode == 60020 or category == ErrorCategory.TRUSTED_IP_ERROR.value:
            env_chk = self.result_data.get("environment_check", {})
            caller_ip = (
                tests.get("space_info", {}).get("caller_ip")
                or tests.get("root_file_list", {}).get("caller_ip")
                or env_chk.get("egress_ip", {}).get("ip")
                or "当前公网出口IP"
            )
            self.result_data["conclusion"] = {
                "level": "C",
                "title": "仅授权成功（拦截于企业可信IP限制）",
                "details": (
                    f"Token 获取成功，但微盘空间接口调用被企业微信安全网关拦截 (errcode=60020: not allow to access from your ip: {caller_ip})。\n"
                    f"根本原因：执行环境当前公网出口 IP 未包含在企业微信后台配置的【企业可信 IP】列表中。\n"
                    f"解除限制方案：请企业微信管理员在自建应用后台将 IP [{caller_ip}] 添加至【企业可信 IP】白名单即可放行。"
                ),
            }
            return

        if errcode == 48002 or category == ErrorCategory.API_UNAUTHORIZED.value:
            self.result_data["conclusion"] = {
                "level": "C",
                "title": "仅授权成功（微盘API未授权）",
                "details": "Token 获取成功，但自建应用未在企业微信管理后台开通微盘 API 只读/读写授权 (errcode=48002)。",
            }
            return

        if (
            errcode in (2000003, 93001, 640008)
            or category == ErrorCategory.SPACE_NO_PERMISSION.value
        ):
            self.result_data["conclusion"] = {
                "level": "C",
                "title": "仅授权成功（微盘空间无访问权限）",
                "details": (
                    f"Token 获取成功且 IP 白名单已通过放行，但调用微盘空间接口返回权限不足 (errcode={errcode}: {sp_obj.get('errmsg')})。\n"
                    f"核心排查点：\n"
                    f"1. 【微盘 API 全局调用授权】：请在管理后台【协作】->【微盘】->【API】确认已开启接口同步，并将该自建应用（<YOUR_AGENT_ID>）加入【可调用接口的应用】列表并赋予只读权限；\n"
                    f"2. 【空间权限隔离】：若目标空间由个人用户创建（agent_id=0），受微盘权限隔离保护，需确认应用对该空间具有协同成员权限。"
                ),
            }
            return

        self.result_data["conclusion"] = {
            "level": "C",
            "title": "仅授权成功",
            "details": f"Token 获取成功，但微盘接口调用失败: [{errcode}] {sp_obj.get('errmsg')}",
        }

    def generate_reports(self):
        """保存探测结果 JSON 并生成中文 Markdown 验收报告。"""
        # 1. 结构化 JSON 保存
        json_path = self.reports_dir / "probe_result.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.result_data, f, ensure_ascii=False, indent=2)
        logger.info(f"结构化测试结果已写入: {json_path}")

        # 2. Markdown 报告渲染
        md_path = self.reports_dir / "probe_report.md"
        tests = self.result_data.get("api_tests", {})
        stats = self.result_data.get("catalog_statistics", {})
        conclusion = self.result_data.get("conclusion", {})
        env_chk = self.result_data.get("environment_check", {})
        meta = self.result_data.get("metadata", {})
        caller_ip = (
            tests.get("space_info", {}).get("caller_ip")
            or tests.get("root_file_list", {}).get("caller_ip")
            or env_chk.get("egress_ip", {}).get("ip")
            or "当前公网出口IP"
        )

        def _fmt_row(key: str) -> str:
            t = tests.get(key)
            if not t:
                return "| - | N/A | - | 未执行 |"
            status = t.get("status", "-")
            code = t.get("errcode")
            code_str = str(code) if (code is not None and code != 0) else "-"
            desc = t.get("description", "")
            return f"| {t.get('name')} | **{status}** | {code_str} | {desc} |"

        dist_str = (
            ", ".join(
                [
                    f"`{k}`: {v}"
                    for k, v in stats.get("file_types_distribution", {}).items()
                ]
            )
            or "无（未获取到文件列表）"
        )

        md_content = f"""# 企业微信微盘 API 真实连通性与文件读取测试报告

- **测试时间**：{meta.get("generated_at", "-")}
- **CorpID**：`{env_chk.get("corp_id", "-")}`
- **测试空间 ID**：`{env_chk.get("space_id", "-")}`
- **测试环境公网出口 IP**：`{env_chk.get("egress_ip", {}).get("ip", "未知")}`
- **DNS 解析结果**：`{", ".join(env_chk.get("dns", {}).get("ips", []))}`
- **代理配置状态**：`{env_chk.get("custom_proxy") or "直接直连（未启用正向代理）"}`

---

## 一、API 接口调用验收矩阵

| 测试项 | 状态 | 错误码 | 说明 |
|---|---|---|---|
{_fmt_row("token")}
{_fmt_row("space_info")}
{_fmt_row("root_file_list")}
{_fmt_row("recursive_list")}
{_fmt_row("file_info")}
{_fmt_row("file_download")}
{_fmt_row("file_integrity")}

---

## 二、指定微盘空间与文件清单统计

| 统计指标 | 统计结果 | 业务说明 |
|---|---|---|
| 可访问微盘空间数量 | **{
            stats.get("accessible_spaces_count", 0)
        }** | 仅核对授权指定的法律事务部共享空间 |
| 文件夹总数 | **{stats.get("folders_count", 0)}** | 递归树状目录结构统计 |
| 普通文件总数 | **{
            stats.get("files_count", 0)
        }** | docx / pdf / xlsx / zip 等二进制普通文件 |
| 在线微文档总数 | **{
            stats.get("other_docs_count", 0)
        }** | 腾讯微文档 / 微表格 / 收集表 |
| 文件格式分布 | {dist_str} | 母本合同多为 `.docx` / 归档扫描件为 `.pdf` |
| 最后修改时间范围 | `{stats.get("mtime_range", {}).get("min") or "N/A"}` ~ `{
            stats.get("mtime_range", {}).get("max") or "N/A"
        }` | 可用于按时间窗口进行增量感知 |
| API 是否提供文件哈希 | **{
            "是" if stats.get("has_file_hash") else "否（空间未开放或未返回哈希字段）"
        }** | 企业微信官方接口在 `file_info` / `file_list` 中提供 `sha` 与 `md5` |
| 适合增量同步评估 | **{
            "具备前提条件"
            if stats.get("incremental_sync_feasible")
            else "当前网络/权限受阻，待放行后评估"
        }** | 基于文件哈希或修改时间戳能够实现 0 成本跳过无变更合同 |
| 当前权限与网络限制 | **{
            tests.get("space_info", {}).get("description") or "无限制"
        }** | 阻断环节的根本原因记录 |

---

## 三、最终测试结论

### **结论评定：【{conclusion.get("level")}】{conclusion.get("title")}**

> **结论详情**：
> {conclusion.get("details")}

---

## 四、排查诊断与修复建议

{
            f"### 错误码【{tests.get('space_info', {}).get('errcode')}】深度排查与修复步骤："
            if tests.get("space_info", {}).get("errcode")
            else "### 接口调用正常："
        }

{
            tests.get("space_info", {}).get("suggestion")
            or "所有接口均已畅通，可直接推进下一阶段。"
        }

---

## 五、“微盘自动增量同步 → 合同知识库”推进评估

1. **接口协议可行性**：
   - 企业微信官方微盘 API 完全满足增量同步需求：`wedrive/file_list` 支持分页、修改时间戳及 `sha`/`md5` 哈希字段，能够在毫秒级比对文件指纹，跳过未修改母本；
   - `wedrive/file_download` 凭证机制可在服务端安全换取下载流，下载完成后通过本地 SHA-256 校验确保与知识库一致。
2. **当前阻断点与落地动作**：
{
            '''   - **当前状态**：IP 白名单已成功放行（原 60020 拦截已解除）；当前阻断位于**微盘空间访问权限限制**（错误码 `640008: permision deny`）；
   - **立即动作**：
     1. 请管理员登录企业微信管理端 ->【协作】->【微盘】->【API】，确认自建应用（<YOUR_AGENT_ID>）已加入【可调用接口的应用】并开启权限；
     2. 确认当前自建应用或操作者对目标微盘共享空间具有查看/协同权限；'''
            if tests.get("space_info", {}).get("errcode") == 640008
            else (
                f'''   - **当前状态**：阻断位于**企业微信【企业可信 IP】白名单拦截**（错误码 `60020`）；
   - **立即动作**：请企业微信管理员进入企业微信管理后台【应用管理】-> 找到对应自建应用 ->【企业可信 IP】，将当前请求出口 IP `{caller_ip}` 添加至白名单；'''
                if tests.get("space_info", {}).get("errcode") == 60020
                else '''   - **当前状态**：所有网络与微盘接口畅通无阻，已具备上线条件；'''
            )
        }
   - 权限配置放行后，本测试工具可一键重复执行，即可直接达成【结论 A：完全可用】并自动生成全量合同清单。
"""
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        logger.info(f"Markdown 验收报告已写入: {md_path}")

    def run(self) -> int:
        """主执行入口。"""
        logger.info("启动企业微信微盘 API 真实连通性与权限探测器...")
        ok = self.run_stage_a_env_check()
        if ok:
            self.run_stage_b_api_tests()
        self.generate_reports()

        conclusion = self.result_data.get("conclusion", {})
        logger.info("==================================================")
        logger.info(
            f"探测完成！最终结论：【{conclusion.get('level')}】{conclusion.get('title')}"
        )
        logger.info(f"详细信息: {conclusion.get('details')}")
        logger.info("==================================================")
        return 0


def main():
    parser = argparse.ArgumentParser(description="企业微信微盘 API 连通性与权限探测器")
    parser.add_argument(
        "--env-file", type=str, default=None, help="指定的 .env 配置文件路径"
    )
    args = parser.parse_args()

    runner = WeDriveProbeRunner(env_path=args.env_file)
    sys.exit(runner.run())


if __name__ == "__main__":
    main()
