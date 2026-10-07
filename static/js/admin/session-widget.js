/* ─────────────────────────────────────────────────
   Varthaai Admin — topbar session widget
   Start/Break/Resume/End for field employees, available on every admin
   page (see admin/base.html). Requires: jQuery, utils.js, alert-modal.js,
   csrf.js — loaded globally by base.html before this file.
───────────────────────────────────────────────── */

var SESSION_API = {
  state: '/admin/api/sessions/state/',
  start: '/admin/api/sessions/start/',
  brk: '/admin/api/sessions/break/',
  resume: '/admin/api/sessions/resume/',
  end: '/admin/api/sessions/end/',
  flavors: '/admin/api/sessions/flavors/',
};

var _sessionFlavors = [];
var _sessionPollTimer = null;

$(function () {
  loadSessionState();
  apiGet(SESSION_API.flavors).done(function (res) { if (res.success) _sessionFlavors = res.data; });

  $('#sessionStartType').on('change', renderSessionStartAreaVisibility);
  $('#sessionStartSubmitBtn').on('click', submitSessionStart);
  $('#sessionBreakLunchBtn').on('click', function () { submitSessionBreak('lunch_break'); });
  $('#sessionBreakEveningBtn').on('click', function () { submitSessionBreak('evening_break'); });
  $('#sessionAddPackingRowBtn').on('click', addSessionPackingRow);
  $('#sessionPackingEndSubmitBtn').on('click', submitSessionPackingEnd);

  // Catch auto-close (19:30 IST) and other-tab changes without a manual refresh.
  _sessionPollTimer = setInterval(loadSessionState, 5 * 60 * 1000);
});

function _sessionFail(xhrOrMsg) {
  var msg = (typeof xhrOrMsg === 'string') ? xhrOrMsg
    : (xhrOrMsg && xhrOrMsg.responseJSON && xhrOrMsg.responseJSON.message) || 'Something went wrong.';
  showAlertModal(msg, 'danger');
}

function loadSessionState() {
  apiGet(SESSION_API.state).done(function (res) {
    if (!res.success) return _sessionFail(res.message);
    renderSessionWidget(res.data);
  }).fail(_sessionFail);
}

function renderSessionWidget(data) {
  var open = data.open_session;
  var $w = $('#sessionWidget');
  if (!open) {
    $w.html(
      '<span class="session-status-label"><span class="session-status-dot"></span>No session</span>' +
      '<button class="btn btn-sm btn-primary" id="sessionStartBtn"><i class="fas fa-play me-1"></i>Start Session</button>',
    );
    $('#sessionStartBtn').on('click', openSessionStartModal);
    return;
  }

  var typeLabel = open.type === 'sales' ? 'Sales' : 'Packing';
  var isBreak = open.status === 'on_break';
  var dotClass = isBreak ? 'is-break' : 'is-active';
  var statusLabel = isBreak ? 'On Break' : 'Active';
  var html = '<span class="session-status-label"><span class="session-status-dot ' + dotClass + '"></span>' +
    typeLabel + ' — ' + statusLabel + '</span>';
  if (!isBreak) {
    html += '<button class="btn btn-sm btn-outline-warning" id="sessionBreakBtn"><i class="fas fa-mug-hot me-1"></i>Break</button>';
  } else {
    html += '<button class="btn btn-sm btn-success" id="sessionResumeBtn"><i class="fas fa-play me-1"></i>Resume</button>';
  }
  html += '<button class="btn btn-sm btn-outline-danger" id="sessionEndBtn"><i class="fas fa-flag-checkered me-1"></i>End</button>';
  $w.html(html);

  $('#sessionBreakBtn').on('click', function () { new bootstrap.Modal(document.getElementById('sessionBreakModal')).show(); });
  $('#sessionResumeBtn').on('click', submitSessionResume);
  $('#sessionEndBtn').on('click', function () { startSessionEndFlow(open.type); });
}

/* ── Start ── */

