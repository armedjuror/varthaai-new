/* ─────────────────────────────────────────────────
   Varthaai Admin — Employee "My Day"
   Requires: jQuery, utils.js, alert-modal.js, csrf.js
───────────────────────────────────────────────── */

var API = {
  state: '/admin/api/sessions/state/',
  start: '/admin/api/sessions/start/',
  brk: '/admin/api/sessions/break/',
  resume: '/admin/api/sessions/resume/',
  end: '/admin/api/sessions/end/',
  editEnd: '/admin/api/sessions/edit-end-time/',
  companies: '/admin/api/sessions/companies/',
  flavors: '/admin/api/sessions/flavors/',
  visits: '/admin/api/sessions/visits/',
  weeklyOff: '/admin/api/sessions/weekly-off/',
  leave: '/admin/api/sessions/leave/',
};

var FLAVORS = [];
var _companySelectTimer = null;

$(function () {
  loadState();
  loadWeeklyOff();
  loadLeave();
  apiGet(API.flavors).done(function (res) { if (res.success) FLAVORS = res.data; });

  $('#visitCompanySearch').on('input', function () {
    var q = $(this).val();
    clearTimeout(_companySelectTimer);
    _companySelectTimer = setTimeout(function () { searchCompanies(q); }, 250);
  });
});

function fail(xhrOrMsg) {
  var msg = (typeof xhrOrMsg === 'string') ? xhrOrMsg
    : (xhrOrMsg && xhrOrMsg.responseJSON && xhrOrMsg.responseJSON.message) || 'Something went wrong.';
  showAlertModal(msg, 'danger');
}

/* ── State ── */

function loadState() {
  showLoader('Loading…');
  apiGet(API.state).done(function (res) {
    hideLoader();
    if (!res.success) return fail(res.message);
    render(res.data);
  }).fail(function (xhr) { hideLoader(); fail(xhr); });
}

function render(data) {
  var today = data.today;
  var banner = $('#dayStatusBanner');
  if (today.day_status === 'leave') {
    banner.removeClass('d-none').text('On Leave today (' + today.leave_balance.used + ' of ' + today.leave_balance.allowance + ' used this year).');
  } else if (today.day_status === 'weekly_off') {
    banner.removeClass('d-none').text('Weekly off today.');
  } else {
    banner.addClass('d-none');
  }

  var open = data.open_session;
  var $card = $('#sessionCard');
  if (!open) {
    $('#startCard').removeClass('d-none');
    $card.html('<p class="text-muted mb-0">No session open. Start one below.</p>');
    $('#visitCard').addClass('d-none');
  } else {
    $('#startCard').addClass('d-none');
    var typeLabel = open.type === 'sales' ? 'Sales Day' : 'Packing';
    var statusLabel = open.status === 'on_break' ? 'On Break' : 'Active';
    var html = '<p class="mb-2"><strong>' + typeLabel + '</strong> — ' + statusLabel +
      ' <span class="text-muted">(started ' + formatDateTime(open.started_at) + ')</span></p>';
    html += '<div class="d-flex flex-column gap-2">';
    if (open.status === 'active') {
      html += '<button class="btn btn-warning big-btn" data-bs-toggle="modal" data-bs-target="#breakModal"><i class="fas fa-mug-hot me-2"></i>Break</button>';
      html += '<button class="btn btn-danger big-btn" onclick="endSession(\'' + open.type + '\')"><i class="fas fa-flag-checkered me-2"></i>End ' + typeLabel + '</button>';
    } else if (open.status === 'on_break') {
      html += '<button class="btn btn-success big-btn" onclick="resumeSession()"><i class="fas fa-play me-2"></i>Resume</button>';
    }
    html += '</div>';
    $card.html(html);

    if (open.type === 'sales') {
      $('#visitCard').removeClass('d-none');
      renderVisits(today.visits);
    } else {
      $('#visitCard').addClass('d-none');
    }
  }

  renderTodayStats(today);
  renderEditEndLinks(today);
}

function renderEditEndLinks(today) {
  var html = '';
  if (today.sales_session && today.sales_session.ended_at) {
    html += '<button class="btn btn-sm btn-outline-secondary me-2 mt-2" onclick="openEditEndTime(' +
      today.sales_session.id + ', \'' + today.sales_session.ended_at + '\')">' +
      '<i class="fas fa-pen me-1"></i>Edit sales end time</button>';
  }
  if (today.packing_session && today.packing_session.ended_at) {
    html += '<button class="btn btn-sm btn-outline-secondary mt-2" onclick="openEditEndTime(' +
      today.packing_session.id + ', \'' + today.packing_session.ended_at + '\')">' +
      '<i class="fas fa-pen me-1"></i>Edit packing end time</button>';
  }
  $('#sessionCard').append(html);
}

