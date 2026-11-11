function handleFileSelect(e) {
  if (e.target.files.length > 0) {
    uploadContractFile(e.target.files[0]);
    closeUploadModal();
  }
}

async function uploadContractFile(file) {
  const formData = new FormData();
  formData.append('file', file);
  showLoading('正在解析送审文档并自动匹配基准母本...');

  try {
    const resp = await fetch('/api/diff/upload', { method: 'POST', body: formData });
    const res = await resp.json();
    hideLoading();
    if (res.error) {
      showToast('上传解析失败: ' + res.error, 'error');
      return;
    }
    renderFullDiff(res);
    initAgentSSE(true);
    // 同步刷新模板置信度与排序
    fetchTemplateListQuietly();
    showToast(`合同《${file.name.replace(/\.[^/.]+$/, '')}》解析完成，已对齐基准母本！`, 'success');
  } catch (err) {
    hideLoading();
    showToast('请求失败，请确保后台 Web 服务正常启动: ' + err, 'error');
  }
}

async function loadSample(sampleKey) {
  showLoading('正在加载实测用例并对齐母本...');
  try {
    const resp = await fetch('/api/diff/sample?key=' + sampleKey);
    const res = await resp.json();
    hideLoading();
    renderFullDiff(res);
    initAgentSSE(true);
    fetchTemplateListQuietly();
    showToast('实测用例加载成功！', 'success');
  } catch (err) {
    hideLoading();
    showToast('加载实测用例失败: ' + err, 'error');
  }
}

// 静默拉取按最新文档排序的模板库列表
async function fetchTemplateListQuietly() {
  try {
    const resp = await fetch('/api/templates/list');
    const res = await resp.json();
    if (res.templates) {
      allTemplates = res.templates;
      if (document.getElementById('templateModal').classList.contains('open')) {
        initTemplateModal();
      }
    }
  } catch (err) {}
}

// ==================== 模板选择与更换模块（按置信度从上往下排） ====================
function initTemplateModal() {
  document.getElementById('tplModalTotalCount').innerText = allTemplates.length;
  const selected = allTemplates.find(t => isCurrentTemplate(t));
  const name = currentData && (currentData.clean_template_name || currentData.matched_template_display || currentData.matched_template_name);
  document.getElementById('tplCurrentSelection').innerText = `当前使用：${(selected && selected.name) || name || '尚未选择'}${currentData ? (currentData.is_manual_override ? '（手动指定）' : '（自动匹配）') : ''}`;
  renderCategoryTabs();
  renderTemplateItems();
}

function renderCategoryTabs() {
  const tabsContainer = document.getElementById('tplCategoryTabs');
  tabsContainer.innerHTML = '';

  const catCounts = {};
  allTemplates.forEach(t => {
    const cat = t.category || 'UNKNOWN';
    catCounts[cat] = (catCounts[cat] || 0) + 1;
  });

  const categoryDef = [
    { key: 'ALL', label: '全部' },
    { key: 'PATENT', label: '专利业务' },
    { key: 'TRADEMARK', label: '商标业务' },
    { key: 'HIGH_END_CONSULTING', label: '高端咨询' },
    { key: 'LEGAL_LITIGATION', label: '法律诉讼' },
    { key: 'INT_IP', label: '国际业务' },
    { key: 'PROJECT_AUDIT', label: '项目申报' },
    { key: 'MIXED_IP', label: '综合业务' },
    { key: 'COPYRIGHT', label: '著作权' },
    { key: 'FRAMEWORK_NDA', label: '保密框架' },
    { key: 'IP_OPERATION', label: '知识产权运营' },
  ];

  categoryDef.forEach(c => {
    const count = c.key === 'ALL' ? allTemplates.length : (catCounts[c.key] || 0);
    if (c.key !== 'ALL' && count === 0) return;
    const btn = document.createElement('button');
    btn.className = `tpl-tab-btn ${activeTemplateCategory === c.key ? 'active' : ''}`;
    btn.innerText = `${c.label} (${count})`;
    btn.onclick = () => {
      activeTemplateCategory = c.key;
      renderCategoryTabs();
      renderTemplateItems();
    };
    tabsContainer.appendChild(btn);
  });
}

function onTemplateSearchInput(e) {
  templateSearchKeyword = (e.target.value || '').trim().toLowerCase();
  renderTemplateItems();
}

function isCurrentTemplate(template) {
  if (!currentData) return false;
  if (currentData.matched_template_raw_id === template.document_id) return true;
  if (currentData.template_content_hash && template.source_hash) {
    return currentData.template_content_hash === template.source_hash;
  }
  return currentData.matched_template_id === template.document_id
    || Boolean(template.template_code && currentData.matched_template_id === template.template_code);
}

