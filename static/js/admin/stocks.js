/* Varthaai Admin — Stocks Page */

var _stockData  = [];   /* cached inventory (per-batch) */
var _allFlavors = [];   /* for dropdowns */
var _allVendors = [];   /* for dropdowns & vendors tab */

var _movTabLoaded = false;
var _altTabLoaded = false;
var _venTabLoaded = false;

/* ── Labels / colours ── */
var movTypeLabel = { in: 'In', out: 'Out', adjustment: 'Adjustment', reserved: 'Reserved', released: 'Released' };
var movTypeColor = { in: '#16a34a', out: '#dc2626', adjustment: '#6b7280', reserved: '#d97706', released: '#2563eb' };
var refTypeLabel = { purchase: 'Purchase', sale: 'Sale', wastage: 'Wastage', adjustment: 'Adjustment', order_reserve: 'Order Reserve', order_release: 'Order Release' };
var alertTypeLabel = { low_stock: 'Low Stock', out_of_stock: 'Out of Stock', expiring_soon: 'Expiring Soon', expired: 'Expired' };
var alertTypeColor = { low_stock: '#d97706', out_of_stock: '#dc2626', expiring_soon: '#ea580c', expired: '#7f1d1d' };
var statusLabel = { good: 'Good', low: 'Low Stock', critical: 'Critical', out: 'Out of Stock' };
var statusColor = { good: '#16a34a', low: '#d97706', critical: '#ea580c', out: '#dc2626' };

$(function () {
  /* load base data then render inventory */
  loadFlavors(function () {
    loadVendorData(function () {
      loadStats();
      loadInventory();
    });
  });

  /* lazy-load other tabs */
  $('#movementsTabBtn').on('shown.bs.tab', function () {
    if (!_movTabLoaded) { _movTabLoaded = true; loadMovements(1); }
  });
  $('#alertsTabBtn').on('shown.bs.tab', function () {
    if (!_altTabLoaded) { _altTabLoaded = true; loadAlerts(); }
  });
  $('#vendorsTabBtn').on('shown.bs.tab', function () {
    if (!_venTabLoaded) { _venTabLoaded = true; renderVendors(_allVendors); }
  });

  /* ── Restock form ── */
  $('#restockForm').on('submit', function (e) {
    e.preventDefault();
    var payload = {
      action:            'restock',
      flavor_id:         parseInt($('#restockFlavorId').val()),
      batch_number:      $('#restockBatch').val().trim(),
      quantity_kg:       parseFloat($('#restockQty').val()),
      vendor_id:         $('#restockVendorId').val() || '',
      cost_price_per_kg: parseFloat($('#restockCostPrice').val()) || 0,
      supplier_name:     $('#restockSupplier').val().trim(),
      supplier_contact:  $('#restockContact').val().trim(),
      expiry_date:       $('#restockExpiry').val(),
      storage_location:  $('#restockLocation').val().trim(),
      notes:             $('#restockNotes').val().trim()
    };

    showLoader('Restocking…');
    apiPost('/admin/api/stocks/', payload)
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) { $('#restockModal').modal('hide'); $('#restockForm')[0].reset(); refreshAll(); }
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });

  /* ── Record Movement form ── */
  $('#movementForm').on('submit', function (e) {
    e.preventDefault();
    showLoader('Recording…');
    apiPost('/admin/api/stocks/', {
      action:         'record_movement',
      stock_id:       parseInt($('#movStockId').val()),
      reference_type: $('#movRefType').val(),
      quantity_kg:    parseFloat($('#movQty').val()),
      reference_id:   $('#movRefId').val().trim(),
      notes:          $('#movNotes').val().trim()
    })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) { $('#movementModal').modal('hide'); $('#movementForm')[0].reset(); refreshAll(); }
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });

  /* ── Edit Batch Settings form ── */
  $('#editSettingsForm').on('submit', function (e) {
    e.preventDefault();
    showLoader('Saving…');
    apiPost('/admin/api/stocks/', {
      action:           'update_settings',
      stock_id:         parseInt($('#editSettingsStockId').val()),
      storage_location: $('#editLocation').val().trim(),
      notes:            $('#editNotes').val().trim()
    })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) { $('#editSettingsModal').modal('hide'); loadInventory(); }
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });

  /* ── Add Vendor form ── */
  $('#addVendorForm').on('submit', function (e) {
    e.preventDefault();
    showLoader('Creating…');
    apiPost('/admin/api/stocks/', {
      action:        'create_vendor',
      name:          $('#addVendorName').val().trim(),
      code:          $('#addVendorCode').val().trim().toUpperCase(),
      contact_name:  $('#addVendorContact').val().trim(),
      phone:         $('#addVendorPhone').val().trim(),
      email:         $('#addVendorEmail').val().trim(),
      fssai_number:  $('#addVendorFssai').val().trim(),
      address:       $('#addVendorAddress').val().trim(),
      notes:         $('#addVendorNotes').val().trim()
    })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) { $('#addVendorModal').modal('hide'); $('#addVendorForm')[0].reset(); reloadVendors(); }
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });

  /* ── Edit Vendor form ── */
  $('#editVendorForm').on('submit', function (e) {
    e.preventDefault();
    showLoader('Saving…');
    apiPost('/admin/api/stocks/', {
      action:        'update_vendor',
      id:            parseInt($('#editVendorId').val()),
      name:          $('#editVendorName').val().trim(),
      code:          $('#editVendorCode').val().trim().toUpperCase(),
      contact_name:  $('#editVendorContact').val().trim(),
      phone:         $('#editVendorPhone').val().trim(),
      email:         $('#editVendorEmail').val().trim(),
      fssai_number:  $('#editVendorFssai').val().trim(),
      address:       $('#editVendorAddress').val().trim(),
      notes:         $('#editVendorNotes').val().trim()
    })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) { $('#editVendorModal').modal('hide'); reloadVendors(); }
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
});

