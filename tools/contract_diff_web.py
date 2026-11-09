# -*- coding: utf-8 -*-
"""合同比对与条款级 Diff 前端可视化服务（Contract Diff Web Visualizer）。

高管/法务级全案设计：
1. 源文件以原格式高保真渲染（支持 docx, doc, pdf 等特别处理）：
   大标题居中大字、副标题居中、合同编号右对齐、双方主体与鉴于条款、条款条标题加粗左对齐、
   段落正文首行缩进 2 字符、两端对齐、尾部双方签署盖章区。真实 A4 纸张质感。
2. 左右对比两版纯净聚焦增删（去冗余批注徽标）：
   正文段落彻底摒弃密密麻麻的小药丸徽标（如 🏷️ [批注 #XX]），纯粹聚焦 Word/PDF 审阅级法律标红：
   - 删除文本：<del class="diff-del">红字删除线</del>
   - 新增/修改：<ins class="diff-ins">下划线新增</ins>
   - 高危数值篡改：金黄高危底色醒目下划线
   - 母本被整段删除的条款：母本端整段优雅标红删除线，送审端呈现极简平齐占位槽（不插大标签）
   - 送审稿新增非标条款：送审端优雅新增标红，母本端平齐占位槽
3. 三栏双向协同联动滚动（Three-Way Synchronized Scrolling）：
   - 左栏：标准参考母本（独立滚动）
   - 中栏：送审修改稿（独立滚动）
   - 右栏：法务审查详情与批注专栏（自适应独立滚动）
   - 上下滑动左右严格对齐（条款高度自动等高平衡计算）
   - 三个栏只要滑动其中任意一个，都会平滑且相对应地滑动对应的另外两个，无死循环抖动
4. 最右侧详情栏自适应（Responsive Sidebar）：
   - 弹性自适应宽度，卡片流式排版无文字截断，支持一键折叠/展开
5. 原生 CDP 调试与自动化验证集成。

用法：
  python tools/contract_diff_web.py [--port 8089]
  python tools/contract_diff_web.py --export-only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from contract_kb import config  # noqa: E402
from contract_kb.full_diff import build_full_contract_diff  # noqa: E402

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI 合同审查 · 条款级 Diff 与整版原格式比对工作台</title>
<style>
  :root {
    --bg-canvas: #E2E8F0;
    --paper-bg: #FFFFFF;
    --border: #CBD5E1;
    --border-light: #E2E8F0;
    --text-main: #0F172A;
    --text-muted: #64748B;
    --text-sub: #94A3B8;
    --primary: #2563EB;
    --primary-light: #EFF6FF;
    --danger: #DC2626;
    --danger-light: #FEF2F2;
    --warning: #D97706;
    --warning-light: #FFFBEB;
    --success: #16A34A;
    --success-light: #F0FDF4;
    --purple: #7C3AED;
    --purple-light: #FAF5FF;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body {
    height: 100vh;
    overflow: hidden;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "SimSun", "STSong", serif;
    background: var(--bg-canvas);
    color: var(--text-main);
    line-height: 1.6;
  }

  /* ==================== 1. 顶部全局极简导航栏 (48px) ==================== */
  .header {
    height: 48px;
    background: #FFFFFF;
    border-bottom: 1px solid var(--border-light);
    padding: 0 20px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    z-index: 100;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
  }
  .brand { display: flex; align-items: center; gap: 10px; }
  .brand-logo {
    width: 28px; height: 28px; border-radius: 7px;
    background: linear-gradient(135deg, #1E40AF 0%, #3B82F6 100%);
    color: #fff; display: flex; align-items: center; justify-content: center;
    font-weight: 800; font-size: 15px; font-family: sans-serif;
  }
  .brand-title { font-size: 15px; font-weight: 700; color: #1E293B; letter-spacing: -0.2px; font-family: sans-serif; }

  .header-meta-group {
    display: flex; align-items: center; gap: 12px; font-size: 12.5px; font-family: sans-serif;
  }
  .meta-tag {
    display: inline-flex; align-items: center; gap: 5px; padding: 3px 10px;
    border-radius: 6px; font-weight: 600;
  }
  .meta-tag.matched {
    background: #DCFCE7; color: #15803D; border: 1px solid #BBF7D0;
  }
  .meta-tag.doc-title {
    background: #EFF6FF; color: #1E40AF; border: 1px solid #BFDBFE;
  }

  .header-actions { display: flex; align-items: center; gap: 8px; font-family: sans-serif; }
  .btn {
    padding: 5px 12px; border-radius: 6px; font-size: 12px; font-weight: 600;
    cursor: pointer; transition: all 0.15s ease; display: inline-flex;
    align-items: center; gap: 5px; border: 1px solid var(--border-light);
    background: #FFFFFF; color: #334155; font-family: sans-serif;
  }
  .btn:hover { background: #F8FAFC; border-color: var(--border); }
  .btn-primary { background: var(--primary); color: #FFF; border-color: var(--primary); }
  .btn-primary:hover { background: #1D4ED8; }

  /* ==================== 2. 紧凑型工具与审阅状态控制条 (40px) ==================== */
  .toolbar-strip {
    height: 40px;
    background: #F8FAFC;
    border-bottom: 1px solid var(--border-light);
    padding: 0 20px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    z-index: 90;
    font-family: sans-serif;
  }
  .tool-left { display: flex; align-items: center; gap: 12px; }
  .segmented-control {
    display: flex; background: #E2E8F0; padding: 2px; border-radius: 6px;
  }
  .seg-btn {
    padding: 3px 10px; font-size: 11.5px; font-weight: 600; border-radius: 4px;
    border: none; background: transparent; color: #64748B; cursor: pointer;
    transition: all 0.15s ease; white-space: nowrap; font-family: sans-serif;
  }
  .seg-btn.active {
    background: #FFF; color: #0F172A; box-shadow: 0 1px 2px rgba(0,0,0,0.06);
  }

  .nav-group { display: flex; align-items: center; gap: 6px; }
  .nav-arrow-btn {
    padding: 2px 8px; border-radius: 4px; border: 1px solid var(--border-light);
    background: #FFF; font-size: 11.5px; font-weight: 600; color: #334155;
    cursor: pointer; font-family: sans-serif;
  }
  .nav-arrow-btn:hover { background: #F1F5F9; }
  .nav-indicator-pill {
    font-size: 11.5px; font-weight: 700; color: #0F172A; min-width: 80px; text-align: center;
  }

  .tool-right { display: flex; align-items: center; gap: 10px; font-family: sans-serif; }
  .stat-pill {
    font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 999px;
    display: inline-flex; align-items: center; gap: 4px; border: 1px solid transparent;
  }
  .stat-pill.danger { background: #FEE2E2; color: #991B1B; border-color: #FECACA; }
  .stat-pill.missing { background: #FEF3C7; color: #92400E; border-color: #FDE68A; }
  .stat-pill.warning { background: #FFFBEB; color: #B45309; border-color: #FDE68A; }
  .stat-pill.progress { background: #F0FDF4; color: #15803D; border-color: #BBF7D0; }

  .btn-toggle-sidebar {
    background: #1E293B; color: #FFF; border: 1px solid #1E293B;
    padding: 3px 10px; border-radius: 6px; font-size: 11.5px; font-weight: 600;
    cursor: pointer; display: flex; align-items: center; gap: 6px; font-family: sans-serif;
  }
  .btn-toggle-sidebar:hover { background: #334155; }
  .sidebar-badge {
    background: #EF4444; color: #FFF; font-size: 10px; padding: 0 5px;
    border-radius: 999px; font-weight: 800;
  }

  /* ==================== 3. 核心工作区：三栏布局 (左母本 / 中送审 / 右详情自适应) ==================== */
  .review-workspace {
    display: grid;
    grid-template-columns: 1fr 1fr minmax(320px, 420px);
    height: calc(100vh - 88px);
    width: 100%;
    overflow: hidden;
    position: relative;
    background: #E2E8F0;
    transition: grid-template-columns 0.25s ease;
  }
  .review-workspace.sidebar-collapsed {
    grid-template-columns: 1fr 1fr 0px !important;
  }
  .review-workspace.sidebar-collapsed .annotations-sidebar-panel {
    display: none !important;
  }
  .mode-single .review-workspace {
    grid-template-columns: 0px 1fr minmax(320px, 420px);
  }
  .mode-single #colStandardPane {
    display: none !important;
  }

  /* 独立列面板 */
  .contract-column {
    display: flex;
    flex-direction: column;
    height: 100%;
    max-height: 100%;
    min-height: 0;
    overflow: hidden;
    min-width: 0;
    position: relative;
    border-right: 1px solid #CBD5E1;
    background: #E2E8F0;
  }

  .column-sticky-header {
    height: 38px;
    padding: 0 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: #FFFFFF;
    border-bottom: 1px solid #CBD5E1;
    z-index: 10;
    font-size: 12px;
    font-weight: 700;
    font-family: sans-serif;
    flex-shrink: 0;
  }
  .column-sticky-header.standard { border-top: 3px solid #64748B; color: #334155; }
  .column-sticky-header.reviewed { border-top: 3px solid #2563EB; color: #1E40AF; background: #F8FAFC; }
  .col-header-info { display: flex; align-items: center; gap: 6px; }
  .col-header-sub { font-size: 11px; color: #64748B; font-weight: 500; }

  /* 独立滚动视口 */
  .contract-scroll-container {
    flex: 1;
    min-height: 0;
    overflow-y: scroll;
    overflow-x: hidden;
    padding: 24px 28px 80px 28px;
    scroll-behavior: auto;
  }

  /* A4 纸张质感画布 */
  .contract-paper-canvas {
    background: #FFFFFF;
    border-radius: 6px;
    box-shadow: 0 4px 20px rgba(15, 23, 42, 0.08);
    border: 1px solid #CBD5E1;
    padding: 40px 44px;
    min-height: 900px;
    max-width: 780px;
    margin: 0 auto;
  }

  /* ==================== 4. 真实公文原格式排版 (Authentic Contract Typography) ==================== */
  .clause-block {
    position: relative;
    margin-bottom: 10px;
    transition: background 0.2s ease, outline 0.2s ease;
    border-radius: 4px;
    cursor: pointer;
  }
  .clause-block.active-clause {
    background: #FEF9C3 !important;
    outline: 2px solid #EAB308;
  }

  .contract-doc-title {
    font-size: 20px;
    font-weight: 700;
    text-align: center;
    margin: 10px 0 6px 0;
    letter-spacing: 1px;
    color: #0F172A;
  }
  .contract-doc-subtitle {
    font-size: 13.5px;
    text-align: center;
    color: #64748B;
    margin-bottom: 14px;
  }
  .contract-doc-meta {
    font-size: 12px;
    text-align: right;
    color: #475569;
    margin-bottom: 18px;
    font-family: monospace;
  }
  .contract-doc-parties {
    font-size: 13.5px;
    line-height: 1.8;
    color: #1E293B;
    margin-bottom: 8px;
  }
  .contract-doc-preamble {
    font-size: 13.5px;
    line-height: 1.85;
    text-indent: 2em;
    color: #1E293B;
    margin-bottom: 12px;
  }
  .contract-article-heading {
    font-size: 14.5px;
    font-weight: 700;
    color: #0F172A;
    margin: 16px 0 6px 0;
    border-left: 3px solid #2563EB;
    padding-left: 8px;
  }
  .contract-doc-p {
    font-size: 13.5px;
    line-height: 1.85;
    text-indent: 2em;
    text-align: justify;
    color: #1E293B;
    margin-bottom: 6px;
  }
  .contract-doc-signatures {
    margin-top: 24px;
    padding-top: 16px;
    border-top: 1px dashed #CBD5E1;
    font-size: 13px;
    line-height: 1.9;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
  }

  /* ==================== 5. 纯净增删标红（彻底去除正文内部密集小药丸徽标） ==================== */
  .diff-del {
    color: #DC2626;
    text-decoration: line-through 1.5px #DC2626;
    background: #FEF2F2;
    padding: 0 2px;
    border-radius: 2px;
  }
  .diff-ins {
    color: #2563EB;
    text-decoration: underline 1.5px #2563EB;
    font-weight: 600;
    background: #EFF6FF;
    padding: 0 2px;
    border-radius: 2px;
  }
  .diff-ins-number {
    color: #92400E;
    text-decoration: underline 2px #D97706;
    font-weight: 700;
    background: #FEF3C7;
    padding: 0 3px;
    border-radius: 3px;
  }
  .mode-color-redgreen .diff-ins {
    color: #15803D;
    text-decoration: underline 1.5px #15803D;
    background: #F0FDF4;
  }

  /* 整段删除（母本端红删除线流） */
  .doc-text-deleted {
    background: #FFF5F5;
    border-left: 3px solid #F87171;
    padding: 8px 12px;
    border-radius: 4px;
    line-height: 1.85;
    text-indent: 2em;
  }
  .diff-del-flow {
    color: #7F1D1D;
    text-decoration: line-through 1.5px #DC2626;
    white-space: pre-wrap;
  }

  /* 送审稿删除本条款后的平齐占位槽（极简干净，无大红感叹号框） */
  .doc-slot-deleted {
    background: #FAF8F8;
    border: 1px dashed #CBD5E1;
    border-radius: 4px;
    padding: 8px 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    color: #94A3B8;
    transition: all 0.15s ease;
  }
  .doc-slot-deleted:hover {
    background: #FEF2F2;
    border-color: #F87171;
    color: #DC2626;
  }
  .slot-placeholder-text { font-family: sans-serif; font-style: italic; }

  /* 母本无此条款的平齐占位槽 */
  .doc-slot-blank {
    background: #FAFAFA;
    border: 1px dashed #E2E8F0;
    border-radius: 4px;
    padding: 8px 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    color: #94A3B8;
  }

  /* 送审稿新增整段非标条款 */
  .doc-text-added {
    background: #FAF5FF;
    border-left: 3px solid #7C3AED;
    padding: 8px 12px;
    border-radius: 4px;
    line-height: 1.85;
    text-indent: 2em;
  }
  .diff-ins-flow {
    color: #4C1D95;
    text-decoration: underline 1.5px #7C3AED;
    white-space: pre-wrap;
  }

  /* ==================== 6. 右侧法务审阅详情与批注专栏 (自适应) ==================== */
  .annotations-sidebar-panel {
    background: #FFFFFF;
    border-left: 1px solid var(--border-light);
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
    z-index: 85;
    font-family: sans-serif;
  }
  .sidebar-header {
    padding: 10px 14px;
    border-bottom: 1px solid var(--border-light);
    background: #F8FAFC;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-shrink: 0;
  }
  .sidebar-title {
    font-size: 13px; font-weight: 700; color: #1E293B; display: flex; align-items: center; gap: 6px;
  }
  .sidebar-count-badge {
    background: #1E293B; color: #FFF; font-size: 10.5px; padding: 1px 6px; border-radius: 999px; font-weight: 800;
  }
  .mini-tool-btn {
    font-size: 11px; font-weight: 600; padding: 3px 6px; border-radius: 4px;
    border: 1px solid var(--border-light); background: #FFF; color: #475569; cursor: pointer;
  }
  .mini-tool-btn:hover { background: #F1F5F9; color: #0F172A; }

  .sidebar-filter-strip {
    padding: 6px 10px;
    border-bottom: 1px solid #F1F5F9;
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
    background: #FFFFFF;
    flex-shrink: 0;
  }
  .filter-chip {
    padding: 2px 7px;
    border-radius: 999px;
    border: 1px solid var(--border-light);
    background: #FFF;
    font-size: 10.5px;
    font-weight: 600;
    color: #475569;
    cursor: pointer;
    transition: all 0.15s ease;
    white-space: nowrap;
  }
  .filter-chip.active {
    background: #1E293B;
    color: #FFF;
    border-color: #1E293B;
  }

  .annotations-list {
    overflow-y: scroll;
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    flex: 1;
    scroll-behavior: auto;
  }

  /* 独立审阅批注卡片 */
  .annotation-card {
    background: #FFFFFF;
    border: 1px solid var(--border-light);
    border-radius: 6px;
    padding: 10px 12px;
    cursor: pointer;
    transition: all 0.15s ease;
    box-shadow: 0 1px 2px rgba(0,0,0,0.02);
    position: relative;
  }
  .annotation-card:hover {
    border-color: var(--primary);
    box-shadow: 0 2px 6px rgba(37,99,235,0.08);
  }
  .annotation-card.active {
    border-color: var(--primary);
    box-shadow: 0 0 0 2px rgba(37,99,235,0.3);
    background: #FAFCFF;
  }
  .annotation-card.status-ACCEPTED { border-left: 4px solid #16A34A; background: #F0FDF4; }
  .annotation-card.status-QUESTIONED { border-left: 4px solid #D97706; background: #FFFBEB; }
  .annotation-card.status-OVERRULED { border-left: 4px solid #94A3B8; background: #F8FAFC; opacity: 0.88; }

  .card-top {
    display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 4px;
  }
  .card-index {
    font-size: 10.5px; font-weight: 800; color: #64748B; background: #F1F5F9;
    padding: 1px 5px; border-radius: 3px;
  }
  .card-risk-pill {
    font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 4px;
  }
  .card-risk-pill.HIGH { background: #FEE2E2; color: #991B1B; }
  .card-risk-pill.MEDIUM { background: #FEF3C7; color: #92400E; }
  .card-risk-pill.LOW { background: #F1F5F9; color: #475569; }

  .card-clause-name {
    font-size: 12px; font-weight: 700; color: #0F172A; margin-bottom: 4px;
  }

  .card-comparison-box {
    background: #F8FAFC;
    border-radius: 4px;
    padding: 6px 8px;
    font-size: 11px;
    margin-bottom: 6px;
    border: 1px solid #EDF2F7;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .comp-row { display: flex; gap: 6px; }
  .comp-label { font-weight: 700; color: #64748B; min-width: 44px; flex-shrink: 0; }
  .comp-text { color: #1E293B; word-break: break-all; }
  .comp-text.del { color: #991B1B; text-decoration: line-through; }
  .comp-text.ins { color: #15803D; font-weight: 600; }

  .card-analysis {
    font-size: 11.5px; color: #334155; margin-bottom: 4px; line-height: 1.45;
  }
  .card-advice {
    font-size: 11px; color: #1E40AF; background: #EFF6FF; border-left: 2px solid var(--primary);
    padding: 4px 6px; border-radius: 0 3px 3px 0; margin-bottom: 6px; line-height: 1.4;
  }

  .card-actions {
    display: flex; justify-content: space-between; align-items: center; border-top: 1px solid #F1F5F9;
    padding-top: 5px; margin-top: 2px;
  }
  .card-action-btn-group { display: flex; gap: 3px; }
  .mini-btn {
    padding: 2px 6px; border-radius: 3px; font-size: 10.5px; font-weight: 600;
    border: 1px solid var(--border-light); background: #FFF; color: #475569; cursor: pointer;
  }
  .mini-btn:hover { background: #F1F5F9; color: #0F172A; }
  .mini-btn.active-accept { background: #DCFCE7; color: #15803D; border-color: #86EFAC; }
  .mini-btn.active-question { background: #FEF3C7; color: #92400E; border-color: #FCD34D; }
  .mini-btn.active-overrule { background: #F1F5F9; color: #64748B; border-color: #CBD5E1; }

  .locate-btn {
    font-size: 10.5px; font-weight: 600; color: var(--primary); border: none; background: transparent;
    cursor: pointer; display: flex; align-items: center; gap: 2px;
  }
  .locate-btn:hover { text-decoration: underline; }

  /* ==================== 7. 上传模态窗 (DOCX/DOC/PDF) ==================== */
  .modal-overlay {
    position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
    background: rgba(15, 23, 42, 0.45); backdrop-filter: blur(2px);
    z-index: 1000; display: none; align-items: center; justify-content: center;
    font-family: sans-serif;
  }
  .modal-overlay.open { display: flex; }
  .modal-card {
    background: #FFFFFF; border-radius: 12px; width: 560px; max-width: 90vw;
    box-shadow: 0 20px 40px rgba(0,0,0,0.18); overflow: hidden;
  }
  .modal-header {
    padding: 14px 18px; border-bottom: 1px solid var(--border-light);
    display: flex; justify-content: space-between; align-items: center; background: #F8FAFC;
  }
  .modal-title { font-size: 14px; font-weight: 700; color: #0F172A; }
  .modal-close-btn {
    background: transparent; border: none; font-size: 16px; cursor: pointer; color: #64748B;
  }
  .modal-body { padding: 18px; display: flex; flex-direction: column; gap: 14px; }

  .drop-zone {
    border: 2px dashed #CBD5E1; border-radius: 8px; padding: 24px;
    text-align: center; cursor: pointer; background: #F8FAFC; transition: all 0.2s ease;
  }
  .drop-zone:hover, .drop-zone.dragover {
    border-color: var(--primary); background: var(--primary-light);
  }
  .drop-icon { font-size: 28px; margin-bottom: 4px; }
  .drop-title { font-size: 13.5px; font-weight: 700; color: #1E293B; }
  .drop-desc { font-size: 11px; color: var(--text-muted); margin-top: 2px; }

  .preset-grid { display: flex; flex-direction: column; gap: 6px; }
  .preset-btn {
    padding: 8px 12px; border-radius: 6px; border: 1px solid var(--border-light);
    background: #F8FAFC; color: #334155; font-size: 12px; text-align: left;
    cursor: pointer; transition: all 0.15s ease; display: flex;
    justify-content: space-between; align-items: center;
  }
  .preset-btn:hover { border-color: var(--primary); background: #EFF6FF; color: var(--primary); }

  /* 打印样式适配 */
  @media print {
    .header, .toolbar-strip, .annotations-sidebar-panel, .modal-overlay, .column-sticky-header { display: none !important; }
    html, body { height: auto; overflow: visible; background: #FFF; }
    .review-workspace { display: block; height: auto; }
    .contract-scroll-container { padding: 0; overflow: visible; }
    .contract-paper-canvas { box-shadow: none; border: none; padding: 0; }
  }
</style>
</head>
<body class="mode-pure-redline">

<!-- 顶部 48px 全局精炼导航栏 -->
<header class="header">
  <div class="brand">
    <div class="brand-logo">Δ</div>
    <span class="brand-title">AI 合同审查 · 条款级 Diff 与整版原格式比对工作台</span>
  </div>

  <div class="header-meta-group">
    <span class="meta-tag matched" id="matchedTemplateTag">🎯 基准母本: 国内专利服务合同（年费缴纳）</span>
    <span class="meta-tag doc-title" id="docFileName">《国内专利服务合同》送审修改稿</span>
  </div>

  <div class="header-actions">
    <button class="btn" onclick="openUploadModal()">📂 上传送审合同 (DOCX/DOC/PDF)</button>
    <button class="btn btn-primary" onclick="window.print()">🖨️ 导出对比单</button>
  </div>
</header>

<!-- 紧凑型 40px 工具与审阅导航条 -->
<div class="toolbar-strip">
  <div class="tool-left">
    <!-- 视图模式切换 -->
    <div class="segmented-control">
      <button class="seg-btn active" id="btnModeSide" onclick="setViewMode('side')">📖 原格式双栏对照</button>
      <button class="seg-btn" id="btnModeSingle" onclick="setViewMode('single')">📄 单版标红审阅</button>
      <button class="seg-btn" id="btnModeDiff" onclick="setViewMode('diff')">🎯 仅看变更条款</button>
    </div>

    <!-- 标红风格切换 -->
    <div class="segmented-control">
      <button class="seg-btn active" id="btnStyleRedline" onclick="setRedlineMode(true)">🔴 纯标红划线</button>
      <button class="seg-btn" id="btnStyleColor" onclick="setRedlineMode(false)">🎨 红绿对照</button>
    </div>

    <!-- 快速导航 -->
    <div class="nav-group">
      <button class="nav-arrow-btn" onclick="prevAnnotation()" title="快捷键: [ 或 Alt+↑">◀ 上一处</button>
      <span class="nav-indicator-pill" id="navIndicator">批注 1 / 0</span>
      <button class="nav-arrow-btn" onclick="nextAnnotation()" title="快捷键: ] 或 Alt+↓">下一处 ▶</button>
    </div>
  </div>

  <div class="tool-right">
    <!-- 核心统计徽章 -->
    <span class="stat-pill danger">🔴 高危篡改 <b id="statHighRisk">0</b></span>
    <span class="stat-pill missing">🚩 核心缺失 <b id="statMissingClauses">0</b></span>
    <span class="stat-pill warning">🟠 期限/违约 <b id="statMediumRisk">0</b></span>
    <span class="stat-pill progress">⚖️ 核验进度 <b id="statProgress">0/0</b></span>

    <button class="btn-toggle-sidebar" id="btnToggleSidebar" onclick="toggleSidebarCollapse()">
      <span>📋 详情专栏</span>
      <span class="sidebar-badge" id="btnSidebarCount">0</span>
    </button>
  </div>
</div>

<!-- ==================== 核心工作区：三栏独立视口与双向联动 ==================== -->
<div class="review-workspace" id="reviewWorkspace">

  <!-- 1. 左栏：标准参考母本（基准条款） -->
  <section class="contract-column pane-standard" id="colStandardPane">
    <div class="column-sticky-header standard">
      <div class="col-header-info">
        <span class="col-icon">🏛️</span>
        <span class="col-header-title">标准参考母本（基准正文）</span>
      </div>
      <span class="col-header-sub" id="headerStdName">母本库</span>
    </div>
    <div class="contract-scroll-container" id="colStandardContainer">
      <div class="contract-paper-canvas" id="paperStandard"></div>
    </div>
  </section>

  <!-- 2. 中栏：送审修改稿（增删标红） -->
  <section class="contract-column pane-reviewed" id="colReviewedPane">
    <div class="column-sticky-header reviewed">
      <div class="col-header-info">
        <span class="col-icon">✏️</span>
        <span class="col-header-title">送审合同（待审修改稿·增删对比）</span>
      </div>
      <span class="col-header-sub" id="headerRevName">待审稿</span>
    </div>
    <div class="contract-scroll-container" id="colReviewedContainer">
      <div class="contract-paper-canvas" id="paperReviewed"></div>
    </div>
  </section>

  <!-- 3. 右栏：法务审阅详情与批注专栏 (自适应) -->
  <aside class="annotations-sidebar-panel" id="annotationsSidebar">
    <div class="sidebar-header">
      <div class="sidebar-title">
        <span>📋 条款变动与法务详情</span>
        <span class="sidebar-count-badge" id="sidebarCount">0</span>
      </div>
      <button class="mini-tool-btn" onclick="toggleSidebarCollapse()" title="收起详情专栏">◀ 收起</button>
    </div>

    <!-- 分类筛选标签 -->
    <div class="sidebar-filter-strip">
      <button class="filter-chip active" onclick="setFilter('ALL')">全部 (<span id="filterAllCount">0</span>)</button>
      <button class="filter-chip" onclick="setFilter('HIGH')">🔴 高危 (<span id="filterHighCount">0</span>)</button>
      <button class="filter-chip" onclick="setFilter('MISSING')">🚩 缺失 (<span id="filterMissingCount">0</span>)</button>
      <button class="filter-chip" onclick="setFilter('NEW')">➕ 新增 (<span id="filterNewCount">0</span>)</button>
      <button class="filter-chip" onclick="setFilter('WARNING')">🟠 违约/期限 (<span id="filterWarnCount">0</span>)</button>
    </div>

    <div class="annotations-list" id="annotationsList"></div>
  </aside>

</div>

<!-- 模态弹窗：上传送审合同 (DOCX/DOC/PDF) 与快速加载实测用例 -->
<div class="modal-overlay" id="uploadModal" onclick="closeUploadModalOnBackdrop(event)">
  <div class="modal-card">
    <div class="modal-header">
      <span class="modal-title">📂 上传送审合同 (DOCX/DOC/PDF) / 快速加载用例</span>
      <button class="modal-close-btn" onclick="closeUploadModal()">✕</button>
    </div>
    <div class="modal-body">
      <div class="drop-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
        <input type="file" id="fileInput" accept=".docx,.doc,.pdf" style="display:none;" onchange="handleFileSelect(event)">
        <div class="drop-icon">📄</div>
        <div class="drop-title">点击或拖拽 DOCX / DOC / PDF 送审合同至此</div>
        <div class="drop-desc">自动高保真结构解析并秒级对齐母本库，出具原格式增删比对</div>
      </div>

      <div style="font-size:12px; font-weight:700; color:#475569; margin-top:6px;">⚡ 快速加载实测高危用例：</div>
      <div class="preset-grid">
        <button class="preset-btn" onclick="loadSample('prescreen_tampered'); closeUploadModal();">
          <span>🚨 快速预审 (违约金日息从 3‰ 偷降到 1‰)</span>
          <span style="font-size:10.5px; font-weight:700; background:#FEE2E2; color:#991B1B; padding:1px 6px; border-radius:3px;">高危篡改</span>
        </button>
        <button class="preset-btn" onclick="loadSample('missing_confidential'); closeUploadModal();">
          <span>🚩 专利代理 (保密条款与核心权利义务被偷删)</span>
          <span style="font-size:10.5px; font-weight:700; background:#FEF3C7; color:#92400E; padding:1px 6px; border-radius:3px;">核心缺失</span>
        </button>
        <button class="preset-btn" onclick="loadSample('trademark_match'); closeUploadModal();">
          <span>✅ 商标驳回复审 (合规原版基准)</span>
          <span style="font-size:10.5px; font-weight:700; background:#DCFCE7; color:#15803D; padding:1px 6px; border-radius:3px;">100%匹配</span>
        </button>
      </div>
    </div>
  </div>
</div>

<script>
// 全局状态管理
let currentData = null;
let currentViewMode = 'side'; // 'side' | 'single' | 'diff'
let currentFilter = 'ALL';
let activeAnnotationIndex = -1;

// 预置默认数据占位符
window.INITIAL_DATA = __INITIAL_DATA_PLACEHOLDER__;

// 模态弹窗控制
function openUploadModal() {
  document.getElementById('uploadModal').classList.add('open');
}
function closeUploadModal() {
  document.getElementById('uploadModal').classList.remove('open');
}
function closeUploadModalOnBackdrop(e) {
  if (e.target.id === 'uploadModal') closeUploadModal();
}

// 拖拽上传支持 (DOCX / DOC / PDF)
const dropZone = document.getElementById('dropZone');
dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  if (e.dataTransfer.files.length > 0) {
    uploadContractFile(e.dataTransfer.files[0]);
    closeUploadModal();
  }
});

function handleFileSelect(e) {
  if (e.target.files.length > 0) {
    uploadContractFile(e.target.files[0]);
    closeUploadModal();
  }
}

async function uploadContractFile(file) {
  const formData = new FormData();
  formData.append('file', file);

  try {
    const resp = await fetch('/api/diff/upload', { method: 'POST', body: formData });
    const res = await resp.json();
    if (res.error) {
      alert('上传解析失败: ' + res.error);
      return;
    }
    renderFullDiff(res);
  } catch (err) {
    alert('请求失败，请确保后台 Web 服务正常启动: ' + err);
  }
}

async function loadSample(sampleKey) {
  try {
    const resp = await fetch('/api/diff/sample?key=' + sampleKey);
    const res = await resp.json();
    renderFullDiff(res);
  } catch (err) {
    alert('加载实测用例失败: ' + err);
  }
}

// 格式判定：识别正文节点原排版角色
function detectBlockCategory(title, text, idx) {
  const cleaned = (text || '').trim();
  if (idx === 0 && (cleaned.includes('合同') || cleaned.includes('协议'))) {
    return 'TITLE';
  }
  if (idx === 1 && (cleaned.startsWith('(') || cleaned.startsWith('（') || cleaned.includes('年费') || cleaned.includes('委托') || cleaned.includes('申请'))) {
    return 'SUBTITLE';
  }
  if (cleaned.startsWith('合同编号') || cleaned.startsWith('编号:') || cleaned.startsWith('协议编号')) {
    return 'META';
  }
  if (title === '双方主体与基本信息' || (cleaned.includes('甲方') && cleaned.includes('乙方') && (cleaned.includes('地址') || cleaned.includes('法定代表人') || cleaned.includes('统一社会信用代码')))) {
    return 'PARTIES';
  }
  if (cleaned.includes('达成如下协议') || cleaned.includes('鉴于') || cleaned.includes('特订立本合同')) {
    return 'PREAMBLE';
  }
  if (/^第[一二三四五六七八九十百]+条/.test(cleaned)) {
    const lines = cleaned.split('\n').map(l => l.trim()).filter(Boolean);
    if (lines.length === 1 && cleaned.length < 35) {
      return 'ARTICLE_HEADING';
    }
    return 'ARTICLE_SECTION';
  }
  if (cleaned.includes('（以下无正文）') || (cleaned.includes('甲方') && cleaned.includes('乙方') && (cleaned.includes('盖章') || cleaned.includes('签字')))) {
    return 'SIGNATURE';
  }
  if (/^\d+\.\d+/.test(cleaned)) {
    return 'SUBCLAUSE';
  }
  return 'PARAGRAPH';
}

// 格式化输出为纯正合同排版 HTML
function formatContractHtml(html, rawText, category) {
  if (!html) return '<div class="empty-placeholder"></div>';

  if (html.includes('doc-text-deleted') || html.includes('doc-slot-deleted') || html.includes('doc-slot-blank') || html.includes('doc-text-added')) {
    return html;
  }

  if (category === 'ARTICLE_SECTION') {
    const headingMatch = html.match(/^(<div class="[^"]*">)?(第[一二三四五六七八九十百]+条[^\n<]*)\n([\s\S]*)$/);
    if (headingMatch) {
      const prefix = headingMatch[1] || '';
      const headingText = headingMatch[2];
      const bodyText = headingMatch[3];
      return `${prefix}<div class="contract-article-heading">${headingText}</div><div class="contract-doc-p">${bodyText}</div>${prefix ? '</div>' : ''}`;
    }
    return `<div class="contract-article-section">${html}</div>`;
  }

  if (category === 'TITLE') {
    return `<h1 class="contract-doc-title">${html}</h1>`;
  }
  if (category === 'SUBTITLE') {
    return `<div class="contract-doc-subtitle">${html}</div>`;
  }
  if (category === 'META') {
    return `<div class="contract-doc-meta">${html}</div>`;
  }
  if (category === 'PARTIES') {
    return `<div class="contract-doc-parties">${html}</div>`;
  }
  if (category === 'PREAMBLE') {
    return `<div class="contract-doc-preamble">${html}</div>`;
  }
  if (category === 'ARTICLE_HEADING') {
    return `<div class="contract-article-heading">${html}</div>`;
  }
  if (category === 'SIGNATURE') {
    return `<div class="contract-doc-signatures">${html}</div>`;
  }

  return `<div class="contract-doc-p">${html}</div>`;
}

function escapeHtml(text) {
  if (!text) return '';
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// 渲染整版原格式比对与批注
function renderFullDiff(data) {
  currentData = data;
  activeAnnotationIndex = -1;

  // 1. 顶部标题与锁定母本
  const rawFileName = (data.uploaded_file || '待审合同.docx').split(/[\/\\]/).pop();
  let friendlyDocTitle = rawFileName;
  if (rawFileName.includes('mut_11_late_fee_change')) {
    friendlyDocTitle = '《国内专利服务合同（年费缴纳）》送审修改稿';
  } else if (rawFileName.includes('mut_06_missing_confidentiality')) {
    friendlyDocTitle = '《国内专利服务合同（年费缴纳）》送审修改稿（保密义务被删）';
  } else if (rawFileName.includes('驳回复审')) {
    friendlyDocTitle = '《商标驳回复审代理协议》送审原稿';
  }

  document.getElementById('docFileName').innerText = friendlyDocTitle;

  const tplFileName = data.matched_template_name || '标准母本.docx';
  const tplCleanName = tplFileName.replace(/\.(docx?|pdf)$/i, '');
  const matchedTagEl = document.getElementById('matchedTemplateTag');
  matchedTagEl.innerText = '🎯 基准母本: ' + tplCleanName;
  matchedTagEl.title = `基准文档: ${tplFileName} (底层版本ID: ${data.matched_template_id || '-'})`;

  // 双栏纸张列标题
  document.getElementById('headerStdName').innerText = tplCleanName;
  document.getElementById('headerRevName').innerText = friendlyDocTitle;

  // 统计数据
  const stats = data.stats || {};
  const annCount = (data.annotations || []).length;
  document.getElementById('statHighRisk').innerText = stats.high_risk || 0;
  document.getElementById('statMissingClauses').innerText = stats.missing_clauses || 0;
  document.getElementById('statMediumRisk').innerText = (stats.medium_risk || 0);

  document.getElementById('sidebarCount').innerText = annCount;
  const btnSidebarCountEl = document.getElementById('btnSidebarCount');
  if (btnSidebarCountEl) btnSidebarCountEl.innerText = annCount;

  // 过滤器数值
  const anns = data.annotations || [];
  document.getElementById('filterAllCount').innerText = anns.length;
  document.getElementById('filterHighCount').innerText = anns.filter(a => a.risk_level === 'HIGH' && a.delta_type !== 'MISSING_CLAUSE').length;
  document.getElementById('filterMissingCount').innerText = anns.filter(a => a.delta_type === 'MISSING_CLAUSE').length;
  document.getElementById('filterNewCount').innerText = anns.filter(a => a.delta_type === 'NEW_CLAUSE').length;
  document.getElementById('filterWarnCount').innerText = anns.filter(a => a.risk_level === 'MEDIUM' && a.delta_type !== 'NEW_CLAUSE').length;

  updateProgressDisplay();
  renderContractColumns();
  renderAnnotationsSidebar();

  // 双栏渲染完毕后，同步左右每一行条款的高度，确保上下滑动严格对齐
  requestAnimationFrame(() => {
    syncClauseHeights();
    setupThreeWaySyncScroll();
  });
}

function updateProgressDisplay() {
  if (!currentData || !currentData.annotations) return;
  const total = currentData.annotations.length;
  const handled = currentData.annotations.filter(a => a.review_status && a.review_status !== 'PENDING').length;
  document.getElementById('statProgress').innerText = `${handled}/${total}`;
}

// 核心渲染：左右双栏分别渲染为完整连续 A4 纸张页面
function renderContractColumns() {
  const paperStd = document.getElementById('paperStandard');
  const paperRev = document.getElementById('paperReviewed');
  paperStd.innerHTML = '';
  paperRev.innerHTML = '';

  const blocks = currentData.aligned_blocks || [];
  blocks.forEach((block, idx) => {
    const isMatch = block.status === 'MATCH';
    if (currentViewMode === 'diff' && isMatch) {
      return; // 仅看变更条款模式下跳过完全一致项
    }

    const txt = block.standard_text || block.reviewed_text || '';
    const category = detectBlockCategory(block.clause_title, txt, idx);

    // 左侧母本条款块
    const stdBlock = document.createElement('div');
    stdBlock.className = `clause-block ${block.status.toLowerCase()}`;
    stdBlock.id = `std-${block.block_id}`;
    stdBlock.dataset.blockId = block.block_id;
    if (block.annotation_ids && block.annotation_ids.length > 0) {
      stdBlock.dataset.annId = block.annotation_ids[0];
      stdBlock.onclick = () => selectAnnotation(block.annotation_ids[0]);
    }
    stdBlock.innerHTML = formatContractHtml(block.standard_html || escapeHtml(block.standard_text), block.standard_text, category);
    paperStd.appendChild(stdBlock);

    // 右侧送审条款块
    const revBlock = document.createElement('div');
    revBlock.className = `clause-block ${block.status.toLowerCase()}`;
    revBlock.id = `rev-${block.block_id}`;
    revBlock.dataset.blockId = block.block_id;
    if (block.annotation_ids && block.annotation_ids.length > 0) {
      revBlock.dataset.annId = block.annotation_ids[0];
      revBlock.onclick = () => selectAnnotation(block.annotation_ids[0]);
    }
    revBlock.innerHTML = formatContractHtml(block.reviewed_html || escapeHtml(block.reviewed_text), block.reviewed_text, category);
    paperRev.appendChild(revBlock);
  });
}

// 左右条款高度等高自适应平衡算法（保证上下滑动左右始终绝对对齐）
function syncClauseHeights() {
  if (!currentData || !currentData.aligned_blocks) return;
  const blocks = currentData.aligned_blocks;

  // 1. 重置初始高度
  blocks.forEach(b => {
    const s = document.getElementById(`std-${b.block_id}`);
    const r = document.getElementById(`rev-${b.block_id}`);
    if (s) s.style.minHeight = '0px';
    if (r) r.style.minHeight = '0px';
  });

  // 2. 测量并设置成两边最大值
  blocks.forEach(b => {
    const s = document.getElementById(`std-${b.block_id}`);
    const r = document.getElementById(`rev-${b.block_id}`);
    if (s && r) {
      const h = Math.max(s.getBoundingClientRect().height, r.getBoundingClientRect().height);
      s.style.minHeight = `${h}px`;
      r.style.minHeight = `${h}px`;
    }
  });
}

window.addEventListener('resize', () => {
  syncClauseHeights();
});

// ==================== 核心：三栏双向协同联动滚动 (Three-Way Sync Scrolling) ====================
let activeDriver = null; // 'std' | 'rev' | 'detail' | null
let driverReleaseTimer = null;
let syncListenerAttached = false;

function claimDriver(source) {
  if (!activeDriver) {
    activeDriver = source;
  }
  clearTimeout(driverReleaseTimer);
  driverReleaseTimer = setTimeout(() => {
    activeDriver = null;
  }, 140);
}

function setupThreeWaySyncScroll() {
  if (syncListenerAttached) return;
  syncListenerAttached = true;

  const colStd = document.getElementById('colStandardContainer');
  const colRev = document.getElementById('colReviewedContainer');
  const colDetail = document.getElementById('annotationsList');

  // 提前通过鼠标手势/触摸捕获滚动主控权，杜绝死循环
  colStd.addEventListener('wheel', () => claimDriver('std'), { passive: true });
  colStd.addEventListener('touchstart', () => claimDriver('std'), { passive: true });
  colRev.addEventListener('wheel', () => claimDriver('rev'), { passive: true });
  colRev.addEventListener('touchstart', () => claimDriver('rev'), { passive: true });
  colDetail.addEventListener('wheel', () => claimDriver('detail'), { passive: true });
  colDetail.addEventListener('touchstart', () => claimDriver('detail'), { passive: true });

  // 1. 左栏（母本）滑动 ➔ 驱动中栏（送审）完全平齐，且驱动右栏（详情）对齐对应条款
  colStd.addEventListener('scroll', () => {
    if (activeDriver && activeDriver !== 'std') return;
    claimDriver('std');

    // 中栏等距跟随
    colRev.scrollTop = colStd.scrollTop;

    // 驱动右侧详情专栏
    syncRightDetailFromDoc(colStd.scrollTop);
  }, { passive: true });

  // 2. 中栏（送审）滑动 ➔ 驱动左栏（母本）完全平齐，且驱动右栏（详情）对齐对应条款
  colRev.addEventListener('scroll', () => {
    if (activeDriver && activeDriver !== 'rev') return;
    claimDriver('rev');

    // 左栏等距跟随
    colStd.scrollTop = colRev.scrollTop;

    // 驱动右侧详情专栏
    syncRightDetailFromDoc(colRev.scrollTop);
  }, { passive: true });

  // 3. 右栏（详情专栏）滑动 ➔ 驱动左栏与中栏同步居中到该条款
  colDetail.addEventListener('scroll', () => {
    if (activeDriver && activeDriver !== 'detail') return;
    claimDriver('detail');

    syncDocFromRightDetail();
  }, { passive: true });
}

// 文档滚动时同步右侧详情专栏可见卡片
function syncRightDetailFromDoc(scrollTop) {
  if (!currentData || !currentData.aligned_blocks) return;
  const blocks = currentData.aligned_blocks;
  const colDetail = document.getElementById('annotationsList');

  // 寻找视口顶部最靠前的条款块
  let visibleBlock = null;
  for (let i = 0; i < blocks.length; i++) {
    const el = document.getElementById(`std-${blocks[i].block_id}`) || document.getElementById(`rev-${blocks[i].block_id}`);
    if (el) {
      if (el.offsetTop >= scrollTop - 20) {
        visibleBlock = blocks[i];
        break;
      }
    }
  }
  if (!visibleBlock && blocks.length > 0) {
    visibleBlock = blocks[blocks.length - 1];
  }

  if (visibleBlock && visibleBlock.annotation_ids && visibleBlock.annotation_ids.length > 0) {
    const annId = visibleBlock.annotation_ids[0];
    const card = document.getElementById(`card-${annId}`);
    if (card) {
      document.querySelectorAll('.annotation-card').forEach(c => c.classList.remove('active'));
      card.classList.add('active');

      const containerRect = colDetail.getBoundingClientRect();
      const cardRect = card.getBoundingClientRect();
      if (cardRect.top < containerRect.top || cardRect.bottom > containerRect.bottom) {
        card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    }
  }
}

// 右侧详情专栏滑动时驱动文档左右两栏对齐该条款
function syncDocFromRightDetail() {
  const colDetail = document.getElementById('annotationsList');
  const cards = colDetail.querySelectorAll('.annotation-card');
  if (cards.length === 0) return;

  const detailCenter = colDetail.scrollTop + (colDetail.clientHeight / 2);
  let closestCard = null;
  let minDiff = 999999;

  cards.forEach(card => {
    const cardCenter = card.offsetTop + (card.offsetHeight / 2);
    const diff = Math.abs(cardCenter - detailCenter);
    if (diff < minDiff) {
      minDiff = diff;
      closestCard = card;
    }
  });

  if (closestCard) {
    const annId = closestCard.id.replace('card-', '');
    const ann = (currentData.annotations || []).find(a => a.id === annId);
    if (ann && ann.block_id) {
      const targetEl = document.getElementById(`rev-${ann.block_id}`) || document.getElementById(`std-${ann.block_id}`);
      if (targetEl) {
        const targetTop = Math.max(0, targetEl.offsetTop - 30);
        document.getElementById('colStandardContainer').scrollTop = targetTop;
        document.getElementById('colReviewedContainer').scrollTop = targetTop;
      }
    }
  }
}

// 渲染右侧法务审阅批注专栏卡片列表
function renderAnnotationsSidebar() {
  const container = document.getElementById('annotationsList');
  container.innerHTML = '';

  const anns = currentData.annotations || [];
  anns.forEach((ann) => {
    // 批注类型过滤
    if (currentFilter === 'HIGH' && (ann.risk_level !== 'HIGH' || ann.delta_type === 'MISSING_CLAUSE')) return;
    if (currentFilter === 'MISSING' && ann.delta_type !== 'MISSING_CLAUSE') return;
    if (currentFilter === 'NEW' && ann.delta_type !== 'NEW_CLAUSE') return;
    if (currentFilter === 'WARNING' && (ann.risk_level !== 'MEDIUM' || ann.delta_type === 'NEW_CLAUSE')) return;

    const card = document.createElement('div');
    card.className = `annotation-card status-${ann.review_status || 'PENDING'}`;
    card.id = `card-${ann.id}`;
    card.onclick = () => selectAnnotation(ann.id);

    // 对比片段 HTML
    let compHtml = '';
    if (ann.delta_type === 'MISSING_CLAUSE') {
      compHtml = `
        <div class="card-comparison-box">
          <div class="comp-row"><span class="comp-label">母本约定:</span><span class="comp-text">${escapeHtml(ann.standard_span)}</span></div>
          <div class="comp-row"><span class="comp-label">送审稿:</span><span class="comp-text" style="color:#DC2626; font-weight:600;">（整段已被删除）</span></div>
        </div>
      `;
    } else if (ann.delta_type === 'NEW_CLAUSE') {
      compHtml = `
        <div class="card-comparison-box">
          <div class="comp-row"><span class="comp-label">母本约定:</span><span class="comp-text" style="color:#94A3B8;">（母本无此条款）</span></div>
          <div class="comp-row"><span class="comp-label">送审稿:</span><span class="comp-text ins">${escapeHtml(ann.reviewed_span)}</span></div>
        </div>
      `;
    } else {
      const isStdBlank = !ann.standard_span || ann.standard_span === '（空白）' || ann.standard_span === '（空白未填）';
      const stdTextHtml = isStdBlank ? `<span style="color:#94A3B8;">（空白未填）</span>` : `<span class="comp-text del">${escapeHtml(ann.standard_span)}</span>`;
      compHtml = `
        <div class="card-comparison-box">
          <div class="comp-row"><span class="comp-label">母本原文:</span>${stdTextHtml}</div>
          <div class="comp-row"><span class="comp-label">送审修改:</span><span class="comp-text ins">${escapeHtml(ann.reviewed_span || '')}</span></div>
        </div>
      `;
    }

    card.innerHTML = `
      <div class="card-top">
        <span class="card-index">批注 #${ann.index < 10 ? '0' + ann.index : ann.index}</span>
        <span class="card-risk-pill ${ann.risk_level}">${escapeHtml(ann.risk_label)}</span>
      </div>

      <div class="card-clause-name">${escapeHtml(ann.clause_title)}</div>
      ${compHtml}

      <div class="card-analysis">${escapeHtml(ann.ai_analysis)}</div>
      <div class="card-advice">${escapeHtml(ann.action_advice)}</div>

      <div class="card-actions">
        <div class="card-action-btn-group">
          <button class="mini-btn ${ann.review_status === 'ACCEPTED' ? 'active-accept' : ''}" onclick="event.stopPropagation(); setAnnotationStatus('${ann.id}', 'ACCEPTED')">✅ 接受</button>
          <button class="mini-btn ${ann.review_status === 'QUESTIONED' ? 'active-question' : ''}" onclick="event.stopPropagation(); setAnnotationStatus('${ann.id}', 'QUESTIONED')">⚠️ 存疑</button>
          <button class="mini-btn ${ann.review_status === 'OVERRULED' ? 'active-overrule' : ''}" onclick="event.stopPropagation(); setAnnotationStatus('${ann.id}', 'OVERRULED')">❌ 驳回</button>
        </div>
        <button class="locate-btn" onclick="event.stopPropagation(); selectAnnotation('${ann.id}')">🎯 定位正文</button>
      </div>
    `;

    container.appendChild(card);
  });
}

// 选中某个批注并实现三栏精确平滑对齐与高光
function selectAnnotation(annId) {
  if (!currentData || !currentData.annotations) return;

  const annIdx = currentData.annotations.findIndex(a => a.id === annId);
  if (annIdx === -1) return;
  activeAnnotationIndex = annIdx;
  const ann = currentData.annotations[annIdx];

  // 1. 高亮右侧专栏中的对应批注卡片
  document.querySelectorAll('.annotation-card').forEach(c => c.classList.remove('active'));
  const card = document.getElementById(`card-${annId}`);
  if (card) {
    card.classList.add('active');
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  // 2. 定位正文条款段落并播放发光动画
  document.querySelectorAll('.clause-block').forEach(el => el.classList.remove('active-clause'));
  const revBlock = document.getElementById(`rev-${ann.block_id}`);
  const stdBlock = document.getElementById(`std-${ann.block_id}`);

  if (revBlock) revBlock.classList.add('active-clause');
  if (stdBlock) stdBlock.classList.add('active-clause');

  const targetEl = revBlock || stdBlock;
  if (targetEl) {
    const targetTop = Math.max(0, targetEl.offsetTop - 30);
    document.getElementById('colStandardContainer').scrollTo({ top: targetTop, behavior: 'smooth' });
    document.getElementById('colReviewedContainer').scrollTo({ top: targetTop, behavior: 'smooth' });
  }

  // 3. 更新导航指示器
  const total = currentData.annotations.length;
  const ind = document.getElementById('navIndicator');
  if (ind) ind.innerText = `批注 ${ann.index} / ${total}`;
}

function nextAnnotation() {
  if (!currentData || !currentData.annotations || currentData.annotations.length === 0) return;
  let nextIdx = activeAnnotationIndex + 1;
  if (nextIdx >= currentData.annotations.length) nextIdx = 0;
  selectAnnotation(currentData.annotations[nextIdx].id);
}

function prevAnnotation() {
  if (!currentData || !currentData.annotations || currentData.annotations.length === 0) return;
  let prevIdx = activeAnnotationIndex - 1;
  if (prevIdx < 0) prevIdx = currentData.annotations.length - 1;
  selectAnnotation(currentData.annotations[prevIdx].id);
}

function setAnnotationStatus(annId, status) {
  if (!currentData || !currentData.annotations) return;
  const ann = currentData.annotations.find(a => a.id === annId);
  if (!ann) return;
  ann.review_status = status;

  const card = document.getElementById(`card-${annId}`);
  if (card) {
    card.className = `annotation-card status-${status} active`;
    const btns = card.querySelectorAll('.mini-btn');
    btns[0].className = `mini-btn ${status === 'ACCEPTED' ? 'active-accept' : ''}`;
    btns[1].className = `mini-btn ${status === 'QUESTIONED' ? 'active-question' : ''}`;
    btns[2].className = `mini-btn ${status === 'OVERRULED' ? 'active-overrule' : ''}`;
  }
  updateProgressDisplay();
}

function setViewMode(mode) {
  currentViewMode = mode;
  document.getElementById('btnModeSide').classList.toggle('active', mode === 'side');
  document.getElementById('btnModeSingle').classList.toggle('active', mode === 'single');
  document.getElementById('btnModeDiff').classList.toggle('active', mode === 'diff');

  document.body.classList.toggle('mode-single', mode === 'single');
  renderContractColumns();
  syncClauseHeights();
}

function setRedlineMode(isRedlineOnly) {
  document.getElementById('btnStyleRedline').classList.toggle('active', isRedlineOnly);
  document.getElementById('btnStyleColor').classList.toggle('active', !isRedlineOnly);
  document.body.classList.toggle('mode-pure-redline', isRedlineOnly);
  document.body.classList.toggle('mode-color-redgreen', !isRedlineOnly);
}

function setFilter(filter) {
  currentFilter = filter;
  document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
  event.target.classList.add('active');
  renderAnnotationsSidebar();
}

function toggleSidebarCollapse() {
  const ws = document.getElementById('reviewWorkspace');
  ws.classList.toggle('sidebar-collapsed');
}

// 键盘快捷键支持：[ 上一处，] 下一处，Esc 关闭弹窗
window.addEventListener('keydown', (e) => {
  if (e.key === '[' || (e.altKey && e.key === 'ArrowUp')) {
    prevAnnotation();
  } else if (e.key === ']' || (e.altKey && e.key === 'ArrowDown')) {
    nextAnnotation();
  } else if (e.key === 'Escape') {
    closeUploadModal();
  }
});

// 页面初始化加载默认高危用例
window.addEventListener('DOMContentLoaded', () => {
  if (window.INITIAL_DATA) {
    renderFullDiff(window.INITIAL_DATA);
  }
});
</script>
</body>
</html>
"""