function renderTemplateItems() {
  const container = document.getElementById('tplListContainer');
  container.innerHTML = '';


  // 过滤并确保严格从上往下按置信度排序
  const filtered = allTemplates.filter(t => {
    if (activeTemplateCategory !== 'ALL' && t.category !== activeTemplateCategory) {
      return false;
    }
    if (templateSearchKeyword) {
      const matchName = (t.name || '').toLowerCase().includes(templateSearchKeyword);
      const matchDocId = (t.document_id || '').toLowerCase().includes(templateSearchKeyword);
      const matchFile = (t.full_name || '').toLowerCase().includes(templateSearchKeyword);
      const matchCat = (t.category_name || '').toLowerCase().includes(templateSearchKeyword);
      if (!matchName && !matchDocId && !matchFile && !matchCat) return false;
    }
    return true;
  });

  // 严格按置信度从高到低从上往下排序
  filtered.sort((a, b) => {
    const confA = typeof a.confidence_score === 'number' ? a.confidence_score : 0;
    const confB = typeof b.confidence_score === 'number' ? b.confidence_score : 0;
    return confB - confA;
  });

  if (filtered.length === 0) {
    container.innerHTML = `
      <div style="grid-column: 1/-1; text-align:center; padding:40px; color:#94A3B8; font-size:13px;">
        未检索到匹配的模板，请尝试其他关键词或点击上方【热加载库】
      </div>
    `;
    return;
  }

  filtered.forEach(t => {
    const isCurrent = isCurrentTemplate(t);
    const card = document.createElement('div');
    card.className = `tpl-card-item ${isCurrent ? 'active-tpl' : ''}`;
    card.onclick = () => switchTemplate(t.document_id, t.name);

    // 置信度样式映射
    const confScore = typeof t.confidence_score === 'number' ? t.confidence_score : 0.0;
    let badgeCls = 'conf-low';
    if (confScore >= 0.90) badgeCls = 'conf-very-high';
    else if (confScore >= 0.75) badgeCls = 'conf-high';
    else if (confScore >= 0.50) badgeCls = 'conf-medium';

    const confPct = t.confidence_percent || `${(confScore * 100).toFixed(1)}%`;
    const confLbl = t.confidence_label || (confScore >= 0.9 ? '权威匹配' : (confScore >= 0.75 ? '推荐' : '备选'));

    // 格式化编号展示（非十六进制哈希才显示）
    const showCode = t.is_standard_mb && t.document_id ? `<span class="tpl-card-pill code">${escapeHtml(t.document_id)}</span>` : '';
    const showVer = t.version ? `<span>版本: ${escapeHtml(t.version)}</span>` : '';

    card.innerHTML = `
      <div class="tpl-card-header">
        <span class="tpl-card-title">${escapeHtml(t.name)}</span>
        <div class="tpl-card-conf-badge ${badgeCls}" title="与送审合同匹配置信度: ${confPct}">
          <span>🎯</span>
          <span>${confPct}</span>
          <span style="font-size:9.5px; opacity:0.85;">${escapeHtml(confLbl)}</span>
        </div>
      </div>
      <div class="tpl-card-meta">
        <span class="tpl-card-pill category">${escapeHtml(t.category_name)}</span>
        ${showCode}
        ${showVer}
        ${isCurrent ? '<span class="tpl-card-pill current">✓ 当前使用</span>' : ''}
      </div>
    `;
    container.appendChild(card);
  });
}

// 执行更换模板
async function switchTemplate(targetDocId, targetName = '') {
  closeTemplateModal();
  showLoading(`正在将基准母本切换为 [${targetName || targetDocId}] 并重新比对...`);

  try {
    const resp = await fetch('/api/diff/switch-template', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ template_id: targetDocId }),
    });
    const res = await resp.json();
    hideLoading();
    if (res.error) {
      showToast('切换模板失败: ' + res.error, 'error');
      return;
    }
    renderFullDiff(res);
    initAgentSSE(false);
    fetchTemplateListQuietly();
    showToast(`基准母本已成功更换为《${res.clean_template_name || targetName}》！`, 'success');
  } catch (err) {
    hideLoading();
    showToast('切换模板网络请求失败: ' + err, 'error');
  }
}

// 恢复 AI 自动识别母本
async function resetAutoTemplate() {
  closeTemplateModal();
  showLoading('正在根据待审合同内容恢复 AI 智能自动匹配母本...');

  try {
    const resp = await fetch('/api/diff/switch-template', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ template_id: 'auto' }),
    });
    const res = await resp.json();
    hideLoading();
    if (res.error) {
      showToast('恢复自动匹配失败: ' + res.error, 'error');
      return;
    }
    renderFullDiff(res);
    initAgentSSE(false);
    fetchTemplateListQuietly();
    showToast(`已恢复 AI 自动识别匹配母本: 《${res.clean_template_name || res.matched_template_name}》！`, 'success');
  } catch (err) {
    hideLoading();
    showToast('请求失败: ' + err, 'error');
  }
}

// 热加载模板库
async function reloadTemplates(fromModal = false) {
  const btn = document.getElementById('btnReloadTemplates');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '🔄 扫描热加载中...';
  }

  try {
    const resp = await fetch('/api/templates/reload', { method: 'POST' });
    const res = await resp.json();
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '⚡ 热加载模板库';
    }

    if (res.error) {
      showToast('热加载模板库失败: ' + res.error, 'error');
      return;
    }

    allTemplates = res.templates || [];
    initTemplateModal();
    showToast(res.message || `热加载成功！库中现有 ${allTemplates.length} 套标准母本`, 'success');
  } catch (err) {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '⚡ 热加载模板库';
    }
    showToast('热加载请求失败: ' + err, 'error');
  }
}

// 格式判定：识别正文节点原排版角色

// ==================== 素材库 (contract_merged) 业务交互 ====================
function initMaterialModal() {
  fetchMaterialStats();
  fetchMaterialCases();
}

