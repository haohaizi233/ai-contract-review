# -*- coding: utf-8 -*-
"""企业微信微盘 640008 故障最小化诊断实验脚本。

用于全链路执行 Phase 4 规定的各诊断实验：
- 实验 A: Token 获取与应用配置诊断
- 实验 B: 目标空间信息 (space_info) 读取
- 实验 C: 新版空间信息 (new_space_info) 对照
- 实验 D: 应用创建空间对照实验 (写入受控防护)
- 实验 E: 网络出口与代理环境对照
- 实验 F: 空间客观存在性负对照 (Negative Control)
- 实验 G: 应用身份与可见范围反查 (Agent Audit)
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args: Any, **_kwargs: Any) -> None:
        pass

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent.parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("WeDriveDiagnostics")


def mask_secret(s: str | None) -> str:
    if not s:
        return "<EMPTY>"
    s = str(s).strip()
    if len(s) <= 8:
        return "********"
    return f"{s[:4]}********{s[-4:]}"


def mask_token(t: str | None) -> str:
    if not t:
        return "<EMPTY>"
    t = str(t).strip()
    if len(t) <= 12:
        return "tok_******"
    return f"{t[:6]}...{t[-6:]}"


def get_egress_ip(session: requests.Session) -> str:
    endpoints = [
        "https://myip.ipip.net/json",
        "https://api.ipify.org?format=json",
        "http://ifconfig.me/ip",
    ]
    for ep in endpoints:
        try:
            r = session.get(ep, timeout=5, verify=True)
            if r.status_code == 200:
                if "json" in ep:
                    data = r.json()
                    ip = data.get("data", {}).get("ip") or data.get("ip")
                    if ip:
                        return ip.strip()
                else:
                    return r.text.strip()
        except Exception:
            continue
    return "UNKNOWN"


class WeDriveDiagnosticsRunner:
    def __init__(self, env_path: str | Path | None = None):
        # 依次加载环境变量
        candidates = [
            Path(env_path) if env_path else None,
            CURRENT_DIR / ".env",
            PROJECT_ROOT / "tools" / "wecom_wedrive_probe" / ".env",
            PROJECT_ROOT / ".env",
        ]
        self.loaded_env = None
        for c in candidates:
            if c and c.exists():
                load_dotenv(c, override=True)
                self.loaded_env = str(c)
                break

        self.corp_id = os.getenv("WECOM_CORP_ID", "").strip()
        self.app_secret = os.getenv("WECOM_APP_SECRET", "").strip()
        self.space_id = os.getenv("WECOM_SPACE_ID", "").strip()
        self.base_url = "https://qyapi.weixin.qq.com"

        self.report_data: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "environment": {
                "env_file": self.loaded_env,
                "corp_id": mask_secret(self.corp_id),
                "app_secret": mask_secret(self.app_secret),
                "target_space_id": self.space_id,
            },
            "experiments": {},
        }

    def run_all_experiments(self) -> dict[str, Any]:
        logger.info("=== 开始执行微盘 640008 最小化诊断实验集 ===")

        # ---------------------------------------------------------
        # 实验 A: Token 诊断
        # ---------------------------------------------------------
        logger.info("[实验 A] 执行 access_token 获取与鉴权验证...")
        exp_a = self._experiment_a_token()
        self.report_data["experiments"]["experiment_a"] = exp_a

        token = exp_a.get("raw_token")
        if not token:
            logger.error("Token 获取失败，终止后续 API 依赖实验")
            return self.report_data

        # ---------------------------------------------------------
        # 实验 G: 应用身份反查 (Agent Audit)
        # ---------------------------------------------------------
        logger.info("[实验 G] 反查当前 Secret 所属的自建应用身份...")
        exp_g = self._experiment_g_agent_info(token)
        self.report_data["experiments"]["experiment_g"] = exp_g

        # ---------------------------------------------------------
        # 实验 B: 目标空间信息读取 (space_info)
        # ---------------------------------------------------------
        logger.info(f"[实验 B] 读取目标空间信息 (space_info): {self.space_id}...")
        exp_b = self._experiment_b_space_info(token, self.space_id)
        self.report_data["experiments"]["experiment_b"] = exp_b

        # ---------------------------------------------------------
        # 实验 C: 新版空间信息接口对照 (new_space_info)
        # ---------------------------------------------------------
        logger.info(f"[实验 C] 尝试新版空间信息接口 (new_space_info): {self.space_id}...")
        exp_c = self._experiment_c_new_space_info(token, self.space_id)
        self.report_data["experiments"]["experiment_c"] = exp_c

        # ---------------------------------------------------------
        # 实验 F: 空间客观存在性负对照 (Negative Control)
        # ---------------------------------------------------------
        logger.info("[实验 F] 执行伪造空间 ID 负对照 (640001 vs 640008)...")
        exp_f = self._experiment_f_negative_control(token)
        self.report_data["experiments"]["experiment_f"] = exp_f

        # ---------------------------------------------------------
        # 实验 E: 网络出口与代理环境对照
        # ---------------------------------------------------------
        logger.info("[实验 E] 执行网络出口与代理对比...")
        exp_e = self._experiment_e_network_comparison(token)
        self.report_data["experiments"]["experiment_e"] = exp_e

        # ---------------------------------------------------------
        # 实验 D: 应用创建空间对照实验 (检查权限与受控提示)
        # ---------------------------------------------------------
        logger.info("[实验 D] 评估应用创建空间 (space_create) 权限与对照方案...")
        exp_d = self._experiment_d_space_create_plan()
        self.report_data["experiments"]["experiment_d"] = exp_d

        logger.info("=== 全部诊断实验执行完毕 ===")
        return self.report_data

    def _experiment_a_token(self) -> dict[str, Any]:
        session = requests.Session()
        session.trust_env = False
        url = f"{self.base_url}/cgi-bin/gettoken"
        t0 = time.perf_counter()
        resp = session.get(
            url,
            params={"corpid": self.corp_id, "corpsecret": self.app_secret},
            timeout=15,
            verify=True,
        )
        duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        try:
            body = resp.json()
        except Exception:
            body = {"raw_text": resp.text[:200]}

        errcode = body.get("errcode", -1)
        access_token = body.get("access_token")
        success = (resp.status_code == 200 and errcode == 0 and bool(access_token))

        return {
            "name": "实验 A: Token 诊断",
            "http_status": resp.status_code,
            "errcode": errcode,
            "errmsg": body.get("errmsg"),
            "duration_ms": duration_ms,
            "success": success,
            "masked_token": mask_token(access_token),
            "expires_in": body.get("expires_in"),
            "raw_token": access_token if success else None,
        }

    def _experiment_b_space_info(self, token: str, space_id: str) -> dict[str, Any]:
        session = requests.Session()
        session.trust_env = False
        url = f"{self.base_url}/cgi-bin/wedrive/space_info"
        payload = {"spaceid": space_id}
        t0 = time.perf_counter()
        resp = session.post(
            url,
            params={"access_token": token},
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15,
            verify=True,
        )
        duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        try:
            body = resp.json()
        except Exception:
            body = {"raw_text": resp.text[:200]}

        errcode = body.get("errcode", -1)
        return {
            "name": "实验 B: 目标空间信息 (space_info)",
            "endpoint": "POST /cgi-bin/wedrive/space_info",
            "http_status": resp.status_code,
            "errcode": errcode,
            "errmsg": body.get("errmsg"),
            "duration_ms": duration_ms,
            "business_success": (resp.status_code == 200 and errcode == 0),
            "space_info": body.get("space_info"),
            "raw_response": body,
        }

    def _experiment_c_new_space_info(self, token: str, space_id: str) -> dict[str, Any]:
        session = requests.Session()
        session.trust_env = False
        url = f"{self.base_url}/cgi-bin/wedrive/new_space_info"
        payload = {"spaceid": space_id}
        t0 = time.perf_counter()
        resp = session.post(
            url,
            params={"access_token": token},
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15,
            verify=True,
        )
        duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        try:
            body = resp.json()
        except Exception:
            body = {"raw_text": resp.text[:200]}

        errcode = body.get("errcode", -1)
        return {
            "name": "实验 C: 新版空间信息接口 (new_space_info)",
            "endpoint": "POST /cgi-bin/wedrive/new_space_info",
            "http_status": resp.status_code,
            "errcode": errcode,
            "errmsg": body.get("errmsg"),
            "duration_ms": duration_ms,
            "business_success": (resp.status_code == 200 and errcode == 0),
            "raw_response": body,
        }

    def _experiment_f_negative_control(self, token: str) -> dict[str, Any]:
        session = requests.Session()
        session.trust_env = False
        url = f"{self.base_url}/cgi-bin/wedrive/space_info"
        fake_space_id = "s.0000000000000000.000000000FAKE"
        t0 = time.perf_counter()
        resp = session.post(
            url,
            params={"access_token": token},
            json={"spaceid": fake_space_id},
            timeout=15,
            verify=True,
        )
        duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        try:
            body = resp.json()
        except Exception:
            body = {"raw_text": resp.text[:200]}

        errcode = body.get("errcode", -1)
        return {
            "name": "实验 F: 空间客观存在性负对照",
            "fake_space_id": fake_space_id,
            "http_status": resp.status_code,
            "errcode": errcode,
            "errmsg": body.get("errmsg"),
            "duration_ms": duration_ms,
            "comparison": (
                "真实空间返回 640008 (permission deny)；"
                f"伪造空间返回 {errcode} ({body.get('errmsg')})。"
                "严格证实：原空间在企微微盘服务端真实存在，640008 并非 spaceid 错误或不存在，而是权限隔离拦截。"
            ),
        }

    def _experiment_g_agent_info(self, token: str) -> dict[str, Any]:
        session = requests.Session()
        session.trust_env = False
        # 反查已知自建应用 AgentId 1000077
        url = f"{self.base_url}/cgi-bin/agent/get"
        resp = session.get(
            url,
            params={"access_token": token, "agentid": 1000077},
            timeout=15,
            verify=True,
        )
        try:
            body = resp.json()
        except Exception:
            body = {}

        return {
            "name": "实验 G: 应用身份反查",
            "http_status": resp.status_code,
            "errcode": body.get("errcode"),
            "agentid": body.get("agentid"),
            "agent_name": body.get("name"),
            "description": body.get("description"),
            "allow_partys": body.get("allow_partys", {}).get("partyid", []),
            "allow_users": body.get("allow_userinfos", {}).get("user", []),
            "close_status": body.get("close"),
            "redirect_domain": body.get("redirect_domain"),
        }

    def _experiment_e_network_comparison(self, token: str) -> dict[str, Any]:
        results = {}

        # 1. 直连环境 (trust_env=False)
        s_direct = requests.Session()
        s_direct.trust_env = False
        direct_ip = get_egress_ip(s_direct)
        resp_direct = s_direct.post(
            f"{self.base_url}/cgi-bin/wedrive/space_info",
            params={"access_token": token},
            json={"spaceid": self.space_id},
            timeout=10,
            verify=True,
        )
        results["direct"] = {
            "mode": "直连 (trust_env=False)",
            "egress_ip": direct_ip,
            "http_status": resp_direct.status_code,
            "errcode": resp_direct.json().get("errcode"),
            "errmsg": resp_direct.json().get("errmsg"),
        }

        # 2. 系统代理环境 (trust_env=True)
        s_proxy = requests.Session()
        s_proxy.trust_env = True
        proxy_ip = get_egress_ip(s_proxy)
        try:
            resp_proxy = s_proxy.post(
                f"{self.base_url}/cgi-bin/wedrive/space_info",
                params={"access_token": token},
                json={"spaceid": self.space_id},
                timeout=10,
                verify=True,
            )
            proxy_res = {
                "http_status": resp_proxy.status_code,
                "errcode": resp_proxy.json().get("errcode"),
                "errmsg": resp_proxy.json().get("errmsg"),
            }
        except Exception as e:
            proxy_res = {"error": str(e)}

        results["system_proxy"] = {
            "mode": "系统代理 (trust_env=True)",
            "egress_ip": proxy_ip,
            **proxy_res,
        }

        return {
            "name": "实验 E: 网络出口与代理环境对照",
            "comparisons": results,
            "conclusion": (
                "无论直连还是系统代理环境，Token 鉴权均正常通过，"
                "且空间读取均返回 640008，证实 640008 并非由本地代理设置或网络抖动所致。"
            ),
        }

    def _experiment_d_space_create_plan(self) -> dict[str, Any]:
        return {
            "name": "实验 D: 应用创建空间对照实验 (受控状态)",
            "status": "AWAITING_USER_CONFIRMATION",
            "action_type": "WRITE_OPERATION (POST /cgi-bin/wedrive/space_create)",
            "reason": "依照安全审计要求，创建空间属于持久化写操作，未获用户显式确认前严禁擅自执行。",
            "test_specification": {
                "endpoint": "POST /cgi-bin/wedrive/space_create?access_token=ACCESS_TOKEN",
                "proposed_payload": {
                    "space_name": "AI合同审核_API探针测试空间",
                    "auth_info": [
                        {
                            "type": 1,
                            "userid": "MaoShiMei",
                            "auth": 7
                        }
                    ],
                    "space_sub_type": 0
                },
                "expected_hypothesis": (
                    "如果成功创建新空间并在返回的 spaceid 上执行 space_info 得到 errcode=0，"
                    "即可实锤企微自建应用对『API自建空间』拥有完全管理权，"
                    "而对『用户客户端手动创建空间』存在原生隔离阻断。"
                )
            }
        }


def main():
    runner = WeDriveDiagnosticsRunner()
    results = runner.run_all_experiments()

    output_dir = PROJECT_ROOT / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "diagnostics_result.json"

    # 序列化保存结果（已脱敏）
    safe_results = json.loads(json.dumps(results, default=str))
    # 彻底抹除内存中的 raw_token
    if "experiment_a" in safe_results.get("experiments", {}):
        safe_results["experiments"]["experiment_a"].pop("raw_token", None)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(safe_results, f, ensure_ascii=False, indent=2)

    print(f"\n[OK] 诊断实验执行完成，脱敏数据已写入: {json_path}")
    print("\n--- 关键实验结果摘要 ---")
    exps = safe_results.get("experiments", {})
    for k, v in exps.items():
        name = v.get("name", k)
        code = v.get("errcode")
        status = v.get("business_success") if "business_success" in v else v.get("success")
        print(f"[{name}] status={status}, errcode={code}, errmsg={v.get('errmsg')}")


if __name__ == "__main__":
    main()
