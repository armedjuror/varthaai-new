/* Varthaai Admin — Form detail (fields, settings, links & QR, results) */

var FORM_ID = $('#formDetail').data('form-id');
var API = '/admin/api/forms/' + FORM_ID + '/';
var formData = null;
var linksData = null;
var resultsState = { page: 1, per_page: 20 };
var ROLE_TYPE = { user_name: 'text', user_phone: 'phone', user_email: 'email' };
var ROLE_LABEL = { user_name: 'Name', user_phone: 'Phone', user_email: 'Email' };
var STATUS_BADGE = { draft: 'bg-warning', live: 'bg-success', closed: 'bg-danger' };
var _filterDebounce = null;

$(function () {
  loadForm();

  $('#fieldType').on('change', syncFieldModal);
  $('#fieldRole').on('change', function () {
    if (ROLE_TYPE[this.value]) $('#fieldType').val(ROLE_TYPE[this.value]);
    syncFieldModal();
  });
  $('#setShowCode').on('change', function () { $('#codeOptions').toggle(this.checked); });
  $('#linksAll').on('change', function () { $('.link-check').prop('checked', this.checked); });

  $('button[data-bs-target="#tabLinks"]').on('shown.bs.tab', loadLinks);
  $('button[data-bs-target="#tabResults"]').on('shown.bs.tab', function () { buildResultFilters(); loadResults(); });
});

/* ── load ── */

function loadForm(done) {
  apiGet(API).done(function (res) {
    if (!res.success) { showAlertModal(res.message, 'danger'); return; }
    formData = res.data;
    $('#formTitleHeading').text(formData.title);
    $('#formStatusBadge').html('<span class="badge ' + STATUS_BADGE[formData.status] + '">' + escHtml(formData.status) + '</span>');
    $('#formResponseCount').text(formData.response_count);
    renderFields();
    fillSettings();
    if (done) done();
  }).fail(function (xhr) { showAlertModal(apiErrorMessage(xhr, 'Failed to load form.'), 'danger'); });
}

function activeFields() { return (formData.fields || []).filter(function (f) { return !f.is_archived; }); }

/* ── fields tab ── */

function renderFields() {
  var fields = formData.fields || [];
  var act = activeFields();
  var roles = {};
  act.forEach(function (f) { if (f.role) roles[f.role] = true; });
  $('#quickName').prop('disabled', !!roles.user_name);
  $('#quickPhone').prop('disabled', !!roles.user_phone);
  $('#quickEmail').prop('disabled', !!roles.user_email);

  if (!fields.length) {
    $('#fieldsBody').html('<tr><td colspan="6" class="text-center text-muted" style="padding:30px">No fields yet. Add one, or use the Name / Phone / Email shortcuts.</td></tr>');
    return;
  }
  $('#fieldsBody').html(fields.map(function (f) {
    var isKey = formData.key_field_id === f.id;
    var special = [];
    if (isKey) special.push('<span class="badge bg-info">Key</span>');
    if (f.role) special.push('<span class="badge bg-success">User ' + ROLE_LABEL[f.role].toLowerCase() + '</span>');
    if (f.is_archived) special.push('<span class="badge bg-warning">Archived</span>');
    var idx = act.indexOf(f);
    var actions = f.is_archived ? '' :
      '<button class="btn-icon view" title="Move up" onclick="moveField(' + f.id + ',-1)"' + (idx === 0 ? ' disabled' : '') + '><i class="fas fa-arrow-up"></i></button> ' +
      '<button class="btn-icon view" title="Move down" onclick="moveField(' + f.id + ',1)"' + (idx === act.length - 1 ? ' disabled' : '') + '><i class="fas fa-arrow-down"></i></button> ' +
      (f.field_type === 'dropdown'
        ? '<button class="btn-icon print" title="' + (isKey ? 'Remove key' : 'Use as key field') + '" onclick="toggleKey(' + f.id + ',' + isKey + ')"><i class="fas fa-key"></i></button> ' : '') +
      '<button class="btn-icon edit" title="Edit" onclick="openFieldModal(' + f.id + ')"><i class="fas fa-pen"></i></button> ' +
      '<button class="btn-icon delete" title="Remove" onclick="removeField(' + f.id + ')"><i class="fas fa-trash"></i></button>';
    return '<tr' + (f.is_archived ? ' style="opacity:.55"' : '') + '>' +
      '<td>' + (f.is_archived ? '' : (idx + 1)) + '</td>' +
      '<td><strong>' + escHtml(f.label) + '</strong>' +
        (f.field_type === 'dropdown' ? '<div class="text-muted" style="font-size:12px">' + f.options.length + ' options</div>' : '') + '</td>' +
      '<td>' + escHtml(f.type_label) + '</td>' +
      '<td>' + (f.required ? 'Yes' : '—') + '</td>' +
      '<td>' + special.join(' ') + '</td>' +
      '<td class="table-actions">' + actions + '</td></tr>';
  }).join(''));
}