function fetchMaterialStats() {
  fetch('/api/materials/stats')
    .then(r => r.json())
    .then(stats => {
      if (stats && stats.total_cases) {
        const pill = document.getElementById('matStatsPill');
        if (pill) {
          pill.innerText = `共 ${stats.total_cases.toLocaleString()} 案卷 · ${stats.total_word_contracts.toLocaleString()} 实单 Word 原件`;
        }
      }
    })
    .catch(() => {});
}

function setMaterialCategory(cat) {
  currentMaterialCategory = cat;
  currentMaterialPage = 1;
  document.querySelectorAll('.mat-tab-btn').forEach(btn => btn.classList.remove('active'));
  const activeBtn = document.getElementById(`matTab-${cat}`);
  if (activeBtn) activeBtn.classList.add('active');
  fetchMaterialCases();
}

function onMaterialSearchKey(e) {
  if (e.key === 'Enter') {
    triggerMaterialSearch();
  }
}

function triggerMaterialSearch() {
  currentMaterialSearch = (document.getElementById('matSearchInput').value || '').trim();
  currentMaterialHasWordOnly = document.getElementById('matHasWordOnly').checked;
  currentMaterialPage = 1;
  fetchMaterialCases();
}

function resetMaterialSearch() {
  document.getElementById('matSearchInput').value = '';
  document.getElementById('matHasWordOnly').checked = true;
  currentMaterialSearch = '';
  currentMaterialHasWordOnly = true;
  currentMaterialPage = 1;
  currentMaterialCategory = 'ALL';
  document.querySelectorAll('.mat-tab-btn').forEach(btn => btn.classList.remove('active'));
  const allBtn = document.getElementById('matTab-ALL');
  if (allBtn) allBtn.classList.add('active');
  fetchMaterialCases();
}

function changeMaterialPage(delta) {
  currentMaterialPage += delta;
  if (currentMaterialPage < 1) currentMaterialPage = 1;
  fetchMaterialCases();
}

function fetchMaterialCases() {
  const container = document.getElementById('matListContainer');
  container.innerHTML = '<div style="grid-column: 1/-1; padding: 40px; text-align: center; color: #64748B;">⚡ 正在秒级调取素材库案例...</div>';

  const params = new URLSearchParams({
    category: currentMaterialCategory,
    search: currentMaterialSearch,
    has_word: currentMaterialHasWordOnly ? '1' : '0',
    page: String(currentMaterialPage),
    page_size: '20'
  });

  fetch(`/api/materials/cases?${params.toString()}`)
    .then(r => r.json())
    .then(data => {
      renderMaterialCases(data);
    })
    .catch(err => {
      if (window.MATERIAL_DATA && window.MATERIAL_DATA.initial_cases) {
        renderMaterialCases(window.MATERIAL_DATA.initial_cases);
      } else {
        container.innerHTML = `<div style="grid-column: 1/-1; padding: 30px; text-align: center; color: #DC2626;">调取素材库失败: ${escapeHtml(err.message || String(err))}</div>`;
      }
    });
}

function renderMaterialCases(data) {
  const container = document.getElementById('matListContainer');
  container.innerHTML = '';

  const cases = data.cases || [];
  const total = data.total || 0;
  const page = data.page || 1;
  const totalPages = data.total_pages || 1;

  document.getElementById('matPageInfo').innerText = `显示第 ${(page - 1) * 20 + 1}-${Math.min(page * 20, total)} 条 / 共 ${total.toLocaleString()} 案卷`;
  document.getElementById('matCurPageIndicator').innerText = `${page} / ${totalPages}`;
  document.getElementById('matBtnPrev').disabled = (page <= 1);
  document.getElementById('matBtnNext').disabled = (page >= totalPages);

  if (cases.length === 0) {
    container.innerHTML = '<div style="grid-column: 1/-1; padding: 50px; text-align: center; color: #64748B;">🔍 未检索到匹配的案卷，请尝试更换关键词或勾选“非 Word 原件”。</div>';
    return;
  }

  cases.forEach(c => {
    const card = document.createElement('div');
    card.className = 'mat-case-card';

    let chipHtml = '';
    const atts = c.attachments || [];
    const hasDocx = atts.some(a => a.ext === '.docx');
    const hasDoc = atts.some(a => a.ext === '.doc');
    const hasPdf = atts.some(a => a.ext === '.pdf');
    if (hasDocx) chipHtml += '<span class="mat-chip docx">DOCX原件</span>';
    if (hasDoc) chipHtml += '<span class="mat-chip doc">DOC原件</span>';
    if (hasPdf) chipHtml += '<span class="mat-chip pdf">PDF扫描</span>';
    if (c.md_file) chipHtml += '<span class="mat-chip md">案卷履约MD</span>';

    const hasParseableAttachment = hasDocx || hasDoc || hasPdf;

    card.innerHTML = `
      <div class="mat-card-header">
        <span class="mat-no-tag">${escapeHtml(c.no)}</span>
        <span class="mat-cat-tag">${escapeHtml(c.category_name || c.folder || '合同案卷')}</span>
      </div>
      <div class="mat-card-title" title="${escapeHtml(c.name || c.no)}">${escapeHtml(c.name || c.no)}</div>
      <div class="mat-card-parties">
        <div class="mat-party-row" title="对方主体: ${escapeHtml(c.party_b || '未载明')}">👥 <b>客户:</b> ${escapeHtml(c.party_b || '未载明')}</div>
        <div class="mat-party-row" title="我方签约: ${escapeHtml(c.party_a || '标准代理公司')}">🏛️ <b>我方:</b> ${escapeHtml(c.party_a || '标准代理公司')}</div>
      </div>
      <div class="mat-card-meta">
        <span>👤 经办: ${escapeHtml(c.applicant || '经办人')}</span>
        <span>📅 ${escapeHtml(c.year || '2026')}年 · 状态: ${escapeHtml(c.status || '已归档')}</span>
      </div>
      <div class="mat-card-chips">${chipHtml || '<span style="font-size:10px; color:#94A3B8;">暂无附件</span>'}</div>
      <div class="mat-card-actions">
        <button class="mat-btn-action mat-btn-diff" onclick="loadMaterialContract('${c.no}')" ${!hasParseableAttachment ? 'disabled title="暂无可解析合同实体"' : ''}>⚡ 载入原格式比对</button>
        <button class="mat-btn-action mat-btn-detail" onclick="openCaseDetailModal('${c.no}')">📋 案卷审批详情</button>
      </div>
    `;
    container.appendChild(card);
  });
}

