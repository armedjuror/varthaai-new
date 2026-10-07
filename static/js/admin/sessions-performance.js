/* ─────────────────────────────────────────────────
   Varthaai Admin — Employee Performance dashboard
   Requires: jQuery, utils.js, alert-modal.js, csrf.js
───────────────────────────────────────────────── */

var PERF_API = {
  employees: '/admin/api/performance/employees/',
  daily: '/admin/api/performance/daily/',
  editEnd: '/admin/api/performance/edit-end-time/',
  period: '/admin/api/performance/period/',
  periodExport: '/admin/api/performance/period/export/',
  overall: '/admin/api/performance/overall/',
};

function fail(xhrOrMsg) {
  var msg = (typeof xhrOrMsg === 'string') ? xhrOrMsg
    : (xhrOrMsg && xhrOrMsg.responseJSON && xhrOrMsg.responseJSON.message) || 'Something went wrong.';
  showAlertModal(msg, 'danger');
}

function qsParams() { return new URLSearchParams(window.location.search); }
function setQueryParams(obj) {
  var params = qsParams();
  Object.keys(obj).forEach(function (k) {
    if (obj[k] === null || obj[k] === undefined || obj[k] === '') params.delete(k);
    else params.set(k, obj[k]);
  });
  history.replaceState(null, '', window.location.pathname + '?' + params.toString());
}

function todayISO() {
  var d = new Date();
  return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2);
}

function loadEmployeeOptions($select, includeAll, done) {
  apiGet(PERF_API.employees).done(function (res) {
    if (!res.success) return fail(res.message);
    var html = includeAll ? '<option value="all">All Employees</option>' : '';
    html += res.data.map(function (e) {
      return '<option value="' + e.id + '">' + escHtml(e.name || e.username) + (e.is_active ? '' : ' (inactive)') + '</option>';
    }).join('');
    $select.html(html);
    if (done) done(res.data);
  }).fail(fail);
}

/* ════════════════════ DAILY ════════════════════ */

function initDailyPage() {
  var params = qsParams();
  loadEmployeeOptions($('#employeeSelect'), false, function (employees) {
    var empId = params.get('employee') || (employees[0] && employees[0].id);
    if (empId) $('#employeeSelect').val(empId);
    $('#dateInput').val(params.get('date') || todayISO());
    fetchDaily();
  });

  $('#employeeSelect, #dateInput').on('change', fetchDaily);
  $('#loadBtn').on('click', fetchDaily);
  $('#prevDayBtn').on('click', function () { shiftDate(-1); });
  $('#nextDayBtn').on('click', function () { shiftDate(1); });
}

function shiftDate(delta) {
  var d = new Date($('#dateInput').val() + 'T00:00:00');
  d.setDate(d.getDate() + delta);
  $('#dateInput').val(d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2));
  fetchDaily();
}

function fetchDaily() {
  var empId = $('#employeeSelect').val();
  var date = $('#dateInput').val();
  if (!empId || !date) return;
  setQueryParams({ employee: empId, date: date });
  showLoader('Loading…');
  apiGet(PERF_API.daily, { user_id: empId, date: date }).done(function (res) {
    hideLoader();
    if (!res.success) return fail(res.message);
    renderDaily(res.data);
  }).fail(function (xhr) { hideLoader(); fail(xhr); });
}

function statCard(label, value) {
  return '<div class="col-6 col-md-3"><div class="stat-card"><div class="stat-body"><h2>' + value + '</h2><p>' + label + '</p></div></div></div>';
}

function renderDaily(r) {
  var banner = $('#dayStatusBanner');
  if (r.day_status === 'leave') {
    banner.removeClass('d-none').text('Leave (' + r.leave_balance.used + ' of ' + r.leave_balance.allowance + ' used).');
  } else if (r.day_status === 'weekly_off') {
    banner.removeClass('d-none').text('Weekly off.');
  } else {
    banner.addClass('d-none');
  }

  var reachedHome = r.reached_home_recorded ? formatDateTime(r.reached_home_time).split(' ').slice(-1)[0] : 'not recorded (auto-closed)';
  $('#statCards').html(
    statCard('Start', r.start_time ? formatDateTime(r.start_time).split(' ').slice(-1)[0] : '—') +
    statCard('First Delivery', r.first_delivery_time ? formatDateTime(r.first_delivery_time).split(' ').slice(-1)[0] : '—') +
    statCard('Last Meeting', r.last_meeting_time ? formatDateTime(r.last_meeting_time).split(' ').slice(-1)[0] : '—') +
    statCard('Reached Home', reachedHome) +
    statCard('Packs', r.packs) + statCard('Orders', r.orders) +
    statCard('Collection', formatCurrency(r.collection)) + statCard('Visits', r.visits_count) +
    statCard('New Leads', r.new_leads) + statCard('Retargeted', r.retargeted_leads),
  );

  renderTimeline(r);
  renderPacking(r.packing_session);
  renderVisitsTable(r.visits);
  renderFlags(r);
}