class DiffVisualizerHandler(BaseHTTPRequestHandler):
    """处理前端页面与比对 API 请求。"""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path in ("/", "/index.html"):
            html = generate_rendered_html()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif parsed.path == "/api/diff/sample":
            qs = urllib.parse.parse_qs(parsed.query)
            key = qs.get("key", ["prescreen_tampered"])[0]

            try:
                if key == "prescreen_tampered":
                    sample_file = config.GOLDEN_DIR / "mut_11_late_fee_change.docx"
                elif key == "missing_confidential":
                    sample_file = (
                        config.GOLDEN_DIR
                        / "mut_06_missing_confidentiality.docx"
                    )
                else:
                    sample_files = list(
                        config.TEMPLATE_DIR.glob("**/*驳回复审*.docx")
                    )
                    sample_file = (
                        sample_files[0]
                        if sample_files
                        else config.GOLDEN_DIR / "mut_11_late_fee_change.docx"
                    )

                res = build_full_contract_diff(sample_file)
                self.send_response(200)
                self.send_header(
                    "Content-Type", "application/json; charset=utf-8"
                )
                self.end_headers()
                self.wfile.write(
                    json.dumps(res, ensure_ascii=False).encode("utf-8")
                )
            except Exception as e:
                self.send_response(500)
                self.send_header(
                    "Content-Type", "application/json; charset=utf-8"
                )
                self.end_headers()
                self.wfile.write(
                    json.dumps({"error": str(e)}, ensure_ascii=False).encode(
                        "utf-8"
                    )
                )

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/diff/upload":
            ctype = self.headers.get("content-type", "")
            if "multipart/form-data" in ctype and "boundary=" in ctype:
                boundary = ctype.split("boundary=")[-1].strip().encode("utf-8")
                length = int(self.headers.get("content-length", 0))
                body = self.rfile.read(length)
                parts = body.split(b"--" + boundary)
                file_bytes = None
                file_name = "uploaded_contract.docx"
                for part in parts:
                    if b'name="file"' in part:
                        # Extract filename if available
                        fn_m = re.search(rb'filename="([^"]+)"', part)
                        if fn_m:
                            try:
                                file_name = fn_m.group(1).decode("utf-8")
                            except Exception:
                                file_name = fn_m.group(1).decode("latin-1")
                        idx = part.find(b"\r\n\r\n")
                        if idx != -1:
                            file_bytes = part[idx + 4 :]
                            if file_bytes.endswith(b"\r\n"):
                                file_bytes = file_bytes[:-2]
                            break

                if file_bytes:
                    suffix = Path(file_name).suffix or ".docx"
                    with tempfile.NamedTemporaryFile(
                        suffix=suffix, delete=False
                    ) as tmp:
                        tmp.write(file_bytes)
                        tmp_path = Path(tmp.name)
                    try:
                        res = build_full_contract_diff(tmp_path)
                        res["uploaded_file"] = file_name
                    finally:
                        try:
                            tmp_path.unlink()
                        except Exception:
                            pass

                    self.send_response(200)
                    self.send_header(
                        "Content-Type", "application/json; charset=utf-8"
                    )
                    self.end_headers()
                    self.wfile.write(
                        json.dumps(res, ensure_ascii=False).encode("utf-8")
                    )
                    return

            self.send_response(400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"error": "invalid_upload"}')
        else:
            self.send_response(404)
            self.end_headers()