function loadMaterialContract(contractNo, attPath) {
  showLoading(`正在调取素材库案卷 [${contractNo}] 合同原件并执行条款级微观比对...`);
  closeMaterialModal();
  closeCaseDetailModal();

  fetch('/api/materials/load_contract', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ contract_no: contractNo, attachment_path: attPath })
  })
    .then(r => {
      if (!r.ok) return r.json().then(j => { throw new Error(j.error || '调取失败'); });
      return r.json();
    })
    .then(data => {
      hideLoading();
      renderFullDiff(data);
      showToast(`成功调取素材库案卷合同 [${contractNo}]，已自动完成高保真原格式对照！`, 'success');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    })
    .catch(err => {
      hideLoading();
      showToast(`调取合同失败: ${err.message}`, 'error');
    });
}

function fetchCaseDetail(contractNo) {
  fetch(`/api/materials/case_detail?contract_no=${encodeURIComponent(contractNo)}`)
    .then(r => r.json())
    .then(data => {
      const body = document.getElementById('detailModalBody');
      const c = data.case || {};
      const md = data.md_content || '';
      const atts = data.attachments || [];

      let attRows = atts.map((a, idx) => `
        <tr>
          <td>${idx + 1}</td>
          <td><b>${escapeHtml(a.original || a.local)}</b></td>
          <td><span class="mat-chip ${a.ext.replace('.', '')}">${a.ext.toUpperCase()}</span></td>
          <td>${(a.size / 1024).toFixed(1)} KB</td>
          <td>${a.local.endsWith('.docx') || a.local.endsWith('.doc') ? '<button class="btn btn-sm btn-emerald" onclick="loadMaterialContract(\'' + c.no + '\', \'' + a.local + '\')">比对本原件</button>' : '-'}</td>
        </tr>
      `).join('');

      let html = `
        <div style="background:#F8FAFC; border:1px solid #E2E8F0; border-radius:8px; padding:14px; margin-bottom:16px;">
          <h3 style="margin-top:0;">📋 案卷基础信息台账</h3>
          <table style="margin-top:8px;">
            <tr><th style="width:120px;">合同编号</th><td><b>${escapeHtml(c.no)}</b></td><th style="width:120px;">合同名称</th><td>${escapeHtml(c.name || '-')}</td></tr>
            <tr><th>客户主体(对方)</th><td>${escapeHtml(c.party_b || '-')}</td><th>我方签约主体</th><td>${escapeHtml(c.party_a || '标准代理主体')}</td></tr>
            <tr><th>业务大类</th><td>${escapeHtml(c.category_name || c.folder || '-')}</td><th>归档状态</th><td>${escapeHtml(c.status || '-')}</td></tr>
            <tr><th>经办申办人</th><td>${escapeHtml(c.applicant || '-')}</td><th>签署立卷年份</th><td>${escapeHtml(c.year || '-')}</td></tr>
          </table>
        </div>

        <div style="margin-bottom:16px;">
          <h3>📎 案卷关联附件与证据实体 (${atts.length} 项)</h3>
          <table>
            <thead>
              <tr><th>#</th><th>文件全称 / 来源</th><th>格式</th><th>体积</th><th>快捷操作</th></tr>
            </thead>
            <tbody>
              ${attRows || '<tr><td colspan="5" style="text-align:center; color:#94A3B8;">暂无附件实体记录</td></tr>'}
            </tbody>
          </table>
        </div>

        ${md ? `
        <div>
          <h3>📜 原始案卷 MD 归档全文</h3>
          <div style="background:#F1F5F9; border-radius:6px; padding:12px; font-family:Consolas, monospace; font-size:12px; max-height:300px; overflow-y:auto; white-space:pre-wrap;">${escapeHtml(md)}</div>
        </div>
        ` : ''}
      `;
      body.innerHTML = html;
    })
    .catch(err => {
      document.getElementById('detailModalBody').innerHTML = `<div style="padding:30px; text-align:center; color:#DC2626;">加载案卷详情失败: ${escapeHtml(err.message || String(err))}</div>`;
    });
}

function loadCurrentDetailContract() {
  if (currentDetailContractNo) {
    loadMaterialContract(currentDetailContractNo, null);
  }
}