/* ══════════════════════════════════════════════ Data loaders ══ */

function loadFlavors(cb) {
  apiGet('/admin/api/flavors/')
    .done(function (res) {
      if (res.success) {
        _allFlavors = (res.data && res.data.flavors) || [];
        populateFlavorSelects();
      }
    })
    .always(function () { if (cb) cb(); });
}

function loadVendorData(cb) {
  apiGet('/admin/api/stocks/', { action: 'vendors' })
    .done(function (res) {
      if (res.success) {
        _allVendors = res.data || [];
        populateVendorSelect();
      }
    })
    .always(function () { if (cb) cb(); });
}

function reloadVendors() {
  apiGet('/admin/api/stocks/', { action: 'vendors' })
    .done(function (res) {
      if (res.success) {
        _allVendors = res.data || [];
        populateVendorSelect();
        renderVendors(_allVendors);
      }
    });
}

function loadStats() {
  apiGet('/admin/api/stocks/', { action: 'stats' })
    .done(function (res) {
      if (!res.success) return;
      $('#statTotalKg').text(Number(res.total_kg).toLocaleString('en-IN') + ' kg');
      $('#statBatches').text(res.total_batches + ' batch' + (res.total_batches !== 1 ? 'es' : '') + ' across ' + res.flavors_tracked + ' flavor' + (res.flavors_tracked !== 1 ? 's' : ''));
      $('#statLowStock').text(res.low_stock);
      $('#statOutOfStock').text(res.out_of_stock);
      $('#statTodayMov').text(res.today_movements);
      if (res.active_alerts > 0) {
        $('#alertsBadge').text(res.active_alerts).show();
      } else {
        $('#alertsBadge').hide();
      }
    });
}

function loadInventory() {
  showLoader('Loading inventory…');
  apiGet('/admin/api/stocks/', { action: 'list' })
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      _stockData = res.data || [];
      renderInventory(_stockData);
    })
    .fail(function () { showAlertModal('Failed to load inventory.', 'danger'); })
    .always(hideLoader);
}

function loadMovements(page) {
  showLoader('Loading movements…');
  apiGet('/admin/api/stocks/', {
    action:         'movements',
    page:           page || 1,
    per_page:       $('#movPerPage').val() || 20,
    flavor_id:      $('#movFlavorFilter').val() || '',
    movement_type:  $('#movTypeFilter').val() || '',
    reference_type: $('#movRefFilter').val() || ''
  })
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      renderMovements(res.data || [], res.total, res.page, res.per_page);
    })
    .fail(function () { showAlertModal('Failed to load movements.', 'danger'); })
    .always(hideLoader);
}

function loadAlerts() {
  showLoader('Loading alerts…');
  apiGet('/admin/api/stocks/', { action: 'alerts' })
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      renderAlerts(res.data || []);
    })
    .fail(function () { showAlertModal('Failed to load alerts.', 'danger'); })
    .always(hideLoader);
}