function renderTimeline(r) {
  var sales = r.sales_session;
  if (!sales) { $('#timelineBody').html('<p class="text-muted">No sales session.</p>'); return; }
  var rows = sales.events.map(function (e) {
    return '<tr><td>' + e.event + (e.reason ? ' (' + e.reason + ')' : '') + '</td><td>' + formatDateTime(e.at).split(' ').slice(-1)[0] + '</td></tr>';
  }).join('');
  var html = (sales.area ? '<p class="mb-1"><strong>Area:</strong> ' + escHtml(sales.area) + '</p>' : '') +
    '<table class="table table-sm mb-2"><tbody>' + rows + '</tbody></table>' +
    '<p class="mb-1">Total ' + sales.total_minutes + 'm | Active ' + sales.active_minutes + 'm | Break ' + sales.break_minutes +
    'm (lunch ' + sales.break_minutes_lunch + 'm, evening ' + sales.break_minutes_evening + 'm)</p>';
  if (sales.ended_at) {
    html += '<button class="btn btn-sm btn-outline-secondary" onclick="openEditEndTime(' + sales.id + ', \'' + sales.ended_at + '\')">' +
      '<i class="fas fa-pen me-1"></i>Correct end time</button>';
  }
  if (sales.end_edited) html += '<p class="small text-muted mt-1">Edited: ' + escHtml(sales.end_edit_note) + '</p>';
  $('#timelineBody').html(html);
}

function renderPacking(p) {
  if (!p) { $('#packingBody').html('<p class="text-muted">No packing session.</p>'); return; }
  var rows = p.items.map(function (i) { return '<li class="list-group-item d-flex justify-content-between">' + escHtml(i.flavor_name) + '<span>' + i.packs + '</span></li>'; }).join('');
  var mpp = p.minutes_per_pack ? p.minutes_per_pack.toFixed(1) : '—';
  var html = '<ul class="list-group list-group-flush mb-2">' + (rows || '<li class="list-group-item text-muted">No items.</li>') + '</ul>' +
    '<p class="mb-1">Total packs: ' + p.total_packs + ' | Minutes/pack: ' + mpp + ' | Active: ' + p.active_minutes + 'm</p>';
  if (p.ended_at) {
    html += '<button class="btn btn-sm btn-outline-secondary" onclick="openEditEndTime(' + p.id + ', \'' + p.ended_at + '\')">' +
      '<i class="fas fa-pen me-1"></i>Correct end time</button>';
  }
  $('#packingBody').html(html);
}

function renderVisitsTable(visits) {
  if (!visits.length) { $('#visitsBody').html('<tr><td colspan="5" class="text-center text-muted">No visits.</td></tr>'); return; }
  $('#visitsBody').html(visits.map(function (v) {
    return '<tr><td>' + formatDateTime(v.visited_at).split(' ').slice(-1)[0] + '</td><td>' + escHtml(v.company_name) +
      '</td><td>' + v.purpose + '</td><td>' + v.outcome + '</td><td>' + escHtml(v.notes || '') + '</td></tr>';
  }).join(''));
}

function renderFlags(r) {
  var f = r.flags;
  var items = [];
  if (f.auto_closed_sales) items.push('Sales session auto-closed at 19:30.');
  if (f.auto_closed_packing) items.push('Packing session auto-closed at 19:30.');
  if (f.end_edited_sales) items.push('Sales end time edited.');
  if (f.end_edited_packing) items.push('Packing end time edited.');
  if (f.packing_no_items) items.push('Packing auto-closed with no packs logged.');
  if (f.no_session_on_working_day) items.push('No session on a working day.');
  if (!items.length) { $('#flagsCard').hide(); return; }
  $('#flagsCard').show();
  $('#flagsBody').html('<ul class="mb-0">' + items.map(function (i) { return '<li>' + i + '</li>'; }).join('') + '</ul>');
}