// ==================== 模板库全景 (参考/) 业务交互 ====================
function initRefTemplateModal() {
  if (refTemplatesCache && refTemplatesCache.length > 0) {
    renderRefCategoryTabs();
    renderRefTemplates();
  }
  fetch('/api/ref_templates/catalog')
    .then(r => r.json())
    .then(data => {
      if (data && data.templates) {
        refTemplatesCache = data.templates;
        renderRefCategoryTabs();
        renderRefTemplates();
      }
    })
    .catch(() => {});
}

function renderRefCategoryTabs() {
  const container = document.getElementById('refCategoryTabs');
  container.innerHTML = '';

  const catCounts = {};
  refTemplatesCache.forEach(t => {
    const f = t.folder || '其他';
    catCounts[f] = (catCounts[f] || 0) + 1;
  });

  const allBtn = document.createElement('button');
  allBtn.className = `ref-cat-btn ${activeRefCategory === 'ALL' ? 'active' : ''}`;
  allBtn.innerText = `全部 (${refTemplatesCache.length})`;
  allBtn.onclick = () => {
    activeRefCategory = 'ALL';
    renderRefCategoryTabs();
    renderRefTemplates();
  };
  container.appendChild(allBtn);

  const folders = ['专利', '商标', '国际专利', '诉讼', '国际商标', '双软', '综合业务', '保密协议', '著作权', '集成电路布图设计登记'];
  folders.forEach(f => {
    const count = catCounts[f] || 0;
    if (count === 0) return;
    const btn = document.createElement('button');
    btn.className = `ref-cat-btn ${activeRefCategory === f ? 'active' : ''}`;
    btn.innerText = `${f} (${count})`;
    btn.onclick = () => {
      activeRefCategory = f;
      renderRefCategoryTabs();
      renderRefTemplates();
    };
    container.appendChild(btn);
  });
}

function onRefSearchInput(e) {
  refSearchKeyword = (e.target.value || '').trim().toLowerCase();
  renderRefTemplates();
}

function renderRefTemplates() {
  const container = document.getElementById('refListContainer');
  container.innerHTML = '';

  const filtered = refTemplatesCache.filter(t => {
    if (activeRefCategory !== 'ALL' && t.folder !== activeRefCategory) return false;
    if (refSearchKeyword) {
      const matchName = (t.name || '').toLowerCase().includes(refSearchKeyword);
      const matchFolder = (t.folder || '').toLowerCase().includes(refSearchKeyword);
      const matchMb = (t.mb_code || '').toLowerCase().includes(refSearchKeyword);
      if (!matchName && !matchFolder && !matchMb) return false;
    }
    return true;
  });

  if (filtered.length === 0) {
    container.innerHTML = '<div style="grid-column: 1/-1; padding: 40px; text-align: center; color: #64748B;">🔍 未检索到匹配的标准模板。</div>';
    return;
  }

  filtered.forEach(t => {
    const card = document.createElement('div');
    card.className = 'ref-card';
    card.innerHTML = `
      <div class="ref-card-header">
        <span class="ref-folder-tag">${escapeHtml(t.folder)}</span>
        ${t.mb_code ? `<span class="ref-mb-tag">[${escapeHtml(t.mb_code)}]</span>` : ''}
      </div>
      <div class="ref-card-title" title="${escapeHtml(t.name)}">${escapeHtml(t.name)}</div>
      <div class="ref-card-meta">
        <span>📄 文件: ${escapeHtml(t.file_name)}</span> · <span>${(t.size / 1024).toFixed(1)} KB</span>
      </div>
      <div class="ref-card-actions">
        <button class="ref-btn-action ref-btn-primary" onclick="loadRefTemplate('${t.rel_path}', 'reviewed')">📄 载入待审</button>
        <button class="ref-btn-action ref-btn-std" onclick="loadRefTemplate('${t.rel_path}', 'standard')">🎯 设为母本</button>
        <button class="ref-btn-action ref-btn-self" onclick="loadRefTemplate('${t.rel_path}', 'self_diff')" title="模板对比自身，验证100%全格式无差异基线">🧪 一键自测</button>
      </div>
    `;
    container.appendChild(card);
  });
}

function loadRefTemplate(relPath, mode) {
  let modeDesc = '作为待审文档';
  if (mode === 'standard') modeDesc = '作为基准母本';
  if (mode === 'self_diff') modeDesc = '执行 100% 格式自测核验';

  showLoading(`正在将【参考/】模板 [${relPath}] ${modeDesc}...`);
  closeRefTemplateModal();

  fetch('/api/ref_templates/load', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rel_path: relPath, mode: mode })
  })
    .then(r => {
      if (!r.ok) return r.json().then(j => { throw new Error(j.error || '载入失败'); });
      return r.json();
    })
    .then(data => {
      hideLoading();
      renderFullDiff(data);
      if (mode === 'self_diff') {
        showToast(`标准模板 [${relPath}] 自测对比完成：100% 格式精准对齐，零虚假差异！`, 'success');
      } else {
        showToast(`成功将模板 [${relPath}] ${modeDesc}！`, 'success');
      }
      window.scrollTo({ top: 0, behavior: 'smooth' });
    })
    .catch(err => {
      hideLoading();
      showToast(`载入模板失败: ${err.message}`, 'error');
    });
}

