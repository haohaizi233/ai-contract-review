// ==================== Agent 智能研判与全链路容灾客户端 (爱马仕/CC/小龙虾) ====================
let agentEventSource = null;
let agentReconnectTimer = null;
let agentHeartbeatTimer = null;
let agentState = {
  status: 'IDLE',
  completed: 0,
  total: 0,
  currentAnnId: null,
  circuitBreaker: { state: 'CLOSED', failure_count: 0 }
};

function initAgentSSE(force = false) {
  const currentSessId = (typeof ReviewApiClient !== 'undefined' && ReviewApiClient.sessionId) ? ReviewApiClient.sessionId : 'default_session';
  const currentTenant = (typeof ReviewApiClient !== 'undefined' && ReviewApiClient.tenantId) ? ReviewApiClient.tenantId : 'default';

  // 若当前已有活跃健康的长连接且非强制重建，无需强行关闭重建
  if (!force && agentEventSource && agentEventSource._forSessionId === currentSessId &&
      (agentEventSource.readyState === EventSource.OPEN || agentEventSource.readyState === EventSource.CONNECTING)) {
    updateHeartbeatTag(true);
    return;
  }

  if (agentEventSource) {
    try { agentEventSource.close(); } catch (e) {}
    agentEventSource = null;
  }
  clearTimeout(agentReconnectTimer);
  clearTimeout(agentHeartbeatTimer);

  const resetHeartbeat = () => {
    clearTimeout(agentHeartbeatTimer);
    updateHeartbeatTag(true);
    agentHeartbeatTimer = setTimeout(() => {
      console.warn('[SSE] 心跳超时（>14s无响应），启动自动重连...');
      updateHeartbeatTag(false);
      initAgentSSE(true);
    }, 14000);
  };

  try {
    const streamUrl = `/api/diff/agent_stream?session_id=${encodeURIComponent(currentSessId)}&tenant_id=${encodeURIComponent(currentTenant)}`;
    agentEventSource = new EventSource(streamUrl);
    agentEventSource._forSessionId = currentSessId;

    agentEventSource.onopen = () => {
      console.log('[SSE] 已接入法务 Agent 实时研判流');
      updateHeartbeatTag(true);
      resetHeartbeat();
    };

    agentEventSource.addEventListener('snapshot', (e) => {
      resetHeartbeat();
      try {
        const snap = JSON.parse(e.data);
        handleAgentSnapshot(snap);
      } catch (err) { console.error('Error handling snapshot:', err); }
    });

    agentEventSource.addEventListener('init', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        agentState.status = data.status || 'RUNNING';
        agentState.completed = data.completed || 0;
        agentState.total = data.total || 0;
        updateAgentUI();
        if (data.restored_count > 0) {
          showToast(`已从持久化检查点秒级恢复 ${data.restored_count} 项研判！`, 'info');
        }
      } catch (err) {}
    });

    agentEventSource.addEventListener('card_start', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        agentState.currentAnnId = data.id;
        updateCardRunningStatus(data.id, true);
        updateAgentUI();
      } catch (err) {}
    });

    agentEventSource.addEventListener('card_complete', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        handleCardComplete(data);
      } catch (err) {}
    });

    agentEventSource.addEventListener('card_prioritized', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        showToast(`条款 #${data.id} 已成功插队至首位！`, 'success');
      } catch (err) {}
    });

    agentEventSource.addEventListener('card_retry', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        showToast(`条款 #${data.id} 已重置并进入优先队列！`, 'info');
      } catch (err) {}
    });

    agentEventSource.addEventListener('card_skip', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        showToast(`条款 #${data.id} 已跳过研判。`, 'info');
      } catch (err) {}
    });

    agentEventSource.addEventListener('progress', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        agentState.completed = data.completed;
        agentState.total = data.total;
        updateAgentUI();
      } catch (err) {}
    });

    agentEventSource.addEventListener('status_change', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        agentState.status = data.status;
        updateAgentUI();
      } catch (err) {}
    });

    agentEventSource.addEventListener('circuit_state_change', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        agentState.circuitBreaker = data;
        updateAgentUI();
        if (data.state === 'OPEN') {
          showToast('⚠️ 大模型连续异常，已自动激活爱马仕/CC级自适应熔断保护（秒级规则兜底）', 'warning');
        } else if (data.state === 'CLOSED') {
          showToast('🟢 大模型服务已恢复，熔断器已复位正常', 'success');
        }
      } catch (err) {}
    });

    agentEventSource.addEventListener('watchdog_alert', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        showToast('🛡️ ' + (data.message || '看门狗抢救超时条款成功'), 'warning');
      } catch (err) {}
    });

    agentEventSource.addEventListener('all_complete', (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        agentState.status = 'COMPLETED';
        agentState.currentAnnId = null;
        updateAgentUI();
        showToast('🎉 ' + (data.message || '全篇条款全部静默研判完毕！'), 'success');
      } catch (err) {}
    });

    agentEventSource.onerror = (e) => {
      // 只有在长连接彻底断开时触发状态更新与退避自愈
      if (!agentEventSource || agentEventSource.readyState === EventSource.CLOSED) {
        updateHeartbeatTag(false);
        clearTimeout(agentReconnectTimer);
        agentReconnectTimer = setTimeout(() => initAgentSSE(true), 2500);
      }
    };
  } catch (err) {
    console.error('SSE initialization failed:', err);
  }
}

