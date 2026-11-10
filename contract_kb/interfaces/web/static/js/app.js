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