function openEditEndTime(sessionId, currentEndedAt) {
  $('#editSessionId').val(sessionId);
  $('#editEndedAt').val(_toLocalDatetimeInput(currentEndedAt));
  $('#editEndNote').val('');
  new bootstrap.Modal(document.getElementById('editEndTimeModal')).show();
}

function submitEditEndTime() {
  var note = $('#editEndNote').val();
  if (!note) return fail('A note is required.');
  apiPost(PERF_API.editEnd, {
    session_id: $('#editSessionId').val(), ended_at: $('#editEndedAt').val(), note: note,
  }).done(function (res) {
    if (!res.success) return fail(res.message);
    $('#editEndTimeModal').modal('hide');
    fetchDaily();
  }).fail(fail);
}

/* ════════════════════ PERIOD ════════════════════ */

function presetRange(preset) {
  var today = new Date();
  var d = new Date(today);
  if (preset === 'this_week') { d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return [d, today]; }
  if (preset === 'last_week') {
    var thisWeekStart = new Date(today); thisWeekStart.setDate(today.getDate() - ((today.getDay() + 6) % 7));
    var start = new Date(thisWeekStart); start.setDate(start.getDate() - 7);
    var end = new Date(thisWeekStart); end.setDate(end.getDate() - 1);
    return [start, end];
  }
  if (preset === 'this_month') { d.setDate(1); return [d, today]; }
  if (preset === 'last_month') {
    var firstThis = new Date(today.getFullYear(), today.getMonth(), 1);
    var lastMonthEnd = new Date(firstThis); lastMonthEnd.setDate(0);
    var lastMonthStart = new Date(lastMonthEnd.getFullYear(), lastMonthEnd.getMonth(), 1);
    return [lastMonthStart, lastMonthEnd];
  }
  return null;
}

function toISO(d) { return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2); }

function initPeriodPage() {
  var params = qsParams();
  loadEmployeeOptions($('#employeeSelect'), true, function () {
    $('#employeeSelect').val(params.get('employee') || 'all');
    var preset = params.get('range') || 'this_week';
    $('#rangePreset').val(preset);
    applyPreset(preset, params);
    fetchPeriod();
  });

  $('#rangePreset').on('change', function () { applyPreset($(this).val(), null); fetchPeriod(); });
  $('#employeeSelect, #startInput, #endInput').on('change', fetchPeriod);
  $('#loadBtn').on('click', fetchPeriod);
}

function applyPreset(preset, params) {
  if (preset === 'custom') {
    $('#startInput, #endInput').prop('disabled', false);
    if (params) { $('#startInput').val(params.get('start') || todayISO()); $('#endInput').val(params.get('end') || todayISO()); }
  } else {
    $('#startInput, #endInput').prop('disabled', true);
    var range = presetRange(preset);
    if (range) { $('#startInput').val(toISO(range[0])); $('#endInput').val(toISO(range[1])); }
  }
}

function fetchPeriod() {
  var empId = $('#employeeSelect').val();
  var preset = $('#rangePreset').val();
  var query = { user_id: empId };
  if (preset === 'custom') { query.start = $('#startInput').val(); query.end = $('#endInput').val(); }
  else { query.range = preset; }
  setQueryParams(Object.assign({ employee: empId, range: preset }, preset === 'custom' ? { start: query.start, end: query.end } : {}));

  showLoader('Loading…');
  apiGet(PERF_API.period, query).done(function (res) {
    hideLoader();
    if (!res.success) return fail(res.message);
    renderPeriod(res.data, empId === 'all');
    $('#exportBtn').attr('href', PERF_API.periodExport + '?' + new URLSearchParams(query).toString());
  }).fail(function (xhr) { hideLoader(); fail(xhr); });
}