function handleAgentSnapshot(snap) {
  if (!snap) return;
  agentState.status = snap.status || 'IDLE';
  agentState.currentAnnId = snap.current_ann_id;
  agentState.completed = (snap.completed !== undefined) ? snap.completed : (snap.completed_count || 0);
  agentState.total = (snap.total !== undefined) ? snap.total : (snap.total_count || 0);
  if (snap.circuit_breaker) {
    agentState.circuitBreaker = snap.circuit_breaker;
  }

  // 批量合并已研判卡片
  const cards = snap.completed_cards || snap.annotations;
  if (currentData && currentData.annotations && cards) {
    cards.forEach(card => {
      const ann = currentData.annotations.find(a => a.id === card.id);
      if (ann && (card._aiDone || card.ai_analysis || (card.result && card.result.ai_analysis))) {
        const res = card.result || card;
        ann.risk_level = res.risk_level || ann.risk_level;
        ann.risk_label = res.risk_label || ann.risk_label;
        ann.ai_analysis = res.ai_analysis || ann.ai_analysis;
        ann.action_advice = res.action_advice || ann.action_advice;
        ann._reasoning = res.reasoning || card._reasoning || '';
        ann._source = res.source || card._source || 'AI_AGENT';
        ann._elapsedSec = (res.elapsed_sec !== undefined) ? res.elapsed_sec : (card._elapsedSec || 0);
        ann._fromCache = (res.from_cache !== undefined) ? res.from_cache : (card._fromCache || false);
        ann._aiDone = true;
      }
    });
    renderAnnotationsSidebar();
  }
  updateAgentUI();
}