function fieldById(id) { return (formData.fields || []).filter(function (f) { return f.id === id; })[0]; }

function syncFieldModal() {
  var t = $('#fieldType').val();
  $('#optionsWrap').toggle(t === 'dropdown');
  var roleType = ROLE_TYPE[$('#fieldRole').val()];
  var locked = !!$('#fieldId').val() && formData.response_count > 0;
  $('#fieldType').prop('disabled', locked || !!roleType);
  $('#typeLockedHint').toggle(locked);
  // A role only fits its own type.
  $('#fieldRole option').each(function () {
    var need = ROLE_TYPE[this.value];
    $(this).prop('disabled', !!need && need !== t && !roleType);
  });
}

function openFieldModal(id) {
  var f = id ? fieldById(id) : null;
  $('#fieldModalTitle').text(f ? 'Edit field' : 'Add field');
  $('#fieldId').val(f ? f.id : '');
  $('#fieldLabel').val(f ? f.label : '');
  $('#fieldType').val(f ? f.field_type : 'text');
  $('#fieldRole').val(f ? f.role : '');
  $('#fieldOptions').val(f ? (f.options || []).join('\n') : '');
  $('#fieldHelp').val(f ? f.help_text : '');
  $('#fieldPlaceholder').val(f ? f.placeholder : '');
  $('#fieldRequired').prop('checked', f ? f.required : false);
  syncFieldModal();
  new bootstrap.Modal('#fieldModal').show();
}

function saveField() {
  var id = $('#fieldId').val();
  var payload = {
    action: id ? 'update_field' : 'add_field',
    field_id: id ? parseInt(id, 10) : undefined,
    label: $('#fieldLabel').val(),
    field_type: $('#fieldType').val(),
    role: $('#fieldRole').val(),
    options: $('#fieldOptions').val().split('\n'),
    help_text: $('#fieldHelp').val(),
    placeholder: $('#fieldPlaceholder').val(),
    required: $('#fieldRequired').is(':checked')
  };
  showLoader('Saving…');
  apiPost(API, payload).done(function (res) {
    hideLoader();
    if (!res.success) { showAlertModal(res.message, 'danger'); return; }
    bootstrap.Modal.getInstance('#fieldModal').hide();
    resetResultsUi();
    loadForm();
  }).fail(function (xhr) { hideLoader(); showAlertModal(apiErrorMessage(xhr, 'Failed to save field.'), 'danger'); });
}

function quickAddField(role) {
  apiPost(API, { action: 'add_user_field', role: role }).done(function (res) {
    if (!res.success) { showAlertModal(res.message, 'danger'); return; }
    resetResultsUi();
    loadForm();
  }).fail(function (xhr) { showAlertModal(apiErrorMessage(xhr, 'Failed to add field.'), 'danger'); });
}

