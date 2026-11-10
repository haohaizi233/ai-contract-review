# 08 — Web 工作台解耦与静态资源化（Web Workbench Modularization）

## 1. 重构背景与目标

历史文件 `tools/contract_diff_web.py` 长达 **4,603 行（205 KB）**，单文件内混合了：
- `ThreadingHTTPServer` 与 `BaseHTTPRequestHandler` 请求分发；
- 合同上传、multipart 解析、路径防穿越校验、临时会话管理；
- 模板目录加载、标准参考模板切换、历史案卷素材库查询；
- 法务 Agent SSE 流式推送、卡片优先/跳过/重试/暂停/取消控制；
- 人工采纳/驳回决策持久化、SFT 训练样本导出；
- 超过 3,700 行的内联 HTML 骨架、CSS 样式表与三栏联动 JavaScript 脚本。

---

## 2. 模块化拆分结构

重构后，`contract_kb/interfaces/web/` 形成职责清晰的 MVC/Service 分层结构：

```text
contract_kb/interfaces/web/
├── __init__.py               # 惰性属性导出（避免 python -m 启动时触发 runpy RuntimeWarning）
├── app.py                    # DiffVisualizerHandler 路由表分发、模板组装与服务启动主函数 main()
├── server.py                 # create_web_server() 与 CLI 启动入口转发
├── services/
│   ├── __init__.py
│   ├── session_files.py      # 会话文件隔离、multipart/form-data 安全解析、文件名防穿越清洗
│   └── diff_service.py       # 整版合同 Diff 执行、置信度排序母本目录、人工审核决策持久化
├── routes/
│   ├── __init__.py
│   ├── contracts.py          # /api/diff/upload, /api/diff/sample, /api/diff/switch-template
│   ├── templates.py          # /api/templates/*, /api/ref_templates/*
│   ├── materials.py          # /api/materials/stats, /api/materials/cases, /api/materials/case_detail, /api/materials/load_contract
│   ├── agents.py             # /api/diff/agent_stream (SSE), /api/diff/agent_status, prioritize/retry/skip/toggle_pause/cancel/rerun/reset_circuit
│   ├── reviews.py            # /api/diff/human_decision
│   └── feedback.py           # /api/diff/feedback, /api/diff/export_training_data
├── templates/
│   └── index.html            # 519 行前端 HTML 骨架（保留全部 DOM ID 与布局结构）
└── static/
    ├── css/
    │   └── app.css           # 948 行完整样式表（保留全部 CSS 类名与差异高亮样式）
    └── js/
        ├── app.js            # 606 行全局状态、初始化与公共弹窗/抽屉控制
        ├── api_client.js     # 580 行上传、模板切换、素材库与标准模板加载客户端
        ├── review_workspace.js # 820 行三栏同步滚动、差异聚焦、批注卡片渲染与筛选
        └── agent_stream.js   # 314 行 EventSource (SSE) 实时流式接收、心跳重连与人工反馈提交
```

---

## 3. 契约与行为 100% 等价保证

1. **HTTP 路由与方法零变化**：全部 22 个 GET/POST 路由 URI、Query 参数、Request JSON Body 与 Response JSON 字段保持字节级兼容。
2. **前端 DOM / CSS / JS 零变化**：`load_html_template()` 在运行时从包内 `templates/index.html`、`static/css/app.css` 与 `static/js/*.js` 按原有顺序拼装，生成的 `HTML_TEMPLATE` 与原内联模板完全一致。
3. **离线独立 HTML 导出保持不变**：`export_standalone_html()` 继续生成内嵌 CSS/JS 与初始数据的单文件 `data/contract_merged/合同整版对照与条款批注工作台.html`。