function handleCardComplete(data) {
  if (!data) return;
  const res = data.result || data;

  agentState.completed = (data.completed !== undefined) ? data.completed : (res.completed !== undefined ? res.completed : agentState.completed);
  agentState.total = (data.total !== undefined) ? data.total : (res.total !== undefined ? res.total : agentState.total);
  if (agentState.currentAnnId === data.id) {
    agentState.currentAnnId = null;
  }

  const riskLevel = res.risk_level || data.risk_level || '';
  const riskLabel = res.risk_label || data.risk_label || '';
  const aiAnalysis = res.ai_analysis !== undefined ? res.ai_analysis : (data.ai_analysis !== undefined ? data.ai_analysis : '');
  const actionAdvice = res.action_advice !== undefined ? res.action_advice : (data.action_advice !== undefined ? data.action_advice : '');
  const reasoning = res.reasoning || data.reasoning || res._reasoning || data._reasoning || '';
  const source = res.source || data.source || res._source || data._source || 'AI_AGENT';
  const elapsedSec = (res.elapsed_sec !== undefined) ? res.elapsed_sec : ((data.elapsed_sec !== undefined) ? data.elapsed_sec : ((res._elapsedSec !== undefined) ? res._elapsedSec : 0));
  const fromCache = (res.from_cache !== undefined) ? res.from_cache : ((data.from_cache !== undefined) ? data.from_cache : ((res._fromCache !== undefined) ? res._fromCache : false));

  if (currentData && currentData.annotations) {
    const ann = currentData.annotations.find(a => a.id === data.id);
    if (ann) {
      if (riskLevel) ann.risk_level = riskLevel;
      if (riskLabel) ann.risk_label = riskLabel;
      if (aiAnalysis) ann.ai_analysis = aiAnalysis;
      if (actionAdvice) ann.action_advice = actionAdvice;
      ann._reasoning = reasoning;
      ann._source = source;
      ann._elapsedSec = elapsedSec;
      ann._fromCache = fromCache;
      ann._aiDone = true;
    }
  }

  // 单卡增量更新 DOM，避免整体重绘丢失滚动位
  const card = document.getElementById(`card-${data.id}`);
  if (card) {
    card.classList.remove('agent-running');
    const pill = card.querySelector('.card-risk-pill');
    if (pill) {
      if (riskLevel) pill.className = `card-risk-pill ${riskLevel}`;
      if (riskLabel) pill.innerText = riskLabel;
    }
    const analysisEl = card.querySelector('.card-analysis');
    if (analysisEl && aiAnalysis) analysisEl.innerText = aiAnalysis;
    const adviceEl = card.querySelector('.card-advice');
    if (adviceEl && actionAdvice) adviceEl.innerText = actionAdvice;

    // 更新来源标签
    let sBadge = card.querySelector('.card-source-badge');
    let badgeText = '🤖 AI研判';
    let badgeClass = 'ai';
    if (source === 'FALLBACK_RULE' || source === 'CIRCUIT_BREAKER_FALLBACK') {
      badgeText = '🛡️ 容灾规则降级'; badgeClass = 'fallback';
    } else if (source === 'WATCHDOG_TIMEOUT_FALLBACK' || source === 'WATCHDOG_FALLBACK') {
      badgeText = '⏱️ 看门狗抢救'; badgeClass = 'watchdog';
    } else if (source === 'CHECKPOINT_RESUMED') {
      badgeText = '💾 断点自愈恢复'; badgeClass = 'resume';
    } else if (fromCache) {
      badgeText = '⚡ 本地缓存'; badgeClass = 'cache';
    } else if (elapsedSec) {
      badgeText = `🤖 AI研判 ${elapsedSec}s`;
    }
    if (sBadge) {
      sBadge.className = `card-source-badge ${badgeClass}`;
      sBadge.innerText = badgeText;
    }
  } else {
    renderAnnotationsSidebar();
  }

  updateAgentUI();
}

function updateCardRunningStatus(annId, isRunning) {
  document.querySelectorAll('.annotation-card').forEach(c => c.classList.remove('agent-running'));
  if (isRunning && annId) {
    const card = document.getElementById(`card-${annId}`);
    if (card) {
      card.classList.add('agent-running');
      let sBadge = card.querySelector('.card-source-badge');
      if (sBadge) {
        sBadge.className = 'card-source-badge running';
        sBadge.innerText = '⚡ 正在深度研判...';
      }
    }
  }
}

