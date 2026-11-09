# -*- coding: utf-8 -*-
"""合同比对与条款级 Diff 前端可视化服务（Contract Diff Web Visualizer）。

高管/法务级全案重构：
1. 文档第一（Document-First Layout）：
   彻底移除首屏顶部占据大半屏幕的上传框与巨型统计卡片。顶部精简为 48px 导航栏 + 38px 工具条，
   首屏 90%+ 空间立即完整呈现两页 A4 真实合同排版。
2. 纯正合同原排版（Authentic Contract Typography）：
   大标题居中大字、副标题居中、合同编号右上右对齐、双方主体信息、鉴于条款首行缩进两字符、
   条款标题粗体、正文首行缩进 2em、尾部双栏签署盖章。
3. 优雅法律红线对照（Refined Legal Redline Flow）：
   删除条款在母本端以优雅红删除线呈现，送审端以柔和单行槽位指示，彻底摒弃丑陋的 23 个红色报错大框！
   修改字句高危数值（如违约金 3‰ ➔ 1‰）突出金黄高危徽章。
4. 批注专栏放右侧（Review Annotations Sidebar）：
   正文段落仅保留轻量微标（如 🏷️ [批注 #01]），所有对比片段、AI 法律风险剖析、处置建议与
   审阅决策按钮（接受/存疑/驳回）统一收纳在右侧 420px 专栏，双向丝滑对焦与高光脉冲联动。
5. 模态框上传（Modal Uploader）：
   上传新合同和切换实测高危用例改为轻量弹出窗口，不占用阅读视线。
6. 原生 CDP 调试与自动化验证集成。

用法：
  python tools/contract_diff_web.py [--port 8089]
  python tools/contract_diff_web.py --export-only
"""

from __future__ import annotations