// ==================== 原格式高保真在线预览 (Alt+1 母本 / Alt+2 送审) ====================
let currentPreviewTarget = 'reviewed';
let previewZoomScale = 1.0;
let previewMetaCache = {};
let previewAbortController = null;

function openOriginalPreview(target) {
  const modal = document.getElementById('previewModal');
  if (!modal) return;
  modal.classList.add('open');
  modal.setAttribute('aria-hidden', 'false');
  switchPreviewTarget(target || 'reviewed');
}

function closePreviewModal() {
  const modal = document.getElementById('previewModal');
  if (!modal) return;
  modal.classList.remove('open');
  modal.setAttribute('aria-hidden', 'true');
  if (previewAbortController) {
    try { previewAbortController.abort(); } catch (e) {}
    previewAbortController = null;
  }
}

function closePreviewModalOnBackdrop(e) {
  if (e.target && e.target.id === 'previewModal') {
    closePreviewModal();
  }
}

function togglePreviewFullscreen() {
  const dialog = document.getElementById('previewModalDialog');
  const btn = document.getElementById('btnPreviewFullscreen');
  if (!dialog) return;
  const isFull = dialog.classList.toggle('fullscreen');
  if (btn) {
    btn.innerHTML = isFull ? '🗗 还原' : '🗖 全屏';
    btn.title = isFull ? '还原窗口大小' : '切换全屏模式';
  }
}

function adjustPreviewZoom(delta) {
  previewZoomScale = Math.min(2.0, Math.max(0.5, Math.round((previewZoomScale + delta) * 10) / 10));
  applyPreviewZoom();
}

function resetPreviewZoom() {
  previewZoomScale = 1.0;
  applyPreviewZoom();
}

function applyPreviewZoom() {
  const label = document.getElementById('previewZoomValue');
  if (label) label.innerText = `${Math.round(previewZoomScale * 100)}%`;
  const wrapper = document.getElementById('previewCanvasWrapper');
  if (wrapper) {
    wrapper.style.transform = `scale(${previewZoomScale})`;
  }
  const splitLeft = document.getElementById('docxSplitBodyLeft');
  const splitRight = document.getElementById('docxSplitBodyRight');
  if (splitLeft) {
    splitLeft.style.transform = `scale(${previewZoomScale * 0.85})`;
    splitLeft.style.transformOrigin = 'top center';
  }
  if (splitRight) {
    splitRight.style.transform = `scale(${previewZoomScale * 0.85})`;
    splitRight.style.transformOrigin = 'top center';
  }
}

function switchPreviewTarget(target) {
  currentPreviewTarget = target || 'reviewed';

  // 更新标签高亮
  const tabTpl = document.getElementById('previewTabTemplate');
  const tabRev = document.getElementById('previewTabReviewed');
  const tabSide = document.getElementById('previewTabSideBySide');
  if (tabTpl) tabTpl.classList.toggle('active', currentPreviewTarget === 'template');
  if (tabRev) tabRev.classList.toggle('active', currentPreviewTarget === 'reviewed');
  if (tabSide) tabSide.classList.toggle('active', currentPreviewTarget === 'side_by_side');

  // 更新顶栏标题
  const titleEl = document.getElementById('previewModalTitle');
  const iconEl = document.getElementById('previewHeaderIcon');
  if (currentPreviewTarget === 'template') {
    if (titleEl) titleEl.innerText = '标准基准母本 · Word 原格式高保真在线预览';
    if (iconEl) iconEl.innerText = '🏛️';
  } else if (currentPreviewTarget === 'reviewed') {
    if (titleEl) titleEl.innerText = '送审修改稿 · Word 原格式高保真在线预览';
    if (iconEl) iconEl.innerText = '✏️';
  } else {
    if (titleEl) titleEl.innerText = '原版排版左右双栏高保真对照预览';
    if (iconEl) iconEl.innerText = '📖';
  }

  const singleContainer = document.getElementById('previewDocContainer');
  const splitContainer = document.getElementById('previewSplitContainer');

  if (currentPreviewTarget === 'side_by_side') {
    if (singleContainer) singleContainer.style.display = 'none';
    if (splitContainer) splitContainer.style.display = 'flex';
    loadSideBySidePreview();
  } else {
    if (singleContainer) singleContainer.style.display = 'flex';
    if (splitContainer) splitContainer.style.display = 'none';
    loadSinglePreview(currentPreviewTarget);
  }
}