function renderPeriod(r, isAll) {
  var t = r.totals, c = r.counts, a = r.averages;
  $('#totalsCards').html([
    statCard('Days Worked', c.days_worked), statCard('Off Days', c.off_days),
    statCard('Leave Days', c.leave_days), statCard('No-Session Days', c.no_session_days),
    statCard('Packs', t.packs), statCard('Orders', t.orders),
    statCard('Collection', formatCurrency(t.collection)), statCard('Visits', t.visits || t.visits_count || 0),
    statCard('New Leads', t.new_leads), statCard('Retargeted', t.retargeted_leads),
  ].join(''));

  if (!isAll) {
    $('#averagesCards').html([
      statCard('Packs/day', a.packs_per_day ? a.packs_per_day.toFixed(1) : '—'),
      statCard('Orders/day', a.orders_per_day ? a.orders_per_day.toFixed(1) : '—'),
      statCard('Collection/day', a.collection_per_day ? formatCurrency(a.collection_per_day) : '—'),
      statCard('Visits/day', a.visits_per_day ? a.visits_per_day.toFixed(1) : '—'),
      statCard('Active min/day', a.active_minutes_per_day ? Math.round(a.active_minutes_per_day) : '—'),
      statCard('Break min/day', a.break_minutes_per_day ? Math.round(a.break_minutes_per_day) : '—'),
      statCard('Min/pack', a.minutes_per_pack ? a.minutes_per_pack.toFixed(1) : '—'),
    ].join(''));
    $('#packingByFlavor').html(
      Object.keys(t.packing_by_flavor || {}).length
        ? '<ul class="list-group list-group-flush">' + Object.entries(t.packing_by_flavor).map(function (kv) {
          return '<li class="list-group-item d-flex justify-content-between">' + escHtml(kv[0]) + '<span>' + kv[1] + '</span></li>';
        }).join('') + '</ul>'
        : '<p class="text-muted mb-0">No packing recorded.</p>',
    );
  } else {
    $('#averagesCards').html(
      ['packs', 'orders', 'collection', 'visits', 'new_leads', 'retargeted_leads'].map(function (k) {
        var v = a[k + '_per_working_day'];
        return statCard(k.replace('_', ' ') + '/employee-day', v ? (k === 'collection' ? formatCurrency(v) : v.toFixed(1)) : '—');
      }).join(''),
    );
    $('#packingByFlavor').html('<p class="text-muted mb-0">Total packs across all employees: ' + t.packing_total_packs + '</p>');
  }

  renderDayTable(r.days, isAll);
}

function renderDayTable(days, isAll) {
  if (isAll) {
    $('#dayTableHead').html('<th>Date</th><th>Status Mix</th><th>With Session</th><th>Packs</th><th>Orders</th><th>Collection</th><th>Visits</th><th>New Leads</th><th>Retargeted</th>');
    $('#dayTableBody').html(days.map(function (d) {
      var mix = Object.entries(d.status_counts).map(function (kv) { return kv[0] + ':' + kv[1]; }).join(', ');
      return '<tr><td>' + formatDate(d.date) + '</td><td>' + mix + '</td><td>' + d.employees_with_session + '</td><td>' +
        d.packs + '</td><td>' + d.orders + '</td><td>' + formatCurrency(d.collection) + '</td><td>' + d.visits_count +
        '</td><td>' + d.new_leads + '</td><td>' + d.retargeted_leads + '</td></tr>';
    }).join(''));
  } else {
    $('#dayTableHead').html('<th>Date</th><th>Status</th><th>Start</th><th>Reached Home</th><th>Active</th><th>Break</th><th>Packs</th><th>Orders</th><th>Collection</th><th>Visits</th><th>New Leads</th><th>Retargeted</th>');
    $('#dayTableBody').html(days.map(function (d) {
      var badge = d.day_status !== 'working' ? ' <span class="badge bg-secondary">' + d.day_status + '</span>' : '';
      var sales = d.sales_session;
      return '<tr><td>' + formatDate(d.date) + badge + '</td><td>' + d.day_status + '</td><td>' +
        (d.start_time ? formatDateTime(d.start_time).split(' ').slice(-1)[0] : '—') + '</td><td>' +
        (d.reached_home_recorded ? formatDateTime(d.reached_home_time).split(' ').slice(-1)[0] : (sales ? 'not recorded' : '—')) + '</td><td>' +
        (sales ? sales.active_minutes : '—') + '</td><td>' + (sales ? sales.break_minutes : '—') + '</td><td>' +
        d.packs + '</td><td>' + d.orders + '</td><td>' + formatCurrency(d.collection) + '</td><td>' + d.visits_count +
        '</td><td>' + d.new_leads + '</td><td>' + d.retargeted_leads + '</td></tr>';
    }).join(''));
  }
}

