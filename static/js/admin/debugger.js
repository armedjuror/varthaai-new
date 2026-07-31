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

  // Render Markdown safely. marked → DOMPurify; falls back to escaped text with
  // line breaks if the CDN libs didn't load.
  function renderMd(text) {
    const src = text || '';
    if (window.marked && window.DOMPurify) {
      const html = window.marked.parse(src, { breaks: true, gfm: true });
      return window.DOMPurify.sanitize(html, { ADD_ATTR: ['target'] });
    }
    return escHtml(src).replace(/\n/g, '<br>');
  }

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
      document.getElementById('rcaText').innerHTML = renderMd(d.rca);
      const diffBox = document.getElementById('diffBox');
      if (d.proposed_diff) {
        diffBox.style.display = 'block';
        document.getElementById('diffText').textContent = d.proposed_diff;
      } else { diffBox.style.display = 'none'; }
    } else { rcaBox.style.display = 'none'; }

    // Messages
    const list = document.getElementById('msgList');
    list.innerHTML = (d.messages || []).map(m => {
      if (m.role === 'system')
        return `<div class="dbg-msg system"><div class="md">${renderMd(m.content)}</div></div>`;
      const who = m.role === 'admin' ? 'You' : 'Agent';
      let tools = '';
      if (m.meta && m.meta.tools_used && m.meta.tools_used.length) {
        const uniq = [...new Set(m.meta.tools_used)].map(shortTool).join(', ');
        tools = `<div class="tools"><i class="fas fa-wrench me-1"></i>${escHtml(uniq)}</div>`;
      }
      // Agent replies are Markdown; admin messages stay verbatim (plain text).
      const bodyHtml = m.role === 'agent'
        ? `<div class="md">${renderMd(m.content)}</div>`
        : `<div class="plain">${escHtml(m.content)}</div>`;
      return `<div class="dbg-msg ${m.role}"><div class="who">${who}</div>${bodyHtml}${tools}</div>`;
    }).join('');
    if (d.is_live) {
      list.insertAdjacentHTML('beforeend',
        '<div class="dbg-msg agent"><div class="who">Agent</div><span class="typing"><span>●</span><span>●</span><span>●</span></span> investigating…</div>');
    }
    list.scrollTop = list.scrollHeight;

    // PR link banner
    const prLink = document.getElementById('prLink');
    if (d.pr_url) {
      prLink.href = d.pr_url;
      document.getElementById('prLinkText').textContent = d.pr_branch
        ? `PR open · ${d.pr_branch}` : 'View pull request';
      prLink.classList.remove('d-none'); prLink.classList.add('d-flex');
    } else {
      prLink.classList.add('d-none'); prLink.classList.remove('d-flex');
    }

    // Action buttons
    const closed = d.status === 'closed';
    document.getElementById('closeBtn').style.display = closed ? 'none' : '';
    // Feature: approve the plan so the agent generates a diff.
    document.getElementById('approvePlanBtn').style.display =
      (d.kind === 'feature' && d.rca && !d.proposed_diff && !d.pr_url
       && !closed && !d.is_live) ? '' : 'none';
    // A fix is proposed and no PR exists yet.
    document.getElementById('createPrBtn').style.display =
      (d.proposed_diff && !d.pr_url && d.status === 'ready' && !closed) ? '' : 'none';
    // A PR exists — allow a manual review-sync.
    document.getElementById('syncPrBtn').style.display =
      (d.pr_url && !closed) ? '' : 'none';

    document.getElementById('replyInput').disabled = closed;
    document.getElementById('sendBtn').disabled = closed || d.is_live;
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

  async function postAction(payload, confirmMsg) {
    if (!currentId) return;
    const go = async () => {
      showLoader();
      const res = await apiPost(API, Object.assign({ id: currentId }, payload));
      hideLoader();
      if (res.success) { openThread(currentId, true); loadList(); }
      else showAlertModal(res.message, 'danger');
    };
    if (confirmMsg) confirmThen(confirmMsg, go); else go();
  }

  document.getElementById('createPrBtn').addEventListener('click', () =>
    postAction({ action: 'request_pr' },
      'Open a pull request against main with the proposed fix? A new branch will be pushed (never main).'));

  document.getElementById('approvePlanBtn').addEventListener('click', () =>
    postAction({ action: 'approve_plan' },
      'Approve this plan? The agent will generate the implementation as a diff.'));

  document.getElementById('syncPrBtn').addEventListener('click', () =>
    postAction({ action: 'sync_pr' }));

  document.getElementById('refreshBtn').addEventListener('click', loadList);
  document.getElementById('filterKind').addEventListener('change', loadList);

  loadList();
})();