def generate_rendered_html() -> str:
    """生成内嵌初始默认高危用例数据的独立完整 HTML。"""
    try:
        sample_file = config.GOLDEN_DIR / "mut_11_late_fee_change.docx"
        initial_data = build_full_contract_diff(sample_file)
        data_json = json.dumps(initial_data, ensure_ascii=False)
    except Exception:
        data_json = "null"

    return HTML_TEMPLATE.replace("__INITIAL_DATA_PLACEHOLDER__", data_json)


def export_standalone_html():
    """将整版对比与批注工作台导出为独立 HTML 页面存放在 data/contract_merged 供直接双击使用。"""
    out_path = (
        config.DATA_DIR.parent / "contract_merged" / "合同模板条款比对与Diff可视化.html"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    html_content = generate_rendered_html()
    out_path.write_text(html_content, encoding="utf-8")
    print(f"[已生成独立整版原格式对照与右侧批注网页] {out_path}")


def main():
    parser = argparse.ArgumentParser(
        description="合同整版原格式对照与右侧批注前端服务"
    )
    parser.add_argument(
        "--port", type=int, default=8089, help="Web 端口（默认 8089）"
    )
    parser.add_argument(
        "--export-only", action="store_true", help="仅导出独立 HTML 页面"
    )
    args = parser.parse_args()

    export_standalone_html()
    if args.export_only:
        return

    server = ThreadingHTTPServer(("127.0.0.1", args.port), DiffVisualizerHandler)
    print(
        f"\n🚀 AI 合同审查 · 条款级 Diff 与整版原格式比对工作台已启动: http://127.0.0.1:{args.port}"
    )
    print(
        "👉 请在浏览器中打开上方网址，即可体验整版双栏原格式对照与右侧智能法务审阅批注！\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")


if __name__ == "__main__":
    main()
