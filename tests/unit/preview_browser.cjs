// Headless Edge test for Word original format preview and shortcut keys (Alt+1 / Alt+2)
const fs = require('node:fs');
const { spawn } = require('node:child_process');
const assert = require('node:assert/strict');

const jszip = fs.readFileSync('contract_kb/interfaces/web/static/js/vendor/jszip.min.js', 'utf8');
const docx = fs.readFileSync('contract_kb/interfaces/web/static/js/vendor/docx-preview.min.js', 'utf8');
const css = fs.readFileSync('contract_kb/interfaces/web/static/css/app.css', 'utf8');
const appJs = fs.readFileSync('contract_kb/interfaces/web/static/js/app.js', 'utf8');
const apiClientJs = fs.readFileSync('contract_kb/interfaces/web/static/js/api_client.js', 'utf8');
const sampleDocxBase64 = fs.readFileSync('参考/专利/国内专利服务合同（年费缴纳）-事务所.docx').toString('base64');
const cleanAppJs = appJs
  .replace('__INITIAL_DATA_PLACEHOLDER__', 'null')
  .replace('__ALL_TEMPLATES_PLACEHOLDER__', '[]')
  .replace('__REF_TEMPLATES_PLACEHOLDER__', '[]')
  .replace('__MATERIAL_DATA_PLACEHOLDER__', '{}');

// Minimal HTML harness containing the headers, buttons, modal, and scripts
const html = `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>${css}</style>
<script>${jszip}</script>
<script>${docx}</script>
</head>
<body>
<div class="column-sticky-header standard">
  <div class="col-header-info">
    <button id="btnStdPreview" class="col-preview-btn" onclick="openOriginalPreview('template')">👁️ 原格式预览 <kbd>Alt+1</kbd></button>
  </div>
</div>
<div class="column-sticky-header reviewed">
  <div class="col-header-info">
    <button id="btnRevPreview" class="col-preview-btn" onclick="openOriginalPreview('reviewed')">👁️ 原格式预览 <kbd>Alt+2</kbd></button>
  </div>
</div>

<!-- 模态弹窗 -->
<div class="modal-overlay preview-modal-overlay" id="previewModal" onclick="closePreviewModalOnBackdrop(event)">
  <div class="preview-modal-dialog" id="previewModalDialog" onclick="event.stopPropagation()">
    <div class="preview-modal-header">
      <span class="preview-title-text" id="previewModalTitle">原格式高保真在线预览</span>
      <div class="preview-tabs-group">
        <button class="preview-tab-btn" id="previewTabTemplate" onclick="switchPreviewTarget('template')">🏛️ 基准母本 <kbd>Alt+1</kbd></button>
        <button class="preview-tab-btn active" id="previewTabReviewed" onclick="switchPreviewTarget('reviewed')">✏️ 送审合同 <kbd>Alt+2</kbd></button>
        <button class="preview-tab-btn" id="previewTabSideBySide" onclick="switchPreviewTarget('side_by_side')">📖 双原版并排 <kbd>Alt+3</kbd></button>
      </div>
      <div class="preview-zoom-group">
        <span class="preview-zoom-label" id="previewZoomValue">100%</span>
      </div>
      <div class="preview-actions-group">
        <button class="preview-action-btn" id="btnPreviewFullscreen" onclick="togglePreviewFullscreen()">🗖 全屏</button>
        <button class="preview-close-btn" id="btnClosePreview" onclick="closePreviewModal()">✕</button>
      </div>
    </div>
    <div class="preview-meta-strip">
      <span id="previewMetaFileName">-</span>
      <span class="preview-ext-badge" id="previewMetaExt">DOCX</span>
      <span id="previewMetaSize">-</span>
      <code class="preview-hash-code" id="previewMetaHash">-</code>
    </div>
    <div class="preview-viewport" id="previewViewport">
      <div class="preview-loading" id="previewLoading"></div>
      <div class="preview-document-container" id="previewDocContainer">
        <div class="preview-canvas-wrapper" id="previewCanvasWrapper">
          <div class="docx-preview-body" id="docxPreviewBody"></div>
        </div>
      </div>
      <div class="preview-split-container" id="previewSplitContainer" style="display:none;">
        <div class="preview-split-pane" id="previewSplitLeft">
          <div class="preview-split-pane-scroll"><div class="docx-preview-body" id="docxSplitBodyLeft"></div></div>
        </div>
        <div class="preview-split-pane" id="previewSplitRight">
          <div class="preview-split-pane-scroll"><div class="docx-preview-body" id="docxSplitBodyRight"></div></div>
        </div>
      </div>
    </div>
  </div>
</div>
<div id="toastContainer"></div>
<div id="dropZone" style="display:none;"></div>

<script>
// Mock fetch for preview_file
window.SAMPLE_DOCX_B64 = "${sampleDocxBase64}";
const originalFetch = window.fetch;
window.fetch = async function(url, options) {
  if (url.includes('/api/diff/preview_file')) {
    if (url.includes('info=1')) {
      const isTpl = url.includes('target=template');
      return {
        ok: true,
        status: 200,
        json: async () => ({
          success: true,
          target: isTpl ? 'template' : 'reviewed',
          file_name: isTpl ? '国内专利服务合同（年费缴纳）-事务所.docx' : '送审合同修改稿.docx',
          display_title: isTpl ? '国内专利服务合同（年费缴纳）' : '送审修改稿',
          ext: '.docx',
          size: 75230,
          sha256: '02fab793d868c5957b4c5a369b6ca859f4a1cb86d73e33657b94a2c5fdbcee5a',
        })
      };
    } else {
      const raw = atob(window.SAMPLE_DOCX_B64);
      const bytes = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
      return {
        ok: true,
        status: 200,
        blob: async () => new Blob([bytes], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
      };
    }
  }
  return originalFetch ? originalFetch(url, options) : Promise.reject(new Error('not mocked'));
};
function showToast(msg, type) {}
window.currentData = { session_id: 'sess_test' };
window.ReviewApiClient = { sessionId: 'sess_test' };
</script>
<script>
${apiClientJs}
</script>
<script>
${cleanAppJs}
</script>
</body>
</html>`;

