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
  // Leads, the orders table/detail/payment/return, and order creation all
  // reuse the existing B2B endpoints directly — same actions/fields as
  // templates/admin/b2b.html and b2b-orders.html/b2b-order-create.html, no
  // new backend API for any of it.
  b2b: '/admin/api/b2b/',
  b2bOrders: '/admin/api/b2b-orders/',
};

var FLAVORS = [];
var _companySelectTimer = null;
var _companyNameCheckTimer = null;
var ORDER_FORM_DATA = null;  // { flavors, packs, companies } — lazy-loaded once
var orderItems = [];
var _addCompanyContext = 'visit';  // 'visit' | 'order' — which flow opened the Add Lead modal

$(function () {
  loadState();
  loadWeeklyOff();
  loadLeave();
  loadB2BDropdowns();
  apiGet(API.flavors).done(function (res) { if (res.success) FLAVORS = res.data; });

  $('#visitCompanySearch').on('input', function () {
    var q = $(this).val();
    clearTimeout(_companySelectTimer);
    _companySelectTimer = setTimeout(function () { searchCompanies(q); }, 250);
  });

  $('#itemFlavor').on('change', onOrderFlavorChange);

  $('#addCompanyName').on('input', function () {
    var q = $(this).val();
    clearTimeout(_companyNameCheckTimer);
    if (!q) { $('#companyNameMatches').hide(); return; }
    _companyNameCheckTimer = setTimeout(function () { checkCompanyNameMatches(q); }, 300);
  });

  $('#addCompanyForm').on('submit', function (e) {
    e.preventDefault();
    var fd = new FormData();
    fd.append('action', 'add_company');
    fd.append('company_name', $('#addCompanyName').val());
    fd.append('category_id', $('#addCategory').val());
    fd.append('source', $('#addSource').val());
    fd.append('address', $('#addAddress').val());
    fd.append('city', $('#addCity').val());
    fd.append('state', $('#addState').val());
    fd.append('pincode', $('#addPincode').val());
    fd.append('gst_number', $('#addGst').val());
    fd.append('location_url', $('#addLocationUrl').val());
    fd.append('assigned_to', $('#addAssignedTo').val());
    fd.append('created_date', $('#addCreatedDate').val());
    fd.append('notes', $('#addNotes').val());
    fd.append('contact_name', $('#addContactName').val());
    fd.append('contact_phone', $('#addContactPhone').val());
    fd.append('contact_designation', $('#addContactDesignation').val());
    var photo = $('#addPhoto')[0].files[0];
    if (photo) fd.append('photo', photo);
    showLoader('Adding lead…');
    apiPostForm(API.b2b, fd)
      .done(function (res) {
        if (!res.success) return fail(res.message);
        $('#addCompanyModal').modal('hide');
        $('#addCompanyForm')[0].reset();
        $('#companyNameMatches').hide();
        showAlertModal(res.message, 'success');
        var newId = res.data && res.data.id;
        var newName = $('#addCompanyName').val() || 'New Lead';
        if (newId && _addCompanyContext === 'order') {
          loadOrderFormData(function () {
            if (!$('#orderCompany option[value="' + newId + '"]').length) {
              $('#orderCompany').append('<option value="' + newId + '">' + escHtml(newName) + '</option>');
            }
            $('#orderCompany').val(newId);
            onOrderCompanyChange();
          });
        } else if (newId) {
          selectCompany(newId, newName);
        }
      })
      .fail(fail)
      .always(hideLoader);
  });
});

function openAddCompanyModal(ctx) {
  _addCompanyContext = ctx;
  new bootstrap.Modal(document.getElementById('addCompanyModal')).show();
}

function loadB2BDropdowns() {
  apiGet(API.b2b, { view: 'categories' }).done(function (res) {
    if (!res.success) return;
    var opts = '<option value="">Select…</option>';
    (res.data || []).forEach(function (c) { opts += '<option value="' + c.id + '">' + escHtml(c.name) + '</option>'; });
    $('#addCategory').html(opts);
  });
  apiGet(API.b2b, { view: 'admins' }).done(function (res) {
    if (!res.success) return;
    var opts = '<option value="">Select…</option>';
    (res.data || []).forEach(function (a) { opts += '<option value="' + a.id + '">' + escHtml(a.name) + '</option>'; });
    $('#addAssignedTo').html(opts);
  });
}