async function loadSinglePreview(target) {
  const loading = document.getElementById('previewLoading');
  const loadingText = document.getElementById('previewLoadingText');
  const body = document.getElementById('docxPreviewBody');
  if (loading) loading.classList.add('active');
  if (loadingText) loadingText.innerText = `正在调取并在线高保真解析【${target === 'template' ? '基准母本' : '送审合同'}】原文件...`;
  if (body) body.innerHTML = '';

  const sessionId = (ReviewApiClient && ReviewApiClient.sessionId) || (currentData && currentData.session_id) || 'default_session';

  try {
    // 1. 获取文件元数据
    const infoResp = await fetch(`/api/diff/preview_file?target=${encodeURIComponent(target)}&session_id=${encodeURIComponent(sessionId)}&info=1`);
    if (!infoResp.ok) {
      throw new Error(`获取文件元数据失败 (HTTP ${infoResp.status})`);
    }
    const info = await infoResp.json();
    if (info.error) throw new Error(info.error);
    previewMetaCache[target] = info;

    // 更新信息状态条（确保展示真实文件名，杜绝暴露内部技术占位 contract.docx）
    const displayDocName = (info.file_name && info.file_name !== 'contract.docx') ? info.file_name : (info.display_title || '-');
    updatePreviewMetaStrip(info, displayDocName);

    // 2. 拉取文件二进制流
    const fileResp = await fetch(`/api/diff/preview_file?target=${encodeURIComponent(target)}&session_id=${encodeURIComponent(sessionId)}`);
    if (!fileResp.ok) throw new Error(`下载原文件数据流失败 (HTTP ${fileResp.status})`);
    const blob = await fileResp.blob();

    // 3. 渲染
    await renderDocumentBlob(blob, info.ext, body);
    if (loading) loading.classList.remove('active');
  } catch (err) {
    if (loading) loading.classList.remove('active');
    if (body) {
      body.innerHTML = `
        <div style="background:#FFF; padding:40px; border-radius:8px; box-shadow:0 4px 16px rgba(0,0,0,0.2); max-width:600px; margin:40px auto; text-align:center; color:#1E293B;">
          <div style="font-size:36px; margin-bottom:12px;">⚠️</div>
          <h3 style="margin-bottom:8px; color:#DC2626;">原文件在线预览未能完成</h3>
          <p style="color:#64748B; font-size:13px; margin-bottom:20px; line-height:1.6;">${escapeHtml(err.message || String(err))}</p>
          <div style="display:flex; justify-content:center; gap:10px;">
            <button class="btn btn-primary" onclick="downloadCurrentPreviewFile()">📥 直接下载原文件</button>
            <button class="btn btn-emerald" onclick="openCurrentFileInLocalApp()">🖥️ 本地Office打开</button>
          </div>
        </div>
      `;
    }
    showToast(`在线预览加载异常: ${err.message}`, 'warning');
  }
}

// 左右分栏各自滑动百分比同步控制器
let isPreviewSyncingScroll = false;

function bindPreviewScrollSync() {
  const left = document.getElementById('previewSplitScrollLeft');
  const right = document.getElementById('previewSplitScrollRight');
  if (!left || !right) return;

  // 重置双方滚动位置到顶部
  left.scrollTop = 0;
  right.scrollTop = 0;

  if (left._previewSyncBound) return;
  left._previewSyncBound = true;

  const syncScroll = (source, target) => {
    if (isPreviewSyncingScroll) return;
    const sourceMax = source.scrollHeight - source.clientHeight;
    const targetMax = target.scrollHeight - target.clientHeight;
    if (sourceMax <= 0 || targetMax <= 0) return;

    isPreviewSyncingScroll = true;
    const ratio = Math.max(0, Math.min(1, source.scrollTop / sourceMax));
    target.scrollTop = Math.round(ratio * targetMax);

    // 释放锁：等待下一帧重绘后重置，彻底避免死循环回弹
    requestAnimationFrame(() => {
      isPreviewSyncingScroll = false;
    });
  };

  left.addEventListener('scroll', () => syncScroll(left, right), { passive: true });
  right.addEventListener('scroll', () => syncScroll(right, left), { passive: true });
}

async function loadSideBySidePreview() {
  const loading = document.getElementById('previewLoading');
  const loadingText = document.getElementById('previewLoadingText');
  const leftBody = document.getElementById('docxSplitBodyLeft');
  const rightBody = document.getElementById('docxSplitBodyRight');
  const leftScroll = document.getElementById('previewSplitScrollLeft');
  const rightScroll = document.getElementById('previewSplitScrollRight');

  if (loading) loading.classList.add('active');
  if (loadingText) loadingText.innerText = '正在并行调取基准母本与送审合同双份原文件排版...';
  if (leftBody) leftBody.innerHTML = '';
  if (rightBody) rightBody.innerHTML = '';
  if (leftScroll) leftScroll.scrollTop = 0;
  if (rightScroll) rightScroll.scrollTop = 0;

  const sessionId = (ReviewApiClient && ReviewApiClient.sessionId) || (currentData && currentData.session_id) || 'default_session';

  try {
    const [tplInfoResp, revInfoResp] = await Promise.all([
      fetch(`/api/diff/preview_file?target=template&session_id=${encodeURIComponent(sessionId)}&info=1`),
      fetch(`/api/diff/preview_file?target=reviewed&session_id=${encodeURIComponent(sessionId)}&info=1`),
    ]);
    const tplInfo = await tplInfoResp.json();
    const revInfo = await revInfoResp.json();
    previewMetaCache['template'] = tplInfo;
    previewMetaCache['reviewed'] = revInfo;

    const tplDisplayName = tplInfo.file_name || tplInfo.display_title || '基准母本';
    const revDisplayName = (revInfo.file_name && revInfo.file_name !== 'contract.docx') ? revInfo.file_name : (revInfo.display_title || '送审合同');

    // 更新顶栏元数据条
    updatePreviewMetaStrip(revInfo, `送审: ${revDisplayName} | 母本: ${tplDisplayName}`);

    // 更新左右分栏标题
    const leftHeader = document.querySelector('#previewSplitLeft .preview-split-pane-header');
    const rightHeader = document.querySelector('#previewSplitRight .preview-split-pane-header');
    if (leftHeader) leftHeader.innerText = `🏛️ 基准母本原始排版: ${tplDisplayName}`;
    if (rightHeader) rightHeader.innerText = `✏️ 送审合同原始排版: ${revDisplayName}`;

    const [tplFileResp, revFileResp] = await Promise.all([
      fetch(`/api/diff/preview_file?target=template&session_id=${encodeURIComponent(sessionId)}`),
      fetch(`/api/diff/preview_file?target=reviewed&session_id=${encodeURIComponent(sessionId)}`),
    ]);
    const tplBlob = await tplFileResp.blob();
    const revBlob = await revFileResp.blob();

    await Promise.all([
      renderDocumentBlob(tplBlob, tplInfo.ext, leftBody),
      renderDocumentBlob(revBlob, revInfo.ext, rightBody),
    ]);

    // 绑定左右分栏各自滑动百分比同步
    bindPreviewScrollSync();

    if (loading) loading.classList.remove('active');
  } catch (err) {
    if (loading) loading.classList.remove('active');
    showToast(`双栏原格式载入异常: ${err.message}`, 'warning');
  }
}

