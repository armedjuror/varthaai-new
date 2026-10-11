/* Varthaai Admin — GST Settings */
var API = '/admin/api/gst/settings/';
var gstData = null;

$(function () {
  load();
  $('#entityForm').on('submit', function (e) { e.preventDefault(); saveEntity(); });
  $('#gstSettingsForm').on('submit', function (e) { e.preventDefault(); saveSettings(); });
});

function post(data, then) {
  showLoader('Saving…');
  return apiPost(API, data)
    .done(function (res) {
      showAlertModal(res.message, res.success ? 'success' : 'danger');
      if (res.success) { load(); if (then) then(); }
    })
    .fail(function (xhr) { showAlertModal(apiErrorMessage(xhr), 'danger'); })
    .always(hideLoader);
}

function load() {
  apiGet(API).done(function (res) {
    if (!res.success) { showAlertModal(res.message, 'danger'); return; }
    gstData = res.data;
    var e = gstData.entity || {};
    $('#entityForm [name]').each(function () { $(this).val(e[this.name] || ''); });
    $('#entityError').toggleClass('d-none', !gstData.entity_error).text(gstData.entity_error ? gstData.entity_error + ' Invoices cannot be issued until this is fixed.' : '');
    var s = gstData.settings;
    $('#gstEffectiveDate').val(s.gst_effective_date || '');
    $('#gstModeB2B').val(s.gst_price_mode_b2b);
    $('#gstModeB2C').val(s.gst_price_mode_b2c);
    var copies = (s.gst_invoice_copies || '').split(',');
    $('.gst-copy').each(function () { this.checked = copies.indexOf(this.value) !== -1; });
    $('#hsnUqc').html(gstData.uqc_choices.map(function (c) { return '<option value="' + c[0] + '">' + escHtml(c[1]) + '</option>'; }).join(''));
    renderHsn();
    renderFlavors();
  }).fail(function (xhr) { showAlertModal(apiErrorMessage(xhr, 'Failed to load GST settings.'), 'danger'); });
}

function renderHsn() {
  var rows = gstData.hsn_codes;
  if (!rows.length) {
    $('#hsnBody').html('<tr><td colspan="6" class="text-center" style="padding:30px;color:var(--gray-400)">No HSN codes yet. Add the HSN your CA confirmed for the chips.</td></tr>');
    return;
  }
  $('#hsnBody').html(rows.map(function (h) {
    var hist = h.rates.map(function (r) {
      return '<div style="font-size:0.8rem">' + r.gst_rate + '%' + (r.cess_rate ? ' + cess ' + r.cess_rate + '%' : '') +
        ' · ' + formatDate(r.effective_from) + ' → ' + (r.effective_to ? formatDate(r.effective_to) : 'open') +
        ' <a href="#" class="text-danger" onclick="deleteRate(' + r.id + ');return false" title="Delete"><i class="fas fa-xmark"></i></a></div>';
    }).join('') || '<span style="color:#dc2626;font-size:0.8rem">No rate — invoices will be blocked</span>';
    return '<tr>' +
      '<td style="font-weight:600">' + escHtml(h.code) + '</td>' +
      '<td>' + escHtml(h.description || '') + '</td>' +
      '<td>' + escHtml(h.uqc) + '</td>' +
      '<td>' + (h.current_rate !== null ? h.current_rate + '%' : '—') + '</td>' +
      '<td>' + hist + '</td>' +
      '<td class="table-actions">' +
        '<button class="btn-icon edit" title="Edit" onclick="openHsnModal(' + h.id + ')"><i class="fas fa-pen"></i></button>' +
        '<button class="btn-icon view" title="Add rate" onclick="openRateModal(' + h.id + ')"><i class="fas fa-percent"></i></button>' +
      '</td></tr>';
  }).join(''));
}

function renderFlavors() {
  var opts = '<option value="">— none —</option>' + gstData.hsn_codes.map(function (h) {
    return '<option value="' + h.id + '">' + escHtml(h.code) + (h.current_rate !== null ? ' (' + h.current_rate + '%)' : '') + '</option>';
  }).join('');
  $('#flavorHsnBody').html(gstData.flavors.map(function (f) {
    return '<tr><td>' + escHtml(f.name) + (f.is_active ? '' : ' <span style="color:var(--gray-400)">(inactive)</span>') + '</td>' +
      '<td style="width:55%"><select class="form-select form-select-sm" onchange="mapFlavor(' + f.id + ', this.value)">' + opts + '</select></td></tr>';
  }).join(''));
  $('#flavorHsnBody select').each(function (i) { $(this).val(gstData.flavors[i].hsn_id || ''); });
}

function saveEntity() {
  var entity = {};
  $('#entityForm [name]').each(function () { entity[this.name] = $(this).val(); });
  post({ action: 'save_entity', entity: entity });
}

function saveSettings() {
  var copies = $('.gst-copy:checked').map(function () { return this.value; }).get();
  post({ action: 'save_settings', settings: {
    gst_effective_date: $('#gstEffectiveDate').val(),
    gst_price_mode_b2b: $('#gstModeB2B').val(),
    gst_price_mode_b2c: $('#gstModeB2C').val(),
    gst_invoice_copies: copies
  } });
}

function openHsnModal(id) {
  var h = (gstData.hsn_codes || []).filter(function (x) { return x.id === id; })[0] || {};
  $('#hsnId').val(h.id || '');
  $('#hsnCode').val(h.code || '');
  $('#hsnDescription').val(h.description || '');
  $('#hsnUqc').val(h.uqc || 'KGS');
  $('#hsnModal').modal('show');
}

function saveHsn() {
  post({ action: 'save_hsn', id: $('#hsnId').val() || null, code: $('#hsnCode').val(),
         description: $('#hsnDescription').val(), uqc: $('#hsnUqc').val() },
       function () { $('#hsnModal').modal('hide'); });
}

function openRateModal(id) {
  var h = gstData.hsn_codes.filter(function (x) { return x.id === id; })[0];
  $('#rateHsnId').val(id);
  $('#rateHsnCode').text(h ? h.code : '');
  $('#rateValue').val('');
  $('#rateCess').val('0');
  $('#rateFrom').val(gstData.settings.gst_effective_date || '');
  $('#rateTo').val('');
  $('#rateModal').modal('show');
}

function saveRate() {
  post({ action: 'add_rate', hsn_id: $('#rateHsnId').val(), gst_rate: $('#rateValue').val(),
         cess_rate: $('#rateCess').val() || 0, effective_from: $('#rateFrom').val(), effective_to: $('#rateTo').val() || null },
       function () { $('#rateModal').modal('hide'); });
}

function deleteRate(id) {
  confirmThen('Delete this rate? Already-issued invoices keep their rate; new invoices in this period will be blocked until a rate exists.', function () {
    post({ action: 'delete_rate', id: id });
  });
}

function mapFlavor(flavorId, hsnId) {
  apiPost(API, { action: 'map_flavor', flavor_id: flavorId, hsn_id: hsnId || null })
    .done(function (res) { if (!res.success) showAlertModal(res.message, 'danger'); })
    .fail(function (xhr) { showAlertModal(apiErrorMessage(xhr), 'danger'); });
}
