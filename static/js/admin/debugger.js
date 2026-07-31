/* ─────────────────────────────────────────────────
   Varthaai Admin — Debugger Agent page
   Requires: jQuery, utils.js (apiGet/apiPost/showLoader/escHtml), alert-modal.js
───────────────────────────────────────────────── */
(function () {
  const API = '/admin/api/debugger/';
  let currentId = null;
  let pollTimer = null;

  const KIND_LABEL = { bug: 'Bug', feature: 'Feature', query: 'Query' };
  const STATUS_LABEL = {
    new: 'Queued', analyzing: 'Analyzing…', awaiting_input: 'Your turn',
    ready: 'Ready', pr_requested: 'PR requested', pr_open: 'PR open',
    changes_requested: 'Changes requested', revising: 'Revising',
    pr_updated: 'PR updated', closed: 'Closed', failed: 'Failed',
  };

  function kindBadge(k) {
    return `<span class="kind-badge kind-${k}">${KIND_LABEL[k] || k}</span>`;
  }
  function statusPill(s) {
    return `<span class="st-pill st-${s}">${STATUS_LABEL[s] || s}</span>`;
  }

  /* ── List ── */
  async function loadList() {
    const kind = document.getElementById('filterKind').value;
    const res = await apiGet(API, kind ? { kind } : {});
    const items = (res.data && res.data.requests) || [];
    const box = document.getElementById('reqList');
    if (!items.length) {
      box.innerHTML = '<div class="dbg-empty">No requests yet.</div>';
      return;
    }
    box.innerHTML = items.map(r => `
      <div class="dbg-item ${r.id === currentId ? 'active' : ''}" data-id="${r.id}">
        <h6>${escHtml(r.title)}</h6>
        <div class="meta">
          ${kindBadge(r.kind)} ${statusPill(r.status)}
          ${r.pr_url ? '<i class="fab fa-github text-muted" title="PR opened"></i>' : ''}
        </div>
        <small class="text-muted">${formatDateTime(r.updated_at)}</small>
      </div>`).join('');
    box.querySelectorAll('.dbg-item').forEach(el =>
      el.addEventListener('click', () => openThread(parseInt(el.dataset.id))));
  }

  /* ── Thread ── */
  async function openThread(id, silent) {
    currentId = id;
    if (!silent) {
      document.getElementById('threadEmpty').style.display = 'none';
      document.getElementById('threadContent').style.display = 'flex';
    }
    const res = await apiGet(API, { id });
    if (!res.success) { showAlertModal(res.message, 'danger'); return; }
    renderThread(res.data);
    if (!silent) loadList();
    schedulePoll(res.data.is_live);
  }

  function renderThread(d) {
    document.getElementById('tKind').outerHTML =
      `<span class="kind-badge kind-${d.kind}" id="tKind">${KIND_LABEL[d.kind]}</span>`;
    document.getElementById('tStatus').outerHTML =
      `<span class="st-pill st-${d.status} ms-1" id="tStatus">${STATUS_LABEL[d.status] || d.status}</span>`;
    document.getElementById('tTitle').textContent = d.title;

    // RCA / plan panel
    const rcaBox = document.getElementById('rcaBox');
    if (d.rca) {
      rcaBox.style.display = 'block';
      document.getElementById('rcaSummary').textContent =
        d.kind === 'feature' ? 'Implementation plan' : 'Root-cause analysis';
      document.getElementById('rcaText').textContent = d.rca;
      const diffBox = document.getElementById('diffBox');
      if (d.proposed_diff) {
        diffBox.style.display = 'block';
        document.getElementById('diffText').textContent = d.proposed_diff;
      } else { diffBox.style.display = 'none'; }
    } else { rcaBox.style.display = 'none'; }

    // Messages
    const list = document.getElementById('msgList');
    list.innerHTML = (d.messages || []).map(m => {
      if (m.role === 'system') return `<div class="dbg-msg system">${escHtml(m.content)}</div>`;
      const who = m.role === 'admin' ? 'You' : 'Agent';
      let tools = '';
      if (m.meta && m.meta.tools_used && m.meta.tools_used.length) {
        const uniq = [...new Set(m.meta.tools_used)].map(shortTool).join(', ');
        tools = `<div class="tools"><i class="fas fa-wrench me-1"></i>${escHtml(uniq)}</div>`;
      }
      return `<div class="dbg-msg ${m.role}"><div class="who">${who}</div>${escHtml(m.content)}${tools}</div>`;
    }).join('');
    if (d.is_live) {
      list.insertAdjacentHTML('beforeend',
        '<div class="dbg-msg agent"><div class="who">Agent</div><span class="typing"><span>●</span><span>●</span><span>●</span></span> investigating…</div>');
    }
    list.scrollTop = list.scrollHeight;

    // Actions
    document.getElementById('closeBtn').style.display = d.status === 'closed' ? 'none' : '';
    document.getElementById('createPrBtn').style.display =
      (d.proposed_diff && d.status !== 'closed') ? '' : 'none';
    document.getElementById('replyInput').disabled = d.status === 'closed';
    document.getElementById('sendBtn').disabled = d.status === 'closed' || d.is_live;
  }

  function shortTool(t) {
    return t.replace(/^mcp__varthaai_debugger__/, '').replace(/^mcp__[^_]+__/, '');
  }

  /* ── Polling while the agent works ── */
  function schedulePoll(isLive) {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    if (isLive && currentId) {
      pollTimer = setTimeout(() => openThread(currentId, true), 3000);
    }
  }

  /* ── Actions ── */
  document.getElementById('newReqForm').addEventListener('submit', async e => {
    e.preventDefault();
    const f = e.target;
    showLoader('Submitting…');
    const res = await apiPost(API, {
      action: 'create',
      kind: f.kind.value,
      title: f.title.value.trim(),
      body: f.body.value.trim(),
    });
    hideLoader();
    if (res.success) {
      bootstrap.Modal.getInstance(document.getElementById('newReqModal')).hide();
      f.reset();
      await loadList();
      openThread(res.data.id);
    } else {
      showAlertModal(res.message, 'danger');
    }
  });

  document.getElementById('replyForm').addEventListener('submit', async e => {
    e.preventDefault();
    const input = document.getElementById('replyInput');
    const content = input.value.trim();
    if (!content || !currentId) return;
    input.value = '';
    const res = await apiPost(API, { action: 'reply', id: currentId, content });
    if (res.success) { openThread(currentId, true); }
    else { showAlertModal(res.message, 'danger'); input.value = content; }
  });

  document.getElementById('closeBtn').addEventListener('click', () => {
    if (!currentId) return;
    confirmThen('Close this thread? The agent will stop working on it.', async () => {
      showLoader();
      const res = await apiPost(API, { action: 'close', id: currentId });
      hideLoader();
      if (res.success) { openThread(currentId, true); loadList(); }
      else showAlertModal(res.message, 'danger');
    });
  });

  document.getElementById('createPrBtn').addEventListener('click', () => {
    showAlertModal('PR creation from the proposed diff arrives in Phase 2. The diff is shown in the analysis panel above.', 'info');
  });

  document.getElementById('refreshBtn').addEventListener('click', loadList);
  document.getElementById('filterKind').addEventListener('change', loadList);

  loadList();
})();