/* ════════════════════ OVERALL ════════════════════ */

var _charts = {};

function initOverallPage() {
  var params = qsParams();
  var end = params.get('end') || todayISO();
  var startDefault = new Date(); startDefault.setDate(startDefault.getDate() - 29);
  var start = params.get('start') || toISO(startDefault);
  $('#startInput').val(start); $('#endInput').val(end);
  $('#loadBtn').on('click', fetchOverall);
  fetchOverall();
}

function fetchOverall() {
  var start = $('#startInput').val(), end = $('#endInput').val();
  setQueryParams({ start: start, end: end });
  showLoader('Loading…');
  apiGet(PERF_API.overall, { start: start, end: end }).done(function (res) {
    hideLoader();
    if (!res.success) return fail(res.message);
    renderOverall(res.data);
  }).fail(function (xhr) { hideLoader(); fail(xhr); });
}

function renderOverall(r) {
  var cv = r.conversion;
  $('#conversionCards').html([
    statCard('Visits → Orders', cv.visits_to_orders !== null ? (cv.visits_to_orders * 100).toFixed(1) + '%' : '—'),
    statCard('Leads → First Order', cv.leads_to_first_order !== null ? (cv.leads_to_first_order * 100).toFixed(1) + '%' : '—'),
    statCard('Total Visits', cv.visits_total), statCard('New Leads', cv.new_leads_total),
  ].join(''));

  $('#rankingBody').html(r.ranking.map(function (row) {
    return '<tr><td>' + escHtml(row.name) + (row.is_active ? '' : ' <span class="badge bg-secondary">inactive</span>') + '</td><td>' +
      formatCurrency(row.collection) + '</td><td>' + row.orders + '</td><td>' + row.packs + '</td><td>' + row.visits +
      '</td><td>' + row.new_leads + '</td><td>' + row.retargeted_leads + '</td><td>' + row.active_hours + '</td><td>' +
      (row.consistency !== null ? (row.consistency * 100).toFixed(0) + '% (' + row.days_worked + '/' + row.expected_working_days + ')' : '—') +
      '</td><td>' + row.off_days + ' / ' + row.leave_days + '</td></tr>';
  }).join('') || '<tr><td colspan="10" class="text-center text-muted">No employees.</td></tr>');

  renderTrendCharts(r.trend);
}

function renderTrendCharts(trend) {
  var labels = trend.map(function (t) { return formatDate(t.week_start); });
  var collection = trend.map(function (t) { return Number(t.collection) || 0; });
  var visits = trend.map(function (t) { return t.visits; });
  var conversion = trend.map(function (t) { return t.conversion !== null ? +(t.conversion * 100).toFixed(1) : null; });
  var mpp = trend.map(function (t) { return t.minutes_per_pack !== null ? +t.minutes_per_pack.toFixed(1) : null; });

  renderChart('collectionChart', labels, [{ label: 'Collection (₹)', data: collection, borderColor: '#85AA4E', tension: 0.3 }]);
  renderChart('visitsChart', labels, [
    { label: 'Visits', data: visits, borderColor: '#2563eb', tension: 0.3 },
    { label: 'Conversion %', data: conversion, borderColor: '#f59e0b', tension: 0.3, yAxisID: 'y1' },
  ], true);
  renderChart('packingChart', labels, [{ label: 'Minutes/Pack', data: mpp, borderColor: '#dc2626', tension: 0.3 }]);
}

function renderChart(canvasId, labels, datasets, dualAxis) {
  if (_charts[canvasId]) _charts[canvasId].destroy();
  var options = { responsive: true, plugins: { legend: { display: datasets.length > 1 } } };
  if (dualAxis) {
    options.scales = { y: { type: 'linear', position: 'left' }, y1: { type: 'linear', position: 'right', grid: { drawOnChartArea: false } } };
  }
  _charts[canvasId] = new Chart(document.getElementById(canvasId).getContext('2d'), {
    type: 'line', data: { labels: labels, datasets: datasets }, options: options,
  });
}