function updateAgentUI() {
  const badge = document.getElementById('agentStateBadge');
  const ratio = document.getElementById('agentProgressRatio');
  const bar = document.getElementById('agentProgressBar');
  const btnPause = document.getElementById('btnAgentPause');
  const btnResetCircuit = document.getElementById('btnResetCircuit');
  const circuitTag = document.getElementById('agentCircuitTag');

  const cur = agentState.completed || 0;
  const tot = agentState.total || 0;
  const pct = tot > 0 ? Math.round((cur / tot) * 100) : 0;

  if (ratio) ratio.innerText = `${cur}/${tot} (${pct}%)`;
  if (bar) bar.style.width = `${pct}%`;

  if (badge) {
    if (agentState.status === 'COMPLETED') {
      badge.className = 'agent-state-pill COMPLETED';
      badge.innerText = '✅ 全部研判完成';
      if (btnPause) btnPause.disabled = true;
    } else if (agentState.status === 'PAUSED') {
      badge.className = 'agent-state-pill PAUSED';
      badge.innerText = '⏸️ 研判已暂停';
      if (btnPause) { btnPause.disabled = false; btnPause.innerText = '▶️ 继续'; }
    } else if (agentState.circuitBreaker && agentState.circuitBreaker.state === 'OPEN') {
      badge.className = 'agent-state-pill CIRCUIT_OPEN';
      badge.innerText = '🛡️ 熔断降级自愈中';
      if (btnPause) { btnPause.disabled = false; btnPause.innerText = '⏸️ 暂停'; }
    } else {
      badge.className = 'agent-state-pill RUNNING';
      badge.innerText = `🟢 智能研判中 (${cur}/${tot})`;
      if (btnPause) { btnPause.disabled = false; btnPause.innerText = '⏸️ 暂停'; }
    }
  }

  if (circuitTag && agentState.circuitBreaker) {
    const cbState = agentState.circuitBreaker.state;
    if (cbState === 'OPEN') {
      circuitTag.innerText = '🛡️ 熔断触发 (企业红线速断)';
      circuitTag.style.color = '#DC2626';
      if (btnResetCircuit) btnResetCircuit.style.display = 'inline-block';
    } else {
      circuitTag.innerText = '🛡️ 熔断防护: 闭合';
      circuitTag.style.color = '#64748B';
      if (btnResetCircuit) btnResetCircuit.style.display = 'none';
    }
  }
}

function updateHeartbeatTag(isAlive) {
  const tag = document.getElementById('agentHeartbeatTag');
  if (tag) {
    if (isAlive) {
      tag.innerText = '💓 心跳正常';
      tag.style.color = '#10B981';
    } else {
      tag.innerText = '💔 重连中...';
      tag.style.color = '#EF4444';
    }
  }
}

async function toggleAgentPause() {
  try {
    const resp = await fetch('/api/diff/toggle_pause', { method: 'POST' });
    const res = await resp.json();
    agentState.status = res.status;
    updateAgentUI();
    showToast(res.status === 'PAUSED' ? '研判已暂停' : '研判已恢复继续', 'info');
  } catch (err) {
    showToast('切换暂停失败: ' + err, 'error');
  }
}

async function rerunAgentReview() {
  showLoading('正在重置并启动全篇重新研判...');
  try {
    const resp = await fetch('/api/diff/rerun_review', { method: 'POST' });
    const res = await resp.json();
    hideLoading();
    if (res.success) {
      showToast('已清空旧任务，全新研判流水线已就绪启动！', 'success');
      if (currentData && currentData.annotations) {
        currentData.annotations.forEach(a => {
          a._aiDone = false;
        });
      }
      agentState.completed = 0;
      agentState.status = 'RUNNING';
      updateAgentUI();
      renderAnnotationsSidebar();
      initAgentSSE(true);
    }
  } catch (err) {
    hideLoading();
    showToast('重置研判请求失败: ' + err, 'error');
  }
}

async function prioritizeCard(annId) {
  try {
    const resp = await fetch('/api/diff/prioritize_card', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: annId })
    });
    const res = await resp.json();
    if (res.success) {
      showToast(`条款 #${annId} 已插队至第一位！`, 'success');
    }
  } catch (err) {
    showToast('插队请求失败: ' + err, 'error');
  }
}

async function retryCard(annId) {
  try {
    const resp = await fetch('/api/diff/retry_card', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: annId })
    });
    const res = await resp.json();
    if (res.success) {
      showToast(`条款 #${annId} 缓存已清除，进入优先重审！`, 'info');
    }
  } catch (err) {
    showToast('重审请求失败: ' + err, 'error');
  }
}

async function resetCircuitBreaker() {
  try {
    const resp = await fetch('/api/diff/reset_circuit', { method: 'POST' });
    const res = await resp.json();
    if (res.success) {
      showToast('大模型熔断器已手动复位！', 'success');
    }
  } catch (err) {
    showToast('复位熔断器失败: ' + err, 'error');
  }
}

function toggleReasoning(annId) {
  const el = document.getElementById(`reasoning-${annId}`);
  if (el) {
    el.style.display = (el.style.display === 'none') ? 'block' : 'none';
  }
}
