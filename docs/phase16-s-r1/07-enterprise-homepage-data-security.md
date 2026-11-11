# Phase 16-S-R1: 07 - 企业首页未认证元数据泄露防护与数据最小化

## 一、审计缺陷诊断 (P1-R1-08)

在上一轮 Phase 16-S 实现中，独立代码审计发现：
尽管在 `is_enterprise=True` 时将当前活动的合同比对数据置空：
```python
elif is_enterprise:
    data_json = "null"
```
但对于母本清单 `get_ranked_templates_catalog()`、参考模板库 `get_ref_templates_catalog()`，以及素材库案例 `mgr.search_cases(category="ALL", ...)`：
**它们依然被无条件全量序列化并内嵌在返回的初始 HTML 页面中！**

**安全隐患**：
任何未经认证的外部网络访问者，仅需向企业工作台发起简单的 HTTP `GET /` 请求，即可在返回的页面源码中直接提取到：
1. 企业全部标准合同模板清单（包含内部模板编号、分类、业务板块与评分）；
2. 企业参考母本库全文清单；
3. 企业素材库中的真实历史合同案例（`initial_cases`，包含具体合同编号、案件名称、对方客户主体名称、经办律师与申请人等高度敏感商业机密）。

---

## 二、数据最小化与防泄露重构方案

本次整改在 `contract_kb/interfaces/web/app.py` 中践行**数据最小化原则 (Data Minimization)**：

```text
未认证用户访问 GET / 或 GET /index.html
                     ↓
             is_enterprise == True
                     ↓
  [数据置空与防泄露脱敏]
  • data_json = "null"
  • catalog_json = "[]"
  • ref_json = "[]"
  • material_json = "{}"
                     ↓
  HTML 页面内嵌数据占位符完全清空！
  (页面仅呈现受保护的登录与框架外壳)
                     ↓
  客户端如需展示模板与案例，必须：
  通过带 HMAC-SHA256 签名的受控 API 请求：
  • GET /api/templates/list
  • GET /api/ref_templates/catalog
  • GET /api/materials/cases
  必须通过服务端 RBAC (ROLE_VIEWER) 与租户权限核验！
```

### 开发环境兼容
在单机本地调试模式下（`DIFF_WEB_MODE != ENTERPRISE` 且无企业密钥），继续允许为本地开发人员注入演示模板与测试案例，保障离线开箱即用的前端体验。

---

## 三、代码改动定位

**`contract_kb/interfaces/web/app.py`** 中的 `generate_rendered_html()`：
```python
if is_enterprise:
    # 企业模式下严禁向未认证用户泄露母本清单、参考母本库以及敏感真实案例元数据
    catalog_json = "[]"
    ref_json = "[]"
    material_json = "{}"
else:
    # 单机开发模式正常装载演示样例
    catalog = get_ranked_templates_catalog(CURRENT_SESSION_FILE)
    catalog_json = _safe_json_embed(catalog)
    ...
```

---

## 四、安全测试验证

在 `tests/security/test_phase16_r1_homepage_data_leak.py` 中建立完整测试用例：
1. **`test_enterprise_mode_html_nullifies_all_internal_metadata`**：
   - 生成企业版 HTML，断言 `allTemplates` 为 `[]`，`MATERIAL_DATA` 为 `{}`，`INITIAL_DATA` 为 `null`，且完全不含 `"initial_cases":`。
2. **`test_unauthenticated_http_get_enterprise_homepage`**：
   - 启动真实 HTTP 服务端，发起未认证 `GET /`，核查响应正文不泄露内部案例。
3. **`test_local_dev_mode_preserves_offline_sample_experience`**：
   - 验证单机开发模式离线可用性保持不变。
4. **`test_authenticated_api_endpoints_still_provide_scoped_data`**：
   - 验证合法认证用户通过受控 API 正常获取模板数据。
