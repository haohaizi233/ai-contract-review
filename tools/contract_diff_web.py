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

  /* 主容器 */
  .main-wrapper {
    max-width: 1820px;
    margin: 16px auto;
    padding: 0 20px;
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
  .control-left-group { display: flex; align-items: center; gap: 14px; }
  .view-switch {
    display: flex; background: #E2E8F0; padding: 2px; border-radius: 8px;
  }
  .view-btn {
    padding: 5px 14px; font-size: 12px; font-weight: 600; border-radius: 6px;
    border: none; background: transparent; color: #64748B; cursor: pointer;
    transition: all 0.15s ease;
  }
  .view-btn.active {
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
    cursor: pointer; transition: all 0.15s ease;
  }
  .filter-chip.active {
    background: #1E293B; color: #FFF; border-color: #1E293B;
  }

  .control-right-group { display: flex; align-items: center; gap: 10px; }
  .sync-toggle-label {
    font-size: 12px; font-weight: 600; color: #475569; display: flex; align-items: center; gap: 6px;
    cursor: pointer;
  }

  /* ==================== 核心工作区：整版对照 + 侧边批注 ==================== */
  .workspace-layout {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 420px;
    gap: 16px;
    align-items: start;
  }

  /* 当切换为单栏审阅模式时 */
  .workspace-layout.mode-single {
    grid-template-columns: minmax(0, 1fr) 420px;
  }

  /* 整版合同双栏对照容器 */
  .document-grid-container {
    background: #FFFFFF;
    border: 1px solid var(--border);
    border-radius: 12px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.03);
    overflow: hidden;
  }
  .document-grid-header {
    display: grid;
    grid-template-columns: 1fr 1fr;
    background: #F8FAFC;
    border-bottom: 2px solid #E2E8F0;
    position: sticky;
    top: 114px;
    z-index: 80;
  }
  .mode-single .document-grid-header {
    grid-template-columns: 1fr;
  }
  .grid-header-col {
    padding: 10px 18px;
    font-size: 13px;
    font-weight: 700;
    color: #334155;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .grid-header-col.standard {
    border-right: 1px solid var(--border);
    background: #F8FAFC;
    color: #475569;
  }
  .grid-header-col.reviewed {
    background: #F0F7FF;
    color: #1E40AF;
  }

  /* 条款行（整版行对齐） */
  .aligned-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    border-bottom: 1px solid #EDF2F7;
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

  /* 左右两栏单元格 */
  .pane-standard, .pane-reviewed {
    padding: 14px 20px;
    font-size: 13.5px;
    line-height: 1.75;
    position: relative;
    word-break: break-all;
    white-space: pre-wrap;
  }
  .pane-standard {
    border-right: 1px solid #F1F5F9;
    color: #334155;
    background: #FFFFFF;
  }
  .pane-reviewed {
    color: #0F172A;
    background: #FFFFFF;
  }

  /* 条款类别小标签 */
  .clause-tag {
    font-size: 11px;
    font-weight: 700;
    padding: 1px 7px;
    border-radius: 4px;
    background: #EEF2F6;
    color: #475569;
    margin-bottom: 6px;
    display: inline-block;
  }

  /* 缺失与新增条款整块样式 */
  .diff-block-missing-wrap {
    background: #FFF5F5;
    border-left: 3px solid #EF4444;
    padding: 10px 12px;
    border-radius: 4px;
  }
  .badge-missing {
    font-size: 11px; font-weight: 700; color: #DC2626; margin-bottom: 4px; display: block;
  }
  .diff-placeholder-missing {
    background: #FEF2F2;
    border: 1px dashed #FECACA;
    border-radius: 6px;
    padding: 14px 16px;
    color: #991B1B;
    font-size: 13px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    align-items: flex-start;
  }
  .placeholder-icon { font-size: 16px; margin-right: 4px; }
  .placeholder-text { font-weight: 600; font-size: 12.5px; }

  .diff-placeholder-new {
    background: #F8FAFC;
    border: 1px dashed #CBD5E1;
    border-radius: 6px;
    padding: 14px 16px;
    color: #64748B;
    font-size: 12.5px;
  }
  .diff-block-new-wrap {
    background: #FAF5FF;
    border-left: 3px solid #8B5CF6;
    padding: 10px 12px;
    border-radius: 4px;
  }
  .badge-new {
    font-size: 11px; font-weight: 700; color: #7C3AED; margin-bottom: 4px; display: block;
  }

  /* 修订高亮 */
  .diff-del {
    background: #FECACA;
    color: #991B1B;
    text-decoration: line-through;
    padding: 1px 3px;
    border-radius: 2px;
  }
  .diff-ins {
    background: #BBF7D0;
    color: #14532D;
    font-weight: 600;
    padding: 1px 3px;
    border-radius: 2px;
  }
  .diff-ins-number {
    background: #FDE68A !important;
    color: #92400E !important;
    font-weight: 700 !important;
    border-bottom: 2px solid #D97706;
    padding: 1px 4px;
    border-radius: 3px;
  }

  /* 行内批注定位锚点药丸标签 */
  .ann-anchor-bar {
    margin-top: 8px;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .ann-pill {
    font-size: 11px;
    font-weight: 700;
    padding: 3px 9px;
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.15s ease;
    display: inline-flex;
    align-items: center;
    gap: 4px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
  }
  .ann-pill:hover {
    transform: translateY(-1px);
    box-shadow: 0 2px 4px rgba(0,0,0,0.12);
  }
  .ann-pill.danger {
    background: #FEE2E2; color: #991B1B; border: 1px solid #FCA5A5;
  }
  .ann-pill.warning {
    background: #FEF3C7; color: #92400E; border: 1px solid #FCD34D;
  }
  .ann-pill.info {
    background: #EFF6FF; color: #1E40AF; border: 1px solid #BFDBFE;
  }
  .ann-pill.purple {
    background: #F3E8FF; color: #6B21A8; border: 1px solid #D8B4FE;
  }

  /* ==================== 右侧：智能法务审阅批注栏 ==================== */
  .annotations-sidebar {
    background: #FFFFFF;
    border: 1px solid var(--border);
    border-radius: 12px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.03);
    position: sticky;
    top: 114px;
    max-height: calc(100vh - 130px);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .sidebar-header {
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
    background: #F8FAFC;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .sidebar-title { font-size: 13.5px; font-weight: 700; color: #1E293B; display: flex; align-items: center; gap: 6px; }
  .sidebar-count-badge {
    background: #1E293B; color: #FFF; font-size: 11px; padding: 1px 6px; border-radius: 999px;
  }

  .annotations-list {
    overflow-y: auto;
    padding: 12px;
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
    .header, .controls-bar, .upload-panel, .header-actions, .card-actions { display: none !important; }
    body { background: #FFF; }
    .main-wrapper { max-width: 100%; margin: 0; padding: 0; }
    .workspace-layout { display: block; }
    .annotations-sidebar { position: static; max-height: none; margin-top: 30px; }
    .aligned-row { page-break-inside: avoid; }
  }
</style>
</head>
<body>

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
        <button class="view-btn active" id="btnModeSide" onclick="setViewMode('side')">📑 整版双栏对照 + 批注</button>
        <button class="view-btn" id="btnModeSingle" onclick="setViewMode('single')">📄 单版正文修订 + 侧边批注</button>
        <button class="view-btn" id="btnModeDiff" onclick="setViewMode('diff')">🎯 仅看变更条款</button>
      </div>

      <!-- 上下批注快速定位 -->
      <div class="nav-group">
        <button class="nav-btn" onclick="prevAnnotation()" title="快捷键: [ 或 Alt+↑">⬅️ 上一处批注</button>
        <span class="nav-indicator" id="navIndicator">批注 0 / 0</span>
        <button class="nav-btn" onclick="nextAnnotation()" title="快捷键: ] 或 Alt+↓">下一处批注 ➡️</button>
      </div>

      <!-- 分类过滤标签 -->
      <div class="filter-chips">
        <button class="filter-chip active" onclick="setFilter('ALL')">全部 (<span id="filterAllCount">0</span>)</button>
        <button class="filter-chip" onclick="setFilter('HIGH')">🔴 高危篡改 (<span id="filterHighCount">0</span>)</button>
        <button class="filter-chip" onclick="setFilter('MISSING')">🚩 缺失条款 (<span id="filterMissingCount">0</span>)</button>
        <button class="filter-chip" onclick="setFilter('NEW')">➕ 客户新增 (<span id="filterNewCount">0</span>)</button>
        <button class="filter-chip" onclick="setFilter('WARNING')">🟠 期限/主体 (<span id="filterWarnCount">0</span>)</button>
      </div>
    </div>

    <div class="control-right-group">
      <label class="sync-toggle-label">
        <input type="checkbox" id="syncScrollCheckbox" checked onchange="toggleSyncScroll(this.checked)">
        <span>🔗 双栏同步滚动</span>
      </label>
    </div>
  </div>

  <!-- 核心对比主工作区 -->
  <div class="workspace-layout" id="workspaceLayout">
    <!-- 左侧/中间：整版合同对照区 -->
    <div class="document-grid-container" id="documentGridContainer">
      <div class="document-grid-header">
        <div class="grid-header-col standard">
          <span>🏛️ 知识库标准参考母本（基准正文）</span>
          <span style="font-size:11px; opacity:0.8;" id="headerStdName">母本库</span>
        </div>
        <div class="grid-header-col reviewed">
          <span>✏️ 送审合同文本（送审修改版）</span>
          <span style="font-size:11px; opacity:0.8;" id="headerRevName">待审稿</span>
        </div>
      </div>

      <!-- 整版行对齐主列表 -->
      <div id="alignedRowsContainer"></div>
    </div>

    <!-- 右侧：智能法务审阅批注栏 -->
    <div class="annotations-sidebar">
      <div class="sidebar-header">
        <div class="sidebar-title">
          <span>📋 智能法务审阅批注</span>
          <span class="sidebar-count-badge" id="sidebarCount">0</span>
        </div>
        <span style="font-size:11px; color:#64748B;">点击直达正文对应行</span>
      </div>

      <div class="annotations-list" id="annotationsList"></div>
    </div>
  </div>

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
  document.getElementById('sidebarCount').innerText = (data.annotations || []).length;

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

// 渲染整版行对齐合同内容
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

    // 左侧（母本）
    const paneStd = document.createElement('div');
    paneStd.className = 'pane-standard';
    paneStd.innerHTML = `
      <span class="clause-tag">${escapeHtml(block.clause_title)}</span>
      ${block.standard_html || '<div style="color:#94A3B8;">（无）</div>'}
    `;

    // 右侧（送审修改版）
    const paneRev = document.createElement('div');
    paneRev.className = 'pane-reviewed';
    paneRev.innerHTML = `
      <span class="clause-tag" style="background:#E0E7FF; color:#3730A3;">${escapeHtml(block.clause_title)}</span>
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

  // 1. 高亮右侧批注卡片
  document.querySelectorAll('.annotation-card').forEach(c => c.classList.remove('active'));
  const card = document.getElementById(`card-${annId}`);
  if (card) {
    card.classList.add('active');
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  // 2. 定位正文行并播放发光脉冲动画
  const targetRow = document.getElementById(ann.block_id);
  if (targetRow) {
    document.querySelectorAll('.aligned-row').forEach(r => r.classList.remove('row-flash'));
    targetRow.classList.add('row-flash');
    targetRow.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  // 3. 更新导航指示器
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

// 设置审阅状态
function setAnnotationStatus(annId, status) {
  const ann = (currentData.annotations || []).find(a => a.id === annId);
  if (!ann) return;
  ann.review_status = (ann.review_status === status) ? 'PENDING' : status;
  renderAnnotationsSidebar();
  // 保持当前高亮
  const card = document.getElementById(`card-${annId}`);
  if (card) card.classList.add('active');
  updateProgressDisplay();
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
