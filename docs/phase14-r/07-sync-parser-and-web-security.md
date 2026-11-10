# Phase 14-R: 微盘同步、文档解析与 Web 安全综合整改 (07-sync-parser-and-web-security)

## 一、漏洞与整改清单 (DEFECT-P1-02, DEFECT-P1-03, DEFECT-P1-04)

### 1.1 企业微信微盘同名文件更新跨租户篡改 (DEFECT-P1-02)

- **缺陷代码**：`tools/sync_wecom_wedrive.py:436`
  ```python
  self.storage.conn.execute(
      "UPDATE documents SET source_file=? WHERE source_hash=?",
      (rel_path, file_sha256),
  )
  ```
- **危害**：当租户 A 与租户 B 同步了相同标准模板（哈希相同）时，租户 A 对模板进行重命名或移动路径，此 SQL 会无条件将所有租户下该哈希文档的 `source_file` 全部修改为租户 A 的私有路径，造成跨租户路径篡改。
- **修复措施**：强制绑定 `tenant_id`：
  ```python
  sync_tenant = getattr(self, "tenant_id", config.TENANT_ID)
  self.storage.conn.execute(
      "UPDATE documents SET source_file=? WHERE source_hash=? AND tenant_id=?",
      (rel_path, file_sha256, sync_tenant),
  )
  ```

---

### 1.2 混合型 PDF 解析漏审与状态断层 (DEFECT-P1-03)

- **缺陷场景**：在上传混合型 PDF 合同（例如：第 1 页包含文字抬头与编号，第 2 页为扫描手写签章条款或补充协议附件）时，原 `contract_kb/document_parser.py` 中的 `parse_pdf` 仅通过全文文字总长度判断：
  ```python
  if (not raw_all or len(raw_all) < 25) and (total_images > 0 or total_pages > 0):
      # 判定为纯影印件
  ```
  因为第 1 页有文字使得 `len(raw_all) >= 25`，导致整份文档被当作纯文字 PDF 处理，第 2 页的扫描条款完全丢失且没有任何告警提示，造成核心违约条款严重漏审。
- **修复措施**：
  - 引入**逐页图层与文字密度扫描检测**：遍历每一页，若某页文字字符 `< 10` 且包含图片或矢量图元，即刻判定为扫描页；
  - 自动插入类型为 `scanned_placeholder` 的 ASTNode 节点，在条款流水线中保持其原始页码位置；
  - 将 `parse_warnings` 明确标记为 `OCR_REQUIRED`，提示用户必须经过 OCR 识别方可进行条款审查，严禁伪造合规全绿结论。

---

### 1.3 Web 独立 HTML 导出 XSS 注入防护 (DEFECT-P1-04)

- **缺陷场景**：在 `tools/contract_diff_web.py` 的 `generate_rendered_html()` 中，初始合同比对数据、母本清单与案卷元数据直接通过字符串替换插入到 HTML `<script>` 标签中：
  ```python
  html = HTML_TEMPLATE.replace(
      "__INITIAL_DATA_PLACEHOLDER__", json.dumps(data, ensure_ascii=False)
  )
  ```
  若合同文件名、条款正文或用户输入包含 `</script><script>alert(1)</script>`，会导致浏览器提前闭合当前 script 上下文，执行注入的恶意 JavaScript 脚本，构成存储型 XSS。
- **修复措施**：
  引入 `_safe_json_embed` 编码器：
  ```python
  def _safe_json_embed(obj: Any) -> str:
      raw = json.dumps(obj, ensure_ascii=False)
      return (
          raw.replace("&", "\\u0026")
          .replace("<", "\\u003c")
          .replace(">", "\\u003e")
          .replace("\u2028", "\\u2028")
          .replace("\u2029", "\\u2029")
      )
  ```
  所有注入 HTML 的 JSON 占位符强制使用 `_safe_json_embed`，彻底杜绝标签逃逸注入。

---

## 二、测试验证证据

- `tests/security/test_web_html_bootstrap_xss.py`：注入含 `</script><script>alert('xss')</script>` 攻击载荷，验证生成 HTML 中不存在任何未转义的裸闭合标签，测试通过；
- `tests/unit/test_document_parser.py`：验证混合型 PDF 逐页扫描识别与 `OCR_REQUIRED` 标记无回退通过。