function loadBatchesForMovement() {
  var flavorId = parseInt($('#movFlavorId').val());
  if (!flavorId) { $('#movStockId').html('<option value="">Select flavor first…</option>'); return; }

  $('#movStockId').html('<option value="">Loading batches…</option>');
  apiGet('/admin/api/stocks/', { action: 'batches', flavor_id: flavorId })
    .done(function (res) {
      if (!res.success || !res.data.length) {
        $('#movStockId').html('<option value="">No stock available for this flavor</option>');
        return;
      }
      var opts = '<option value="">Select batch…</option>';
      res.data.forEach(function (b) {
        var label = b.batch_number ? 'Batch: ' + b.batch_number : 'Unnamed batch';
        if (b.vendor_name) label += ' · ' + b.vendor_name;
        label += ' (' + Number(b.available_kg).toLocaleString('en-IN', {minimumFractionDigits: 3}) + ' kg available)';
        if (b.expiry_date) label += ' — expires ' + b.expiry_date;
        opts += '<option value="' + b.id + '">' + escHtml(label) + '</option>';
      });
      $('#movStockId').html(opts);
    });
}

function refreshAll() {
  loadStats();
  loadInventory();
  if (_movTabLoaded) loadMovements(1);
  if (_altTabLoaded) loadAlerts();
}

/* ══════════════════════════════════════════════ Render ══ */

function renderInventory(stocks) {
  var $tbody = $('#inventoryTableBody');
  if (!stocks || stocks.length === 0) {
    $tbody.html('<tr><td colspan="11" class="text-center" style="padding:40px;color:var(--gray-400)">No stock records yet. Click <strong>Restock</strong> to add the first batch.</td></tr>');
    return;
  }

  var html = '';
  var prevFlavor = null;

  stocks.forEach(function (s) {
    var color = statusColor[s.status] || '#6b7280';
    var label = statusLabel[s.status] || s.status;
    var isNewFlavor = s.flavor_id !== prevFlavor;
    prevFlavor = s.flavor_id;

    /* Flavor cell: only show name for first batch of each flavor */
    var flavorCell = isNewFlavor
      ? '<strong>' + escHtml(s.flavor_name) + '</strong>'
      : '<span style="color:var(--gray-300)">↳</span>';

    var isActive = parseInt(s.is_active_batch) === 1;

    var batchLabel = s.batch_number
      ? '<code style="font-size:0.82rem;background:var(--gray-100);padding:1px 5px;border-radius:4px">' + escHtml(s.batch_number) + '</code>'
      : '<span style="color:var(--gray-400)">—</span>';

    var activeCell = isActive
      ? '<span class="badge badge-success" style="font-size:0.7rem">Active</span>'
      : (s.available_kg > 0
          ? '<button class="btn btn-outline-secondary btn-sm" style="font-size:0.72rem;padding:1px 7px" onclick="setActiveBatch(' + s.id + ')">Set Active</button>'
          : '<span style="color:var(--gray-300);font-size:0.78rem">—</span>');

    var vendorLabel = s.vendor_name
      ? escHtml(s.vendor_name)
      : (s.supplier_name ? escHtml(s.supplier_name) : '<span style="color:var(--gray-400)">—</span>');

    var expiryCell = s.expiry_date
      ? '<span style="font-size:0.82rem;color:' + (new Date(s.expiry_date) < new Date() ? '#dc2626' : 'var(--gray-600)') + '">' + s.expiry_date + '</span>'
      : '<span style="color:var(--gray-400)">—</span>';

    var locationNote = s.storage_location
      ? '<br><small style="color:var(--gray-400)"><i class="fas fa-location-dot me-1"></i>' + escHtml(s.storage_location) + '</small>'
      : '';

    var rowStyle = isActive ? ' style="background:rgba(133,170,78,0.06)"' : '';

    html += '<tr' + rowStyle + '>' +
      '<td>' + flavorCell + locationNote + '</td>' +
      '<td>' + batchLabel + '</td>' +
      '<td>' + activeCell + '</td>' +
      '<td style="font-weight:600">' + Number(s.quantity_kg).toLocaleString('en-IN', {minimumFractionDigits:3}) + '</td>' +
      '<td style="color:var(--gray-500)">' + Number(s.reserved_kg).toLocaleString('en-IN', {minimumFractionDigits:3}) + '</td>' +
      '<td style="font-weight:600;color:' + (s.available_kg <= 0 ? '#dc2626' : 'inherit') + '">' + Number(s.available_kg).toLocaleString('en-IN', {minimumFractionDigits:3}) + '</td>' +
      '<td><span class="badge" style="background:' + color + '">' + label + '</span></td>' +
      '<td style="font-size:0.82rem">' + vendorLabel + '</td>' +
      '<td>' + expiryCell + '</td>' +
      '<td style="font-size:0.8rem;color:var(--gray-400);white-space:nowrap">' + (s.last_restocked_date ? formatDate(s.last_restocked_date) : '—') + '</td>' +
      '<td><div class="table-actions">' +
        '<button class="btn-icon edit" title="Restock this flavor" onclick="openRestockModal(' + s.flavor_id + ')"><i class="fas fa-plus"></i></button>' +
        '<button class="btn-icon" title="Batch settings" onclick="openEditSettingsModal(' + s.id + ')" style="color:var(--gray-400)"><i class="fas fa-gear"></i></button>' +
        '<button class="btn-icon delete" title="Delete batch" onclick="deleteStock(' + s.id + ')"><i class="fas fa-trash"></i></button>' +
      '</div></td>' +
      '</tr>';
  });
  $tbody.html(html);
}

