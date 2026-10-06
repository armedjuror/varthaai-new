/* ─────────────────────────────────────────────────
   Varthaai Admin — Poster Review
   (content-generator-plan.md §13 Phase 3 / §12's "Poster review" page;
   poster-generation-plan.md §7's versioning/edit-loop UI)
   Requires: jQuery 3.6+, utils.js, Bootstrap 5
───────────────────────────────────────────────── */

var CONTENT_TYPE_LABELS = {
  poster_learn: 'Learn With Varthaai', poster_occasion: 'Occasion', poster_event: 'Event',
};
var POSTER_STATUS_BADGE = {
  draft: 'badge-info', needs_review: 'badge-warning', approved: 'badge-success', changes_requested: 'badge-danger',
};
var POSTER_STATUS_LABEL = {
  draft: 'Draft', needs_review: 'Needs review', approved: 'Approved', changes_requested: 'Changes requested',
};

var postersData = [];
var currentPosterDetail = null;

// apiGet/apiPost (utils.js) return a jQuery jqXHR — on a non-2xx response
// (this app's err() helper returns 400/404 with a real {success:false,...}
// body) the promise REJECTS rather than resolving, so a bare `await` would
// throw the jqXHR itself instead of the parsed envelope. Normalise both
// paths through here so callers only ever see {success, message, data}.
async function apiCall(promiseFactory) {
  try {
    return await promiseFactory();
  } catch (jqXHR) {
    return (jqXHR && jqXHR.responseJSON) || { success: false, message: 'Request failed.' };
  }
}

function postPosterAction(payload) {
  return apiCall(function () { return apiPost('/admin/api/content/posters/', payload); });
}

async function loadPosters() {
  var status = document.getElementById('posterFilterStatus').value;
  var res = await apiCall(function () {
    return apiGet('/admin/api/content/posters/', status ? { status: status } : {});
  });
  if (!res.success) { showAlertModal(res.message, 'danger'); return; }
  postersData = res.data.items || [];
  renderPostersTable(postersData);
  maybeOpenLinkedPoster();
}

// Deep-link from Pending Tasks (?plan_item_id=<id>) — open that item's
// latest poster automatically (list is ordered -created_at, so the first
// match for this plan_item is its latest version). Only acts once per page
// load (guarded by linkedPosterOpened) so it doesn't reopen after a filter
// change re-triggers loadPosters().
var linkedPosterOpened = false;
function maybeOpenLinkedPoster() {
  if (linkedPosterOpened) return;
  var planItemId = new URLSearchParams(window.location.search).get('plan_item_id');
  if (!planItemId) return;
  var match = postersData.find(function (p) { return String(p.plan_item_id) === planItemId; });
  if (match) { linkedPosterOpened = true; openPosterDetail(match.id); }
}

function renderPostersTable(items) {
  document.getElementById('postersCount').textContent = items.length;
  document.getElementById('postersEmptyState').style.display = items.length ? 'none' : '';
  var tbody = document.getElementById('postersTableBody');
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="text-center" style="padding:40px;color:var(--gray-400)">No posters found.</td></tr>';
    return;
  }
  tbody.innerHTML = items.map(function (p) {
    var thumb = p.image_url
      ? '<img src="' + p.image_url + '" style="width:48px;height:48px;object-fit:cover;border-radius:8px;border:1px solid var(--gray-200)">'
      : '<div style="width:48px;height:48px;border-radius:8px;background:var(--gray-100);display:flex;align-items:center;justify-content:center;color:var(--gray-400)"><i class="fas fa-triangle-exclamation"></i></div>';
    var badge = '<span class="badge ' + (POSTER_STATUS_BADGE[p.status] || 'badge-info') + '">' + (POSTER_STATUS_LABEL[p.status] || p.status) + '</span>';
    var failedNote = p.failed ? '<div style="font-size:0.7rem;color:#dc2626;margin-top:2px">Generation failed</div>' : '';
    return '<tr>' +
      '<td>' + thumb + '</td>' +
      '<td><div style="font-weight:600;font-size:0.85rem">' + escHtml(p.working_title) + '</div>' + failedNote + '</td>' +
      '<td>' + escHtml(CONTENT_TYPE_LABELS[p.content_type] || p.content_type) + '</td>' +
      '<td>' + escHtml(p.format) + '</td>' +
      '<td>v' + p.version + '</td>' +
      '<td>' + badge + '</td>' +
      '<td style="color:var(--gray-400);font-size:0.8rem;white-space:nowrap">' + formatDate(p.created_at) + '</td>' +
      '<td><div class="table-actions">' +
        '<button class="btn-icon view" title="View" onclick="openPosterDetail(' + p.id + ')"><i class="fas fa-eye"></i></button>' +
      '</div></td>' +
    '</tr>';
  }).join('');
}

async function openPosterDetail(id) {
  showLoader('Loading…');
  var res = await apiCall(function () { return apiGet('/admin/api/content/posters/', { id: id }); });
  hideLoader();
  if (!res.success) { showAlertModal(res.message, 'danger'); return; }
  currentPosterDetail = res.data;
  renderPosterDetail(currentPosterDetail);
  bootstrap.Modal.getOrCreateInstance(document.getElementById('posterDetailModal')).show();
}

