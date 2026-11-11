// 全局状态管理
let currentData = null;
let currentViewMode = 'side'; // 'side' | 'single' | 'diff'
let currentFilter = 'ALL';
let activeAnnotationIndex = -1;

// 模板库数据与筛选状态
let allTemplates = __ALL_TEMPLATES_PLACEHOLDER__ || [];
let activeTemplateCategory = 'ALL';
let templateSearchKeyword = '';

// 素材库 (contract_merged) 状态
window.MATERIAL_DATA = __MATERIAL_DATA_PLACEHOLDER__ || {};
let currentMaterialCategory = 'ALL';
let currentMaterialSearch = '';
let currentMaterialPage = 1;
let currentMaterialHasWordOnly = true;
let currentDetailContractNo = null;

// 模板全景 (参考/) 状态
let refTemplatesCache = __REF_TEMPLATES_PLACEHOLDER__ || [];
let activeRefCategory = 'ALL';
let refSearchKeyword = '';

// 预置默认数据占位符
window.INITIAL_DATA = __INITIAL_DATA_PLACEHOLDER__;

// 统一审查会话与鉴权客户端 (ReviewApiClient)
const ReviewApiClient = {
  sessionId: (window.INITIAL_DATA && window.INITIAL_DATA.session_id) || 'default_session',
  tenantId: (window.INITIAL_DATA && window.INITIAL_DATA.tenant_id) || 'default',
  userId: (window.INITIAL_DATA && window.INITIAL_DATA.user_id) || 'local_dev_user',

  setSession(sId, tId, uId) {
    if (sId) this.sessionId = sId;
    if (tId) this.tenantId = tId;
    if (uId) this.userId = uId;
  },

  async request(url, options = {}) {
    const opts = Object.assign({}, options);
    opts.headers = Object.assign({}, opts.headers || {});
    opts.headers['X-Session-ID'] = this.sessionId;
    opts.headers['X-Tenant-ID'] = this.tenantId;
    opts.headers['X-User-ID'] = this.userId;

    const separator = url.includes('?') ? '&' : '?';
    const finalUrl = url.includes('session_id=') ? url : `${url}${separator}session_id=${encodeURIComponent(this.sessionId)}`;
    return fetch(finalUrl, opts);
  }
};
window.ReviewApiClient = ReviewApiClient;

// 自动拦截与透明注入 X-Session-ID / X-Tenant-ID
const _origFetch = window.fetch;
window.fetch = function(input, init) {
  let url = (typeof input === 'string') ? input : (input && input.url ? input.url : '');
  if (typeof url === 'string' && url.startsWith('/api/')) {
    init = init || {};
    init.headers = Object.assign({}, init.headers || {});
    if (typeof init.headers.append !== 'function') {
      if (!init.headers['X-Session-ID'] && ReviewApiClient.sessionId) {
        init.headers['X-Session-ID'] = ReviewApiClient.sessionId;
      }
      if (!init.headers['X-Tenant-ID'] && ReviewApiClient.tenantId) {
        init.headers['X-Tenant-ID'] = ReviewApiClient.tenantId;
      }
      if (!init.headers['X-User-ID'] && ReviewApiClient.userId) {
        init.headers['X-User-ID'] = ReviewApiClient.userId;
      }
    }
  }
  return _origFetch.call(this, input, init);
};