function renderMovements(movements, total, page, perPage) {
  var $tbody = $('#movementsTableBody');
  if (!movements || movements.length === 0) {
    $tbody.html('<tr><td colspan="10" class="text-center" style="padding:40px;color:var(--gray-400)">No movements found.</td></tr>');
    $('#movementsPagination').html('');
    return;
  }

  var html = '';
  movements.forEach(function (m) {
    var mColor = movTypeColor[m.movement_type] || '#6b7280';
    var mLabel = movTypeLabel[m.movement_type] || m.movement_type;
    var rLabel = refTypeLabel[m.reference_type] || m.reference_type;
    var batchLabel = m.batch_number
      ? '<code style="font-size:0.78rem;background:var(--gray-100);padding:1px 4px;border-radius:3px">' + escHtml(m.batch_number) + '</code>'
      : '<span style="color:var(--gray-400)">—</span>';

    html += '<tr>' +
      '<td style="white-space:nowrap;font-size:0.8rem;color:var(--gray-500)">' + formatDate(m.created_at) + '</td>' +
      '<td><strong>' + escHtml(m.flavor_name) + '</strong></td>' +
      '<td>' + batchLabel + '</td>' +
      '<td style="font-size:0.82rem;color:var(--gray-600)">' + (m.vendor_name ? escHtml(m.vendor_name) : '—') + '</td>' +
      '<td><span class="badge" style="background:' + mColor + '">' + mLabel + '</span></td>' +
      '<td style="font-size:0.82rem">' + escHtml(rLabel) +
        (m.reference_id ? '<br><small style="color:var(--gray-400)">' + escHtml(m.reference_id) + '</small>' : '') +
      '</td>' +
      '<td style="font-weight:600">' + Number(m.quantity_kg).toLocaleString('en-IN', {minimumFractionDigits:3}) + '</td>' +
      '<td style="font-size:0.8rem;color:var(--gray-500);max-width:160px">' + (m.notes ? escHtml(m.notes) : '—') + '</td>' +
      '<td style="font-size:0.8rem;color:var(--gray-400)">' + (m.created_by_name ? escHtml(m.created_by_name) : '—') + '</td>' +
      '<td><div class="table-actions"><button class="btn-icon delete" title="Delete movement" onclick="deleteMovement(' + m.id + ')"><i class="fas fa-trash"></i></button></div></td>' +
      '</tr>';
  });
  $tbody.html(html);

  /* pagination */
  var totalPages = Math.ceil(total / perPage);
  if (totalPages <= 1) { $('#movementsPagination').html(''); return; }

  var pg = '<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">';
  pg += '<span style="font-size:0.82rem;color:var(--gray-500);margin-right:4px">' + total + ' records</span>';
  pg += pgBtn('«', 1, page === 1) + pgBtn('‹', page - 1, page === 1);
  var start = Math.max(1, page - 2), end = Math.min(totalPages, page + 2);
  for (var i = start; i <= end; i++) {
    pg += '<button class="btn btn-sm ' + (i === page ? 'btn-primary' : 'btn-outline-secondary') + '" onclick="loadMovements(' + i + ')" style="min-width:32px;padding:2px 8px">' + i + '</button>';
  }
  pg += pgBtn('›', page + 1, page === totalPages) + pgBtn('»', totalPages, page === totalPages);
  pg += '</div>';
  $('#movementsPagination').html(pg);
}