function openSessionStartModal() {
  $('#sessionStartType').val('sales');
  $('#sessionStartArea').val('');
  renderSessionStartAreaVisibility();
  new bootstrap.Modal(document.getElementById('sessionStartModal')).show();
}

function renderSessionStartAreaVisibility() {
  $('#sessionStartAreaWrap').toggleClass('d-none', $('#sessionStartType').val() !== 'sales');
}

function submitSessionStart() {
  var type = $('#sessionStartType').val();
  var area = $('#sessionStartArea').val().trim();
  if (type === 'sales' && !area) return _sessionFail('Area is required to start a sales session.');
  showLoader('Starting…');
  apiPost(SESSION_API.start, { type: type, area: area }).done(function (res) {
    if (!res.success) { hideLoader(); return _sessionFail(res.message); }
    $('#sessionStartModal').modal('hide');
    loadSessionState();
  }).fail(function (xhr) { hideLoader(); _sessionFail(xhr); }).always(function () { hideLoader(); });
}

/* ── Break / Resume ── */

function submitSessionBreak(reason) {
  showLoader('Starting break…');
  apiPost(SESSION_API.brk, { reason: reason }).done(function (res) {
    $('#sessionBreakModal').modal('hide');
    if (!res.success) { hideLoader(); return _sessionFail(res.message); }
    loadSessionState();
  }).fail(function (xhr) { hideLoader(); _sessionFail(xhr); }).always(function () { hideLoader(); });
}

function submitSessionResume() {
  showLoader('Resuming…');
  apiPost(SESSION_API.resume, {}).done(function (res) {
    if (!res.success) { hideLoader(); return _sessionFail(res.message); }
    loadSessionState();
  }).fail(function (xhr) { hideLoader(); _sessionFail(xhr); }).always(function () { hideLoader(); });
}

/* ── End (+ packing pack-count modal) ── */

function startSessionEndFlow(type) {
  if (type === 'packing') {
    $('#sessionPackingRows').empty();
    addSessionPackingRow();
    new bootstrap.Modal(document.getElementById('sessionPackingEndModal')).show();
    return;
  }
  confirmThen('End your sales session for today?', function () {
    showLoader('Ending session…');
    apiPost(SESSION_API.end, {}).done(function (res) {
      if (!res.success) { hideLoader(); return _sessionFail(res.message); }
      loadSessionState();
    }).fail(function (xhr) { hideLoader(); _sessionFail(xhr); }).always(function () { hideLoader(); });
  });
}

function addSessionPackingRow() {
  var options = _sessionFlavors.map(function (f) { return '<option value="' + f.id + '">' + escHtml(f.name) + '</option>'; }).join('');
  var row = $('<div class="row g-2 mb-2 session-packing-row">' +
    '<div class="col-7"><select class="form-select session-packing-flavor">' + options + '</select></div>' +
    '<div class="col-3"><input type="number" min="1" class="form-control session-packing-packs" placeholder="Packs"></div>' +
    '<div class="col-2"><button type="button" class="btn btn-outline-danger w-100 session-packing-row-remove"><i class="fas fa-trash"></i></button></div>' +
    '</div>');
  row.find('.session-packing-row-remove').on('click', function () { row.remove(); });
  $('#sessionPackingRows').append(row);
}

function submitSessionPackingEnd() {
  var items = [];
  $('.session-packing-row').each(function () {
    var flavorId = $(this).find('.session-packing-flavor').val();
    var packs = parseInt($(this).find('.session-packing-packs').val(), 10);
    if (flavorId && packs > 0) items.push({ flavor_id: flavorId, packs: packs });
  });
  if (!items.length) return _sessionFail('Add at least one flavour with packs.');
  showLoader('Ending packing session…');
  apiPost(SESSION_API.end, { items: items }).done(function (res) {
    if (!res.success) { hideLoader(); return _sessionFail(res.message); }
    $('#sessionPackingEndModal').modal('hide');
    loadSessionState();
  }).fail(function (xhr) { hideLoader(); _sessionFail(xhr); }).always(function () { hideLoader(); });
}