function renderTodayStats(today) {
  var stats = [
    ['Packs', today.packs], ['Orders', today.orders],
    ['Collection', formatCurrency(today.collection)], ['Visits', today.visits_count],
    ['New Leads', today.new_leads], ['Retargeted', today.retargeted_leads],
  ];
  var html = stats.map(function (s) {
    return '<div class="col-4 col-sm-4"><div class="stat-card"><div class="stat-body"><h2>' + s[1] + '</h2><p>' + s[0] + '</p></div></div></div>';
  }).join('');
  $('#todayStats').html(html);
}

function renderVisits(visits) {
  $('#visitCount').text(visits.length);
  if (!visits.length) {
    $('#visitList').html('<li class="list-group-item text-muted">No visits logged yet.</li>');
    return;
  }
  $('#visitList').html(visits.map(function (v) {
    return '<li class="list-group-item">' +
      '<div class="d-flex justify-content-between"><strong>' + escHtml(v.company_name) + '</strong>' +
      '<span class="text-muted small">' + formatDateTime(v.visited_at).split(' ').slice(-1)[0] + '</span></div>' +
      '<div class="small text-muted">' + v.purpose + ' · ' + v.outcome + '</div>' +
      (v.notes ? '<div class="small">' + escHtml(v.notes) + '</div>' : '') +
      '</li>';
  }).join(''));
}

/* ── Session actions ── */

function startSession(type) {
  showLoader('Starting…');
  apiPost(API.start, { type: type }).done(function (res) {
    hideLoader();
    if (!res.success) return fail(res.message);
    loadState();
  }).fail(function (xhr) { hideLoader(); fail(xhr); });
}

function breakSession(reason) {
  apiPost(API.brk, { reason: reason }).done(function (res) {
    $('#breakModal').modal('hide');
    if (!res.success) return fail(res.message);
    loadState();
  }).fail(fail);
}

function resumeSession() {
  apiPost(API.resume, {}).done(function (res) {
    if (!res.success) return fail(res.message);
    loadState();
  }).fail(fail);
}

function endSession(type) {
  if (type === 'packing') {
    $('#packingRows').empty();
    addPackingRow();
    var modal = new bootstrap.Modal(document.getElementById('packingEndModal'));
    modal.show();
    return;
  }
  confirmThen('End your sales session for today?', function () {
    apiPost(API.end, {}).done(function (res) {
      if (!res.success) return fail(res.message);
      loadState();
    }).fail(fail);
  });
}

/* ── Packing end ── */

function addPackingRow() {
  var options = FLAVORS.map(function (f) { return '<option value="' + f.id + '">' + escHtml(f.name) + '</option>'; }).join('');
  var row = $('<div class="row g-2 mb-2 packing-row">' +
    '<div class="col-7"><select class="form-select packing-flavor">' + options + '</select></div>' +
    '<div class="col-3"><input type="number" min="1" class="form-control packing-packs" placeholder="Packs"></div>' +
    '<div class="col-2"><button type="button" class="btn btn-outline-danger w-100" onclick="$(this).closest(\'.packing-row\').remove()"><i class="fas fa-trash"></i></button></div>' +
    '</div>');
  $('#packingRows').append(row);
}

function submitPackingEnd() {
  var items = [];
  $('.packing-row').each(function () {
    var flavorId = $(this).find('.packing-flavor').val();
    var packs = parseInt($(this).find('.packing-packs').val(), 10);
    if (flavorId && packs > 0) items.push({ flavor_id: flavorId, packs: packs });
  });
  if (!items.length) return fail('Add at least one flavour with packs.');
  apiPost(API.end, { items: items }).done(function (res) {
    if (!res.success) return fail(res.message);
    $('#packingEndModal').modal('hide');
    loadState();
  }).fail(fail);
}

/* ── Visits ── */