import argparse
import json
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
<title>AI 合同审查 · 整版原格式比对与法务审阅工作台</title>
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
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
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
    font-weight: 800; font-size: 15px;
  }
  .brand-title { font-size: 15px; font-weight: 700; color: #1E293B; letter-spacing: -0.2px; }

  .header-meta-group {
    display: flex; align-items: center; gap: 12px; font-size: 12.5px;
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

  .header-actions { display: flex; align-items: center; gap: 8px; }
  .btn {
    padding: 5px 12px; border-radius: 6px; font-size: 12px; font-weight: 600;
    cursor: pointer; transition: all 0.15s ease; display: inline-flex;
    align-items: center; gap: 5px; border: 1px solid var(--border-light);
    background: #FFFFFF; color: #334155;
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
  }
  .tool-left { display: flex; align-items: center; gap: 12px; }
  .segmented-control {
    display: flex; background: #E2E8F0; padding: 2px; border-radius: 6px;
  }
  .seg-btn {
    padding: 3px 10px; font-size: 11.5px; font-weight: 600; border-radius: 4px;
    border: none; background: transparent; color: #64748B; cursor: pointer;
    transition: all 0.15s ease; white-space: nowrap;
  }
  .seg-btn.active {
    background: #FFF; color: #0F172A; box-shadow: 0 1px 2px rgba(0,0,0,0.06);
  }

  .nav-group { display: flex; align-items: center; gap: 6px; }
  .nav-arrow-btn {
    padding: 2px 8px; border-radius: 4px; border: 1px solid var(--border-light);
    background: #FFF; font-size: 11.5px; font-weight: 600; color: #334155;
    cursor: pointer;
  }
  .nav-arrow-btn:hover { background: #F1F5F9; }
  .nav-indicator-pill {
    font-size: 11.5px; font-weight: 700; color: #0F172A; min-width: 80px; text-align: center;
  }

  .tool-right { display: flex; align-items: center; gap: 10px; }
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
    cursor: pointer; display: flex; align-items: center; gap: 6px;
  }
  .btn-toggle-sidebar:hover { background: #334155; }
  .sidebar-badge {
    background: #EF4444; color: #FFF; font-size: 10px; padding: 0 5px;
    border-radius: 999px; font-weight: 800;
  }

  /* ==================== 3. 核心工作台：整版对照 + 批注专栏 ==================== */
  .review-workspace {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 420px;
    height: calc(100vh - 88px);
    width: 100%;
    overflow: hidden;
    transition: grid-template-columns 0.25s ease;
  }
  .review-workspace.sidebar-collapsed {
    grid-template-columns: minmax(0, 1fr) 0px;
  }
  .review-workspace.sidebar-collapsed .annotations-sidebar-panel {
    display: none !important;
  }

  /* ==================== 4. A4 纸张质感连续合同画布 ==================== */
  .document-canvas-scroll-area {
    overflow-y: auto;
    padding: 24px 32px 60px 32px;
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
  }

  .paper-column-headers {
    width: 100%;
    max-width: 1360px;
    display: grid;
    grid-template-columns: 1fr 1fr;
    column-gap: 40px;
    margin-bottom: 12px;
  }
  .mode-single .paper-column-headers {
    grid-template-columns: 1fr;
  }
  .mode-single .col-header.standard {
    display: none !important;
  }
  .col-header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 8px 14px; border-radius: 6px; background: #FFFFFF;
    border: 1px solid var(--border); box-shadow: 0 1px 3px rgba(0,0,0,0.03);
    font-size: 12.5px; font-weight: 700;
  }
  .col-header.standard { border-left: 4px solid #64748B; color: #334155; }
  .col-header.reviewed { border-left: 4px solid #2563EB; color: #1E40AF; background: #F8FAFC; }
  .col-header-sub { font-size: 11px; color: #64748B; font-weight: 500; }

  /* 真实 A4 双页连续纸张（杜绝表格卡片感） */
  .contract-continuous-stage {
    width: 100%;
    max-width: 1360px;
    background: #FFFFFF;
    border-radius: 6px;
    box-shadow: 0 4px 20px rgba(15, 23, 42, 0.07);
    padding: 44px 52px;
    display: grid;
    grid-template-columns: 1fr 1fr;
    column-gap: 40px;
    position: relative;
    min-height: 900px;
  }
  /* 双页中央装订脊线 */
  .contract-continuous-stage::after {
    content: "";
    position: absolute;
    top: 36px;
    bottom: 36px;
    left: 50%;
    width: 1px;
    background: #E2E8F0;
    pointer-events: none;
  }
  .mode-single .contract-continuous-stage {
    grid-template-columns: 1fr;
    padding: 44px 64px;
  }
  .mode-single .contract-continuous-stage::after {
    display: none !important;
  }
  .mode-single .doc-col-std {
    display: none !important;
  }

  /* 条款段落行：基于 display: contents 保持自然阅读流水平严格平齐 */
  .doc-section-row {
    display: contents;
  }
  .doc-col-std {
    grid-column: 1;
    position: relative;
    padding-right: 12px;
    transition: background 0.3s ease;
  }
  .doc-col-rev {
    grid-column: 2;
    position: relative;
    padding-left: 12px;
    transition: background 0.3s ease;
  }
  .mode-single .doc-col-rev {
    grid-column: 1;
    padding-left: 0;
  }

  /* 点击批注后对齐正文段落的高光脉冲动画 */
  .pulse-active {
    background: #FEF9C3 !important;
    border-radius: 4px;
    outline: 2px solid #EAB308;
    animation: pulseGlow 2.2s ease-in-out;
  }
  @keyframes pulseGlow {
    0% { background: #FEF08A; outline-color: #CA8A04; }
    50% { background: #FEF9C3; }
    100% { background: transparent; outline-color: transparent; }
  }

  /* ==================== 5. 真实合同原格式排版规范 ==================== */
  .contract-doc-title {
    font-size: 22px;
    font-weight: 800;
    text-align: center;
    color: #0F172A;
    margin: 8px 0 6px 0;
    letter-spacing: 1px;
  }
  .contract-doc-subtitle {
    font-size: 13.5px;
    font-weight: 500;
    text-align: center;
    color: #64748B;
    margin-bottom: 14px;
  }
  .contract-doc-meta {
    font-size: 12.5px;
    color: #64748B;
    text-align: right;
    margin-bottom: 16px;
    font-family: monospace;
  }
  .contract-doc-parties {
    font-size: 13px;
    line-height: 1.8;
    color: #1E293B;
    margin-bottom: 16px;
    padding: 10px 14px;
    background: #F8FAFC;
    border-radius: 4px;
    border-left: 3px solid #CBD5E1;
    white-space: pre-wrap;
  }
  .contract-doc-preamble {
    font-size: 13.5px;
    line-height: 1.9;
    color: #334155;
    text-indent: 2em;
    margin-bottom: 18px;
    text-align: justify;
  }
  .contract-article-heading {
    font-size: 15px;
    font-weight: 700;
    color: #0F172A;
    margin-top: 20px;
    margin-bottom: 8px;
    letter-spacing: 0.2px;
  }
  .contract-article-section {
    font-size: 13.5px;
    line-height: 1.88;
    color: #1E293B;
    margin-top: 14px;
    margin-bottom: 10px;
    white-space: pre-wrap;
    text-align: justify;
  }
  .contract-doc-p {
    font-size: 13.5px;
    line-height: 1.88;
    color: #1E293B;
    text-indent: 2em;
    margin-bottom: 8px;
    text-align: justify;
    word-break: break-all;
    white-space: pre-wrap;
  }
  .contract-doc-signatures {
    margin-top: 30px;
    padding-top: 16px;
    border-top: 1px dashed #CBD5E1;
    font-size: 13px;
    line-height: 1.85;
    white-space: pre-wrap;
  }

  /* ==================== 6. 标红 (Redline) 视觉规范 ==================== */
  .diff-del, del {
    background: #FEE2E2 !important;
    color: #B91C1C !important;
    text-decoration: line-through 2px #DC2626 !important;
    border: 1px solid #FECACA !important;
    font-weight: 700 !important;
    padding: 1px 4px !important;
    border-radius: 3px !important;
    display: inline !important;
  }
  /* 纯标红模式 */
  .mode-pure-redline .diff-ins,
  .mode-pure-redline ins {
    background: #FEF2F2 !important;
    color: #B91C1C !important;
    text-decoration: underline double 2px #DC2626 !important;
    border: 1px solid #FECACA !important;
    font-weight: 700 !important;
    padding: 1px 4px !important;
    border-radius: 3px !important;
    display: inline !important;
  }
  /* 红绿对照模式 */
  body:not(.mode-pure-redline) .diff-ins,
  body:not(.mode-pure-redline) ins {
    background: #DCFCE7 !important;
    color: #15803D !important;
    text-decoration: underline 2px #16A34A !important;
    border: 1px solid #BBF7D0 !important;
    font-weight: 700 !important;
    padding: 1px 4px !important;
    border-radius: 3px !important;
    display: inline !important;
  }
  /* 高危数值篡改特别强化 */
  .diff-ins-number {
    background: #FEF08A !important;
    color: #991B1B !important;
    border: 2px solid #EF4444 !important;
    font-weight: 900 !important;
    padding: 1px 6px !important;
    border-radius: 4px !important;
    box-shadow: 0 1px 4px rgba(220, 38, 38, 0.2) !important;
  }

  /* 正文轻量级法务批注引用微标 */
  .ann-ref-anchor {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    font-size: 11px;
    font-weight: 700;
    color: #2563EB;
    background: #EFF6FF;
    border: 1px solid #BFDBFE;
    border-radius: 4px;
    padding: 1px 6px;
    margin: 0 4px;
    cursor: pointer;
    vertical-align: middle;
    user-select: none;
    transition: all 0.15s ease;
  }
  .ann-ref-anchor:hover {
    background: #2563EB;
    color: #FFFFFF;
    border-color: #2563EB;
    box-shadow: 0 2px 6px rgba(37,99,235,0.3);
  }
  .ann-pills-wrap {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    margin-left: 4px;
    vertical-align: middle;
  }

  /* 优雅的删除与新增段落排版（摒弃丑陋的大红警告框） */
  .doc-text-deleted {
    background: #FFF5F5;
    border-left: 3px solid #F87171;
    padding: 8px 12px;
    border-radius: 4px;
    margin-bottom: 8px;
    font-size: 13.5px;
    line-height: 1.85;
  }
  .del-clause-pill {
    font-size: 10.5px;
    font-weight: 700;
    color: #DC2626;
    background: #FEE2E2;
    padding: 1px 5px;
    border-radius: 3px;
    margin-right: 6px;
    display: inline-block;
  }
  .diff-del-flow {
    color: #7F1D1D;
    text-decoration: line-through 1.5px #DC2626;
    white-space: pre-wrap;
  }

  .doc-slot-deleted {
    background: #FAF8F8;
    border: 1px dashed #CBD5E1;
    border-radius: 4px;
    padding: 8px 12px;
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    cursor: pointer;
    font-size: 12px;
    color: #94A3B8;
    min-height: 38px;
    transition: all 0.15s ease;
  }
  .doc-slot-deleted:hover {
    background: #FEF2F2;
    border-color: #F87171;
    color: #DC2626;
  }
  .del-slot-tag { font-weight: 600; }

  .doc-slot-blank {
    background: #FAFAFA;
    border: 1px dashed #E2E8F0;
    border-radius: 4px;
    padding: 8px 12px;
    margin-bottom: 8px;
    font-size: 12px;
    color: #94A3B8;
    display: flex;
    align-items: center;
  }
  .doc-text-added {
    background: #FAF5FF;
    border-left: 3px solid #7C3AED;
    padding: 8px 12px;
    border-radius: 4px;
    margin-bottom: 8px;
    font-size: 13.5px;
    line-height: 1.85;
    cursor: pointer;
  }
  .add-clause-pill {
    font-size: 10.5px;
    font-weight: 700;
    color: #6D28D9;
    background: #EDE9FE;
    padding: 1px 5px;
    border-radius: 3px;
    margin-right: 6px;
    display: inline-block;
  }
  .diff-ins-flow {
    color: #4C1D95;
    text-decoration: underline 1.5px #7C3AED;
    white-space: pre-wrap;
  }

  /* ==================== 7. 右侧法务审阅批注专栏 ==================== */
  .annotations-sidebar-panel {
    background: #FFFFFF;
    border-left: 1px solid var(--border-light);
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
    z-index: 85;
  }
  .sidebar-header {
    padding: 10px 14px;
    border-bottom: 1px solid var(--border-light);
    background: #F8FAFC;
    display: flex;
    justify-content: space-between;
    align-items: center;
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
    overflow-y: auto;
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    flex: 1;
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
  .comp-label { font-weight: 700; color: #64748B; min-width: 44px; }
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

  /* ==================== 8. 上传模态窗 (Modal Dialog) ==================== */
  .modal-overlay {
    position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
    background: rgba(15, 23, 42, 0.45); backdrop-filter: blur(2px);
    z-index: 1000; display: none; align-items: center; justify-content: center;
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
    .header, .toolbar-strip, .annotations-sidebar-panel, .modal-overlay { display: none !important; }
    html, body { height: auto; overflow: visible; background: #FFF; }
    .review-workspace { display: block; height: auto; }
    .document-canvas-scroll-area { padding: 0; overflow: visible; }
    .contract-continuous-stage { box-shadow: none; padding: 0; }
  }
</style>
</head>
<body class="mode-pure-redline">

<!-- 顶部 48px 全局精炼导航栏 -->
<header class="header">
  <div class="brand">
    <div class="brand-logo">Δ</div>
    <span class="brand-title">AI 合同审查 · 条款级 Diff 与整版比对</span>
  </div>

  <div class="header-meta-group">
    <span class="meta-tag matched" id="matchedTemplateTag">🎯 基准母本: 国内专利服务合同（年费缴纳）</span>
    <span class="meta-tag doc-title" id="docFileName">《国内专利服务合同》送审修改稿</span>
  </div>

  <div class="header-actions">
    <button class="btn" onclick="openUploadModal()">📂 上传送审合同</button>
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
      <button class="seg-btn active" id="btnStyleRedline" onclick="setRedlineMode(true)">🔴 纯标红</button>
      <button class="seg-btn" id="btnStyleColor" onclick="setRedlineMode(false)">🎨 红绿对照</button>
    </div>

    <!-- 快速导航 -->
    <div class="nav-group">
      <button class="nav-arrow-btn" onclick="prevAnnotation()" title="快捷键: [ 或 Alt+↑">◀ 上一处</button>
      <span class="nav-indicator-pill" id="navIndicator">批注 1 / 35</span>
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
      <span>📋 批注专栏</span>
      <span class="sidebar-badge" id="btnSidebarCount">0</span>
    </button>
  </div>
</div>

<!-- ==================== 核心工作区：首屏 100% 呈现真实合同排版 ==================== -->
<div class="review-workspace" id="reviewWorkspace">

  <!-- 左/中：整版原格式连续 A4 纸质画布 -->
  <div class="document-canvas-scroll-area" id="documentCanvasArea">
    <div class="paper-column-headers" id="paperHeaders">
      <div class="col-header standard">
        <span>🏛️ 知识库标准参考母本（基准正文）</span>
        <span class="col-header-sub" id="headerStdName">母本库</span>
      </div>
      <div class="col-header reviewed">
        <span>✏️ 送审合同文本（待审稿·标红对照）</span>
        <span class="col-header-sub" id="headerRevName">待审稿</span>
      </div>
    </div>

    <!-- 连续整版合同页面（基于 display: contents 水平对齐） -->
    <div class="contract-continuous-stage" id="contractContinuousStage"></div>
  </div>

  <!-- 右：专享法务审阅批注栏 (批注项放右边) -->
  <aside class="annotations-sidebar-panel" id="annotationsSidebar">
    <div class="sidebar-header">
      <div class="sidebar-title">
        <span>📋 智能法务审阅批注</span>
        <span class="sidebar-count-badge" id="sidebarCount">0</span>
      </div>
      <button class="mini-tool-btn" onclick="toggleSidebarCollapse()" title="收起批注专栏">◀ 收起</button>
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

<!-- 模态弹窗：上传送审合同与加载高危实测用例 -->
<div class="modal-overlay" id="uploadModal" onclick="closeUploadModalOnBackdrop(event)">
  <div class="modal-card">
    <div class="modal-header">
      <span class="modal-title">📂 上传送审合同 / 快速加载实测用例</span>
      <button class="modal-close-btn" onclick="closeUploadModal()">✕</button>
    </div>
    <div class="modal-body">
      <div class="drop-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
        <input type="file" id="fileInput" accept=".docx" style="display:none;" onchange="handleFileSelect(event)">
        <div class="drop-icon">📄</div>
        <div class="drop-title">点击或拖拽修改后的 DOCX 送审合同至此</div>
        <div class="drop-desc">自动结构解构并秒级匹配 78 套标准母本库，出具整版原格式比对</div>
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

// 拖拽上传支持
const dropZone = document.getElementById('dropZone');
dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  if (e.dataTransfer.files.length > 0) {
    uploadDocx(e.dataTransfer.files[0]);
    closeUploadModal();
  }
});

function handleFileSelect(e) {
  if (e.target.files.length > 0) {
    uploadDocx(e.target.files[0]);
    closeUploadModal();
  }
}

async function uploadDocx(file) {
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
  if (idx === 1 && (cleaned.startsWith('(') || cleaned.startsWith('（') || cleaned.includes('年费') || cleaned.includes('委托'))) {
    return 'SUBTITLE';
  }
  if (cleaned.startsWith('合同编号') || cleaned.startsWith('编号:')) {
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
function formatContractHtml(html, rawText, category, colType) {
  if (!html) return '<div class="empty-placeholder"></div>';

  // 已由后端结构化包装的特殊插槽
  if (html.includes('doc-text-deleted') || html.includes('doc-slot-deleted') || html.includes('doc-slot-blank') || html.includes('doc-text-added')) {
    return html;
  }

  // 条款混合节点（条标题 + 条正文段落）精细化分层
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

  // 标准条款段落
  return `<div class="contract-doc-p">${html}</div>`;
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
  const tplCleanName = tplFileName.replace(/\.docx?$/i, '');
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
  renderContinuousContract();
  renderAnnotationsSidebar();
}

function updateProgressDisplay() {
  if (!currentData || !currentData.annotations) return;
  const total = currentData.annotations.length;
  const handled = currentData.annotations.filter(a => a.review_status && a.review_status !== 'PENDING').length;
  document.getElementById('statProgress').innerText = `${handled}/${total}`;
}

// 核心渲染：整版整页 A4 原格式对照排版（杜绝表格卡片感）
function renderContinuousContract() {
  const stage = document.getElementById('contractContinuousStage');
  stage.innerHTML = '';

  const blocks = currentData.aligned_blocks || [];
  blocks.forEach((block, idx) => {
    const isMatch = block.status === 'MATCH';
    if (currentViewMode === 'diff' && isMatch) {
      return; // 仅看变更条款模式下跳过完全一致项
    }

    const txt = block.standard_text || block.reviewed_text || '';
    const category = detectBlockCategory(block.clause_title, txt, idx);

    // 虚拟行容器（基于 display: contents 保持左右两栏平滑网格对齐，不渲染任何边框或表格线）
    const row = document.createElement('div');
    row.className = 'doc-section-row';
    row.id = block.block_id;

    // 左侧（标准参考母本基准正文）
    const colStd = document.createElement('div');
    colStd.className = `doc-col-std ${block.status.toLowerCase()}`;
    colStd.id = `std-${block.block_id}`;
    colStd.innerHTML = formatContractHtml(block.standard_html || escapeHtml(block.standard_text), block.standard_text, category, 'STD');

    // 右侧（送审待审修改版，标红排版 + 批注锚点）
    const colRev = document.createElement('div');
    colRev.className = `doc-col-rev ${block.status.toLowerCase()}`;
    colRev.id = `rev-${block.block_id}`;
    colRev.innerHTML = formatContractHtml(block.reviewed_html || escapeHtml(block.reviewed_text), block.reviewed_text, category, 'REV');

    row.appendChild(colStd);
    row.appendChild(colRev);
    stage.appendChild(row);
  });
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

// 选中某个批注并实现正文与右侧卡片双向对齐平滑滚动与高光脉冲
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

  // 2. 定位正文条款段落并播放发光脉冲动画
  document.querySelectorAll('.doc-col-std, .doc-col-rev').forEach(el => el.classList.remove('pulse-active'));
  const revCol = document.getElementById(`rev-${ann.block_id}`);
  const stdCol = document.getElementById(`std-${ann.block_id}`);
  if (revCol) {
    revCol.classList.add('pulse-active');
    revCol.scrollIntoView({ behavior: 'smooth', block: 'center' });
  } else if (stdCol) {
    stdCol.classList.add('pulse-active');
    stdCol.scrollIntoView({ behavior: 'smooth', block: 'center' });
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

// 设置审阅状态
function setAnnotationStatus(annId, status) {
  const ann = (currentData.annotations || []).find(a => a.id === annId);
  if (!ann) return;
  ann.review_status = (ann.review_status === status) ? 'PENDING' : status;

  renderAnnotationsSidebar();
  const card = document.getElementById(`card-${annId}`);
  if (card) card.classList.add('active');

  updateProgressDisplay();
}

// 右侧批注专栏收起/展开
function toggleSidebarCollapse() {
  const workspace = document.getElementById('reviewWorkspace');
  const isCollapsed = workspace.classList.toggle('sidebar-collapsed');
  const btn = document.getElementById('btnToggleSidebar');
  if (btn) {
    btn.innerHTML = isCollapsed
      ? `<span>▶ 展开批注</span> <span class="sidebar-badge">${(currentData.annotations || []).length}</span>`
      : `<span>📋 批注专栏</span> <span class="sidebar-badge">${(currentData.annotations || []).length}</span>`;
  }
}

// 标红风格切换：经典法务纯标红 vs 红绿对照
function setRedlineMode(isPureRedline) {
  document.body.classList.toggle('mode-pure-redline', isPureRedline);
  document.getElementById('btnStyleRedline').classList.toggle('active', isPureRedline);
  document.getElementById('btnStyleColor').classList.toggle('active', !isPureRedline);
}

// 视图切换
function setViewMode(mode) {
  currentViewMode = mode;
  document.getElementById('btnModeSide').classList.toggle('active', mode === 'side');
  document.getElementById('btnModeSingle').classList.toggle('active', mode === 'single');
  document.getElementById('btnModeDiff').classList.toggle('active', mode === 'diff');

  document.body.classList.toggle('mode-single', mode === 'single');
  renderContinuousContract();
}

// 分类筛选
function setFilter(filterType) {
  currentFilter = filterType;
  document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
  event.target.classList.add('active');
  renderAnnotationsSidebar();
}

// 键盘快捷键支持：[ 上一个，] 下一个
window.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === '[' || (e.altKey && e.key === 'ArrowUp')) {
    e.preventDefault();
    prevAnnotation();
  } else if (e.key === ']' || (e.altKey && e.key === 'ArrowDown')) {
    e.preventDefault();
    nextAnnotation();
  }
});

function escapeHtml(text) {
  if (!text) return '';
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// 初始化加载
window.addEventListener('DOMContentLoaded', () => {
  if (window.INITIAL_DATA && window.INITIAL_DATA.aligned_blocks) {
    renderFullDiff(window.INITIAL_DATA);
  } else {
    loadSample('prescreen_tampered');
  }
});
</script>

</body>
</html>
"""


class DiffVisualizerHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器。"""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            html_content = generate_rendered_html()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html_content.encode("utf-8"))

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
                for part in parts:
                    if b'name="file"' in part:
                        idx = part.find(b"\r\n\r\n")
                        if idx != -1:
                            file_bytes = part[idx + 4 :]
                            if file_bytes.endswith(b"\r\n"):
                                file_bytes = file_bytes[:-2]
                            break

                if file_bytes:
                    with tempfile.NamedTemporaryFile(
                        suffix=".docx", delete=False
                    ) as tmp:
                        tmp.write(file_bytes)
                        tmp_path = Path(tmp.name)
                    try:
                        res = build_full_contract_diff(tmp_path)
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