function removeField(id) {
  confirmThen('Remove this field? If it already has responses it is archived and the data is kept.', function () {
    apiPost(API, { action: 'remove_field', field_id: id }).done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      if (res.data.result === 'archived') showAlertModal(res.message, 'info');
      resetResultsUi();
      loadForm();
    }).fail(function (xhr) { showAlertModal(apiErrorMessage(xhr, 'Failed to remove field.'), 'danger'); });
  });
}

function moveField(id, delta) {
  var ids = activeFields().map(function (f) { return f.id; });
  var i = ids.indexOf(id), j = i + delta;
  if (i < 0 || j < 0 || j >= ids.length) return;
  ids.splice(j, 0, ids.splice(i, 1)[0]);
  apiPost(API, { action: 'reorder_fields', field_ids: ids }).done(function () { resetResultsUi(); loadForm(); });
}

function toggleKey(id, isKey) {
  apiPost(API, { action: 'set_key_field', field_id: isKey ? null : id }).done(function (res) {
    if (!res.success) { showAlertModal(res.message, 'danger'); return; }
    linksData = null;
    loadForm();
  }).fail(function (xhr) { showAlertModal(apiErrorMessage(xhr, 'Failed to update key field.'), 'danger'); });
}

/* ── settings tab ── */

function fillSettings() {
  var d = formData;
  $('#setTitle').val(d.title); $('#setDescription').val(d.description); $('#setStatus').val(d.status);
  $('#setCreateUsers').prop('checked', d.create_users);
  $('#setOnePerPhone').prop('checked', d.one_response_per_phone);
  $('#setConsent').val(d.consent_text);
  $('#setSuccessTitle').val(d.success_title); $('#setSuccessMessage').val(d.success_message);
  $('#setRedirect').val(d.success_redirect_url);
  $('#setShowCode').prop('checked', d.show_code); $('#codeOptions').toggle(d.show_code);
  $('#setCodeLength').val(d.code_length); $('#setCodePrefix').val(d.code_prefix); $('#setCodeLabel').val(d.code_label);
}

function saveSettings() {
  showLoader('Saving…');
  apiPost(API, {
    action: 'update_settings',
    title: $('#setTitle').val(), description: $('#setDescription').val(), status: $('#setStatus').val(),
    create_users: $('#setCreateUsers').is(':checked'), one_response_per_phone: $('#setOnePerPhone').is(':checked'),
    consent_text: $('#setConsent').val(),
    success_title: $('#setSuccessTitle').val(), success_message: $('#setSuccessMessage').val(),
    success_redirect_url: $('#setRedirect').val(),
    show_code: $('#setShowCode').is(':checked'), code_length: $('#setCodeLength').val(),
    code_prefix: $('#setCodePrefix').val(), code_label: $('#setCodeLabel').val()
  }).done(function (res) {
    hideLoader();
    if (!res.success) { showAlertModal(res.message, 'danger'); return; }
    showAlertModal('Settings saved.', 'success');
    loadForm();
  }).fail(function (xhr) { hideLoader(); showAlertModal(apiErrorMessage(xhr, 'Failed to save settings.'), 'danger'); });
}

/* ── links tab ── */

function loadLinks() {
  $('#liveWarning').toggle(formData.status !== 'live');
  apiGet(API + 'links/').done(function (res) {
    if (!res.success) { showAlertModal(res.message, 'danger'); return; }
    linksData = res.data;
    $('#generalUrl').val(linksData.general_url);
    var has = !!linksData.key_label && linksData.links.length > 0;
    $('#noKeyHint').toggle(!has); $('#keyLinksWrap, #qrControls').toggle(has);
    $('#keyLinksTitle').text(linksData.key_label ? 'Links per “' + linksData.key_label + '”' : 'Links per key option');
    $('#keyLabelHead').text(linksData.key_label || 'Option');
    $('#linksAll').prop('checked', true);
    $('#linksBody').html(linksData.links.map(function (l, i) {
      return '<tr><td><input type="checkbox" class="link-check" data-i="' + i + '" checked></td>' +
        '<td><strong>' + escHtml(l.value) + '</strong></td>' +
        '<td><input class="form-control form-control-sm link-url" readonly value="' + escHtml(l.url) + '"></td>' +
        '<td><button class="btn-icon view" title="Copy link" onclick="copyUrl(' + i + ')"><i class="fas fa-copy"></i></button></td></tr>';
    }).join(''));
  }).fail(function (xhr) { showAlertModal(apiErrorMessage(xhr, 'Failed to load links.'), 'danger'); });
}