async function renderDocumentBlob(blob, ext, container) {
  ext = (ext || '').toLowerCase();
  container.innerHTML = '';

  if (ext === '.pdf') {
    const url = URL.createObjectURL(blob);
    const iframe = document.createElement('iframe');
    iframe.src = url;
    iframe.style.width = '100%';
    iframe.style.minHeight = '80vh';
    iframe.style.border = 'none';
    container.appendChild(iframe);
    return;
  }

  // 默认作为 DOCX 高保真解析
  if (window.docx && typeof window.docx.renderAsync === 'function') {
    const arrayBuffer = await blob.arrayBuffer();
    await window.docx.renderAsync(arrayBuffer, container, null, {
      className: 'docx',
      inWrapper: true,
      ignoreWidth: false,
      ignoreHeight: false,
      ignoreFonts: false,
      breakPages: true,
      experimental: true,
      trimXmlDeclaration: true,
      useBase64URL: true,
      showChanges: false,
    });
  } else {
    // 降级提示
    container.innerHTML = `
      <div style="background:#FFF; padding:30px; border-radius:8px; text-align:center; color:#334155;">
        <p>⚠️ 前端 docx-preview 引擎尚未加载就绪，请直接下载原文件预览。</p>
        <button class="btn btn-primary" onclick="downloadCurrentPreviewFile()">📥 下载此文件</button>
      </div>
    `;
  }
}

function updatePreviewMetaStrip(info, customSummary) {
  const fileNameEl = document.getElementById('previewMetaFileName');
  const extEl = document.getElementById('previewMetaExt');
  const sizeEl = document.getElementById('previewMetaSize');
  const hashEl = document.getElementById('previewMetaHash');
  const tipEl = document.getElementById('previewMetaTip');

  if (fileNameEl) fileNameEl.innerText = customSummary || info.file_name || '-';
  if (extEl) extEl.innerText = (info.ext || '.docx').replace('.', '').toUpperCase();
  if (sizeEl) sizeEl.innerText = `${((info.size || 0) / 1024).toFixed(1)} KB`;
  if (hashEl) {
    const h = info.sha256 || '';
    hashEl.innerText = h ? `${h.slice(0, 10)}...${h.slice(-6)}` : '-';
    hashEl.title = `完整 SHA256: ${h}`;
  }
  if (tipEl) {
    tipEl.innerText = `💡 原格式还原：支持真实页边距、段落缩进、印章表格与字体样式。快捷键 Alt+1 查母本，Alt+2 查送审。`;
  }
}

function downloadCurrentPreviewFile() {
  const target = currentPreviewTarget === 'side_by_side' ? 'reviewed' : currentPreviewTarget;
  const sessionId = (ReviewApiClient && ReviewApiClient.sessionId) || (currentData && currentData.session_id) || 'default_session';
  const url = `/api/diff/preview_file?target=${encodeURIComponent(target)}&session_id=${encodeURIComponent(sessionId)}`;
  const a = document.createElement('a');
  a.href = url;
  const info = previewMetaCache[target];
  if (info && info.file_name && info.file_name !== 'contract.docx') {
    a.download = info.file_name;
  } else if (info && info.display_title) {
    const ext = info.ext || '.docx';
    const clean = info.display_title.replace(/^[《<]+|[》>]+$/g, '').trim();
    a.download = clean.endsWith(ext) ? clean : `${clean}${ext}`;
  } else {
    a.download = '';
  }
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  showToast(`已开始下载【${target === 'template' ? '基准母本' : '送审合同'}】原件！`, 'info');
}

function openCurrentFileInLocalApp() {
  const target = currentPreviewTarget === 'side_by_side' ? 'reviewed' : currentPreviewTarget;
  const sessionId = (ReviewApiClient && ReviewApiClient.sessionId) || (currentData && currentData.session_id) || 'default_session';

  showToast(`正在通过系统关联程序打开【${target === 'template' ? '基准母本' : '送审合同'}】原件...`, 'info');
  fetch('/api/diff/open_local', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target: target, session_id: sessionId })
  })
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        showToast(`调用本地打开提示: ${data.message || data.error}`, 'warning');
      } else {
        showToast(data.message || '已成功唤起系统默认 Office 软件！', 'success');
      }
    })
    .catch(err => {
      showToast(`唤起失败: ${err.message}`, 'error');
    });
}