// Toast 提示工具
function showToast(message, type = 'success') {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = `toast-msg ${type}`;
  let icon = '✅';
  if (type === 'info') icon = 'ℹ️';
  if (type === 'warning') icon = '⚠️';
  if (type === 'error') icon = '❌';
  toast.innerHTML = `<span>${icon}</span><span>${escapeHtml(message)}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(-10px)';
    toast.style.transition = 'all 0.25s ease';
    setTimeout(() => toast.remove(), 260);
  }, 2600);
}

function showLoading(text = '正在处理中...') {
  const mask = document.getElementById('globalLoadingMask');
  document.getElementById('loadingMaskText').innerText = text;
  mask.classList.add('show');
}
function hideLoading() {
  document.getElementById('globalLoadingMask').classList.remove('show');
}

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

function openTemplateModal() {
  initTemplateModal();
  document.getElementById('templateModal').classList.add('open');
}
function closeTemplateModal() {
  document.getElementById('templateModal').classList.remove('open');
}
function closeTemplateModalOnBackdrop(e) {
  if (e.target.id === 'templateModal') closeTemplateModal();
}

function openMaterialModal() {
  document.getElementById('materialModal').classList.add('open');
  initMaterialModal();
}
function closeMaterialModal() {
  document.getElementById('materialModal').classList.remove('open');
}
function closeMaterialModalOnBackdrop(e) {
  if (e.target.id === 'materialModal') closeMaterialModal();
}

function openRefTemplateModal() {
  document.getElementById('refTemplateModal').classList.add('open');
  initRefTemplateModal();
}
function closeRefTemplateModal() {
  document.getElementById('refTemplateModal').classList.remove('open');
}
function closeRefTemplateModalOnBackdrop(e) {
  if (e.target.id === 'refTemplateModal') closeRefTemplateModal();
}

function openCaseDetailModal(contractNo) {
  currentDetailContractNo = contractNo;
  document.getElementById('detailModalNo').innerText = contractNo;
  document.getElementById('detailModalTitle').innerText = '案卷详情: ' + contractNo;
  document.getElementById('detailModalBody').innerHTML = '<div style="padding:40px; text-align:center; color:#64748B;">正在调取案卷全宗履约与审批档案...</div>';
  document.getElementById('caseDetailModal').classList.add('open');
  fetchCaseDetail(contractNo);
}
function closeCaseDetailModal() {
  document.getElementById('caseDetailModal').classList.remove('open');
}
function closeCaseDetailModalOnBackdrop(e) {
  if (e.target.id === 'caseDetailModal') closeCaseDetailModal();
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

window.addEventListener('keydown', (e) => {
  // 1. 原格式高保真在线预览核心快捷键 (Alt+1 母本 / Alt+2 送审 / Alt+3 并排)
  if (e.altKey && (e.key === '1' || e.key === 'm' || e.key === 'M')) {
    e.preventDefault();
    openOriginalPreview('template');
    return;
  }
  if (e.altKey && (e.key === '2' || e.key === 's' || e.key === 'S')) {
    e.preventDefault();
    openOriginalPreview('reviewed');
    return;
  }
  if (e.altKey && (e.key === '3' || e.key === 'b' || e.key === 'B')) {
    e.preventDefault();
    openOriginalPreview('side_by_side');
    return;
  }

  // 2. 预览模态弹窗处于打开状态时的专用交互快捷键
  const previewModal = document.getElementById('previewModal');
  if (previewModal && previewModal.classList.contains('open')) {
    if (e.key === 'Escape') {
      e.preventDefault();
      closePreviewModal();
      return;
    }
    if (e.key === '+' || e.key === '=') {
      e.preventDefault();
      adjustPreviewZoom(0.1);
      return;
    }
    if (e.key === '-' || e.key === '_') {
      e.preventDefault();
      adjustPreviewZoom(-0.1);
      return;
    }
    if (e.key === '0') {
      e.preventDefault();
      resetPreviewZoom();
      return;
    }
  }

  // 3. 原有批注导航与弹窗关闭快捷键
  if (e.key === '[' || (e.altKey && e.key === 'ArrowUp')) {
    prevAnnotation();
  } else if (e.key === ']' || (e.altKey && e.key === 'ArrowDown')) {
    nextAnnotation();
  } else if (e.key === 'Escape') {
    closeUploadModal();
    closeTemplateModal();
    closeMaterialModal();
    closeRefTemplateModal();
    closeCaseDetailModal();
    closePreviewModal();
  }
});

window.addEventListener('DOMContentLoaded', () => {
  if (window.INITIAL_DATA) {
    renderFullDiff(window.INITIAL_DATA);
    initAgentSSE();
  }
  if (window.MATERIAL_DATA && window.MATERIAL_DATA.stats) {
    const stats = window.MATERIAL_DATA.stats;
    const pill = document.getElementById('matStatsPill');
    if (pill) {
      pill.innerText = `共 ${(stats.total_cases || 21623).toLocaleString()} 案卷 · ${(stats.total_word_contracts || 4586).toLocaleString()} 实单 Word 原件`;
    }
  }
});