function copyText(sel) { copyToClipboard($(sel).val()); }
function copyUrl(i) { copyToClipboard(linksData.links[i].url); }

function copyToClipboard(text) {
  var done = function () { showAlertModal('Link copied.', 'success'); };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done);
  } else {
    var $t = $('<textarea>').val(text).appendTo('body').select();
    document.execCommand('copy'); $t.remove(); done();
  }
}

function downloadQr() {
  var picked = [];
  $('.link-check:checked').each(function () { picked.push(linksData.links[$(this).data('i')].value); });
  if (!picked.length) { showAlertModal('Select at least one option.', 'danger'); return; }
  var qs = $.param({ per_page: $('#qrPerPage').val(), options: JSON.stringify(picked) });
  window.location.href = '/admin/forms/' + FORM_ID + '/qr.pdf?' + qs;
}

/* ── results tab ── */

function resetResultsUi() { $('#resultFilters').data('built', false); }

function buildResultFilters() {
  var $box = $('#resultFilters');
  if ($box.data('built')) return;
  var html = '<div class="col-md-3"><label class="form-label">Search</label>' +
    '<input class="form-control" id="rfSearch" placeholder="Any text or code…"></div>' +
    '<div class="col-md-2"><label class="form-label">From</label><input type="date" class="form-control" id="rfFrom"></div>' +
    '<div class="col-md-2"><label class="form-label">To</label><input type="date" class="form-control" id="rfTo"></div>';
  (formData.fields || []).forEach(function (f) {
    var id = 'rf_' + f.slug, label = escHtml(f.label);
    if (f.field_type === 'dropdown') {
      html += '<div class="col-md-3"><label class="form-label">' + label + '</label>' +
        '<select class="form-select rf" data-slug="' + f.slug + '"><option value="">All</option>' +
        f.options.map(function (o) { return '<option value="' + escHtml(o) + '">' + escHtml(o) + '</option>'; }).join('') +
        '</select></div>';
    } else if (f.field_type === 'checkbox') {
      html += '<div class="col-md-2"><label class="form-label">' + label + '</label>' +
        '<select class="form-select rf" data-slug="' + f.slug + '"><option value="">All</option><option value="yes">Yes</option><option value="no">No</option></select></div>';
    } else if (f.field_type === 'number' || f.field_type === 'rating' || f.field_type === 'date') {
      var t = f.field_type === 'date' ? 'date' : 'number';
      html += '<div class="col-md-3"><label class="form-label">' + label + '</label><div class="input-group">' +
        '<input type="' + t + '" class="form-control rf-min" data-slug="' + f.slug + '" placeholder="Min">' +
        '<input type="' + t + '" class="form-control rf-max" data-slug="' + f.slug + '" placeholder="Max"></div></div>';
    } else {
      html += '<div class="col-md-3"><label class="form-label">' + label + '</label>' +
        '<input class="form-control rf" data-slug="' + f.slug + '" placeholder="Contains…"></div>';
    }
  });
  html += '<div class="col-md-1"><label class="form-label">Per page</label><select class="form-select" id="rfPerPage">' +
    '<option>10</option><option selected>20</option><option>50</option><option>100</option></select></div>';
  $box.html(html).data('built', true);

  $box.on('input change', 'input, select', function () {
    clearTimeout(_filterDebounce);
    _filterDebounce = setTimeout(function () {
      resultsState.page = 1;
      resultsState.per_page = parseInt($('#rfPerPage').val(), 10);
      loadResults();
    }, 350);
  });
}