function checkCompanyNameMatches(q) {
  apiGet(API.b2b, { search: q, per_page: 10 }).done(function (res) {
    if (!res.success) return;
    var companies = (res.data && res.data.companies) || [];
    if (!companies.length) { $('#companyNameMatches').hide(); return; }
    $('#companyNameMatches').html(companies.map(function (c) {
      return '<div class="p-2 border-bottom small text-muted">' + escHtml(c.company_name) + '</div>';
    }).join('')).show();
  });
}

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
    $('#visitCard, #b2bOrdersCard').addClass('d-none');
  } else {
    $('#startCard').addClass('d-none');
    var typeLabel = open.type === 'sales' ? 'Sales Day' : 'Packing';
    var statusLabel = open.status === 'on_break' ? 'On Break' : 'Active';
    var html = '<p class="mb-2"><strong>' + typeLabel + '</strong> — ' + statusLabel +
      ' <span class="text-muted">(started ' + formatDateTime(open.started_at) + ')</span></p>';
    if (open.area) html += '<p class="mb-2 text-muted"><i class="fas fa-location-dot me-1"></i>' + escHtml(open.area) + '</p>';
    html += '<div class="d-flex flex-column gap-2">';
    if (open.status === 'active') {
      html += '<button class="btn btn-warning big-btn" data-bs-toggle="modal" data-bs-target="#breakModal"><i class="fas fa-mug-hot me-2"></i>Break</button>';
      html += '<button class="btn btn-danger big-btn" onclick="endSession(\'' + open.type + '\')"><i class="fas fa-flag-checkered me-2"></i>End ' + typeLabel + '</button>';
    } else if (open.status === 'on_break') {
      html += '<button class="btn btn-success big-btn" onclick="resumeSession()"><i class="fas fa-play me-2"></i>Resume</button>';
      // Ending while on break is allowed — the backend closes the open
      // break (auto-resume) at the same instant before recording the end,
      // so break time stays well-defined. Don't force a Resume tap first.
      html += '<button class="btn btn-danger big-btn" onclick="endSession(\'' + open.type + '\')"><i class="fas fa-flag-checkered me-2"></i>End ' + typeLabel + '</button>';
    }
    html += '</div>';
    $card.html(html);

    if (open.type === 'sales') {
      $('#visitCard, #b2bOrdersCard').removeClass('d-none');
      renderVisits(today.visits);
    } else {
      $('#visitCard, #b2bOrdersCard').addClass('d-none');
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
  var area = $('#startAreaInput').val().trim();
  if (type === 'sales' && !area) return fail('Area is required to start a sales session.');
  showLoader('Starting…');
  apiPost(API.start, { type: type, area: area }).done(function (res) {
    if (!res.success) { hideLoader(); return fail(res.message); }
    $('#startAreaInput').val('');
    loadState();  // keeps the loader up through its own fetch — no hide/flash gap
  }).fail(function (xhr) { hideLoader(); fail(xhr); });
}

function breakSession(reason) {
  showLoader('Starting break…');
  apiPost(API.brk, { reason: reason }).done(function (res) {
    $('#breakModal').modal('hide');
    if (!res.success) { hideLoader(); return fail(res.message); }
    loadState();  // keeps the loader up through its own fetch — no hide/flash gap
  }).fail(function (xhr) { hideLoader(); fail(xhr); });
}

function resumeSession() {
  showLoader('Resuming…');
  apiPost(API.resume, {}).done(function (res) {
    if (!res.success) { hideLoader(); return fail(res.message); }
    loadState();
  }).fail(function (xhr) { hideLoader(); fail(xhr); });
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
    showLoader('Ending session…');
    apiPost(API.end, {}).done(function (res) {
      if (!res.success) { hideLoader(); return fail(res.message); }
      loadState();
    }).fail(function (xhr) { hideLoader(); fail(xhr); });
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
  showLoader('Ending packing session…');
  apiPost(API.end, { items: items }).done(function (res) {
    if (!res.success) { hideLoader(); return fail(res.message); }
    $('#packingEndModal').modal('hide');
    loadState();
  }).fail(function (xhr) { hideLoader(); fail(xhr); });
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
}

function submitVisit() {
  var companyId = $('#visitCompanyId').val();
  if (!companyId) return fail('Pick a company — use "Add a new lead" if it\'s not listed.');
  var payload = {
    company_id: companyId,
    purpose: $('#visitPurpose').val(),
    outcome: $('#visitOutcome').val(),
    notes: $('#visitNotes').val(),
  };
  showLoader('Saving visit…');
  apiPost(API.visits, payload).done(function (res) {
    if (!res.success) { hideLoader(); return fail(res.message); }
    $('#visitModal').modal('hide');
    $('#visitCompanyId,#visitNotes').val('');
    $('#visitCompanySelected').text('');
    loadState();
  }).fail(function (xhr) { hideLoader(); fail(xhr); });
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
  showLoader('Saving…');
  apiPost(API.weeklyOff, { weekday: $('#weeklyOffSelect').val() }).done(function (res) {
    if (!res.success) return fail(res.message);
    $('#weeklyOffModal').modal('hide');
    showAlertModal(res.message, 'success');
    loadWeeklyOff();
  }).fail(fail).always(hideLoader);
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
  showLoader('Saving…');
  apiPost(API.leave, { date: date }).done(function (res) {
    if (!res.success) return fail(res.message);
    $('#leaveDateInput').val('');
    loadLeave();
  }).fail(fail).always(hideLoader);
}

function removeLeave(date) {
  confirmThen('Remove leave on ' + date + '?', function () {
    showLoader('Removing…');
    $.ajax({
      url: API.leave + '?date=' + encodeURIComponent(date), type: 'DELETE', dataType: 'json',
      statusCode: { 401: function () { window.location.href = '/admin/'; } },
    }).done(function (res) {
      if (!res.success) return fail(res.message);
      loadLeave();
    }).fail(fail).always(hideLoader);
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
  showLoader('Saving…');
  apiPost(API.editEnd, {
    session_id: $('#editSessionId').val(),
    ended_at: $('#editEndedAt').val(),
    note: note,
  }).done(function (res) {
    if (!res.success) { hideLoader(); return fail(res.message); }
    $('#editEndTimeModal').modal('hide');
    loadState();
  }).fail(function (xhr) { hideLoader(); fail(xhr); });
}

/* ── B2B Orders table/detail/payment/return — all handled by the unmodified
   static/js/admin/b2b-orders.js (loaded only when the 'b2b' permission is
   present — see my_day.html). Nothing to do here except feed today's
   numbers after an order-affecting action; see openNewOrderModal() below
   for order *creation*, which b2b-orders.js doesn't cover. ── */

/* ── New order creation — ports b2b-order-create.html's company/items/
   discount/payment logic into a modal; same endpoint+action+field
   contract (POST /admin/api/b2b-orders/, action=create_order) as that
   page, just without its offers/batch-pinning/company-balance panels. ── */

function loadOrderFormData(cb) {
  if (ORDER_FORM_DATA) { if (cb) cb(); return; }
  showLoader('Loading…');
  apiGet(API.b2bOrders, { view: 'order_form_data' }).done(function (res) {
    if (!res.success) return fail(res.message);
    ORDER_FORM_DATA = res.data;
    renderOrderFormDropdowns();
    if (cb) cb();
  }).fail(fail).always(hideLoader);
}

function renderOrderFormDropdowns() {
  var d = ORDER_FORM_DATA;
  $('#orderCompany').html('<option value="">Select…</option>' + d.companies.map(function (c) {
    return '<option value="' + c.id + '">' + escHtml(c.company_name) + '</option>';
  }).join(''));
  $('#itemFlavor').html('<option value="">Flavour…</option>' + d.flavors.map(function (f) {
    return '<option value="' + f.id + '">' + escHtml(f.name) + '</option>';
  }).join(''));
}

function onOrderCompanyChange() {
  var d = ORDER_FORM_DATA;
  var companyId = $('#orderCompany').val();
  var company = d && d.companies.filter(function (c) { return String(c.id) === String(companyId); })[0];
  var opts = '<option value="">No contact</option>';
  if (company && company.contacts_raw) {
    company.contacts_raw.split('|').forEach(function (s) {
      var parts = s.split(':');
      opts += '<option value="' + parts[0] + '">' + escHtml(parts.slice(1).join(':')) + '</option>';
    });
  }
  $('#orderContact').html(opts);
}

function onOrderFlavorChange() {
  var d = ORDER_FORM_DATA;
  var flavorId = $('#itemFlavor').val();
  var opts = '<option value="">Pack…</option>';
  if (d && flavorId) {
    d.packs.filter(function (p) { return String(p.flavor_id) === String(flavorId); }).forEach(function (p) {
      opts += '<option value="' + p.id + '">' + escHtml(p.label) + ' (' + p.weight_grams + 'g) — ' + formatCurrency(p.selling_price) + '</option>';
    });
  }
  $('#itemPack').html(opts);
}

function addOrderItem() {
  var d = ORDER_FORM_DATA;
  var flavorId = $('#itemFlavor').val();
  var packId = $('#itemPack').val();
  var qty = parseInt($('#itemQty').val(), 10);
  if (!flavorId || !packId) return fail('Pick a flavour and a pack.');
  if (!qty || qty <= 0) return fail('Enter a valid quantity.');
  var flavor = d.flavors.filter(function (f) { return String(f.id) === String(flavorId); })[0];
  var pack = d.packs.filter(function (p) { return String(p.id) === String(packId); })[0];
  if (!flavor || !pack) return fail('Invalid selection.');
  orderItems.push({
    _id: Date.now() + Math.random(),
    flavor_id: flavor.id, flavor_pack_id: pack.id, stock_id: null,
    quantity: qty, weight_grams: pack.weight_grams, mrp: pack.mrp,
    selling_price: pack.selling_price, cost_price: pack.cost_price || null,
    is_free_item: 0, offer_id: null,
    flavor_name: flavor.name, pack_label: pack.label,
  });
  $('#itemQty').val(1);
  renderOrderItems();
}

function removeOrderItem(id) {
  orderItems = orderItems.filter(function (it) { return it._id !== id; });
  renderOrderItems();
}

function renderOrderItems() {
  $('#orderItemsList').html(orderItems.length ? orderItems.map(function (it) {
    return '<li class="list-group-item d-flex justify-content-between align-items-center">' +
      '<span>' + escHtml(it.flavor_name) + ' (' + escHtml(it.pack_label) + ') x' + it.quantity + '</span>' +
      '<span>' + formatCurrency(it.selling_price * it.quantity) +
      ' <button type="button" class="btn btn-sm btn-outline-danger ms-2" onclick="removeOrderItem(' + it._id + ')"><i class="fas fa-trash"></i></button></span>' +
      '</li>';
  }).join('') : '<li class="list-group-item text-muted">No items yet.</li>');
  recalcOrderTotals();
}

function recalcOrderTotals() {
  var subtotal = 0;
  orderItems.forEach(function (it) { if (!it.is_free_item) subtotal += it.selling_price * it.quantity; });
  var discType = $('#discountType').val();
  var discVal = parseFloat($('#discountValue').val()) || 0;
  var discAmt = 0;
  if (discType === 'percentage' && discVal > 0) discAmt = Math.round(subtotal * discVal / 100 * 100) / 100;
  else if (discType === 'amount' && discVal > 0) discAmt = Math.min(discVal, subtotal);
  var total = Math.max(0, subtotal - discAmt);
  $('#summSubtotal').text(formatCurrency(subtotal));
  if (discAmt > 0) { $('#summDiscRow').removeClass('d-none'); $('#summDiscount').text(formatCurrency(discAmt)); }
  else { $('#summDiscRow').addClass('d-none'); }
  $('#summTotal').text(formatCurrency(total));
}

function openNewOrderModal() {
  orderItems = [];
  $('#orderCompany,#itemFlavor,#itemPack,#discountType,#discountValue,#orderDueDate,#orderNotes,#orderPayAmount,#orderPayRef').val('');
  $('#orderContact').html('<option value="">No contact</option>');
  $('#itemQty').val(1);
  $('#orderStatus').val('confirmed');
  $('#recordPaymentNow').prop('checked', false);
  $('#orderPaymentFields').addClass('d-none');
  renderOrderItems();
  loadOrderFormData(function () {
    new bootstrap.Modal(document.getElementById('newOrderModal')).show();
  });
}

function submitNewOrder() {
  var companyId = $('#orderCompany').val();
  if (!companyId) return fail('Pick a company.');
  if (!orderItems.length) return fail('Add at least one item.');

  var data = {
    action: 'create_order',
    company_id: companyId,
    contact_id: $('#orderContact').val() || null,
    discount_type: $('#discountType').val() || null,
    discount_value: parseFloat($('#discountValue').val()) || null,
    notes: $('#orderNotes').val() || null,
    due_date: $('#orderDueDate').val() || null,
    status: $('#orderStatus').val() || 'draft',
    items: orderItems.map(function (it) {
      return {
        flavor_id: it.flavor_id, flavor_pack_id: it.flavor_pack_id, stock_id: it.stock_id,
        quantity: it.quantity, weight_grams: it.weight_grams, mrp: it.mrp,
        selling_price: it.selling_price, cost_price: it.cost_price,
        is_free_item: it.is_free_item, offer_id: it.offer_id,
        flavor_name: it.flavor_name, pack_label: it.pack_label,
      };
    }),
  };

  if ($('#recordPaymentNow').is(':checked')) {
    var payAmount = parseFloat($('#orderPayAmount').val());
    if (!payAmount || payAmount <= 0) return fail('Enter a valid payment amount.');
    data.payment_amount = payAmount;
    data.payment_method = $('#orderPayMethod').val();
    data.payment_reference = $('#orderPayRef').val() || null;
  }

  showLoader('Creating order…');
  apiPost(API.b2bOrders, data).done(function (res) {
    if (!res.success) return fail(res.message);
    $('#newOrderModal').modal('hide');
    showAlertModal(res.message, 'success');
    if (typeof loadOrders === 'function') loadOrders();
  }).fail(fail).always(hideLoader);
}