function pgBtn(lbl, target, disabled) {
  return '<button class="btn btn-sm btn-outline-secondary" onclick="loadMovements(' + target + ')" ' +
    (disabled ? 'disabled' : '') + ' style="min-width:32px;padding:2px 8px">' + lbl + '</button>';
}

function renderAlerts(alerts) {
  var $ct = $('#alertsContainer');
  if (!alerts || alerts.length === 0) {
    $ct.html('<div class="text-center" style="padding:40px;color:var(--gray-400)"><i class="fas fa-check-circle" style="font-size:2rem;margin-bottom:8px;display:block"></i>No active alerts — all stock levels are healthy!</div>');
    return;
  }
  var html = '';
  alerts.forEach(function (a) {
    var color = alertTypeColor[a.alert_type] || '#6b7280';
    var label = alertTypeLabel[a.alert_type] || a.alert_type;
    html += '<div style="display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border:1px solid ' + color + '33;border-radius:8px;background:' + color + '08;margin-bottom:10px">' +
      '<div>' +
        '<span class="badge" style="background:' + color + ';margin-bottom:4px">' + label + '</span>' +
        '<div style="font-weight:600;font-size:0.9rem">' + escHtml(a.flavor_name) + '</div>' +
        '<div style="font-size:0.8rem;color:var(--gray-500)">' +
          'Stock: <strong>' + Number(a.current_quantity_kg).toLocaleString('en-IN', {minimumFractionDigits:3}) + ' kg</strong>' +
          (a.reorder_level_kg > 0 ? ' &nbsp;·&nbsp; Reorder at: ' + Number(a.reorder_level_kg).toLocaleString('en-IN', {minimumFractionDigits:3}) + ' kg' : '') +
          (a.expiry_date ? ' &nbsp;·&nbsp; Expiry: ' + a.expiry_date : '') +
        '</div>' +
        '<div style="font-size:0.75rem;color:var(--gray-400);margin-top:2px">' + formatDate(a.created_at) + '</div>' +
      '</div>' +
      '<div style="display:flex;gap:8px;flex-shrink:0">' +
        '<button class="btn btn-sm btn-outline-primary" onclick="openRestockModal()"><i class="fas fa-plus me-1"></i>Restock</button>' +
        '<button class="btn btn-sm btn-outline-secondary" onclick="acknowledgeAlert(' + a.id + ')"><i class="fas fa-check me-1"></i>Dismiss</button>' +
      '</div>' +
      '</div>';
  });
  $ct.html(html);
}

function renderVendors(vendors) {
  var $tbody = $('#vendorsTableBody');
  if (!vendors || vendors.length === 0) {
    $tbody.html('<tr><td colspan="10" class="text-center" style="padding:40px;color:var(--gray-400)">No vendors yet. Add one to link them to stock batches.</td></tr>');
    return;
  }
  var html = '';
  vendors.forEach(function (v) {
    html += '<tr>' +
      '<td><strong>' + escHtml(v.name) + '</strong>' +
        (v.address ? '<br><small style="color:var(--gray-400)">' + escHtml(v.address) + '</small>' : '') +
      '</td>' +
      '<td><code style="font-size:0.82rem;background:var(--gray-100);padding:1px 5px;border-radius:4px">' + escHtml(v.code || '—') + '</code></td>' +
      '<td style="font-size:0.85rem">' + (v.contact_name ? escHtml(v.contact_name) : '—') + '</td>' +
      '<td style="font-size:0.85rem">' + (v.phone ? escHtml(v.phone) : '—') + '</td>' +
      '<td style="font-size:0.85rem">' + (v.email ? escHtml(v.email) : '—') + '</td>' +
      '<td style="font-size:0.82rem;color:var(--gray-600)">' + (v.fssai_number ? escHtml(v.fssai_number) : '—') + '</td>' +
      '<td style="font-weight:600;text-align:center">' + (v.batch_count || 0) + '</td>' +
      '<td style="font-weight:600">' + Number(v.total_stock_kg).toLocaleString('en-IN', {minimumFractionDigits:3}) + ' kg</td>' +
      '<td><span class="badge ' + (parseInt(v.is_active) ? 'badge-success' : 'badge-warning') + '">' + (parseInt(v.is_active) ? 'Active' : 'Inactive') + '</span></td>' +
      '<td><div class="table-actions">' +
        '<button class="btn-icon edit" title="Edit" onclick="openEditVendorModal(' + v.id + ')"><i class="fas fa-pen"></i></button>' +
        '<button class="btn-icon" title="Toggle active" onclick="toggleVendor(' + v.id + ')" style="color:var(--gray-400)"><i class="fas fa-power-off"></i></button>' +
        '<button class="btn-icon delete" title="Delete vendor" onclick="deleteVendor(' + v.id + ', ' + JSON.stringify(escHtml(v.name)) + ')"><i class="fas fa-trash"></i></button>' +
      '</div></td>' +
      '</tr>';
  });
  $tbody.html(html);
}