function resultParams() {
  var p = { search: $('#rfSearch').val(), date_from: $('#rfFrom').val(), date_to: $('#rfTo').val() };
  $('#resultFilters .rf').each(function () { if (this.value) p['f.' + $(this).data('slug')] = this.value; });
  $('#resultFilters .rf-min').each(function () { if (this.value) p['f.' + $(this).data('slug') + '.min'] = this.value; });
  $('#resultFilters .rf-max').each(function () { if (this.value) p['f.' + $(this).data('slug') + '.max'] = this.value; });
  return p;
}

function clearResultFilters() {
  $('#resultFilters input').val(''); $('#resultFilters select').not('#rfPerPage').val('');
  resultsState.page = 1; loadResults();
}

function exportCsv() {
  window.location.href = '/admin/forms/' + FORM_ID + '/export.csv?' + $.param(resultParams());
}

function cell(f, v) {
  if (v === undefined || v === null || v === '') return '<span class="text-muted">—</span>';
  if (v === true) return 'Yes';
  if (v === false) return 'No';
  if (f.field_type === 'long_text') return '<div style="max-width:320px;white-space:pre-wrap">' + escHtml(String(v)) + '</div>';
  return escHtml(String(v));
}

function loadResults() {
  var params = Object.assign({ page: resultsState.page, per_page: resultsState.per_page }, resultParams());
  apiGet(API + 'results/', params).done(function (res) {
    if (!res.success) { showAlertModal(res.message, 'danger'); return; }
    var d = res.data, fields = d.fields;
    $('#resultsCount').text(d.total);
    $('#resultsHead').html('<tr><th>Submitted</th>' + (formData.show_code ? '<th>Code</th>' : '') +
      fields.map(function (f) { return '<th>' + escHtml(f.label) + (f.is_archived ? ' <small class="text-muted">(archived)</small>' : '') + '</th>'; }).join('') +
      '<th>Customer</th></tr>');
    if (!d.rows.length) {
      $('#resultsBody').html('<tr><td colspan="' + (fields.length + 3) + '" class="text-center text-muted" style="padding:30px">No responses match.</td></tr>');
    } else {
      $('#resultsBody').html(d.rows.map(function (r) {
        return '<tr><td style="white-space:nowrap">' + formatDateTime(r.created_at) + '</td>' +
          (formData.show_code ? '<td><code>' + escHtml(r.code) + '</code></td>' : '') +
          fields.map(function (f) { return '<td>' + cell(f, r.answers[f.slug]) + '</td>'; }).join('') +
          '<td>' + (r.user_id ? '<a href="/admin/customers/' + r.user_id + '/' + '">' + escHtml(r.user_name || 'View') + '</a>' : '—') + '</td></tr>';
      }).join(''));
    }
    renderPagination(d.pages, d.page);
  }).fail(function (xhr) { showAlertModal(apiErrorMessage(xhr, 'Failed to load results.'), 'danger'); });
}

function renderPagination(pages, current) {
  var $p = $('#resultsPagination');
  if (pages <= 1) { $p.html(''); return; }
  function pi(disabled, page, label) {
    return '<li class="page-item' + (disabled ? ' disabled' : '') + '"><a class="page-link" href="#" onclick="resultsGo(' + page + ');return false">' + label + '</a></li>';
  }
  var html = '<nav><ul class="pagination justify-content-center mb-0 flex-wrap">' + pi(current <= 1, current - 1, '‹');
  for (var i = Math.max(1, current - 2); i <= Math.min(pages, current + 2); i++) {
    html += '<li class="page-item' + (i === current ? ' active' : '') + '"><a class="page-link" href="#" onclick="resultsGo(' + i + ');return false">' + i + '</a></li>';
  }
  $p.html(html + pi(current >= pages, current + 1, '›') + '</ul></nav>');
}

function resultsGo(p) { resultsState.page = p; loadResults(); }
