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

  if (html.includes('<table') || html.includes('contract-table-card') || html.includes('contract-doc-table')) {
    return html;
  }

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
  resetThreeWaySyncScroll();
  currentData = data;
  if (data && data.session_id) {
    ReviewApiClient.setSession(data.session_id, data.tenant_id, data.user_id);
  }
  activeAnnotationIndex = -1;

  // 1. 规范化送审合同名称展示（彻底杜绝技术内部文件名暴露）
  let friendlyDocTitle = data.friendly_doc_title || data.reviewed_doc_title || data.display_doc_name || data.doc_title || '';
  if (!friendlyDocTitle || /active_review|mut_|\.docx$/i.test(friendlyDocTitle)) {
    const raw = (data.uploaded_file || '').split(/[\/\\]/).pop();
    if (raw.includes('06') || raw.includes('confidential')) {
      friendlyDocTitle = '《国内专利服务合同（年费缴纳）》送审修改稿（保密义务被删）';
    } else if (raw.includes('驳回复审')) {
      friendlyDocTitle = '《商标驳回复审代理协议》送审原稿';
    } else {
      friendlyDocTitle = '《国内专利服务合同（年费缴纳）》送审修改稿';
    }
  }

  // 确保书名号格式优美
  if (!friendlyDocTitle.startsWith('《') || !friendlyDocTitle.endsWith('》')) {
    const rawInner = friendlyDocTitle.replace(/^[《<]+|[》>]+$/g, '');
    friendlyDocTitle = '《' + rawInner + '》';
  }

  document.getElementById('docFileName').innerText = friendlyDocTitle;

  // 2. 规范化基准母本名称展示（彻底杜绝十六进制哈希 ID 暴露）
  let tplCleanName = data.clean_template_name || data.matched_template_display || '';
  if (!tplCleanName) {
    const rawTplName = data.matched_template_name || '国内专利服务合同（年费缴纳）';
    const mbCode = data.clean_template_code || data.matched_template_code || (String(data.matched_template_id || '').startsWith('MB') ? data.matched_template_id : '');
    const cleanBase = rawTplName.replace(/\.(docx?|pdf)$/i, '')
      .replace(/^WDCLI_[a-f0-9]+_/, '')
      .replace(/^MB\w+?_V[\d\.]+?_/, '')
      .replace(/[-_](恒程|创新|代理|知识产权|事务所|公司|企业).*$/, '')
      .trim();
    tplCleanName = mbCode ? '[' + mbCode + '] ' + cleanBase : cleanBase;
  }

  const matchedNameEl = document.getElementById('matchedTemplateName');
  if (matchedNameEl) matchedNameEl.innerText = tplCleanName;
  // 顶部母本悬停提示（若为 MB 编号则展示，若是十六进制哈希则不暴露底层哈希）
  const tplId = data.matched_template_id || '';
  const isHash = data.is_hex_hash_id || (tplId.length > 12 && !tplId.startsWith('MB'));
  document.getElementById('matchedTemplateTag').title = `基准模板: ${tplCleanName}${(!isHash && tplId) ? ' (' + tplId + ')' : ''} · 点击更换`;

  // 3. 渲染置信度标签
  const confTag = document.getElementById('confidenceTag');
  const confValText = document.getElementById('confValueText');
  const confPillText = document.getElementById('confPillText');

  const confScore = typeof data.confidence_score === 'number' ? data.confidence_score : (typeof data.classification_confidence === 'number' ? data.classification_confidence : 1.0);
  const isManual = data.is_manual_override === true || data.matched_method === 'MANUAL_SELECTION';

  if (isManual) {
    confTag.className = 'meta-tag confidence conf-manual';
    confValText.innerText = '100.0%';
    confPillText.innerText = '手动指定';
    confTag.title = `【模板匹配机制】人工手动指定模式\n基准母本已由用户锁定: ${tplCleanName}\n点击基准母本可随时更换或恢复自动识别。`;
  } else {
    const pct = data.confidence_percent || `${(confScore * 100).toFixed(1)}%`;
    confValText.innerText = pct;

    if (confScore >= 0.85) {
      confTag.className = 'meta-tag confidence conf-high';
      confPillText.innerText = '权威匹配';
    } else if (confScore >= 0.70) {
      confTag.className = 'meta-tag confidence conf-medium';
      confPillText.innerText = '良好匹配';
    } else {
      confTag.className = 'meta-tag confidence conf-low';
      confPillText.innerText = '建议复核';
    }

    confTag.title = `【AI 智能路由置信度】${pct}\n识别业务子类: ${data.detected_subtype || '标准合同'}\n匹配基准母本: ${tplCleanName}\n状态: ${data.confidence_desc || '自动五级分类对齐'}`;
  }

  // 4. 双栏纸张列标题规范化呈现（绝不显示底层 16 进制 Hash 码）
  const stdCodeStr = (!isHash && data.clean_template_code) ? ` (${data.clean_template_code})` : '';
  document.getElementById('headerStdName').innerText = `${tplCleanName}${stdCodeStr}`;
  document.getElementById('headerRevName').innerText = friendlyDocTitle;

  // 5. 统计数据
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

  // 双栏渲染完毕后，同步左右每一行条款的高度
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
      return;
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