/* ══════════════════════════════════════════════ Dropdowns ══ */

function populateFlavorSelects() {
  var opts = '<option value="">Select flavor…</option>';
  _allFlavors.forEach(function (f) {
    opts += '<option value="' + f.id + '">' + escHtml(f.name) + '</option>';
  });
  $('#restockFlavorId, #movFlavorId').html(opts);

  var filterOpts = '<option value="">All Flavors</option>';
  _allFlavors.forEach(function (f) {
    filterOpts += '<option value="' + f.id + '">' + escHtml(f.name) + '</option>';
  });
  $('#movFlavorFilter').html(filterOpts);
}

function populateVendorSelect() {
  var opts = '<option value="">— No vendor / enter manually —</option>';
  _allVendors.filter(function (v) { return parseInt(v.is_active); }).forEach(function (v) {
    opts += '<option value="' + v.id + '">' + escHtml(v.name) + '</option>';
  });
  $('#restockVendorId').html(opts);
}

/* ══════════════════════════════════════════════ Modal openers ══ */

function openRestockModal(flavorId) {
  $('#restockForm')[0].reset();
  $('#restockBatch').val('');
  if (flavorId) $('#restockFlavorId').val(flavorId);
  $('#restockModal').modal('show');
}

function generateBatchCode() {
  var vendorId = $('#restockVendorId').val();
  if (!vendorId) { $('#restockBatch').val(''); return; }
  apiGet('/admin/api/stocks/', { action: 'generate_batch_code', vendor_id: vendorId })
    .done(function (res) {
      if (res.success) $('#restockBatch').val(res.batch_code);
    });
}

function openMovementModal(flavorId) {
  $('#movementForm')[0].reset();
  $('#movStockId').html('<option value="">Select flavor first…</option>');
  if (flavorId) {
    $('#movFlavorId').val(flavorId);
    loadBatchesForMovement();
  }
  $('#movementModal').modal('show');
}

function openEditSettingsModal(stockId) {
  var s = _stockData.find(function (x) { return parseInt(x.id) === stockId; });
  if (!s) return;
  $('#editSettingsStockId').val(s.id);
  $('#editSettingsBatchLabel').text(s.flavor_name + (s.batch_number ? ' — ' + s.batch_number : ''));
  $('#editLocation').val(s.storage_location || '');
  $('#editNotes').val(s.notes || '');
  $('#editSettingsModal').modal('show');
}

function openAddVendorModal() {
  $('#addVendorForm')[0].reset();
  $('#addVendorCode').removeClass('is-valid is-invalid');
  $('.code-check-feedback').text('');
  $('#addVendorModal').modal('show');
}

function openEditVendorModal(id) {
  var v = _allVendors.find(function (x) { return parseInt(x.id) === id; });
  if (!v) return;
  $('#editVendorId').val(v.id);
  $('#editVendorName').val(v.name);
  $('#editVendorCode').val(v.code || '');
  $('#editVendorContact').val(v.contact_name || '');
  $('#editVendorPhone').val(v.phone || '');
  $('#editVendorEmail').val(v.email || '');
  $('#editVendorFssai').val(v.fssai_number || '');
  $('#editVendorAddress').val(v.address || '');
  $('#editVendorNotes').val(v.notes || '');
  $('#editVendorModal').modal('show');
}

