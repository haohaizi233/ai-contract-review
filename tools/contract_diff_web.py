# -*- coding: utf-8 -*-
"""合同比对与条款级 Diff 前端可视化服务（Contract Diff Web Visualizer）。

全新升级：
1. 整版双栏对照（Full-Document Side-by-Side Comparison）：
   从合同卷头、事项、各条款直到尾部签署页，完整呈现整篇合同阅读排版（A4 纸质排版风格），
   条款逐行精准水平对齐，支持双栏同步滚动。
2. 改变的地方加批注（Legal Review Annotations & Margin Callouts）：
   对所有修改、篡改、删除、新增条款建立独立法务批注卡片；
   正文中以互动标签 [批注 #XX] 锚定，右侧批注栏同步展现风险等级、母本对比、法律风险剖析与处置建议；
   支持正文与批注卡片双向点击对焦、高亮脉冲动画与快捷键定位。
3. 三种视图自由切换：
   - 整版双栏对照 + 侧边批注（Side-by-Side + Annotations）
   - 单版正文修订 + 批注引导（Word/WPS 经典审阅模式）
   - 仅看变更条款（Diff Only Focus）
4. 交互式审阅流程：
   支持标记“确认接受”、“存疑待核”、“驳回修改”，实时统计审阅处理进度；
   支持拖拽上传 DOCX 与快速加载高危实测用例；
   支持独立离线 HTML 导出与开箱即用。

用法：
  python tools/contract_diff_web.py [--port 8772]
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
<title>AI 合同审查 · 条款级 Diff 与整版比对工作台</title>
<style>
  :root {
    --bg: #F1F5F9;
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

  /* 主容器（宽幅双栏优先） */
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

  /* 状态概览卡片 */
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

  .filter-chips { display: flex; gap: 6px; }
  .filter-chip {
    padding: 3px 10px; border-radius: 999px; border: 1px solid var(--border);
    background: #FFF; font-size: 11px; font-weight: 600; color: #475569;
    cursor: pointer; transition: all 0.15s ease; white-space: nowrap;
  }
  .filter-chip.active {
    background: #1E293B; color: #FFF; border-color: #1E293B;
  }

  .control-right-group { display: flex; align-items: center; gap: 10px; }
  .btn-drawer {
    background: #1E293B !important; color: #FFF !important; border-color: #1E293B !important;
  }
  .btn-drawer:hover { background: #334155 !important; }
  .drawer-count-badge {
    background: #EF4444; color: #FFF; font-size: 10.5px; padding: 1px 6px; border-radius: 999px; font-weight: 800;
  }
  .sync-toggle-label {
    font-size: 12px; font-weight: 600; color: #475569; display: flex; align-items: center; gap: 6px;
    cursor: pointer;
  }

  /* ==================== 核心工作区：左右 50/50 纯双栏对齐 ==================== */
  .workspace-layout {
    display: block;
    width: 100%;
    position: relative;
    transition: all 0.2s ease;
  }

  /* 宽屏固定侧栏模式 */
  .workspace-layout.sidebar-pinned {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 440px;
    gap: 18px;
    align-items: start;
  }

  /* 单版正文修订模式 */
  .workspace-layout.mode-single {
    display: block;
    width: 100%;
  }

  /* 整版合同双栏对照容器（A4纸张风格） */
  .document-grid-container {
    background: #FFFFFF;
    border: 1px solid var(--border);
    border-radius: 12px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.03);
    overflow: hidden;
    width: 100%;
  }
  .document-grid-header {
    display: grid;
    grid-template-columns: 1fr 1fr;
    background: #F8FAFC;
    border-bottom: 2px solid #CBD5E1;
    position: sticky;
    top: 114px;
    z-index: 80;
  }
  .mode-single .document-grid-header {
    grid-template-columns: 1fr;
  }
  .grid-header-col {
    padding: 12px 24px;
    font-size: 14px;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .grid-header-col.standard {
    border-right: 2px solid #CBD5E1;
    background: #F8FAFC;
    color: #475569;
  }
  .grid-header-col.reviewed {
    background: #EFF6FF;
    color: #1E40AF;
  }

  /* 条款行（整版行对齐） */
  .aligned-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    border-bottom: 1px solid #E2E8F0;
    position: relative;
    transition: background 0.15s ease, box-shadow 0.15s ease;
  }
  .mode-single .aligned-row {
    grid-template-columns: 1fr;
  }
  .mode-single .pane-standard {
    display: none !important;
  }

  .aligned-row:hover {
    background: #F8FAFC;
  }
  .aligned-row.row-flash {
    animation: rowPulse 1.8s ease-in-out;
  }
  @keyframes rowPulse {
    0% { background: #FEF08A; box-shadow: 0 0 0 2px #EAB308 inset; }
    50% { background: #FEF9C3; }
    100% { background: transparent; }
  }

  /* 左右两栏单元格（A4质感排版） */
  .pane-standard, .pane-reviewed {
    padding: 18px 24px;
    font-size: 14px;
    line-height: 1.85;
    position: relative;
    word-break: break-all;
    white-space: pre-wrap;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
  }
  .pane-standard {
    border-right: 2px solid #E2E8F0;
    color: #334155;
    background: #FAFAFC;
  }
  .pane-reviewed {
    color: #0F172A;
    background: #FFFFFF;
  }

  /* 条款顶栏标签条 */
  .clause-header-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
    padding-bottom: 6px;
    border-bottom: 1px dashed #E2E8F0;
  }
  .clause-tag {
    font-size: 11px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 4px;
    display: inline-block;
  }
  .clause-tag.std {
    background: #EEF2F6;
    color: #475569;
    border: 1px solid #CBD5E1;
  }
  .clause-tag.rev {
    background: #E0E7FF;
    color: #3730A3;
    border: 1px solid #C7D2FE;
  }
  .clause-status-pill {
    font-size: 10.5px;
    font-weight: 700;
    padding: 1px 7px;
    border-radius: 4px;
  }
  .clause-status-pill.status-match { background: #DCFCE7; color: #15803D; }
  .clause-status-pill.status-diff { background: #FEF3C7; color: #92400E; }
  .clause-status-pill.status-missing { background: #FEE2E2; color: #991B1B; }
  .clause-status-pill.status-new { background: #F3E8FF; color: #7C3AED; }

  /* ==================== 标红 (Redline) 核心视觉规范 ==================== */
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

  /* 2. 插入文本（红绿对比模式） */
  .diff-ins, ins {
    background: #DCFCE7;
    color: #15803D;
    text-decoration: underline 2px #16A34A;
    border: 1px solid #BBF7D0;
    font-weight: 700;
    padding: 1px 4px;
    border-radius: 3px;
    display: inline;
  }

  /* 3. 高危数值/比率/期限修改特别标红醒目徽章 */
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

  /* 4. 【经典法务纯标红模式】：所有插入文本改为双红下划线/红字标红 */
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
  .mode-pure-redline .diff-ins-number {
    background: #FEF08A !important;
    color: #7F1D1D !important;
    border: 2px solid #DC2626 !important;
    font-weight: 900 !important;
    box-shadow: 0 2px 6px rgba(220, 38, 38, 0.3) !important;
  }

  /* 缺失与新增条款整块样式 */
  .diff-block-missing-wrap {
    background: #FFF5F5;
    border: 1.5px solid #FECACA;
    border-left: 5px solid #DC2626;
    padding: 12px 16px;
    border-radius: 6px;
  }
  .badge-missing {
    background: #DC2626;
    color: #FFF;
    font-size: 11px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 4px;
    display: inline-block;
    margin-bottom: 8px;
  }
  .diff-placeholder-missing {
    background: #FEF2F2;
    border: 2px dashed #F87171;
    border-radius: 8px;
    padding: 16px;
    color: #991B1B;
    font-size: 13.5px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    align-items: stretch;
  }
  .placeholder-icon { font-size: 16px; margin-right: 4px; }
  .placeholder-text { font-weight: 700; font-size: 13px; }

  .diff-placeholder-new {
    background: #F8FAFC;
    border: 2px dashed #CBD5E1;
    border-radius: 8px;
    padding: 16px;
    color: #64748B;
    font-size: 13px;
  }
  .diff-block-new-wrap {
    background: #FAF5FF;
    border: 1.5px solid #DDD6FE;
    border-left: 5px solid #7C3AED;
    padding: 12px 16px;
    border-radius: 6px;
  }
  .badge-new {
    background: #7C3AED;
    color: #FFF;
    font-size: 11px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 4px;
    display: inline-block;
    margin-bottom: 8px;
  }

  /* ==================== 条款内嵌法务批注卡片 (Inline Annotation Callout) ==================== */
  .inline-ann-container {
    margin-top: 14px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .inline-ann-card {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-left: 4px solid #2563EB;
    border-radius: 8px;
    padding: 12px 16px;
    box-shadow: 0 2px 6px rgba(0,0,0,0.04);
    cursor: pointer;
    transition: all 0.2s ease;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
  }
  .inline-ann-card:hover {
    border-color: #93C5FD;
    box-shadow: 0 4px 12px rgba(37, 99, 235, 0.12);
  }
  .inline-ann-card.HIGH { border-left-color: #DC2626; background: #FFFDFD; }
  .inline-ann-card.MEDIUM { border-left-color: #D97706; background: #FFFDF8; }
  .inline-ann-card.LOW { border-left-color: #64748B; background: #F8FAFC; }
  .inline-ann-card.active { box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.4), 0 4px 12px rgba(0,0,0,0.08); }

  .inline-ann-card.status-ACCEPTED { border-left-color: #16A34A !important; background: #F0FDF4 !important; }
  .inline-ann-card.status-QUESTIONED { border-left-color: #D97706 !important; background: #FFFBEB !important; }
  .inline-ann-card.status-OVERRULED { border-left-color: #94A3B8 !important; background: #F8FAFC !important; opacity: 0.85; }

  .inline-ann-top {
    display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; gap: 8px;
  }
  .inline-ann-tag {
    font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 4px;
  }
  .inline-ann-tag.HIGH { background: #FEE2E2; color: #991B1B; border: 1px solid #FECACA; }
  .inline-ann-tag.MEDIUM { background: #FEF3C7; color: #92400E; border: 1px solid #FDE68A; }
  .inline-ann-tag.LOW { background: #F1F5F9; color: #475569; }

  .inline-ann-title { font-size: 13px; font-weight: 700; color: #1E293B; flex: 1; }
  .inline-status-badge {
    font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 4px;
    background: #E2E8F0; color: #475569;
  }
  .inline-status-badge.ACCEPTED { background: #DCFCE7; color: #15803D; border: 1px solid #BBF7D0; }
  .inline-status-badge.QUESTIONED { background: #FEF3C7; color: #92400E; border: 1px solid #FDE68A; }
  .inline-status-badge.OVERRULED { background: #F1F5F9; color: #64748B; border: 1px solid #CBD5E1; }

  .inline-ann-diff-summary {
    background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px;
    padding: 6px 10px; font-size: 12px; margin-bottom: 8px; display: flex;
    align-items: center; gap: 8px; flex-wrap: wrap;
  }
  .comp-std { color: #64748B; }
  .comp-std del { color: #B91C1C; background: #FEE2E2; text-decoration: line-through 1.5px #DC2626; padding: 0 4px; border-radius: 2px; }
  .comp-arrow { font-weight: bold; color: #94A3B8; }
  .comp-rev { color: #0F172A; }
  .comp-rev ins { color: #15803D; background: #DCFCE7; text-decoration: underline 1.5px #16A34A; padding: 0 4px; border-radius: 2px; }
  .mode-pure-redline .comp-rev ins { color: #B91C1C !important; background: #FEF2F2 !important; text-decoration: underline 1.5px #DC2626 !important; }

  .inline-ann-desc { font-size: 12.5px; color: #334155; margin-bottom: 6px; line-height: 1.55; }
  .inline-ann-advice {
    font-size: 12px; color: #1E40AF; background: #EFF6FF; border-left: 3px solid #2563EB;
    padding: 7px 10px; border-radius: 0 4px 4px 0; margin-bottom: 8px; line-height: 1.5;
  }

  .inline-ann-actions {
    display: flex; align-items: center; gap: 8px; padding-top: 8px;
    border-top: 1px dashed #E2E8F0; margin-top: 6px;
  }
  .inline-actions-label { font-size: 11px; color: #64748B; font-weight: 700; }
  .inline-action-btn {
    font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 5px;
    border: 1px solid var(--border); background: #FFFFFF; color: #334155; cursor: pointer;
    transition: all 0.15s ease;
  }
  .inline-action-btn:hover { background: #F1F5F9; }
  .inline-action-btn.accept:hover { background: #DCFCE7; color: #15803D; border-color: #86EFAC; }
  .inline-action-btn.question:hover { background: #FEF3C7; color: #92400E; border-color: #FCD34D; }
  .inline-action-btn.overrule:hover { background: #F1F5F9; color: #64748B; border-color: #94A3B8; }

  /* ==================== 滑出式法务批注抽屉 (Slide-out Drawer) ==================== */
  .annotations-drawer {
    position: fixed;
    top: 0;
    right: -500px;
    width: 480px;
    height: 100vh;
    background: #FFFFFF;
    box-shadow: -6px 0 28px rgba(0,0,0,0.16);
    z-index: 1000;
    transition: right 0.3s cubic-bezier(0.16, 1, 0.3, 1);
    display: flex;
    flex-direction: column;
  }
  .annotations-drawer.open {
    right: 0;
  }
  .drawer-backdrop {
    position: fixed;
    top: 0;
    left: 0;
    width: 100vw;
    height: 100vh;
    background: rgba(15, 23, 42, 0.45);
    backdrop-filter: blur(2px);
    z-index: 999;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.25s ease;
  }
  .drawer-backdrop.open {
    opacity: 1;
    pointer-events: auto;
  }

  /* 当用户选择固定侧边栏时，取消 fixed 抽屉，变为标准 grid 侧栏 */
  .workspace-layout.sidebar-pinned .annotations-drawer {
    position: sticky;
    top: 114px;
    right: auto;
    width: 100%;
    max-height: calc(100vh - 130px);
    box-shadow: 0 1px 4px rgba(0,0,0,0.03);
    border: 1px solid var(--border);
    border-radius: 12px;
    z-index: 85;
  }
  .workspace-layout.sidebar-pinned + .drawer-backdrop {
    display: none !important;
  }

  .sidebar-header {
    padding: 14px 18px;
    border-bottom: 1px solid var(--border);
    background: #F8FAFC;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .sidebar-title { font-size: 14px; font-weight: 700; color: #1E293B; display: flex; align-items: center; gap: 8px; }
  .sidebar-count-badge {
    background: #1E293B; color: #FFF; font-size: 11px; padding: 1px 7px; border-radius: 999px; font-weight: 800;
  }
  .mini-tool-btn {
    font-size: 11px; font-weight: 600; padding: 4px 8px; border-radius: 4px;
    border: 1px solid var(--border); background: #FFF; color: #475569; cursor: pointer;
  }
  .mini-tool-btn:hover { background: #F1F5F9; color: #0F172A; }
  .mini-tool-btn.close-btn { font-size: 14px; font-weight: 800; padding: 2px 7px; color: #64748B; }

  .annotations-list {
    overflow-y: auto;
    padding: 14px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    flex: 1;
  }

  /* 独立批注卡片 */
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
  }
  .annotation-card.status-QUESTIONED {
    border-left: 4px solid #D97706;
  }
  .annotation-card.status-OVERRULED {
    border-left: 4px solid #94A3B8;
    opacity: 0.8;
  }

  .card-top {
    display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px;
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
    font-size: 13px; font-weight: 700; color: #0F172A; margin-bottom: 6px;
  }

  /* 卡片内修改对比 */
  .card-comparison-box {
    background: #F8FAFC;
    border-radius: 6px;
    padding: 8px 10px;
    font-size: 11.5px;
    margin-bottom: 8px;
    border: 1px solid #EDF2F7;
    display: flex;
    flex-direction: column;
    gap: 4px;
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
    padding: 6px 8px; border-radius: 0 4px 4px 0; margin-bottom: 10px; line-height: 1.45;
  }

  /* 审阅处置按钮栏 */
  .card-actions {
    display: flex; justify-content: space-between; align-items: center; border-top: 1px solid #F1F5F9;
    padding-top: 8px; margin-top: 4px;
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
    .header, .controls-bar, .upload-panel, .header-actions, .card-actions, .annotations-drawer, .drawer-backdrop { display: none !important; }
    body { background: #FFF; }
    .main-wrapper { max-width: 100%; margin: 0; padding: 0; }
    .workspace-layout { display: block; }
    .aligned-row { page-break-inside: avoid; }
  }
</style>
</head>
<body class="mode-pure-redline">

<header class="header">
  <div class="brand">
    <div class="brand-logo">Δ</div>
    <div>
      <div style="display:flex; align-items:center; gap:8px;">
        <span class="brand-title">AI 合同审查 · 条款级 Diff 与整版比对工作台</span>
        <span class="brand-tag">78 套标准母本库接入版</span>
      </div>
      <div class="brand-sub">双栏整版阅读 · 字符级微观对齐 · 智能法务审阅批注</div>
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
      <div class="drop-desc">无需人工匹配模板：系统自动解析正文结构，秒级定位 78 套标准母本并出具整版比对与批注</div>
    </div>

    <div class="preset-section">
      <div class="preset-title">
        <span>⚡ 快速加载实测高危用例</span>
        <span style="font-size:10px; color:#94A3B8;">一键秒级还原法务实务</span>
      </div>
      <div class="preset-grid">
        <button class="preset-btn" onclick="loadSample('prescreen_tampered')">
          <span>🚨 快速预审 (违约金被偷改)</span>
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
        <span class="stat-card-title">🟠 期限/主体/弱化</span>
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
        <button class="view-btn active" id="btnModeSide" onclick="setViewMode('side')">📖 左右纯双栏标红 (50/50 宽屏)</button>
        <button class="view-btn" id="btnModeSingle" onclick="setViewMode('single')">📄 单版正文修订</button>
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
      <!-- 分类过滤标签 -->
      <div class="filter-chips">
        <button class="filter-chip active" onclick="setFilter('ALL')">全部 (<span id="filterAllCount">0</span>)</button>
        <button class="filter-chip" onclick="setFilter('HIGH')">🔴 高危篡改 (<span id="filterHighCount">0</span>)</button>
        <button class="filter-chip" onclick="setFilter('MISSING')">🚩 缺失条款 (<span id="filterMissingCount">0</span>)</button>
        <button class="filter-chip" onclick="setFilter('NEW')">➕ 客户新增 (<span id="filterNewCount">0</span>)</button>
        <button class="filter-chip" onclick="setFilter('WARNING')">🟠 期限/主体 (<span id="filterWarnCount">0</span>)</button>
      </div>

      <button class="btn btn-drawer" id="btnToggleDrawer" onclick="toggleDrawer()">
        <span>📋 审查批注抽屉</span>
        <span class="drawer-count-badge" id="btnDrawerCount">0</span>
      </button>
      <button class="btn" id="btnPinSidebar" onclick="toggleSidebarPin()" title="在超宽显示器上固定侧边栏">📌 固定侧栏</button>
      <label class="sync-toggle-label">
        <input type="checkbox" id="syncScrollCheckbox" checked onchange="toggleSyncScroll(this.checked)">
        <span>🔗 同步滚动</span>
      </label>
    </div>
  </div>

  <!-- 核心对比主工作区：纯 50/50 宽幅双栏 -->
  <div class="workspace-layout" id="workspaceLayout">
    <!-- 整版合同左右双栏对照区 -->
    <div class="document-grid-container" id="documentGridContainer">
      <div class="document-grid-header">
        <div class="grid-header-col standard">
          <span>🏛️ 知识库标准参考母本（基准正文）</span>
          <span style="font-size:11px; opacity:0.8;" id="headerStdName">母本库</span>
        </div>
        <div class="grid-header-col reviewed">
          <span>✏️ 送审合同文本（待审稿·标红对照）</span>
          <span style="font-size:11px; opacity:0.8;" id="headerRevName">待审稿</span>
        </div>
      </div>

      <!-- 整版行对齐主列表 -->
      <div id="alignedRowsContainer"></div>
    </div>

    <!-- 智能法务审阅批注抽屉 / 可固定侧边栏 -->
    <div class="annotations-drawer" id="annotationsDrawer">
      <div class="sidebar-header">
        <div class="sidebar-title">
          <span>📋 智能法务审阅批注</span>
          <span class="sidebar-count-badge" id="sidebarCount">0</span>
        </div>
        <div style="display:flex; align-items:center; gap:6px;">
          <button class="mini-tool-btn" id="btnDrawerPin" onclick="toggleSidebarPin()" title="切换固定为侧边栏或浮动抽屉">📌 固定</button>
          <button class="mini-tool-btn close-btn" onclick="toggleDrawer(false)" title="关闭抽屉">✕</button>
        </div>
      </div>

      <div class="annotations-list" id="annotationsList"></div>
    </div>
  </div>

  <!-- 抽屉遮罩背景 -->
  <div class="drawer-backdrop" id="drawerBackdrop" onclick="toggleDrawer(false)"></div>

</main>

<script>
// 全局状态管理
let currentData = null;
let currentViewMode = 'side'; // 'side' | 'single' | 'diff'
let currentFilter = 'ALL';
let activeAnnotationIndex = -1;
let syncScrollEnabled = true;

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

// 渲染整版对比与批注
function renderFullDiff(data) {
  currentData = data;
  activeAnnotationIndex = -1;

  // 1. 顶部元信息与审查看板
  const fileName = (data.uploaded_file || '待审合同.docx').split(/[\/\\]/).pop();
  document.getElementById('docFileName').innerText = fileName;
  document.getElementById('headerRevName').innerText = fileName;
  document.getElementById('matchedTemplateTag').innerText = '🎯 已锁定母本: ' + data.matched_template_id;
  document.getElementById('headerStdName').innerText = data.matched_template_id;

  document.getElementById('detectedSubtype').innerText = data.detected_subtype || '-';
  document.getElementById('confidenceRate').innerText = ((data.classification_confidence || 1.0) * 100).toFixed(1) + '%';
  document.getElementById('matchedTemplateName').innerText = data.matched_template_name || '-';
  document.getElementById('stdCount').innerText = data.standard_clause_count || 0;
  document.getElementById('revCount').innerText = data.uploaded_clause_count || 0;

  // 统计数据
  const stats = data.stats || {};
  document.getElementById('statTotalAnnotations').innerText = stats.total_annotations || (data.annotations ? data.annotations.length : 0);
  document.getElementById('statHighRisk').innerText = stats.high_risk || 0;
  document.getElementById('statMissingClauses').innerText = stats.missing_clauses || 0;
  document.getElementById('statNewClauses').innerText = stats.new_clauses || 0;
  document.getElementById('statMediumRisk').innerText = (stats.medium_risk || 0);
  const annCount = (data.annotations || []).length;
  document.getElementById('sidebarCount').innerText = annCount;
  const btnDrawerEl = document.getElementById('btnDrawerCount');
  if (btnDrawerEl) btnDrawerEl.innerText = annCount;

  // 综合判定 Banner
  const verdictEl = document.getElementById('verdictBanner');
  if (stats.high_risk > 0 || stats.missing_clauses > 0) {
    verdictEl.className = 'verdict-banner danger';
    verdictEl.innerText = `🚨 发现 ${stats.high_risk} 处高危数值篡改与 ${stats.missing_clauses} 处核心条款缺失，建议重点复核并驳回修改`;
  } else if ((data.annotations || []).length > 0) {
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
  renderAlignedRows();
  renderAnnotationsSidebar();
}

function updateProgressDisplay() {
  if (!currentData || !currentData.annotations) return;
  const total = currentData.annotations.length;
  const handled = currentData.annotations.filter(a => a.review_status && a.review_status !== 'PENDING').length;
  document.getElementById('statProgress').innerText = `${handled} / ${total}`;
}

// 渲染整版行对齐合同内容（左右纯双栏 50/50 宽屏）
function renderAlignedRows() {
  const container = document.getElementById('alignedRowsContainer');
  container.innerHTML = '';

  const blocks = currentData.aligned_blocks || [];
  blocks.forEach((block, bIdx) => {
    // 过滤逻辑
    const isMatch = block.status === 'MATCH';
    if (currentViewMode === 'diff' && isMatch) {
      return; // 仅看变更模式下跳过完全一致项
    }

    const row = document.createElement('div');
    row.className = 'aligned-row';
    row.id = block.block_id;

    // 状态标签
    let statusText = '完全一致';
    let statusClass = 'status-match';
    if (block.status === 'DIFF') {
      statusText = '存在修订';
      statusClass = 'status-diff';
    } else if (block.status === 'MISSING_IN_REVIEWED') {
      statusText = '母本条款被删';
      statusClass = 'status-missing';
    } else if (block.status === 'NEW_IN_REVIEWED') {
      statusText = '客户非标新增';
      statusClass = 'status-new';
    }

    // 左侧（母本基准正文）
    const paneStd = document.createElement('div');
    paneStd.className = 'pane-standard';
    paneStd.innerHTML = `
      <div class="clause-header-row">
        <span class="clause-tag std">🏛️ 标准参考母本 · ${escapeHtml(block.clause_title)}</span>
      </div>
      ${block.standard_html || '<div style="color:#94A3B8;">（无）</div>'}
    `;

    // 右侧（送审修改版，改变处就地内嵌批注卡片）
    const paneRev = document.createElement('div');
    paneRev.className = 'pane-reviewed';
    paneRev.innerHTML = `
      <div class="clause-header-row">
        <span class="clause-tag rev">✏️ 送审修改版 · ${escapeHtml(block.clause_title)}</span>
        <span class="clause-status-pill ${statusClass}">${statusText}</span>
      </div>
      ${block.reviewed_html || '<div style="color:#94A3B8;">（无）</div>'}
    `;

    row.appendChild(paneStd);
    row.appendChild(paneRev);
    container.appendChild(row);
  });
}

// 渲染右侧审阅批注卡片列表
function renderAnnotationsSidebar() {
  const container = document.getElementById('annotationsList');
  container.innerHTML = '';

  const anns = currentData.annotations || [];
  let visibleIndex = 0;

  anns.forEach((ann, aIdx) => {
    // 批注筛选
    if (currentFilter === 'HIGH' && (ann.risk_level !== 'HIGH' || ann.delta_type === 'MISSING_CLAUSE')) return;
    if (currentFilter === 'MISSING' && ann.delta_type !== 'MISSING_CLAUSE') return;
    if (currentFilter === 'NEW' && ann.delta_type !== 'NEW_CLAUSE') return;
    if (currentFilter === 'WARNING' && (ann.risk_level !== 'MEDIUM' || ann.delta_type === 'NEW_CLAUSE')) return;

    visibleIndex++;

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

// 选中某个批注并实现正文与卡片双向对齐平滑滚动与高光脉冲
function selectAnnotation(annId) {
  if (!currentData || !currentData.annotations) return;

  const annIdx = currentData.annotations.findIndex(a => a.id === annId);
  if (annIdx === -1) return;
  activeAnnotationIndex = annIdx;
  const ann = currentData.annotations[annIdx];

  // 1. 高亮抽屉中的对应批注卡片
  document.querySelectorAll('.annotation-card').forEach(c => c.classList.remove('active'));
  const card = document.getElementById(`card-${annId}`);
  if (card) {
    card.classList.add('active');
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  // 2. 高亮正文行内的内嵌卡片
  document.querySelectorAll('.inline-ann-card').forEach(c => c.classList.remove('active'));
  const inlineCard = document.getElementById(`inline-${annId}`);
  if (inlineCard) {
    inlineCard.classList.add('active');
  }

  // 3. 定位正文条款行并播放发光脉冲动画
  const targetRow = document.getElementById(ann.block_id);
  if (targetRow) {
    document.querySelectorAll('.aligned-row').forEach(r => r.classList.remove('row-flash'));
    targetRow.classList.add('row-flash');
    targetRow.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  // 4. 更新导航指示器
  const total = currentData.annotations.length;
  document.getElementById('navIndicator').innerText = `批注 ${ann.index} / ${total}`;
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

// 设置审阅状态并同步更新抽屉与正文内嵌卡片
function setAnnotationStatus(annId, status) {
  const ann = (currentData.annotations || []).find(a => a.id === annId);
  if (!ann) return;
  ann.review_status = (ann.review_status === status) ? 'PENDING' : status;

  // 1. 刷新抽屉列表并保持激活
  renderAnnotationsSidebar();
  const card = document.getElementById(`card-${annId}`);
  if (card) card.classList.add('active');

  // 2. 同步就地更新正文行内嵌入的卡片
  const inlineCard = document.getElementById(`inline-${annId}`);
  if (inlineCard) {
    inlineCard.classList.remove('status-ACCEPTED', 'status-QUESTIONED', 'status-OVERRULED');
    if (ann.review_status !== 'PENDING') {
      inlineCard.classList.add(`status-${ann.review_status}`);
    }
  }
  const statusBadge = document.getElementById(`status-badge-${annId}`);
  if (statusBadge) {
    statusBadge.className = 'inline-status-badge ' + (ann.review_status || 'PENDING');
    if (ann.review_status === 'ACCEPTED') statusBadge.innerText = '✅ 已接受';
    else if (ann.review_status === 'QUESTIONED') statusBadge.innerText = '⚠️ 存疑';
    else if (ann.review_status === 'OVERRULED') statusBadge.innerText = '❌ 已驳回';
    else statusBadge.innerText = '待审阅';
  }

  updateProgressDisplay();
}

// 抽屉展开/关闭切换
function toggleDrawer(forceState) {
  const drawer = document.getElementById('annotationsDrawer');
  const backdrop = document.getElementById('drawerBackdrop');
  const isOpen = (typeof forceState === 'boolean') ? forceState : !drawer.classList.contains('open');
  drawer.classList.toggle('open', isOpen);
  backdrop.classList.toggle('open', isOpen);
}

// 宽屏模式下固定为右侧栏 / 浮动抽屉切换
function toggleSidebarPin() {
  const layout = document.getElementById('workspaceLayout');
  const drawer = document.getElementById('annotationsDrawer');
  const backdrop = document.getElementById('drawerBackdrop');
  const isPinned = layout.classList.toggle('sidebar-pinned');
  drawer.classList.toggle('open', false);
  backdrop.classList.toggle('open', false);

  const pinBtn = document.getElementById('btnPinSidebar');
  const drawerPinBtn = document.getElementById('btnDrawerPin');
  const label = isPinned ? '🔓 浮动抽屉' : '📌 固定侧栏';
  if (pinBtn) pinBtn.innerText = label;
  if (drawerPinBtn) drawerPinBtn.innerText = label;
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

  const layout = document.getElementById('workspaceLayout');
  layout.classList.toggle('mode-single', mode === 'single');

  renderAlignedRows();
}

// 分类筛选
function setFilter(filterType) {
  currentFilter = filterType;
  document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
  event.target.classList.add('active');
  renderAnnotationsSidebar();
}

function toggleSyncScroll(enabled) {
  syncScrollEnabled = enabled;
}

// 键盘快捷键支持：[ 上一个，] 下一个，Esc 关闭抽屉
window.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === '[' || (e.altKey && e.key === 'ArrowUp')) {
    e.preventDefault();
    prevAnnotation();
  } else if (e.key === ']' || (e.altKey && e.key === 'ArrowDown')) {
    e.preventDefault();
    nextAnnotation();
  } else if (e.key === 'Escape') {
    toggleDrawer(false);
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
            # 渲染并提供页面
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
    print(f"[已生成独立整版对照与批注网页] {out_path}")


def main():
    parser = argparse.ArgumentParser(
        description="合同整版对照与条款批注前端服务"
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
        f"\n🚀 AI 合同审查 · 条款级 Diff 与整版比对工作台已启动: http://127.0.0.1:{args.port}"
    )
    print(
        "👉 请在浏览器中打开上方网址，即可体验整版双栏对照与智能法务审阅批注！\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")


if __name__ == "__main__":
    main()
