# -*- coding: utf-8 -*-
"""合同比对与条款级 Diff 前端可视化服务（Contract Diff Web Visualizer）。

核心体验升级：
1. 整版整页 A4 原格式对照（Continuous Full-Page Side-by-Side Comparison）：
   彻底摒弃死板表格与条款卡片框，完整还原真实合同纸质排版（大标题、副标题、合同编号、双方信息、
   鉴于条款、条款标题、缩进正文、尾部签署页）。左右两栏基于 CSS contents 水平对准，行云流水。
2. 经典法务审阅批注放右边（Dedicated Review Annotations Sidebar）：
   正文段落仅保留精美标签锚点（如 🏷️ [批注 #01]），所有法务风险卡片、母本对比片段、AI 风险剖析、
   处置建议与接受/存疑/驳回决策按钮统一收纳在右侧审阅专栏，支持双向对焦与平滑高光脉冲联动。
3. 多视图自由切换：
   - 📖 整版原格式双栏对照（50/50 宽屏基准）
   - 📄 送审单版标红审阅（Word/WPS 经典修订视图）
   - 🎯 仅看变更条款（高危聚焦视图）
4. 标红版式风格自由切换：
   - 🔴 经典法务纯标红（删除线标红 + 双下划线标红 + 关键数值强提醒）
   - 🎨 红绿差异对照（红删绿增经典版）
5. 独立离线 HTML 导出：开箱即用，双击即可在任意浏览器独立浏览。

用法：
  python tools/contract_diff_web.py [--port 8772]
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
    --bg: #F1F5F9;
    --stage-bg: #E2E8F0;
    --card-bg: #FFFFFF;
    --border: #E2E8F0;
    --border-dark: #CBD5E1;
    --text-main: #0F172A;
    --text-muted: #64748B;
    --text-sub: #94A3B8;
    --primary: #2563EB;
    --primary-light: #EFF6FF;
    --primary-border: #BFDBFE;
    --danger: #DC2626;
    --danger-light: #FEF2F2;
    --danger-border: #FECACA;
    --warning: #D97706;
    --warning-light: #FFFBEB;
    --warning-border: #FDE68A;
    --success: #16A34A;
    --success-light: #F0FDF4;
    --success-border: #BBF7D0;
    --purple: #7C3AED;
    --purple-light: #F5F3FF;
    --purple-border: #DDD6FE;
    --accent-glow: rgba(37, 99, 235, 0.35);
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    background: var(--bg);
    color: var(--text-main);
    line-height: 1.6;
    overflow-x: hidden;
  }

  /* 顶部全局导航栏 */
  .header {
    background: #FFFFFF;
    border-bottom: 1px solid var(--border);
    padding: 12px 28px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    position: sticky;
    top: 0;
    z-index: 100;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
  }
  .brand { display: flex; align-items: center; gap: 12px; }
  .brand-logo {
    width: 36px; height: 36px; border-radius: 9px;
    background: linear-gradient(135deg, #1E40AF 0%, #3B82F6 100%);
    color: #fff; display: flex; align-items: center; justify-content: center;
    font-weight: 800; font-size: 18px; box-shadow: 0 2px 6px rgba(37,99,235,0.28);
  }
  .brand-title { font-size: 17px; font-weight: 700; color: #1E293B; letter-spacing: -0.3px; }
  .brand-sub { font-size: 11px; color: var(--text-muted); margin-top: 1px; }
  .brand-tag {
    font-size: 11px; padding: 2px 8px; border-radius: 999px;
    background: #EEF2F6; color: #334155; font-weight: 600;
  }
  .header-actions { display: flex; gap: 10px; align-items: center; }

  .btn {
    padding: 6px 14px; border-radius: 7px; font-size: 13px; font-weight: 600;
    cursor: pointer; transition: all 0.15s ease; display: inline-flex;
    align-items: center; gap: 6px; border: 1px solid var(--border);
    background: #FFFFFF; color: #334155;
  }
  .btn:hover { background: #F8FAFC; border-color: var(--border-dark); }
  .btn-primary {
    background: var(--primary); color: #FFF; border-color: var(--primary);
  }
  .btn-primary:hover { background: #1D4ED8; }

  /* 主容器 */
  .main-wrapper {
    max-width: 100%;
    margin: 16px auto;
    padding: 0 24px;
  }

  /* 上传折叠区与实测用例切换 */
  .upload-panel {
    background: #FFFFFF;
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px 20px;
    margin-bottom: 16px;
    display: grid;
    grid-template-columns: 1fr 380px;
    gap: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.02);
  }
  .drop-zone {
    border: 2px dashed #CBD5E1;
    border-radius: 10px;
    padding: 18px;
    text-align: center;
    cursor: pointer;
    background: #F8FAFC;
    transition: all 0.2s ease;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
  }
  .drop-zone:hover, .drop-zone.dragover {
    border-color: var(--primary);
    background: var(--primary-light);
  }
  .drop-icon { font-size: 26px; margin-bottom: 4px; }
  .drop-title { font-size: 14px; font-weight: 600; color: #1E293B; }
  .drop-desc { font-size: 11px; color: var(--text-muted); margin-top: 2px; }

  .preset-section {
    display: flex; flex-direction: column; justify-content: space-between;
  }
  .preset-title {
    font-size: 12px; font-weight: 700; color: #475569; margin-bottom: 8px;
    display: flex; align-items: center; justify-content: space-between;
  }
  .preset-grid { display: grid; grid-template-columns: 1fr; gap: 6px; }
  .preset-btn {
    padding: 7px 12px; border-radius: 6px; border: 1px solid var(--border);
    background: #F8FAFC; color: #334155; font-size: 12px; text-align: left;
    cursor: pointer; transition: all 0.15s ease; display: flex;
    justify-content: space-between; align-items: center;
  }
  .preset-btn:hover {
    border-color: var(--primary); background: #EFF6FF; color: var(--primary);
  }
  .badge-tag {
    font-size: 10px; padding: 1px 6px; border-radius: 4px; font-weight: 600;
  }
  .badge-tag.danger { background: #FEE2E2; color: #991B1B; }
  .badge-tag.warning { background: #FEF3C7; color: #92400E; }
  .badge-tag.success { background: #DCFCE7; color: #15803D; }

  /* 状态概览看板 */
  .meta-dashboard {
    background: #FFFFFF;
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px 20px;
    margin-bottom: 14px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.02);
  }
  .meta-top-row {
    display: flex; justify-content: space-between; align-items: center;
    padding-bottom: 12px; border-bottom: 1px solid #F1F5F9;
  }
  .doc-title-box { display: flex; align-items: center; gap: 12px; }
  .doc-title { font-size: 17px; font-weight: 700; color: #0F172A; }
  .matched-tag {
    font-size: 12px; font-weight: 600; padding: 3px 10px; border-radius: 6px;
    background: #DCFCE7; color: #15803D; border: 1px solid #BBF7D0;
  }
  .verdict-banner {
    font-size: 13px; font-weight: 600; padding: 4px 12px; border-radius: 6px;
  }
  .verdict-banner.danger { background: #FEE2E2; color: #991B1B; border: 1px solid #FECACA; }
  .verdict-banner.success { background: #DCFCE7; color: #15803D; border: 1px solid #BBF7D0; }

  .meta-details-strip {
    display: flex; gap: 24px; margin-top: 10px; font-size: 12px; color: var(--text-muted);
  }
  .meta-details-strip strong { color: #1E293B; font-weight: 600; }

  /* 统计徽章栏 */
  .stats-row {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 10px;
    margin-top: 12px;
  }
  .stat-card {
    padding: 8px 12px; border-radius: 8px; border: 1px solid var(--border);
    background: #F8FAFC; display: flex; flex-direction: column; gap: 2px;
  }
  .stat-card.total { background: #EFF6FF; border-color: #BFDBFE; color: #1E40AF; }
  .stat-card.danger { background: var(--danger-light); border-color: var(--danger-border); color: var(--danger); }
  .stat-card.missing { background: #FEF3C7; border-color: #FDE68A; color: #92400E; }
  .stat-card.new { background: var(--purple-light); border-color: var(--purple-border); color: var(--purple); }
  .stat-card.warning { background: #FFFBEB; border-color: #FDE68A; color: #B45309; }
  .stat-card.progress { background: #F0FDF4; border-color: #BBF7D0; color: #15803D; }
  .stat-card-title { font-size: 11px; font-weight: 600; opacity: 0.9; }
  .stat-card-val { font-size: 19px; font-weight: 800; }

  /* 固定操作控制条 */
  .controls-bar {
    position: sticky;
    top: 61px;
    z-index: 90;
    background: rgba(255, 255, 255, 0.95);
    backdrop-filter: blur(8px);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 10px 16px;
    margin-bottom: 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    box-shadow: 0 2px 6px rgba(0,0,0,0.04);
  }
  .control-left-group { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .view-switch, .style-switch {
    display: flex; background: #E2E8F0; padding: 2px; border-radius: 8px;
  }
  .view-btn, .style-btn {
    padding: 5px 12px; font-size: 12px; font-weight: 600; border-radius: 6px;
    border: none; background: transparent; color: #64748B; cursor: pointer;
    transition: all 0.15s ease; white-space: nowrap;
  }
  .view-btn.active, .style-btn.active {
    background: #FFF; color: #0F172A; box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  }

  .nav-group { display: flex; align-items: center; gap: 6px; }
  .nav-btn {
    padding: 4px 10px; border-radius: 6px; border: 1px solid var(--border);
    background: #FFF; font-size: 12px; font-weight: 600; color: #334155;
    cursor: pointer;
  }
  .nav-btn:hover { background: #F1F5F9; border-color: var(--border-dark); }
  .nav-indicator { font-size: 12px; font-weight: 700; color: #0F172A; min-width: 90px; text-align: center; }

  .control-right-group { display: flex; align-items: center; gap: 10px; }
  .btn-toggle-sidebar {
    background: #1E293B !important; color: #FFF !important; border-color: #1E293B !important;
  }
  .btn-toggle-sidebar:hover { background: #334155 !important; }
  .sidebar-badge-count {
    background: #EF4444; color: #FFF; font-size: 10.5px; padding: 1px 6px; border-radius: 999px; font-weight: 800;
  }

  /* ==================== 核心工作区：整版对照 + 批注放右边 ==================== */
  .review-workspace {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 420px;
    gap: 20px;
    align-items: start;
    width: 100%;
    transition: grid-template-columns 0.25s ease;
  }
  .review-workspace.sidebar-collapsed {
    grid-template-columns: minmax(0, 1fr) 0px;
    gap: 0;
  }
  .review-workspace.sidebar-collapsed .annotations-sidebar-panel {
    display: none !important;
  }

  /* 单版正文修订模式 */
  .mode-single .review-workspace {
    grid-template-columns: minmax(0, 1fr) 420px;
  }
  .mode-single.sidebar-collapsed .review-workspace {
    grid-template-columns: minmax(0, 1fr) 0px;
  }

  /* ==================== A4 连续整版纸质工作台 ==================== */
  .document-dual-stage-wrapper {
    background: var(--stage-bg);
    padding: 24px;
    border-radius: 12px;
    box-shadow: inset 0 2px 6px rgba(0,0,0,0.06);
    overflow-x: auto;
  }

  .stage-headers {
    display: grid;
    grid-template-columns: 1fr 1fr;
    column-gap: 36px;
    margin-bottom: 12px;
    padding: 0 44px;
    position: sticky;
    top: 114px;
    z-index: 50;
  }
  .mode-single .stage-headers {
    grid-template-columns: 1fr;
    padding: 0;
  }
  .mode-single .stage-col-header.standard {
    display: none !important;
  }

  .stage-col-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 9px 16px;
    border-radius: 8px;
    background: #FFFFFF;
    border: 1px solid #CBD5E1;
    box-shadow: 0 2px 5px rgba(0,0,0,0.04);
    font-size: 13.5px;
    font-weight: 700;
  }
  .stage-col-header.standard {
    border-left: 4px solid #64748B;
    color: #334155;
  }
  .stage-col-header.reviewed {
    border-left: 4px solid #2563EB;
    color: #1E40AF;
    background: #F8FAFC;
  }
  .stage-header-sub {
    font-size: 11px;
    color: #64748B;
    font-weight: 600;
  }

  /* 连续整版 A4 纸张排版容器（杜绝表格卡片感） */
  .contract-continuous-stage {
    display: grid;
    grid-template-columns: 1fr 1fr;
    column-gap: 36px;
    background: #FFFFFF;
    padding: 44px 48px;
    border-radius: 8px;
    box-shadow: 0 4px 24px rgba(15, 23, 42, 0.08);
    position: relative;
    min-height: 800px;
  }
  /* 左右版面中央书脊微线 */
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
    padding: 44px 56px;
  }
  .mode-single .contract-continuous-stage::after {
    display: none !important;
  }
  .mode-single .doc-col-std {
    display: none !important;
  }

  /* 条款段落行：采用 display: contents 保持左右严格自然对齐，绝不增加外部方框或表格线 */
  .doc-section-row {
    display: contents;
  }
  .doc-col-std {
    grid-column: 1;
    position: relative;
    padding-right: 14px;
    transition: background 0.3s ease;
  }
  .doc-col-rev {
    grid-column: 2;
    position: relative;
    padding-left: 14px;
    transition: background 0.3s ease;
  }
  .mode-single .doc-col-rev {
    grid-column: 1;
    padding-left: 0;
  }

  /* 点击批注后对齐正文段落的高光脉冲动画 */
  .pulse-active {
    background: #FEF9C3 !important;
    border-radius: 6px;
    box-shadow: 0 0 0 2px #EAB308;
    animation: pulseGlow 2.2s ease-in-out;
  }
  @keyframes pulseGlow {
    0% { background: #FEF08A; box-shadow: 0 0 0 3px #EAB308; }
    50% { background: #FEF9C3; }
    100% { background: transparent; box-shadow: none; }
  }

  /* ==================== 真实合同原格式排版规范 ==================== */
  .contract-doc-title {
    font-size: 22px;
    font-weight: 800;
    text-align: center;
    color: #0F172A;
    margin: 12px 0 8px 0;
    letter-spacing: 1px;
  }
  .contract-doc-subtitle {
    font-size: 14px;
    font-weight: 500;
    text-align: center;
    color: #64748B;
    margin-bottom: 18px;
  }
  .contract-doc-meta {
    font-size: 13px;
    color: #64748B;
    text-align: right;
    margin-bottom: 18px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  .contract-doc-parties {
    font-size: 13.5px;
    line-height: 1.85;
    color: #1E293B;
    margin-bottom: 20px;
    padding: 12px 16px;
    background: #F8FAFC;
    border-radius: 6px;
    border-left: 3px solid #CBD5E1;
    white-space: pre-wrap;
  }
  .contract-doc-preamble {
    font-size: 14px;
    line-height: 1.9;
    color: #334155;
    text-indent: 2em;
    margin-bottom: 22px;
    text-align: justify;
  }
  .contract-article-heading {
    font-size: 15.5px;
    font-weight: 700;
    color: #0F172A;
    margin-top: 24px;
    margin-bottom: 10px;
    letter-spacing: 0.2px;
  }
  .contract-article-section {
    font-size: 14px;
    line-height: 1.88;
    color: #1E293B;
    margin-top: 18px;
    margin-bottom: 12px;
    white-space: pre-wrap;
    text-align: justify;
  }
  .contract-doc-p {
    font-size: 14px;
    line-height: 1.88;
    color: #1E293B;
    text-indent: 2em;
    margin-bottom: 10px;
    text-align: justify;
    word-break: break-all;
    white-space: pre-wrap;
  }
  .contract-doc-signatures {
    margin-top: 36px;
    padding-top: 20px;
    border-top: 1px dashed #CBD5E1;
    font-size: 13.5px;
    line-height: 1.9;
    white-space: pre-wrap;
  }

  /* ==================== 标红 (Redline) 视觉规范 ==================== */
  /* 1. 删除文本标红 */
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

  /* 2. 插入文本：经典法务纯标红模式（默认） */
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

  /* 3. 插入文本：红绿对照模式 */
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

  /* 4. 高危违约金比例/期限偷改醒目徽章 */
  .diff-ins-number {
    background: #FEF08A !important;
    color: #991B1B !important;
    border: 2px solid #EF4444 !important;
    font-weight: 900 !important;
    padding: 2px 6px !important;
    border-radius: 4px !important;
    box-shadow: 0 1px 4px rgba(220, 38, 38, 0.25) !important;
    letter-spacing: 0.5px !important;
  }

  /* 5. 正文轻量级法务批注引用锚点（不污染正文排版，点击联动右侧专栏） */
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
    margin-left: 6px;
    vertical-align: middle;
  }

  /* ==================== 缺失条款与新增条款整块排版 ==================== */
  .doc-block-missing-std {
    background: #FFF5F5;
    border: 1px solid #FECACA;
    border-left: 4px solid #DC2626;
    border-radius: 6px;
    padding: 12px 16px;
    margin-bottom: 12px;
  }
  .doc-badge-missing {
    font-size: 11.5px;
    font-weight: 700;
    color: #DC2626;
    margin-bottom: 6px;
  }
  .doc-missing-text {
    font-size: 13.5px;
    line-height: 1.85;
    color: #7F1D1D;
    text-indent: 2em;
    text-align: justify;
  }

  .doc-block-missing-slot {
    background: #FEF2F2;
    border: 1.5px dashed #F87171;
    border-radius: 6px;
    padding: 12px 16px;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    cursor: pointer;
    transition: all 0.15s ease;
  }
  .doc-block-missing-slot:hover {
    background: #FEE2E2;
    border-color: #DC2626;
  }
  .slot-icon { font-size: 16px; }
  .slot-text {
    font-size: 13px;
    font-weight: 600;
    color: #991B1B;
    flex: 1;
  }

  .doc-block-blank-slot {
    background: #F8FAFC;
    border: 1.5px dashed #CBD5E1;
    border-radius: 6px;
    padding: 12px 16px;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 8px;
    color: #64748B;
    font-size: 13px;
  }

  .doc-block-new-rev {
    background: #FAF5FF;
    border: 1px solid #DDD6FE;
    border-left: 4px solid #7C3AED;
    border-radius: 6px;
    padding: 12px 16px;
    margin-bottom: 12px;
    cursor: pointer;
  }
  .doc-badge-new {
    font-size: 11.5px;
    font-weight: 700;
    color: #6D28D9;
    margin-bottom: 6px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .doc-new-text {
    font-size: 14px;
    line-height: 1.85;
    color: #4C1D95;
    text-indent: 2em;
    text-align: justify;
  }

  /* ==================== 右侧法务审阅批注专栏 (Annotations Sidebar) ==================== */
  .annotations-sidebar-panel {
    position: sticky;
    top: 114px;
    max-height: calc(100vh - 130px);
    background: #FFFFFF;
    border: 1px solid var(--border);
    border-radius: 12px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.05);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    z-index: 85;
  }
  .sidebar-header {
    padding: 14px 18px;
    border-bottom: 1px solid var(--border);
    background: #F8FAFC;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .sidebar-title {
    font-size: 14px;
    font-weight: 700;
    color: #1E293B;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .sidebar-count-badge {
    background: #1E293B;
    color: #FFF;
    font-size: 11px;
    padding: 1px 7px;
    border-radius: 999px;
    font-weight: 800;
  }
  .mini-tool-btn {
    font-size: 11.5px;
    font-weight: 600;
    padding: 4px 8px;
    border-radius: 4px;
    border: 1px solid var(--border);
    background: #FFF;
    color: #475569;
    cursor: pointer;
  }
  .mini-tool-btn:hover { background: #F1F5F9; color: #0F172A; }

  .sidebar-filter-strip {
    padding: 8px 12px;
    border-bottom: 1px solid #F1F5F9;
    display: flex;
    gap: 5px;
    flex-wrap: wrap;
    background: #FFFFFF;
  }
  .filter-chip {
    padding: 3px 9px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: #FFF;
    font-size: 11px;
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
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    flex: 1;
  }

  /* 独立审阅批注卡片 */
  .annotation-card {
    background: #FFFFFF;
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 14px;
    cursor: pointer;
    transition: all 0.2s ease;
    box-shadow: 0 1px 3px rgba(0,0,0,0.02);
    position: relative;
  }
  .annotation-card:hover {
    border-color: var(--primary);
    box-shadow: 0 3px 8px rgba(37,99,235,0.08);
  }
  .annotation-card.active {
    border-color: var(--primary);
    box-shadow: 0 0 0 2px var(--accent-glow);
    background: #FAFCFF;
  }
  .annotation-card.status-ACCEPTED {
    border-left: 4px solid #16A34A;
    background: #F0FDF4;
  }
  .annotation-card.status-QUESTIONED {
    border-left: 4px solid #D97706;
    background: #FFFBEB;
  }
  .annotation-card.status-OVERRULED {
    border-left: 4px solid #94A3B8;
    background: #F8FAFC;
    opacity: 0.88;
  }

  .card-top {
    display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 6px;
  }
  .card-index {
    font-size: 11px; font-weight: 800; color: #64748B; background: #F1F5F9;
    padding: 2px 6px; border-radius: 4px;
  }
  .card-risk-pill {
    font-size: 10.5px; font-weight: 700; padding: 2px 7px; border-radius: 4px;
  }
  .card-risk-pill.HIGH { background: #FEE2E2; color: #991B1B; }
  .card-risk-pill.MEDIUM { background: #FEF3C7; color: #92400E; }
  .card-risk-pill.LOW { background: #F1F5F9; color: #475569; }

  .card-clause-name {
    font-size: 12.5px; font-weight: 700; color: #0F172A; margin-bottom: 6px;
  }

  /* 卡片内修改对比 */
  .card-comparison-box {
    background: #F8FAFC;
    border-radius: 6px;
    padding: 7px 9px;
    font-size: 11.5px;
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

  /* AI 法务剖析与建议 */
  .card-analysis {
    font-size: 12px; color: #334155; margin-bottom: 6px; line-height: 1.5;
  }
  .card-advice {
    font-size: 11.5px; color: #1E40AF; background: #EFF6FF; border-left: 3px solid var(--primary);
    padding: 6px 8px; border-radius: 0 4px 4px 0; margin-bottom: 8px; line-height: 1.45;
  }

  /* 审阅处置按钮栏 */
  .card-actions {
    display: flex; justify-content: space-between; align-items: center; border-top: 1px solid #F1F5F9;
    padding-top: 6px; margin-top: 2px;
  }
  .card-action-btn-group { display: flex; gap: 4px; }
  .mini-btn {
    padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 600;
    border: 1px solid var(--border); background: #FFF; color: #475569; cursor: pointer;
  }
  .mini-btn:hover { background: #F1F5F9; color: #0F172A; }
  .mini-btn.active-accept { background: #DCFCE7; color: #15803D; border-color: #86EFAC; }
  .mini-btn.active-question { background: #FEF3C7; color: #92400E; border-color: #FCD34D; }
  .mini-btn.active-overrule { background: #F1F5F9; color: #64748B; border-color: #CBD5E1; }

  .locate-btn {
    font-size: 11px; font-weight: 600; color: var(--primary); border: none; background: transparent;
    cursor: pointer; display: flex; align-items: center; gap: 2px;
  }
  .locate-btn:hover { text-decoration: underline; }

  /* 打印样式适配 */
  @media print {
    .header, .controls-bar, .upload-panel, .header-actions, .card-actions, .annotations-sidebar-panel { display: none !important; }
    body { background: #FFF; }
    .main-wrapper { max-width: 100%; margin: 0; padding: 0; }
    .review-workspace { display: block; }
    .document-dual-stage-wrapper { background: #FFF; padding: 0; }
    .contract-continuous-stage { box-shadow: none; padding: 0; }
  }
</style>
</head>
<body class="mode-pure-redline">

<header class="header">
  <div class="brand">
    <div class="brand-logo">Δ</div>
    <div>
      <div style="display:flex; align-items:center; gap:8px;">
        <span class="brand-title">AI 合同审查 · 整版原格式比对与法务审阅工作台</span>
        <span class="brand-tag">78 套标准母本库接入版</span>
      </div>
      <div class="brand-sub">双栏整版阅读 · 字符级微观对齐 · 智能法务审阅批注专栏</div>
    </div>
  </div>
  <div class="header-actions">
    <button class="btn" onclick="toggleUploadPanel()">📂 上传新合同</button>
    <button class="btn btn-primary" onclick="window.print()">🖨️ 导出审阅对比单</button>
  </div>
</header>

<main class="main-wrapper">

  <!-- 上传与快捷用例区（可折叠） -->
  <div class="upload-panel" id="uploadPanel">
    <div class="drop-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
      <input type="file" id="fileInput" accept=".docx" style="display:none;" onchange="handleFileSelect(event)">
      <div class="drop-icon">📄</div>
      <div class="drop-title">点击或拖拽修改后的 DOCX 送审合同至此</div>
      <div class="drop-desc">无需人工选择母本：自动结构解构并秒级匹配 78 套标准母本库，出具整版原格式比对</div>
    </div>

    <div class="preset-section">
      <div class="preset-title">
        <span>⚡ 快速加载实测高危用例</span>
        <span style="font-size:10px; color:#94A3B8;">一键秒级还原法务实务</span>
      </div>
      <div class="preset-grid">
        <button class="preset-btn" onclick="loadSample('prescreen_tampered')">
          <span>🚨 快速预审 (违约金偷降千分之一)</span>
          <span class="badge-tag danger">高危篡改</span>
        </button>
        <button class="preset-btn" onclick="loadSample('missing_confidential')">
          <span>🚩 专利代理 (保密与权利义务被偷删)</span>
          <span class="badge-tag warning">核心缺失</span>
        </button>
        <button class="preset-btn" onclick="loadSample('trademark_match')">
          <span>✅ 商标驳回复审 (合规原版基准)</span>
          <span class="badge-tag success">100%匹配</span>
        </button>
      </div>
    </div>
  </div>

  <!-- 审查看板 -->
  <div class="meta-dashboard">
    <div class="meta-top-row">
      <div class="doc-title-box">
        <span class="doc-title" id="docFileName">待审合同.docx</span>
        <span class="matched-tag" id="matchedTemplateTag">已锁定标准母本: MB20251205012</span>
      </div>
      <div class="verdict-banner danger" id="verdictBanner">🚨 发现高危条款篡改与核心义务缺失，建议重点复核并驳回修改</div>
    </div>

    <div class="meta-details-strip">
      <span>识别业务子类: <strong id="detectedSubtype">-</strong></span>
      <span>母本匹配置信度: <strong id="confidenceRate">-</strong></span>
      <span>基准母本文件: <strong id="matchedTemplateName">-</strong></span>
      <span>母本底层哈希: <strong id="matchedTemplateId" style="font-family:monospace; font-size:11px; color:#64748B;">-</strong></span>
      <span>条款规模: 母本 <strong id="stdCount">-</strong> 条 vs 送审 <strong id="revCount">-</strong> 条</span>
    </div>

    <div class="stats-row">
      <div class="stat-card total">
        <span class="stat-card-title">📌 发现审查批注</span>
        <span class="stat-card-val" id="statTotalAnnotations">0</span>
      </div>
      <div class="stat-card danger">
        <span class="stat-card-title">🔴 高危数值篡改</span>
        <span class="stat-card-val" id="statHighRisk">0</span>
      </div>
      <div class="stat-card missing">
        <span class="stat-card-title">🚩 核心条款缺失</span>
        <span class="stat-card-val" id="statMissingClauses">0</span>
      </div>
      <div class="stat-card new">
        <span class="stat-card-title">➕ 客户新增非标</span>
        <span class="stat-card-val" id="statNewClauses">0</span>
      </div>
      <div class="stat-card warning">
        <span class="stat-card-title">🟠 期限/违约微调</span>
        <span class="stat-card-val" id="statMediumRisk">0</span>
      </div>
      <div class="stat-card progress">
        <span class="stat-card-title">⚖️ 批注核验进度</span>
        <span class="stat-card-val" id="statProgress">0 / 0</span>
      </div>
    </div>
  </div>

  <!-- 固定控制导航条 -->
  <div class="controls-bar">
    <div class="control-left-group">
      <!-- 视图模式切换 -->
      <div class="view-switch">
        <button class="view-btn active" id="btnModeSide" onclick="setViewMode('side')">📖 整版原格式双栏对照</button>
        <button class="view-btn" id="btnModeSingle" onclick="setViewMode('single')">📄 送审单版标红审阅</button>
        <button class="view-btn" id="btnModeDiff" onclick="setViewMode('diff')">🎯 仅看变更条款</button>
      </div>

      <!-- 标红版式风格切换 -->
      <div class="style-switch">
        <button class="style-btn active" id="btnStyleRedline" onclick="setRedlineMode(true)">🔴 经典法务纯标红</button>
        <button class="style-btn" id="btnStyleColor" onclick="setRedlineMode(false)">🎨 红绿对照</button>
      </div>

      <!-- 上下批注快速定位 -->
      <div class="nav-group">
        <button class="nav-btn" onclick="prevAnnotation()" title="快捷键: [ 或 Alt+↑">⬅️ 上一处</button>
        <span class="nav-indicator" id="navIndicator">批注 0 / 0</span>
        <button class="nav-btn" onclick="nextAnnotation()" title="快捷键: ] 或 Alt+↓">下一处 ➡️</button>
      </div>
    </div>

    <div class="control-right-group">
      <button class="btn btn-toggle-sidebar" id="btnToggleSidebar" onclick="toggleSidebarCollapse()">
        <span>📋 审阅批注专栏</span>
        <span class="sidebar-badge-count" id="btnSidebarCount">0</span>
      </button>
    </div>
  </div>

  <!-- ==================== 核心对比工作区：整版原格式对照 + 批注专栏 ==================== -->
  <div class="review-workspace" id="reviewWorkspace">

    <!-- 左/中：整版原格式连续 A4 纸质对照画布 -->
    <div class="document-dual-stage-wrapper" id="documentStageWrapper">
      <div class="stage-headers" id="stageHeaders">
        <div class="stage-col-header standard">
          <span>🏛️ 知识库标准参考母本（基准正文）</span>
          <span class="stage-header-sub" id="headerStdName">母本库</span>
        </div>
        <div class="stage-col-header reviewed">
          <span>✏️ 送审合同文本（待审稿·标红对照）</span>
          <span class="stage-header-sub" id="headerRevName">待审稿</span>
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

</main>

<script>
// 全局状态管理
let currentData = null;
let currentViewMode = 'side'; // 'side' | 'single' | 'diff'
let currentFilter = 'ALL';
let activeAnnotationIndex = -1;

// 预置默认数据占位符
window.INITIAL_DATA = __INITIAL_DATA_PLACEHOLDER__;

// 拖拽上传支持
const dropZone = document.getElementById('dropZone');
dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  if (e.dataTransfer.files.length > 0) {
    uploadDocx(e.dataTransfer.files[0]);
  }
});

function handleFileSelect(e) {
  if (e.target.files.length > 0) {
    uploadDocx(e.target.files[0]);
  }
}

function toggleUploadPanel() {
  const panel = document.getElementById('uploadPanel');
  panel.style.display = panel.style.display === 'none' ? 'grid' : 'none';
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

  // 已由后端结构化包装的特殊插槽（如缺失整块、新增整块）
  if (html.includes('doc-block-missing-') || html.includes('doc-block-blank-') || html.includes('doc-block-new-')) {
    return html;
  }

  // 条款混合节点（条标题 + 条正文段落）精细化分层
  if (category === 'ARTICLE_SECTION') {
    // 检查是否包含 "第X条 事项\n正文..."
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

  // 1. 顶部元信息与审查看板
  const rawFileName = (data.uploaded_file || '待审合同.docx').split(/[\/\\]/).pop();
  let friendlyDocTitle = rawFileName;
  if (rawFileName.includes('mut_11_late_fee_change')) {
    friendlyDocTitle = '《国内专利服务合同（年费缴纳）》送审修改稿';
  } else if (rawFileName.includes('mut_06_missing_confidentiality')) {
    friendlyDocTitle = '《国内专利服务合同（年费缴纳）》送审修改稿（保密义务被删）';
  } else if (rawFileName.includes('驳回复审')) {
    friendlyDocTitle = '《商标驳回复审代理协议》送审原稿';
  } else if (rawFileName.startsWith('mut_')) {
    friendlyDocTitle = '合同送审修改稿';
  }

  // 待审文档标题展示（面向用户纯净展示规范名称或上传文件名，不暴露内部测试样本文件名）
  const docFileEl = document.getElementById('docFileName');
  docFileEl.innerText = friendlyDocTitle;

  // 母本文件名称展示（杜绝显示 16 位底层十六进制 Hash 码，展示真实母本文档名）
  const tplFileName = data.matched_template_name || '标准母本.docx';
  const tplCleanName = tplFileName.replace(/\.docx?$/i, '');
  const matchedTagEl = document.getElementById('matchedTemplateTag');
  matchedTagEl.innerText = '🎯 已锁定母本: ' + tplCleanName;
  matchedTagEl.title = `基准文档: ${tplFileName} (母本库底层哈希: ${data.matched_template_id || '-'})`;

  // 双栏纸张列标题
  document.getElementById('headerStdName').innerText = tplCleanName;
  document.getElementById('headerRevName').innerText = friendlyDocTitle;

  // 业务子类中文映射字典（避免显示未翻译的英文枚举代码）
  const SUBTYPE_NAMES = {
    'PATENT_ANNUAL_FEE': '国内专利服务（年费缴纳）',
    'TRADEMARK_REJECTION': '商标驳回复审代理',
    'PATENT_AGENT': '国内专利代理委托',
    'PATENT_FAST_PRESCREEN': '国内专利快速预审服务',
    'PURCHASE_AGREEMENT': '设备与材料采购合同',
    'LABOR_CONTRACT': '劳动用工合同',
    'NDA': '商业秘密保密协议 (NDA)',
    'SERVICE_AGREEMENT': '技术咨询与专业服务合同'
  };
  const subtypeCn = SUBTYPE_NAMES[data.detected_subtype] || data.detected_subtype || '-';
  document.getElementById('detectedSubtype').innerText = subtypeCn;
  document.getElementById('confidenceRate').innerText = ((data.classification_confidence || 1.0) * 100).toFixed(1) + '%';
  document.getElementById('matchedTemplateName').innerText = tplFileName;
  const tplIdEl = document.getElementById('matchedTemplateId');
  if (tplIdEl) tplIdEl.innerText = data.matched_template_id || '-';
  document.getElementById('stdCount').innerText = data.standard_clause_count || 0;
  document.getElementById('revCount').innerText = data.uploaded_clause_count || 0;

  // 统计数据
  const stats = data.stats || {};
  const annCount = (data.annotations || []).length;
  document.getElementById('statTotalAnnotations').innerText = stats.total_annotations || annCount;
  document.getElementById('statHighRisk').innerText = stats.high_risk || 0;
  document.getElementById('statMissingClauses').innerText = stats.missing_clauses || 0;
  document.getElementById('statNewClauses').innerText = stats.new_clauses || 0;
  document.getElementById('statMediumRisk').innerText = (stats.medium_risk || 0);

  document.getElementById('sidebarCount').innerText = annCount;
  const btnSidebarCountEl = document.getElementById('btnSidebarCount');
  if (btnSidebarCountEl) btnSidebarCountEl.innerText = annCount;

  // 综合判定 Banner
  const verdictEl = document.getElementById('verdictBanner');
  if (stats.high_risk > 0 || stats.missing_clauses > 0) {
    verdictEl.className = 'verdict-banner danger';
    verdictEl.innerText = `🚨 发现 ${stats.high_risk} 处高危条款篡改与 ${stats.missing_clauses} 处核心条款缺失，建议重点复核并驳回修改`;
  } else if (annCount > 0) {
    verdictEl.className = 'verdict-banner';
    verdictEl.style.background = '#FEF3C7';
    verdictEl.style.color = '#92400E';
    verdictEl.innerText = `⚠️ 存在 ${stats.medium_risk || 0} 处常规条款微调，请确认商业合规性`;
  } else {
    verdictEl.className = 'verdict-banner success';
    verdictEl.innerText = `✅ 与 78 套标准母本库 100% 吻合，无任何删改，可直接推进审批签署`;
  }

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
  document.getElementById('statProgress').innerText = `${handled} / ${total}`;
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
          <div class="comp-row"><span class="comp-label">送审稿:</span><span class="comp-text del">（整段已被删除）</span></div>
        </div>
      `;
    } else if (ann.delta_type === 'NEW_CLAUSE') {
      compHtml = `
        <div class="card-comparison-box">
          <div class="comp-row"><span class="comp-label">母本约定:</span><span class="comp-text">（母本无此条款）</span></div>
          <div class="comp-row"><span class="comp-label">送审稿:</span><span class="comp-text ins">${escapeHtml(ann.reviewed_span)}</span></div>
        </div>
      `;
    } else {
      compHtml = `
        <div class="card-comparison-box">
          <div class="comp-row"><span class="comp-label">母本原文:</span><span class="comp-text del">${escapeHtml(ann.standard_span || '')}</span></div>
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
      ? `<span>▶ 展开批注专栏</span> <span class="sidebar-badge-count">${(currentData.annotations || []).length}</span>`
      : `<span>📋 审阅批注专栏</span> <span class="sidebar-badge-count">${(currentData.annotations || []).length}</span>`;
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
        "--port", type=int, default=8772, help="Web 端口（默认 8772）"
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