(async () => {
  const child = spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', [
    '--headless',
    '--disable-gpu',
    '--no-first-run',
    '--remote-debugging-port=9347',
    '--user-data-dir=' + process.cwd() + '/tmp/edge-preview-test',
    'about:blank'
  ], { windowsHide: true, stdio: 'ignore' });

  try {
    let pages;
    for (let i = 0; i < 40; i++) {
      try {
        pages = await (await fetch('http://127.0.0.1:9347/json')).json();
        break;
      } catch {
        await new Promise(r => setTimeout(r, 100));
      }
    }
    const ws = new WebSocket(pages.find(p => p.type === 'page').webSocketDebuggerUrl);
    await new Promise(r => ws.onopen = r);
    let id = 0;
    const pending = new Map();
    ws.onmessage = e => {
      const m = JSON.parse(e.data);
      if (m.method === 'Runtime.exceptionThrown') {
        console.log('PAGE EXCEPTION:', m.params.exceptionDetails);
      }
      if (m.id) { pending.get(m.id)(m); pending.delete(m.id); }
    };
    const send = (method, params = {}) => new Promise(r => {
      pending.set(++id, r);
      ws.send(JSON.stringify({ id, method, params }));
    });

    await send('Page.enable');
    await send('Runtime.enable');
    await send('Page.setDocumentContent', {
      frameId: (await send('Page.getFrameTree')).result.frameTree.frame.id,
      html
    });

    const evaluate = async (expr) => {
      const res = await send('Runtime.evaluate', {
        expression: expr,
        awaitPromise: true,
        returnByValue: true
      });
      if (res.result && res.result.exceptionDetails) {
        throw new Error(JSON.stringify(res.result.exceptionDetails));
      }
      if (res.result && res.result.result) {
        return res.result.result.value;
      }
      return res.result ? res.result.value : undefined;
    };

    console.log('1. Testing initial state...');
    let state = await evaluate(`({
      isOpen: document.getElementById('previewModal').classList.contains('open'),
      hasButtons: Boolean(document.getElementById('btnStdPreview') && document.getElementById('btnRevPreview'))
    })`);
    assert.equal(state.isOpen, false);
    assert.equal(state.hasButtons, true);

    console.log('2. Testing Alt+1 shortcut for template preview...');
    await evaluate(`window.dispatchEvent(new KeyboardEvent('keydown', { key: '1', altKey: true, bubbles: true }))`);
    await new Promise(r => setTimeout(r, 800));

    state = await evaluate(`({
      isOpen: document.getElementById('previewModal').classList.contains('open'),
      target: currentPreviewTarget,
      title: document.getElementById('previewModalTitle').innerText,
      renderedChildren: document.getElementById('docxPreviewBody').children.length,
      docText: document.getElementById('docxPreviewBody').innerText.slice(0, 50)
    })`);
    console.log('Alt+1 Result:', state);
    assert.equal(state.isOpen, true);
    assert.equal(state.target, 'template');
    assert.ok(state.renderedChildren > 0, 'docx should render child elements');
    assert.ok(state.docText.includes('国内专利') || state.docText.includes('合同'), 'rendered text check');

    console.log('3. Testing Alt+2 shortcut to switch to reviewed preview...');
    await evaluate(`window.dispatchEvent(new KeyboardEvent('keydown', { key: '2', altKey: true, bubbles: true }))`);
    await new Promise(r => setTimeout(r, 800));

    state = await evaluate(`({
      target: currentPreviewTarget,
      title: document.getElementById('previewModalTitle').innerText,
      renderedChildren: document.getElementById('docxPreviewBody').children.length
    })`);
    console.log('Alt+2 Result:', state);
    assert.equal(state.target, 'reviewed');
    assert.ok(state.renderedChildren > 0);

    console.log('4. Testing Escape shortcut to close preview...');
    await evaluate(`window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))`);
    state = await evaluate(`document.getElementById('previewModal').classList.contains('open')`);
    assert.equal(state, false, 'Modal should close on Escape');

    console.log('5. Testing column button click for standard template...');
    await evaluate(`document.getElementById('btnStdPreview').click()`);
    await new Promise(r => setTimeout(r, 600));
    state = await evaluate(`({
      isOpen: document.getElementById('previewModal').classList.contains('open'),
      target: currentPreviewTarget
    })`);
    assert.equal(state.isOpen, true);
    assert.equal(state.target, 'template');

    console.log('6. Testing fullscreen toggle...');
    await evaluate(`togglePreviewFullscreen()`);
    state = await evaluate(`document.getElementById('previewModalDialog').classList.contains('fullscreen')`);
    assert.equal(state, true);

    console.log('7. Testing zoom controls (+, -, 0)...');
    await evaluate(`window.dispatchEvent(new KeyboardEvent('keydown', { key: '+', bubbles: true }))`);
    state = await evaluate(`({ scale: previewZoomScale, label: document.getElementById('previewZoomValue').innerText })`);
    assert.equal(state.scale, 1.1);
    assert.equal(state.label, '110%');

    await evaluate(`window.dispatchEvent(new KeyboardEvent('keydown', { key: '0', bubbles: true }))`);
    state = await evaluate(`({ scale: previewZoomScale, label: document.getElementById('previewZoomValue').innerText })`);
    assert.equal(state.scale, 1.0);
    assert.equal(state.label, '100%');

    console.log('ALL BROWSER PREVIEW TESTS PASSED SUCCESSFULLY!');
    ws.close();
  } catch(err) {
    console.error('Browser Test Error:', err);
    process.exit(1);
  } finally {
    child.kill();
  }
})();
