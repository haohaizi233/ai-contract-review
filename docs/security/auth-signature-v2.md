# 认证签名 V2 规范与防重放对接指南（Auth Signature V2 Specification）

## 1. 背景与安全目标

为消除 HTTP 报文被抓包在时间窗口内重放执行特权操作的漏洞（F08），系统引入 **HMAC-SHA256 签名 V2 契约**：
1. **完整绑定**：将 HTTP Method、规范化 Path、排序 Query、认证身份（User/Roles/Tenant）、时间戳、随机 Nonce 及 Body 摘要全量纳入签名载荷；
2. **防重放**：每个 Nonce 具备单次有效性（One-Time Use），并在服务端通过内存与数据库（`auth_replay_nonces`）双层持久化校验；
3. **有限 Body 摘要**：GET 请求及空 Body 使用标准空字符串 SHA-256，文件上传及大请求通过请求头携带 `X-Content-SHA256`，避免服务端认证层缓冲导致内存耗尽（OOM）。

---

## 2. 签名载荷规范（Canonical Payload）

签名载荷由 9 个字段组成，以换行符 `\n` 连接：

```text
HTTP_METHOD\n
NORMALIZED_PATH\n
SORTED_QUERY_STRING\n
X_USER\n
X_ROLES\n
X_TENANT\n
X_AUTH_TIMESTAMP\n
X_AUTH_NONCE\n
BODY_SHA256
```

### 字段说明与规范化规则

| 字段 | 示例 | 规范化规则 |
|---|---|---|
| `HTTP_METHOD` | `POST` | 全部大写（`GET`, `POST`, `DELETE` 等） |
| `NORMALIZED_PATH` | `/api/v1/contract-review/tasks` | 去除首尾空格，保留绝对路径 |
| `SORTED_QUERY_STRING` | `limit=20&mode=STANDARD&offset=0` | 查询参数按键名升序排序，无参数时留空字符串 |
| `X_USER` | `zhangsan` | 操作人身份标识 |
| `X_ROLES` | `reviewer,admin` | 角色列表 |
| `X_TENANT` | `enterprise_tenant_01` | 租户标识，未指定时默认为 `default` |
| `X_AUTH_TIMESTAMP` | `1730966400` | Unix 秒级时间戳（服务端校验窗口 ±300 秒） |
| `X_AUTH_NONCE` | `c8b9d8e7f6a543210fedcba987654321` | 密码学安全随机字符串（长度 >= 16） |
| `BODY_SHA256` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | 请求体 SHA-256 十六进制小写；空 Body 为固定空哈希 |

---

## 3. 请求头清单

网关或客户端调用时须携带以下请求头：

```http
X-User: zhangsan
X-Roles: reviewer
X-Tenant: enterprise_tenant_01
X-Auth-Timestamp: 1730966400
X-Auth-Nonce: c8b9d8e7f6a543210fedcba987654321
X-Auth-Version: 2
X-Content-SHA256: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
X-Auth-Signature: <64位十六进制HMAC-SHA256签名>
```

---

## 4. Python 生成参考实现

```python
import hashlib
import hmac
import time
import urllib.parse
import uuid

EMPTY_BODY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

def sign_v2_request(shared_key: str, method: str, path: str, query: str, user: str, roles: str, tenant: str, body_bytes: bytes = b"") -> dict[str, str]:
    ts = str(int(time.time()))
    nonce = uuid.uuid4().hex + uuid.uuid4().hex[:8]  # 40 chars
    body_sha = hashlib.sha256(body_bytes).hexdigest() if body_bytes else EMPTY_BODY_SHA256
    
    # 规范化 query
    if query:
        pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
        pairs.sort(key=lambda x: (x[0], x[1]))
        canonical_query = urllib.parse.urlencode(pairs)
    else:
        canonical_query = ""
        
    canonical_payload = "\n".join([
        method.upper().strip(),
        path.strip(),
        canonical_query,
        user.strip(),
        roles.strip(),
        tenant.strip(),
        ts,
        nonce,
        body_sha.lower(),
    ])
    
    sig = hmac.new(shared_key.encode("utf-8"), canonical_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    
    return {
        "X-User": user,
        "X-Roles": roles,
        "X-Tenant": tenant,
        "X-Auth-Timestamp": ts,
        "X-Auth-Nonce": nonce,
        "X-Auth-Version": "2",
        "X-Content-SHA256": body_sha,
        "X-Auth-Signature": sig,
    }
```

---

## 5. cURL 示例

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/contract-review/tasks" \
  -H "X-User: alice" \
  -H "X-Roles: reviewer" \
  -H "X-Tenant: tenant_alpha" \
  -H "X-Auth-Timestamp: 1730966400" \
  -H "X-Auth-Nonce: 4f2c9b3a1e8d7c6b5a4f3e2d1c0b9a8f" \
  -H "X-Auth-Version: 2" \
  -H "X-Content-SHA256: 7d865e959b2466918c9863afca942d0fb89d7c9ac0c99bafc3749504ded97730" \
  -H "X-Auth-Signature: a1b2c3d4e5f6...32bytes" \
  -F "file=@test_contract.docx" \
  -F "mode=STANDARD"
```

---

## 6. 版本迁移与兼容策略

- **阶段一（当前）**：服务端支持 `CONTRACT_KB_AUTH_VERSION=COMPAT`，对于未携带 `X-Auth-Nonce` 且版本为 1 的旧网关请求继续校验 V1 签名并记录告警；
- **阶段二（两周后）**：企业网关全量部署 V2 签名逻辑；
- **阶段三（全面切换）**：服务端配置 `CONTRACT_KB_AUTH_VERSION=2`，全面拒绝一切 V1 签名。