function searchCompanies(q) {
  apiGet(API.companies, { q: q }).done(function (res) {
    if (!res.success) return;
    $('#visitCompanyResults').html(res.data.map(function (c) {
      return '<button type="button" class="list-group-item list-group-item-action" onclick="selectCompany(' + c.id + ', \'' + escHtml(c.company_name).replace(/'/g, "\\'") + '\')">' +
        escHtml(c.company_name) + ' <span class="text-muted small">(' + c.stage + (c.city ? ', ' + escHtml(c.city) : '') + ')</span></button>';
    }).join(''));
  });
}

function selectCompany(id, name) {
  $('#visitCompanyId').val(id);
  $('#visitCompanySelected').text('Selected: ' + name);
  $('#visitCompanyResults').empty();
  $('#visitCompanySearch').val('');
  $('#newCompanyFields').addClass('d-none');
}

function toggleNewCompany() {
  $('#newCompanyFields').toggleClass('d-none');
  $('#visitCompanyId').val('');
  $('#visitCompanySelected').text('');
}

function submitVisit() {
  var payload = {
    purpose: $('#visitPurpose').val(),
    outcome: $('#visitOutcome').val(),
    notes: $('#visitNotes').val(),
  };
  var companyId = $('#visitCompanyId').val();
  if (companyId) {
    payload.company_id = companyId;
  } else {
    var name = $('#newCompanyName').val();
    if (!name) return fail('Pick a company, or enter a new lead\'s company name.');
    payload.new_company = { company_name: name, city: $('#newCompanyCity').val() };
  }
  apiPost(API.visits, payload).done(function (res) {
    if (!res.success) return fail(res.message);
    $('#visitModal').modal('hide');
    $('#visitCompanyId,#newCompanyName,#newCompanyCity,#visitNotes').val('');
    $('#visitCompanySelected').text('');
    loadState();
  }).fail(fail);
}

/* ── Weekly off ── */

function loadWeeklyOff() {
  var days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  apiGet(API.weeklyOff).done(function (res) {
    if (!res.success) return;
    $('#weeklyOffLabel').text(days[res.data.effective_weekday]);
    $('#weeklyOffSelect').val(res.data.effective_weekday);
  });
}

function submitWeeklyOff() {
  apiPost(API.weeklyOff, { weekday: $('#weeklyOffSelect').val() }).done(function (res) {
    if (!res.success) return fail(res.message);
    $('#weeklyOffModal').modal('hide');
    showAlertModal(res.message, 'success');
    loadWeeklyOff();
  }).fail(fail);
}

/* ── Leave ── */

function loadLeave() {
  apiGet(API.leave).done(function (res) {
    if (!res.success) return;
    var b = res.data.balance;
    $('#leaveBalanceLabel').text(b.remaining + ' of ' + b.allowance + ' remaining');
    $('#leaveBalanceDetail').text(b.used + ' used, ' + b.remaining + ' remaining of ' + b.allowance + ' for ' + b.year + '.');
    $('#leaveList').html((res.data.days || []).map(function (d) {
      return '<li class="list-group-item d-flex justify-content-between align-items-center">' +
        formatDate(d.date) + (d.note ? ' — ' + escHtml(d.note) : '') +
        '<button class="btn btn-sm btn-outline-danger" onclick="removeLeave(\'' + d.date + '\')"><i class="fas fa-times"></i></button></li>';
    }).join('') || '<li class="list-group-item text-muted">No leave recorded this year.</li>');
  });
}

function addLeave() {
  var date = $('#leaveDateInput').val();
  if (!date) return fail('Pick a date.');
  apiPost(API.leave, { date: date }).done(function (res) {
    if (!res.success) return fail(res.message);
    loadLeave();
  }).fail(fail);
}

function removeLeave(date) {
  confirmThen('Remove leave on ' + date + '?', function () {
    $.ajax({
      url: API.leave + '?date=' + encodeURIComponent(date), type: 'DELETE', dataType: 'json',
      statusCode: { 401: function () { window.location.href = '/admin/'; } },
    }).done(function (res) {
      if (!res.success) return fail(res.message);
      loadLeave();
    }).fail(fail);
  });
}

/* ── Edit end time (own session, today) ── */

function openEditEndTime(sessionId, currentEndedAt) {
  $('#editSessionId').val(sessionId);
  $('#editEndedAt').val(_toLocalDatetimeInput(currentEndedAt || new Date()));
  $('#editEndNote').val('');
  new bootstrap.Modal(document.getElementById('editEndTimeModal')).show();
}

function submitEditEndTime() {
  var note = $('#editEndNote').val();
  if (!note) return fail('A note is required.');
  apiPost(API.editEnd, {
    session_id: $('#editSessionId').val(),
    ended_at: $('#editEndedAt').val(),
    note: note,
  }).done(function (res) {
    if (!res.success) return fail(res.message);
    $('#editEndTimeModal').modal('hide');
    loadState();
  }).fail(fail);
}