// 左右条款高度等高自适应平衡算法
function syncClauseHeights() {
  if (!currentData || !currentData.aligned_blocks) return;
  const blocks = currentData.aligned_blocks;

  blocks.forEach(b => {
    const s = document.getElementById(`std-${b.block_id}`);
    const r = document.getElementById(`rev-${b.block_id}`);
    if (s) s.style.minHeight = '0px';
    if (r) r.style.minHeight = '0px';
  });

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

// ==================== 三栏双向协同联动滚动 ====================
let activeDriver = null;
let syncListenerAttached = false;
let syncFrame = 0;
let resyncFrame = 0;
let scrollGeometry = null;
let scrollPanes = null;
const followerPositions = new WeakMap();

// Coordinates are relative to each scrollport, never to an offsetParent.
function contentTop(element, pane) {
  return element.getBoundingClientRect().top - pane.getBoundingClientRect().top
    - pane.clientTop + pane.scrollTop;
}
function scrollLimit(pane) { return Math.max(0, pane.scrollHeight - pane.clientHeight); }
function invalidateScrollGeometry() { scrollGeometry = null; }
function resetThreeWaySyncScroll() {
  cancelAnimationFrame(syncFrame);
  cancelAnimationFrame(resyncFrame);
  syncFrame = 0;
  resyncFrame = 0;
  activeDriver = null;
  invalidateScrollGeometry();
  if (scrollPanes) Object.values(scrollPanes).forEach(pane => {
    pane.scrollTo({top: pane.scrollTop, behavior: 'instant'});
    followerPositions.delete(pane);
  });
}

function highlightAnnotation(annId) {
  const card = document.getElementById(`card-${annId}`);
  if (currentData?.annotations?.[activeAnnotationIndex]?.id === annId && card?.classList.contains('active')) return;
  const index = (currentData?.annotations || []).findIndex(a => a.id === annId);
  if (index < 0) return;
  const ann = currentData.annotations[index];
  activeAnnotationIndex = index;
  document.querySelectorAll('.annotation-card.active').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.clause-block.active-clause').forEach(el => el.classList.remove('active-clause'));
  card?.classList.add('active');
  document.getElementById(`std-${ann.block_id}`)?.classList.add('active-clause');
  document.getElementById(`rev-${ann.block_id}`)?.classList.add('active-clause');
  const indicator = document.getElementById('navIndicator');
  if (indicator) indicator.innerText = `批注 ${ann.index || index + 1} / ${currentData.annotations.length}`;
}

// `readingY` is a content coordinate: highlight the clause/card under the pane's reading line.
function highlightScrollPosition(source, readingY) {
  const stops = scrollGeometry.annotationStops[source];
  if (!stops.length) return;
  let low = 0, high = stops.length;
  while (low < high) {
    const mid = (low + high) >> 1;
    if (stops[mid].position <= readingY) low = mid + 1; else high = mid;
  }
  highlightAnnotation(stops[Math.max(0, low - 1)].id);
}

function mapScrollPosition(position, anchors) {
  if (position <= anchors[0][0]) return anchors[0][1];
  const last = anchors[anchors.length - 1];
  if (position >= last[0]) return last[1];
  let low = 0, high = anchors.length - 1;
  while (high - low > 1) {
    const mid = (low + high) >> 1;
    if (anchors[mid][0] <= position) low = mid; else high = mid;
  }
  const a = anchors[low], b = anchors[high];
  return a[1] + (b[1] - a[1]) * (position - a[0]) / (b[0] - a[0]);
}

function invertAnchors(anchors) { return anchors.map(([a, b]) => [b, a]); }

// Reading line (after Meld's calc_syncpoint): the viewport top in the first half screen, the
// viewport middle in the body, the viewport bottom in the last half screen. It maps scrollTop
// [0, max] one-to-one onto content [0, scrollHeight], so clauses in the first and last screen
// can still be aligned instead of being clipped away. Returns null for a non-scrollable pane.
function readingLineAnchors(pane) {
  const max = scrollLimit(pane), view = pane.clientHeight;
  if (max <= 0 || view <= 0) return null;
  const ramp = Math.min(view / 2, max / 2);
  const knots = [[0, 0], [ramp, ramp + view / 2]];
  if (max - ramp > ramp) knots.push([max - ramp, max - ramp + view / 2]);
  knots.push([max, max + view]);
  return knots;
}

// Longest chain strictly increasing in both coordinates (patience LIS, O(n log n)). A single
// out-of-order anchor is dropped instead of blocking every later anchor, which is what made the
// greedy filter collapse the sidebar-to-document map into one flat segment.
function monotoneAnchors(points, endX, endY) {
  const inside = points.filter(([x, y]) => x > 0 && y > 0 && x < endX && y < endY)
    .sort((a, b) => a[0] - b[0] || b[1] - a[1]);
  const tails = [], previous = new Array(inside.length);
  for (let i = 0; i < inside.length; i++) {
    let low = 0, high = tails.length;
    while (low < high) {
      const mid = (low + high) >> 1;
      if (inside[tails[mid]][1] < inside[i][1]) low = mid + 1; else high = mid;
    }
    previous[i] = low ? tails[low - 1] : -1;
    tails[low] = i;
  }
  const chain = [];
  for (let i = tails.length ? tails[tails.length - 1] : -1; i >= 0; i = previous[i]) chain.push(inside[i]);
  return [[0, 0], ...chain.reverse(), [endX, endY]];
}

// Every pane is mapped to ONE shared coordinate: the reviewed document's content (hub, the middle
// pane, as in Meld's three-way view). Each pane owns a monotone bijection to the hub, so any pane
// can drive the other two, and A->B->A round trips are exact: switching driver never jumps.
function buildScrollGeometry() {
  const result = {annotationStops: {std: [], rev: [], detail: []}, reading: {}, curves: {}};
  const hubPane = scrollPanes.rev, hubEnd = hubPane.scrollHeight;
  const blocks = currentData?.aligned_blocks || [];
  const annotations = new Map((currentData?.annotations || []).map(a => [a.id, a]));
  const blockBox = {std: new Map(), rev: new Map()};
  for (const key of ['std', 'rev']) {
    if (scrollPanes[key].scrollHeight <= 0) continue;
    for (const block of blocks) {
      const elem = document.getElementById(`${key}-${block.block_id}`);
      if (!elem) continue;
      const top = contentTop(elem, scrollPanes[key]), h = elem.getBoundingClientRect().height;
      blockBox[key].set(block.block_id, {top, center: top + h / 2, bottom: top + h});
    }
  }

  // Annotation stops for reading-line highlights.
  for (const card of scrollPanes.detail.querySelectorAll('.annotation-card')) {
    const ann = annotations.get(card.id.replace('card-', ''));
    if (!ann) continue;
    result.annotationStops.detail.push({id: ann.id,
      position: contentTop(card, scrollPanes.detail) + card.getBoundingClientRect().height / 2});
    for (const key of ['std', 'rev']) {
      const box = blockBox[key].get(ann.block_id);
      if (box) result.annotationStops[key].push({id: ann.id, position: box.center});
    }
  }
  Object.values(result.annotationStops).forEach(stops => stops.sort((a, b) => a.position - b.position));

  // Monotone reading lines: convert scrollTop -> reading line content Y and back.
  for (const [key, pane] of Object.entries(scrollPanes)) {
    const line = readingLineAnchors(pane);
    result.reading[key] = line ? {line, scroll: invertAnchors(line)} : null;
  }

  // Pane -> Hub (rev content) curves.
  result.curves.rev = {toPane: [[0, 0], [hubEnd, hubEnd]], toHub: [[0, 0], [hubEnd, hubEnd]]};
  for (const key of ['std', 'detail']) {
    const pane = scrollPanes[key], end = pane.scrollHeight;
    if (end <= 0 || hubEnd <= 0) {
      result.curves[key] = {toPane: [[0, 0], [end, hubEnd]], toHub: [[0, 0], [hubEnd, end]]};
      continue;
    }
    const points = [];
    if (key === 'std') {
      for (const block of blocks) {
        const a = blockBox.std.get(block.block_id), b = blockBox.rev.get(block.block_id);
        if (a && b) points.push([a.center, b.center]);
      }
    } else {
      for (const card of pane.querySelectorAll('.annotation-card')) {
        const ann = annotations.get(card.id.replace('card-', ''));
        if (!ann) continue;
        const b = blockBox.rev.get(ann.block_id);
        if (!b) continue;
        const cardCenter = contentTop(card, pane) + card.getBoundingClientRect().height / 2;
        points.push([cardCenter, b.center]);
      }
    }
    const toHub = monotoneAnchors(points, end, hubEnd);
    const toPane = invertAnchors(toHub);
    result.curves[key] = {toPane, toHub};
  }
  return result;
}

// Target scrollTop for `target` when `source` sits at `position`; null when either side cannot scroll.
function syncedScrollTop(source, position, target) {
  const from = scrollGeometry.reading[source], fromCurve = scrollGeometry.curves[source];
  const to = scrollGeometry.reading[target], toCurve = scrollGeometry.curves[target];
  if (!from || !fromCurve) return null;
  if (!to || !toCurve) return 0;
  const hubY = mapScrollPosition(mapScrollPosition(position, from.line), fromCurve.toHub);
  const top = mapScrollPosition(mapScrollPosition(hubY, toCurve.toPane), to.scroll);
  return Math.max(0, Math.min(scrollLimit(scrollPanes[target]), top));
}

// Move every follower to the position implied by `source`; follower echoes are recorded so they never drive back.
function applySync(source) {
  if (!scrollGeometry) scrollGeometry = buildScrollGeometry();
  const pane = scrollPanes[source];
  const position = Math.max(0, Math.min(scrollLimit(pane), pane.scrollTop));
  const reading = scrollGeometry.reading[source];
  if (reading) highlightScrollPosition(source, mapScrollPosition(position, reading.line));
  for (const [target, other] of Object.entries(scrollPanes)) {
    if (target === source) continue;
    const top = syncedScrollTop(source, position, target);
    if (top === null) continue;
    if (Math.abs(other.scrollTop - top) > 0.5) other.scrollTop = top;
    followerPositions.set(other, other.scrollTop);
  }
}

// Layout changed (filter, streamed card text, resize): rebuild once and re-align followers to the
// current driver, so the next gesture starts from a consistent state instead of jumping.
function scheduleResync() {
  invalidateScrollGeometry();
  if (resyncFrame || !scrollPanes) return;
  resyncFrame = requestAnimationFrame(() => {
    resyncFrame = 0;
    if (activeDriver === 'navigation' || syncFrame) return;
    applySync(activeDriver && scrollPanes[activeDriver] ? activeDriver : 'rev');
  });
}

function claimDriver(source) {
  if (activeDriver !== source) {
    cancelAnimationFrame(syncFrame);
    syncFrame = 0;
    // Stop a previous click-navigation animation before accepting native input.
    if (activeDriver === 'navigation') {
      Object.values(scrollPanes).forEach(p => p.scrollTo({top: p.scrollTop, behavior: 'instant'}));
    }
  }
  activeDriver = source;
  followerPositions.delete(scrollPanes[source]);
}

function setupThreeWaySyncScroll() {
  resetThreeWaySyncScroll();
  if (syncListenerAttached) {
    // A new render keeps the listeners; re-align followers to the reviewed document once laid out.
    scheduleResync();
    return;
  }
  syncListenerAttached = true;
  scrollPanes = {
    std: document.getElementById('colStandardContainer'),
    rev: document.getElementById('colReviewedContainer'),
    detail: document.getElementById('annotationsList')
  };
  const resizeObserver = new ResizeObserver(scheduleResync);
  const mutationObserver = new MutationObserver(() => {
    scheduleResync();
    // Release removed cards before observing the current render.
    resizeObserver.disconnect();
    // Observe new cards as well as existing cards whose content expands asynchronously.
    Object.values(scrollPanes).forEach(p => {
      resizeObserver.observe(p);
      p.querySelectorAll('.annotation-card, .contract-paper-canvas').forEach(el => resizeObserver.observe(el));
    });
  });
  for (const [key, pane] of Object.entries(scrollPanes)) {
    pane.tabIndex = 0;
    pane.addEventListener('wheel', event => { if (!event.ctrlKey) claimDriver(key); }, {passive: true});
    pane.addEventListener('touchstart', () => claimDriver(key), {passive: true});
    pane.addEventListener('pointerdown', () => claimDriver(key), {passive: true});
    pane.addEventListener('keydown', event => {
      if (/^(ArrowUp|ArrowDown|PageUp|PageDown|Home|End| )$/.test(event.key)
          && !event.target.closest?.('input, textarea, select, [contenteditable="true"]')) claimDriver(key);
    });
    pane.addEventListener('scroll', () => {
      if (activeDriver === 'navigation') return;
      const expected = followerPositions.get(pane);
      if (expected !== undefined && Math.abs(pane.scrollTop - expected) < 1) return;
      if (activeDriver && activeDriver !== key) return;
      activeDriver = key;
      if (syncFrame) return;
      // Coalesce writes into a single paint; cached geometry avoids per-frame layout scans.
      syncFrame = requestAnimationFrame(() => {
        syncFrame = 0;
        if (activeDriver !== key) return;
        applySync(key);
      });
    }, {passive: true});
    resizeObserver.observe(pane);
    mutationObserver.observe(pane, {childList: true, subtree: true, characterData: true});
    pane.querySelectorAll('.annotation-card, .contract-paper-canvas').forEach(el => resizeObserver.observe(el));
  }
  window.addEventListener('resize', scheduleResync);
  document.fonts?.ready.then(scheduleResync);
}


function renderAnnotationsSidebar() {
  const container = document.getElementById('annotationsList');
  // Streamed agent updates re-render the list; keep the reader's place instead of snapping to the top.
  const keptScrollTop = container.scrollTop;
  container.innerHTML = '';

  const anns = currentData.annotations || [];
  anns.forEach((ann) => {
    if (currentFilter === 'HIGH' && (ann.risk_level !== 'HIGH' || ann.delta_type === 'MISSING_CLAUSE')) return;
    if (currentFilter === 'MISSING' && ann.delta_type !== 'MISSING_CLAUSE') return;
    if (currentFilter === 'NEW' && ann.delta_type !== 'NEW_CLAUSE') return;
    if (currentFilter === 'WARNING' && (ann.risk_level !== 'MEDIUM' || ann.delta_type === 'NEW_CLAUSE')) return;

    const isCurrentRunning = (agentState && agentState.currentAnnId === ann.id);

    const card = document.createElement('div');
    card.className = `annotation-card status-${ann.review_status || 'PENDING'} ${isCurrentRunning ? 'agent-running' : ''}`;
    card.id = `card-${ann.id}`;
    card.onclick = () => selectAnnotation(ann.id);

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

    // 研判来源状态徽章
    let sourceBadge = '';
    if (isCurrentRunning) {
      sourceBadge = `<span class="card-source-badge running">⚡ 正在深度研判...</span>`;
    } else if (ann._aiDone) {
      if (ann._source === 'FALLBACK_RULE' || ann._source === 'CIRCUIT_BREAKER_FALLBACK') {
        sourceBadge = `<span class="card-source-badge fallback" title="${escapeHtml(ann.ai_analysis || '')}">🛡️ 容灾规则降级</span>`;
      } else if (ann._source === 'WATCHDOG_TIMEOUT_FALLBACK') {
        sourceBadge = `<span class="card-source-badge watchdog" title="看门狗超时抢救兜底">⏱️ 看门狗抢救</span>`;
      } else if (ann._source === 'CHECKPOINT_RESUMED') {
        sourceBadge = `<span class="card-source-badge resume" title="从断点检查点秒级自愈恢复">💾 断点自愈恢复</span>`;
      } else if (ann._fromCache) {
        sourceBadge = `<span class="card-source-badge cache" title="命中本地安全 Hash 缓存">⚡ 本地缓存</span>`;
      } else {
        const sec = ann._elapsedSec ? `${ann._elapsedSec}s` : '';
        sourceBadge = `<span class="card-source-badge ai" title="大模型实时研判">🤖 AI研判 ${sec}</span>`;
      }
    }

    // 思考过程
    let reasoningBoxHtml = '';
    let reasoningBtnHtml = '';
    if (ann._reasoning) {
      reasoningBtnHtml = `<button class="card-mini-action-btn" onclick="event.stopPropagation(); toggleReasoning('${ann.id}')" title="展开/收起思考链">🧠 思考链</button>`;
      reasoningBoxHtml = `<div class="card-reasoning-collapse" id="reasoning-${ann.id}" style="display:none;">${escapeHtml(ann._reasoning)}</div>`;
    }

    // 优先插队或单卡重审按钮
    let cardControlBtn = '';
    if (!ann._aiDone && !isCurrentRunning) {
      cardControlBtn = `<button class="card-mini-action-btn" onclick="event.stopPropagation(); prioritizeCard('${ann.id}')" title="插队至第一位优先研判">⚡ 优先</button>`;
    } else {
      cardControlBtn = `<button class="card-mini-action-btn" onclick="event.stopPropagation(); retryCard('${ann.id}')" title="清除缓存并重新研判此卡片">🔁 重审</button>`;
    }

    card.innerHTML = `
      <div class="card-top">
        <div style="display:flex; align-items:center; gap:6px;">
          <span class="card-index">批注 #${ann.index < 10 ? '0' + ann.index : ann.index}</span>
          ${sourceBadge}
        </div>
        <span class="card-risk-pill ${ann.risk_level}">${escapeHtml(ann.risk_label)}</span>
      </div>

      <div class="card-clause-name">${escapeHtml(ann.clause_title)}</div>
      ${compHtml}

      <div class="card-analysis">${escapeHtml(ann.ai_analysis)}</div>
      <div class="card-advice">${escapeHtml(ann.action_advice)}</div>
      ${reasoningBoxHtml}

      <div class="card-actions">
        <div class="card-action-btn-group">
          <button class="mini-btn ${ann.review_status === 'ACCEPTED' ? 'active-accept' : ''}" onclick="event.stopPropagation(); setAnnotationStatus('${ann.id}', 'ACCEPTED')">✅ 接受</button>
          <button class="mini-btn ${ann.review_status === 'QUESTIONED' ? 'active-question' : ''}" onclick="event.stopPropagation(); setAnnotationStatus('${ann.id}', 'QUESTIONED')">⚠️ 存疑</button>
          <button class="mini-btn ${ann.review_status === 'OVERRULED' ? 'active-overrule' : ''}" onclick="event.stopPropagation(); setAnnotationStatus('${ann.id}', 'OVERRULED')">❌ 驳回</button>
        </div>
        <div class="card-extra-action">
          ${cardControlBtn}
          ${reasoningBtnHtml}
          <button class="locate-btn" onclick="event.stopPropagation(); selectAnnotation('${ann.id}')">🎯 定位正文</button>
        </div>
      </div>
    `;

    container.appendChild(card);
  });
  container.scrollTop = keptScrollTop;
}

function selectAnnotation(annId) {
  if (!currentData || !currentData.annotations) return;

  const annIdx = currentData.annotations.findIndex(a => a.id === annId);
  if (annIdx === -1) return;
  cancelAnimationFrame(syncFrame);
  syncFrame = 0;
  activeDriver = 'navigation';
  const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth';
  activeAnnotationIndex = annIdx;
  const ann = currentData.annotations[annIdx];

  document.querySelectorAll('.annotation-card').forEach(c => c.classList.remove('active'));
  const card = document.getElementById(`card-${annId}`);
  if (card) {
    card.classList.add('active');
    const pane = document.getElementById('annotationsList');
    const top = contentTop(card, pane);
    const bottom = top + card.getBoundingClientRect().height;
    if (top < pane.scrollTop || bottom > pane.scrollTop + pane.clientHeight) {
      pane.scrollTo({top: Math.max(0, Math.min(top, bottom - pane.clientHeight)), behavior});
    }
  }

  document.querySelectorAll('.clause-block').forEach(el => el.classList.remove('active-clause'));
  const revBlock = document.getElementById(`rev-${ann.block_id}`);
  const stdBlock = document.getElementById(`std-${ann.block_id}`);

  if (revBlock) revBlock.classList.add('active-clause');
  if (stdBlock) stdBlock.classList.add('active-clause');

  for (const [element, id] of [[stdBlock, 'colStandardContainer'], [revBlock, 'colReviewedContainer']]) {
    if (!element) continue;
    const pane = document.getElementById(id);
    pane.scrollTo({top: Math.max(0, contentTop(element, pane) - 30), behavior});
  }

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