function renderPosterDetail(d) {
  document.getElementById('posterDetailTitle').textContent = d.working_title + ' (v' + d.version + ')';

  var imageBlock = d.image_url
    ? '<img src="' + d.image_url + '" style="max-width:100%;border-radius:10px;border:1px solid var(--gray-200)">'
    : '<div class="alert alert-warning" style="font-size:0.85rem">This attempt failed to generate an image — see debug details, then retry.</div>';

  var metaJson = escHtml(JSON.stringify(d.generation_metadata || {}, null, 2));
  var reviewNotes = d.review_notes
    ? '<div class="alert alert-secondary" style="font-size:0.82rem"><strong>Review notes:</strong> ' + escHtml(d.review_notes) + '</div>'
    : '';

  var body = '<div class="row g-3">' +
    '<div class="col-md-6">' + imageBlock + '</div>' +
    '<div class="col-md-6">' +
      '<div style="font-size:0.78rem;color:var(--gray-400);margin-bottom:8px">' +
        escHtml(CONTENT_TYPE_LABELS[d.content_type] || d.content_type) + ' &middot; ' + escHtml(d.format) +
        (d.inspiration_label ? ' &middot; ref: ' + escHtml(d.inspiration_label) : ' &middot; no reference used') +
      '</div>' +
      reviewNotes +
      '<label class="form-label">Brief</label>' +
      '<pre style="white-space:pre-wrap;background:var(--gray-50);padding:12px;border-radius:8px;font-size:0.78rem;max-height:260px;overflow:auto">' + escHtml(d.brief_text || '(no brief — generation failed before a brief was produced)') + '</pre>' +
      '<details><summary style="cursor:pointer;font-size:0.8rem;color:var(--gray-500)">Debug details</summary>' +
        '<pre style="white-space:pre-wrap;background:var(--gray-50);padding:12px;border-radius:8px;font-size:0.72rem;margin-top:8px">' + metaJson + '</pre></details>' +
    '</div>' +
  '</div>';
  document.getElementById('posterDetailBody').innerHTML = body;

  var actions = [];
  if (d.failed) {
    actions.push('<button class="btn btn-sm btn-outline-primary" onclick="retryPoster()"><i class="fas fa-rotate-right me-1"></i>Retry</button>');
  } else {
    actions.push('<button class="btn btn-sm btn-outline-secondary" onclick="openRegenerateModal()"><i class="fas fa-arrows-rotate me-1"></i>Regenerate</button>');
    if (d.status !== 'approved') {
      actions.push('<button class="btn btn-sm btn-outline-danger" onclick="openRequestChangesModal()"><i class="fas fa-comment-dots me-1"></i>Request Changes</button>');
      actions.push('<button class="btn btn-sm btn-success" onclick="approvePoster()"><i class="fas fa-check me-1"></i>Approve</button>');
    }
  }
  document.getElementById('posterDetailActions').innerHTML = actions.join('');
}

async function approvePoster() {
  if (!currentPosterDetail) return;
  showLoader();
  var res = await postPosterAction({ action: 'approve', id: currentPosterDetail.id });
  hideLoader();
  showAlertModal(res.message, res.success ? 'success' : 'danger');
  if (res.success) {
    bootstrap.Modal.getInstance(document.getElementById('posterDetailModal')).hide();
    loadPosters();
  }
}

function openRequestChangesModal() {
  if (!currentPosterDetail) return;
  var form = document.getElementById('requestChangesForm');
  form.id.value = currentPosterDetail.id;
  form.notes.value = '';
  bootstrap.Modal.getOrCreateInstance(document.getElementById('requestChangesModal')).show();
}

function openRegenerateModal() {
  if (!currentPosterDetail) return;
  var form = document.getElementById('regeneratePosterForm');
  form.id.value = currentPosterDetail.id;
  form.instruction.value = '';
  var select = form.inspiration_id;
  var options = ['<option value="">Auto-select</option>'];
  (currentPosterDetail.eligible_inspirations || []).forEach(function (insp) {
    var selected = insp.id === currentPosterDetail.inspiration_id ? ' selected' : '';
    options.push('<option value="' + insp.id + '"' + selected + '>' + escHtml(insp.source_label) + ' (' + escHtml(insp.format) + ')</option>');
  });
  select.innerHTML = options.join('');
  bootstrap.Modal.getOrCreateInstance(document.getElementById('regeneratePosterModal')).show();
}

async function retryPoster() {
  if (!currentPosterDetail) return;
  showLoader('Retrying — this can take 10-30 seconds…');
  var res = await postPosterAction({ action: 'retry', id: currentPosterDetail.id });
  hideLoader();
  showAlertModal(res.message, res.success ? 'success' : 'danger');
  if (res.success) {
    bootstrap.Modal.getInstance(document.getElementById('posterDetailModal')).hide();
    loadPosters();
  }
}

document.addEventListener('DOMContentLoaded', function () {
  loadPosters();

  document.getElementById('posterFilterStatus').addEventListener('change', loadPosters);

  document.getElementById('requestChangesForm').addEventListener('submit', async function (e) {
    e.preventDefault();
    var form = e.target;
    showLoader();
    var res = await postPosterAction({ action: 'request_changes', id: form.id.value, notes: form.notes.value });
    hideLoader();
    showAlertModal(res.message, res.success ? 'success' : 'danger');
    if (res.success) {
      bootstrap.Modal.getInstance(document.getElementById('requestChangesModal')).hide();
      bootstrap.Modal.getInstance(document.getElementById('posterDetailModal'))?.hide();
      loadPosters();
    }
  });

  document.getElementById('regeneratePosterForm').addEventListener('submit', async function (e) {
    e.preventDefault();
    var form = e.target;
    showLoader('Regenerating — this can take 10-30 seconds…');
    var res = await postPosterAction({
      action: 'regenerate',
      id: form.id.value,
      instruction: form.instruction.value,
      inspiration_id: form.inspiration_id.value,
    });
    hideLoader();
    showAlertModal(res.message, res.success ? 'success' : 'danger');
    if (res.success) {
      bootstrap.Modal.getInstance(document.getElementById('regeneratePosterModal')).hide();
      bootstrap.Modal.getInstance(document.getElementById('posterDetailModal'))?.hide();
      loadPosters();
    }
  });
});
