# -*- coding: utf-8 -*-
"""合同比对与条款级 Diff 前端可视化服务（Contract Diff Web Visualizer）。

功能：
1. 交互式 DOCX 拖拽上传与盲审自动识别（无需人工指定模板，自动锁定 78 套母本）；
2. 双栏并排对照（Side-by-Side）与行内标记（Inline Redline）双模式自由切换；
3. 法律风控级要素高亮：数值篡改(NUMBER_CHANGE)、期限放宽(DEADLINE_CHANGE)、责任倒置(PARTY_CHANGE)、
   否定词反转(NEGATION_CHANGE)、义务弱化(OBLIGATION_CHANGE)、条款缺失(MISSING)、客户新增(ADDED)；
4. 内置真实高危变异用例（快速预审违约金篡改、保密条款偷删、商标驳回复审），一键秒级体验；
5. 提供单文件独立 HTML 导出与轻量本地 Web 服务（零第三方 npm 依赖，开箱即用）。

用法：
  python tools/contract_diff_web.py [--port 8772]
"""

from __future__ import annotations

import argparse
import io
import json
import os
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

from contract_kb import config
from contract_kb.storage import Storage
from tools.diff_contract import run_diff

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI 合同审查 · 条款级 Diff 与风险透视工作台</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<style>
  :root {
    --bg: #F8FAFC;
    --card: #FFFFFF;
    --border: #E2E8F0;
    --border-hover: #CBD5E1;
    --text-main: #0F172A;
    --text-muted: #64748B;
    --text-sub: #94A3B8;
    --primary: #2563EB;
    --primary-light: #EFF6FF;
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
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    background: var(--bg);
    color: var(--text-main);
    line-height: 1.6;
    padding-bottom: 60px;
  }
  .header {
    background: #FFFFFF;
    border-bottom: 1px solid var(--border);
    padding: 14px 28px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    position: sticky;
    top: 0;
    z-index: 50;
    box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
  }
  .brand { display: flex; align-items: center; gap: 12px; }
  .brand-logo {
    width: 34px; height: 34px; border-radius: 8px;
    background: linear-gradient(135deg, #1E40AF 0%, #3B82F6 100%);
    color: #fff; display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 16px; box-shadow: 0 2px 4px rgba(37,99,235,0.25);
  }
  .brand-title { font-size: 17px; font-weight: 600; color: #1E293B; }
  .brand-tag { font-size: 11px; padding: 2px 8px; border-radius: 999px; background: #EEF2F6; color: #475569; font-weight: 500; }
  
  .container { max-width: 1440px; margin: 24px auto; padding: 0 24px; }
  
  /* 上传与预设区域 */
  .control-grid {
    display: grid;
    grid-template-columns: 1fr 340px;
    gap: 20px;
    margin-bottom: 24px;
  }
  .upload-box {
    background: var(--card);
    border: 2px dashed #CBD5E1;
    border-radius: 12px;
    padding: 24px 20px;
    text-align: center;
    cursor: pointer;
    transition: all 0.2s ease;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: 140px;
  }
  .upload-box:hover, .upload-box.dragover {
    border-color: var(--primary);
    background: var(--primary-light);
  }
  .upload-icon { font-size: 28px; margin-bottom: 8px; }
  .upload-title { font-size: 15px; font-weight: 600; color: #1E293B; margin-bottom: 4px; }
  .upload-desc { font-size: 12px; color: var(--text-muted); }
  
  .preset-box {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
  }
  .preset-title { font-size: 13px; font-weight: 600; color: #334155; margin-bottom: 10px; display: flex; align-items: center; gap: 6px; }
  .preset-btn-group { display: flex; flex-direction: column; gap: 8px; }
  .preset-btn {
    padding: 8px 12px; border-radius: 6px; border: 1px solid var(--border);
    background: #F8FAFC; color: #334155; font-size: 12px; text-align: left;
    cursor: pointer; transition: all 0.15s ease; display: flex; justify-content: space-between; align-items: center;
  }
  .preset-btn:hover { border-color: var(--primary); background: #EFF6FF; color: var(--primary); }
  .preset-tag { font-size: 10px; padding: 1px 6px; border-radius: 4px; background: #E2E8F0; color: #475569; }

  /* 状态概览卡片 */
  .meta-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 18px 24px;
    margin-bottom: 24px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.02);
  }
  .meta-header {
    display: flex; justify-content: space-between; align-items: flex-start;
    padding-bottom: 16px; border-bottom: 1px solid #F1F5F9;
  }
  .doc-name { font-size: 18px; font-weight: 600; color: #0F172A; display: flex; align-items: center; gap: 10px; }
  .match-badge {
    padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 600;
    background: #DCFCE7; color: #15803D; border: 1px solid #BBF7D0;
  }
  .meta-details { display: flex; gap: 24px; margin-top: 10px; font-size: 13px; color: var(--text-muted); }
  .meta-details strong { color: #334155; }
  
  .stats-strip {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 12px;
    margin-top: 16px;
  }
  .stat-pill {
    padding: 10px 14px; border-radius: 8px; border: 1px solid var(--border);
    background: #F8FAFC; display: flex; flex-direction: column; gap: 4px;
  }
  .stat-pill.danger { background: var(--danger-light); border-color: var(--danger-border); color: var(--danger); }
  .stat-pill.warning { background: var(--warning-light); border-color: var(--warning-border); color: var(--warning); }
  .stat-pill.success { background: var(--success-light); border-color: var(--success-border); color: var(--success); }
  .stat-pill.purple { background: var(--purple-light); border-color: #DDD6FE; color: var(--purple); }
  .stat-label { font-size: 11px; font-weight: 500; opacity: 0.9; }
  .stat-value { font-size: 20px; font-weight: 700; }

  /* 视图切换与过滤器 */
  .toolbar {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 16px;
  }
  .filter-group { display: flex; gap: 8px; }
  .filter-chip {
    padding: 5px 12px; border-radius: 999px; border: 1px solid var(--border);
    background: #FFF; font-size: 12px; color: #475569; cursor: pointer;
    transition: all 0.15s ease; font-weight: 500;
  }
  .filter-chip.active { background: #1E293B; color: #FFF; border-color: #1E293B; }
  
  .view-switch {
    display: flex; background: #E2E8F0; padding: 2px; border-radius: 8px;
  }
  .switch-btn {
    padding: 4px 12px; font-size: 12px; font-weight: 600; border-radius: 6px;
    border: none; background: transparent; color: #64748B; cursor: pointer;
  }
  .switch-btn.active { background: #FFF; color: #0F172A; box-shadow: 0 1px 2px rgba(0,0,0,0.06); }

  /* 对比列表 */
  .diff-table-container {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
    box-shadow: 0 2px 4px rgba(0,0,0,0.02);
  }
  .diff-row {
    border-bottom: 1px solid #F1F5F9;
    padding: 16px 20px;
    transition: background 0.15s ease;
  }
  .diff-row:last-child { border-bottom: none; }
  .diff-row:hover { background: #F8FAFC; }

  .row-meta {
    display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;
  }
  .row-cat-badge {
    font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 4px;
    background: #F1F5F9; color: #475569;
  }
  .status-tag {
    font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 4px; text-transform: uppercase;
  }
  .status-tag.DIFF { background: #FEE2E2; color: #B91C1C; }
  .status-tag.MATCH { background: #DCFCE7; color: #15803D; }
  .status-tag.MISSING { background: #FEF3C7; color: #92400E; }
  .status-tag.NEW { background: #E0E7FF; color: #4338CA; }

  /* 双栏对照视图 (Side-by-Side) */
  .side-by-side {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    font-size: 13px;
  }
  .pane {
    background: #FAFAFA; border: 1px solid #E5E7EB; border-radius: 8px; padding: 12px 14px;
    position: relative;
  }
  .pane.standard { border-left: 3px solid #64748B; }
  .pane.reviewed { border-left: 3px solid var(--primary); }
  .pane-title {
    font-size: 11px; font-weight: 700; color: #94A3B8; text-transform: uppercase;
    margin-bottom: 6px; letter-spacing: 0.5px;
  }
  .pane-content {
    white-space: pre-wrap; word-break: break-all; color: #1F2937; line-height: 1.6;
  }

  /* 法律要素标签 */
  .delta-pills { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
  .delta-pill {
    font-size: 11px; font-weight: 600; padding: 3px 8px; border-radius: 4px;
    display: inline-flex; align-items: center; gap: 4px;
  }
  .delta-pill.NUMBER_CHANGE { background: #FEE2E2; color: #991B1B; border: 1px solid #FCA5A5; }
  .delta-pill.DEADLINE_CHANGE { background: #FEF3C7; color: #92400E; border: 1px solid #FCD34D; }
  .delta-pill.PARTY_CHANGE { background: #EDE9FE; color: #5B21B6; border: 1px solid #C4B5FD; }
  .delta-pill.NEGATION_CHANGE { background: #FFE4E6; color: #9F1239; border: 1px solid #FDA4AF; }
  .delta-pill.OBLIGATION_CHANGE { background: #E0E7FF; color: #3730A3; border: 1px solid #A5B4FC; }
  .delta-pill.TEXT_DELTA { background: #F1F5F9; color: #475569; border: 1px solid #CBD5E1; }

  /* 高亮样式 */
  .del-hi { background: #FECACA; color: #7F1D1D; text-decoration: line-through; padding: 1px 3px; border-radius: 2px; }
  .ins-hi { background: #BBF7D0; color: #14532D; font-weight: 600; padding: 1px 3px; border-radius: 2px; }

  .empty-state {
    padding: 60px 20px; text-align: center; color: var(--text-muted); font-size: 14px;
  }
  .loading-spinner {
    display: inline-block; width: 18px; height: 18px; border: 2px solid #CBD5E1;
    border-top-color: var(--primary); border-radius: 50%; animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<header class="header">
  <div class="brand">
    <div class="brand-logo">Δ</div>
    <div>
      <span class="brand-title">AI 合同审查 · 条款级 Diff 与风险透视工作台</span>
      <span class="brand-tag">78 套标准母本库接入版</span>
    </div>
  </div>
  <div>
    <button class="filter-chip" onclick="window.print()" style="cursor:pointer;">🖨️ 导出/打印对比报告</button>
  </div>
</header>

<main class="container">

  <!-- 控制与上传区域 -->
  <div class="control-grid">
    <div class="upload-box" id="dropZone" onclick="document.getElementById('fileInput').click()">
      <input type="file" id="fileInput" accept=".docx" style="display:none;" onchange="handleFileSelect(event)">
      <div class="upload-icon">📄</div>
      <div class="upload-title">点击或将修改后的 DOCX 合同拖拽至此</div>
      <div class="upload-desc">零前置参数：系统将自动提取正文与表头特征，秒级锁定 78 套标准母本并出具 Diff</div>
    </div>

    <div class="preset-box">
      <div class="preset-title">⚡ 快速加载实测用例</div>
      <div class="preset-btn-group">
        <button class="preset-btn" onclick="loadSample('prescreen_tampered')">
          <span>🚨 快速预审 (违约金被偷改)</span>
          <span class="preset-tag">高危篡改</span>
        </button>
        <button class="preset-btn" onclick="loadSample('missing_confidential')">
          <span>🚩 专利代理 (保密条款被删)</span>
          <span class="preset-tag">条款缺失</span>
        </button>
        <button class="preset-btn" onclick="loadSample('trademark_match')">
          <span>✅ 商标驳回复审 (合规原版)</span>
          <span class="preset-tag">100%匹配</span>
        </button>
      </div>
    </div>
  </div>

  <!-- 识别与比对结果看板 -->
  <div id="resultContainer" style="display:none;">
    <div class="meta-card">
      <div class="meta-header">
        <div>
          <div class="doc-name">
            <span id="docFileName">待审合同.docx</span>
            <span class="match-badge" id="matchBadge">已锁定母本: MB20251205012</span>
          </div>
          <div class="meta-details">
            <span>识别业务子类: <strong id="detectedSubtype">-</strong></span>
            <span>识别置信度: <strong id="confidenceRate">-</strong></span>
            <span>基准母本库: <strong id="matchedTemplateName">-</strong></span>
            <span>条款规模: 母本 <strong id="stdCount">-</strong> 条 vs 待审 <strong id="revCount">-</strong> 条</span>
          </div>
        </div>
        <div id="overallVerdict"></div>
      </div>

      <div class="stats-strip">
        <div class="stat-pill success">
          <span class="stat-label">完全一致类别</span>
          <span class="stat-value" id="statMatch">0</span>
        </div>
        <div class="stat-pill danger">
          <span class="stat-label">数值金额篡改</span>
          <span class="stat-value" id="statNumberChange">0</span>
        </div>
        <div class="stat-pill warning">
          <span class="stat-label">期限/主体/否定词变动</span>
          <span class="stat-value" id="statLegalChange">0</span>
        </div>
        <div class="stat-pill danger">
          <span class="stat-label">客户删减母本条款</span>
          <span class="stat-value" id="statMissing">0</span>
        </div>
        <div class="stat-pill purple">
          <span class="stat-label">客户新增非标条款</span>
          <span class="stat-value" id="statNew">0</span>
        </div>
      </div>
    </div>

    <!-- 工具栏 -->
    <div class="toolbar">
      <div class="filter-group">
        <button class="filter-chip active" onclick="setFilter('ALL')">全部条款 (<span id="countAll">0</span>)</button>
        <button class="filter-chip" onclick="setFilter('RISK')">🚨 只看风险变动 (<span id="countRisk">0</span>)</button>
        <button class="filter-chip" onclick="setFilter('MISSING')">🚩 客户删减项 (<span id="countMissing">0</span>)</button>
        <button class="filter-chip" onclick="setFilter('NEW')">⚠️ 客户新增项 (<span id="countNew">0</span>)</button>
      </div>

      <div class="view-switch">
        <button class="switch-btn active" id="btnSide" onclick="setViewMode('side')">双栏并排 (Side-by-Side)</button>
        <button class="switch-btn" id="btnUnified" onclick="setViewMode('unified')">行内修订 (Unified)</button>
      </div>
    </div>

    <!-- 条款比对卡片列表 -->
    <div class="diff-table-container" id="diffList"></div>
  </div>

  <div id="initialPrompt" class="empty-state">
    <div style="font-size:42px; margin-bottom:12px;">📑</div>
    <div style="font-size:16px; font-weight:600; color:#334155; margin-bottom:6px;">请在上方拖入或选择任意一份合同 DOCX</div>
    <div>系统将自动通过第一条事项与表格特征匹配 78 套标准母本，并逐条款生成法律要素 Diff</div>
  </div>

</main>

<script>
let currentDiffData = null;
let currentFilter = 'ALL';
let currentViewMode = 'side';

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

function handleFileSelect(event) {
  if (event.target.files.length > 0) {
    uploadDocx(event.target.files[0]);
  }
}

async function uploadDocx(file) {
  showLoading();
  const formData = new FormData();
  formData.append('file', file);

  try {
    const resp = await fetch('/api/diff/upload', {
      method: 'POST',
      body: formData
    });
    const res = await resp.json();
    if (res.error) {
      alert('解析失败: ' + res.error);
      hideLoading();
      return;
    }
    renderDiffResult(res);
  } catch (err) {
    alert('请求失败，请确保本地后台服务运行中: ' + err);
    hideLoading();
  }
}

async function loadSample(sampleKey) {
  showLoading();
  try {
    const resp = await fetch('/api/diff/sample?key=' + sampleKey);
    const res = await resp.json();
    renderDiffResult(res);
  } catch (err) {
    alert('加载用例失败: ' + err);
    hideLoading();
  }
}

function showLoading() {
  document.getElementById('initialPrompt').innerHTML = '<div class="loading-spinner"></div><div style="margin-top:12px;">正在自动提取附件指纹、匹配 78 套母本并计算条款 Diff...</div>';
  document.getElementById('initialPrompt').style.display = 'block';
  document.getElementById('resultContainer').style.display = 'none';
}

function hideLoading() {
  document.getElementById('initialPrompt').style.display = 'none';
}

function renderDiffResult(res) {
  currentDiffData = res;
  hideLoading();
  document.getElementById('resultContainer').style.display = 'block';

  document.getElementById('docFileName').innerText = res.uploaded_file.split(/[\/\\]/).pop();
  document.getElementById('matchBadge').innerText = '🎯 命中母本: ' + res.matched_template_id;
  document.getElementById('detectedSubtype').innerText = res.detected_subtype;
  document.getElementById('confidenceRate').innerText = (res.classification_confidence * 100).toFixed(1) + '%';
  document.getElementById('matchedTemplateName').innerText = res.matched_template_name;
  document.getElementById('stdCount').innerText = res.standard_clause_count;
  document.getElementById('revCount').innerText = res.uploaded_clause_count;

  // 统计数值
  const reports = res.diff_report || [];
  let matches = 0, numChanges = 0, legalChanges = 0, missings = 0, news = 0;

  reports.forEach(r => {
    if (r.status === 'MATCH') matches++;
    if (r.status === 'MISSING_IN_REVIEWED') missings++;
    if (r.status === 'NEW_IN_REVIEWED') news++;
    if (r.deltas) {
      r.deltas.forEach(d => {
        if (d.delta_type === 'NUMBER_CHANGE') numChanges++;
        if (['DEADLINE_CHANGE', 'PARTY_CHANGE', 'NEGATION_CHANGE', 'OBLIGATION_CHANGE'].includes(d.delta_type)) legalChanges++;
      });
    }
  });

  document.getElementById('statMatch').innerText = matches;
  document.getElementById('statNumberChange').innerText = numChanges;
  document.getElementById('statLegalChange').innerText = legalChanges;
  document.getElementById('statMissing').innerText = missings;
  document.getElementById('statNew').innerText = news;

  document.getElementById('countAll').innerText = reports.length;
  document.getElementById('countRisk').innerText = numChanges + legalChanges + missings;
  document.getElementById('countMissing').innerText = missings;
  document.getElementById('countNew').innerText = news;

  renderDiffList();
}

function setFilter(filterType) {
  currentFilter = filterType;
  document.querySelectorAll('.filter-chip').forEach(btn => btn.classList.remove('active'));
  event.target.classList.add('active');
  renderDiffList();
}

function setViewMode(mode) {
  currentViewMode = mode;
  document.getElementById('btnSide').classList.toggle('active', mode === 'side');
  document.getElementById('btnUnified').classList.toggle('active', mode === 'unified');
  renderDiffList();
}

function renderDiffList() {
  if (!currentDiffData) return;
  const listEl = document.getElementById('diffList');
  listEl.innerHTML = '';

  const reports = currentDiffData.diff_report || [];

  reports.forEach((item, idx) => {
    const isMissing = item.status === 'MISSING_IN_REVIEWED';
    const isNew = item.status === 'NEW_IN_REVIEWED';
    const isDiff = item.status === 'DIFF';
    const isMatch = item.status === 'MATCH';

    // 过滤逻辑
    if (currentFilter === 'MISSING' && !isMissing) return;
    if (currentFilter === 'NEW' && !isNew) return;
    if (currentFilter === 'RISK' && isMatch) return;

    const row = document.createElement('div');
    row.className = 'diff-row';

    let statusLabel = isMatch ? '一致' : (isMissing ? '客户删减' : (isNew ? '客户新增' : '实质差异'));
    let statusClass = isMatch ? 'MATCH' : (isMissing ? 'MISSING' : (isNew ? 'NEW' : 'DIFF'));

    // 法律要素变动标签
    let deltasHtml = '';
    if (item.deltas && item.deltas.length > 0) {
      deltasHtml = '<div class="delta-pills">' + item.deltas.map(d => {
        let icon = '🔍';
        if (d.delta_type === 'NUMBER_CHANGE') icon = '🔴 数值篡改: ';
        if (d.delta_type === 'DEADLINE_CHANGE') icon = '🟠 期限放宽: ';
        if (d.delta_type === 'PARTY_CHANGE') icon = '🟣 主体倒置: ';
        if (d.delta_type === 'NEGATION_CHANGE') icon = '⛔ 否定词反转: ';
        if (d.delta_type === 'OBLIGATION_CHANGE') icon = '🟡 义务弱化: ';
        if (d.delta_type === 'TEXT_DELTA') icon = '📝 措辞差异: ';
        return `<span class="delta-pill ${d.delta_type}">${icon} ${escapeHtml(d.description)}</span>`;
      }).join('') + '</div>';
    }

    let stdText = isNew ? '（标准母本无此条款）' : (item.standard_text || (item.deltas && item.deltas[0] ? item.deltas[0].standard_text : ''));
    let revText = isMissing ? '（待审文档已删除此项）' : (item.reviewed_text || (item.deltas && item.deltas[0] ? item.deltas[0].reviewed_text : ''));

    let contentHtml = '';
    if (currentViewMode === 'side') {
      contentHtml = `
        <div class="side-by-side">
          <div class="pane standard">
            <div class="pane-title">🏛️ 标准参考母本条款</div>
            <div class="pane-content">${escapeHtml(stdText)}</div>
          </div>
          <div class="pane reviewed">
            <div class="pane-title">✏️ 用户修改版送审条款</div>
            <div class="pane-content">${escapeHtml(revText)}</div>
          </div>
        </div>
      `;
    } else {
      contentHtml = `
        <div class="pane" style="background:#FFF;">
          <div class="pane-title">修订对比</div>
          <div class="pane-content">
            ${isMissing ? `<div class="del-hi">${escapeHtml(stdText)}</div>` : ''}
            ${isNew ? `<div class="ins-hi">${escapeHtml(revText)}</div>` : ''}
            ${(!isMissing && !isNew) ? `<div>${escapeHtml(revText)}</div>` : ''}
          </div>
        </div>
      `;
    }

    row.innerHTML = `
      <div class="row-meta">
        <span class="row-cat-badge">第 ${idx + 1} 项 · ${item.clause_category}</span>
        <span class="status-tag ${statusClass}">${statusLabel}</span>
      </div>
      ${contentHtml}
      ${deltasHtml}
    `;

    listEl.appendChild(row);
  });
}

function escapeHtml(text) {
  if (!text) return '';
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
</script>

</body>
</html>
"""


class DiffVisualizerHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器。"""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))

        elif parsed.path == "/api/diff/sample":
            qs = urllib.parse.parse_qs(parsed.query)
            key = qs.get("key", ["prescreen_tampered"])[0]

            # 生成对应的样本比对结果
            if key == "prescreen_tampered":
                # 寻找快速预审样本
                sample_file = config.GOLDEN_DIR / "mut_11_late_fee_change.docx"
                res = run_diff(sample_file)
            elif key == "missing_confidential":
                sample_file = config.GOLDEN_DIR / "mut_06_missing_confidentiality.docx"
                res = run_diff(sample_file)
            else:
                # 找一份合规模板
                sample_file = list(config.TEMPLATE_DIR.glob("**/*驳回复审*.docx"))[0]
                res = run_diff(sample_file)

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
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
                    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
                        tmp.write(file_bytes)
                        tmp_path = Path(tmp.name)
                    try:
                        res = run_diff(tmp_path)
                    finally:
                        try:
                            tmp_path.unlink()
                        except Exception:
                            pass

                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
                    return

            self.send_response(400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"error": "invalid_upload"}')
        else:
            self.send_response(404)
            self.end_headers()


def export_standalone_html():
    """将可视化页面导出为独立 HTML 文件存放在 data/contract_merged 供直接双击打开。"""
    out_path = config.DATA_DIR.parent / "contract_merged" / "合同模板条款比对与Diff可视化.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(HTML_TEMPLATE, encoding="utf-8")
    print(f"[已生成独立可视化网页] {out_path}")


def main():
    parser = argparse.ArgumentParser(description="合同 Diff 前端可视化服务")
    parser.add_argument("--port", type=int, default=8772, help="Web 端口（默认 8772）")
    parser.add_argument("--export-only", action="store_true", help="仅导出独立 HTML 页面")
    args = parser.parse_args()

    export_standalone_html()
    if args.export_only:
        return

    server = ThreadingHTTPServer(("127.0.0.1", args.port), DiffVisualizerHandler)
    print(f"\n🚀 AI 合同条款级 Diff 前端工作台已启动: http://127.0.0.1:{args.port}")
    print("👉 请在浏览器中打开上方网址，即可拖入任意 DOCX 合同进行可视化对比！\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")


if __name__ == "__main__":
    main()