/* ══════════════════════════════════════════════ Actions ══ */

function acknowledgeAlert(id) {
  showLoader('Dismissing…');
  apiPost('/admin/api/stocks/', { action: 'acknowledge_alert', id: id })
    .done(function (res) {
      showAlertModal(res.message, res.success ? 'success' : 'danger');
      if (res.success) { loadAlerts(); loadStats(); }
    })
    .fail(function () { showAlertModal('Request failed.', 'danger'); })
    .always(hideLoader);
}

function toggleVendor(id) {
  showLoader('Updating…');
  apiPost('/admin/api/stocks/', { action: 'toggle_vendor', id: id })
    .done(function (res) {
      showAlertModal(res.message, res.success ? 'success' : 'danger');
      if (res.success) reloadVendors();
    })
    .fail(function () { showAlertModal('Request failed.', 'danger'); })
    .always(hideLoader);
}

function setActiveBatch(stockId) {
  showLoader('Updating…');
  apiPost('/admin/api/stocks/', { action: 'set_active_batch', stock_id: stockId })
    .done(function (res) {
      showAlertModal(res.message, res.success ? 'success' : 'danger');
      if (res.success) refreshAll();
    })
    .fail(function () { showAlertModal('Request failed.', 'danger'); })
    .always(hideLoader);
}

function deleteVendor(id, name) {
  confirm('Delete vendor "' + name + '"? This cannot be undone.\n\nNote: deletion is blocked if inventory is linked to this vendor.')
    .then(function () {
      showLoader('Deleting…');
      apiPost('/admin/api/stocks/', { action: 'delete_vendor', id: id })
        .done(function (res) {
          showAlertModal(res.message, res.success ? 'success' : 'danger');
          if (res.success) reloadVendors();
        })
        .fail(function () { showAlertModal('Request failed.', 'danger'); })
        .always(hideLoader);
    }).catch(function () {});
}

function deleteStock(stockId) {
  confirm('Delete this inventory batch? Its movement history will also be removed. This cannot be undone.')
    .then(function () {
      showLoader('Deleting…');
      apiPost('/admin/api/stocks/', { action: 'delete_stock', stock_id: stockId })
        .done(function (res) {
          showAlertModal(res.message, res.success ? 'success' : 'danger');
          if (res.success) refreshAll();
        })
        .fail(function () { showAlertModal('Request failed.', 'danger'); })
        .always(hideLoader);
    }).catch(function () {});
}

function deleteMovement(id) {
  confirm('Delete this movement record? This cannot be undone.')
    .then(function () {
      showLoader('Deleting…');
      apiPost('/admin/api/stocks/', { action: 'delete_movement', id: id })
        .done(function (res) {
          showAlertModal(res.message, res.success ? 'success' : 'danger');
          if (res.success) { loadMovements(1); loadStats(); }
        })
        .fail(function () { showAlertModal('Request failed.', 'danger'); })
        .always(hideLoader);
    }).catch(function () {});
}

/* ── Vendor code uniqueness check (debounced) ── */
var _codeCheckTimer = null;
$(document).on('input', '#addVendorCode, #editVendorCode', function () {
  var $input = $(this);
  var $feedback = $input.siblings('.code-check-feedback');
  if (!$feedback.length) {
    $feedback = $('<div class="form-text code-check-feedback"></div>').insertAfter($input.siblings('.form-text').last().length ? $input.siblings('.form-text').last() : $input);
  }
  var code = $input.val().trim().toUpperCase();
  $input.val(code);
  clearTimeout(_codeCheckTimer);
  if (!code) { $feedback.text(''); $input.removeClass('is-valid is-invalid'); return; }
  $feedback.text('Checking…').css('color', 'var(--gray-400)');
  var excludeId = parseInt($('#editVendorId').val()) || 0;
  _codeCheckTimer = setTimeout(function () {
    apiGet('/admin/api/stocks/', { action: 'check_vendor_code', code: code, exclude_id: excludeId })
      .done(function (res) {
        if (res.data && res.data.available) {
          $input.removeClass('is-invalid').addClass('is-valid');
          $feedback.text('Code is available.').css('color', '#16a34a');
        } else {
          $input.removeClass('is-valid').addClass('is-invalid');
          $feedback.text('Code already taken.').css('color', '#dc2626');
        }
      })
      .fail(function () { $feedback.text(''); });
  }, 400);
});
