/* Varthaai Admin — Packs Page */

var allPacks = [];
var flavorOptions = [];

$(function () {
  loadFlavorOptions(function () {
    loadPacks();
  });

  $('#addPackModal').on('hidden.bs.modal', function () {
    $('#packForm')[0].reset();
    $('#packId').val('');
    $('#packModalTitle').html('<i class="fas fa-box me-2"></i>Add Pack');
  });

  $('#packForm').on('submit', function (e) {
    e.preventDefault();
    var id = $('#packId').val();
    var payload = {
      action:        id ? 'edit' : 'add',
      flavor_id:     parseInt($('#packFlavorId').val()),
      weight_grams:  parseInt($('#packWeight').val()),
      label:         $('#packLabel').val().trim(),
      mrp:           parseFloat($('#packMrp').val()) || 0,
      selling_price: parseFloat($('#packSelling').val()) || 0,
      cost_price:    parseFloat($('#packCost').val()) || 0,
      sku:           $('#packSku').val().trim(),
      is_active:     $('#packIsActive').is(':checked') ? 1 : 0
    };
    if (id) payload.id = parseInt(id);

    if (!payload.flavor_id || !payload.weight_grams || !payload.label) {
      showAlertModal('Flavor, weight and label are required.', 'warning');
      return;
    }

    showLoader(id ? 'Saving…' : 'Adding pack…');
    apiPost('/admin/api/packs/', payload)
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) { $('#addPackModal').modal('hide'); loadPacks(); }
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
});

function loadFlavorOptions(cb) {
  apiGet('/admin/api/packs/', { action: 'flavor_options' })
    .done(function (res) {
      if (!res.success) return;
      flavorOptions = res.data || [];
      var opts = '<option value="">Select a flavor…</option>';
      flavorOptions.forEach(function (f) {
        opts += '<option value="' + f.id + '">' + escHtml(f.name) + '</option>';
      });
      $('#packFlavorId').html(opts);
    })
    .always(function () { if (cb) cb(); });
}

function loadPacks() {
  showLoader('Loading packs…');
  apiGet('/admin/api/packs/')
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      allPacks = res.data || [];
      renderPacks(allPacks);
    })
    .fail(function () { showAlertModal('Failed to load packs.', 'danger'); })
    .always(hideLoader);
}

function renderPacks(packs) {
  var $tbody = $('#packsTableBody');
  if (!packs || packs.length === 0) {
    $tbody.html('<tr><td colspan="9" class="text-center" style="color:var(--gray-400);padding:30px">No packs yet. Add one above.</td></tr>');
    return;
  }
  var html = '';
  packs.forEach(function (p) {
    var active = parseInt(p.is_active) === 1;
    html += '<tr>' +
      '<td>' + escHtml(p.flavor_name) + '</td>' +
      '<td>' + p.weight_grams + 'g</td>' +
      '<td>' + escHtml(p.label) + '</td>' +
      '<td>' + formatCurrency(p.mrp) + '</td>' +
      '<td>' + formatCurrency(p.selling_price) + '</td>' +
      '<td>' + formatCurrency(p.cost_price) + '</td>' +
      '<td>' + (p.sku ? escHtml(p.sku) : '—') + '</td>' +
      '<td><span class="badge ' + (active ? 'badge-success' : 'badge-warning') + '">' + (active ? 'Active' : 'Inactive') + '</span></td>' +
      '<td class="table-actions">' +
        '<button class="btn-icon edit" title="Edit" onclick="openEditPack(' + p.id + ')"><i class="fas fa-pen"></i></button>' +
        '<button class="btn-icon" title="' + (active ? 'Deactivate' : 'Activate') + '" onclick="togglePack(' + p.id + ')"><i class="fas fa-' + (active ? 'ban' : 'check') + '"></i></button>' +
        '<button class="btn-icon delete" title="Delete" onclick="deletePack(' + p.id + ')"><i class="fas fa-trash"></i></button>' +
      '</td>' +
    '</tr>';
  });
  $tbody.html(html);
}

function openAddPack() {
  $('#packForm')[0].reset();
  $('#packId').val('');
  $('#packModalTitle').html('<i class="fas fa-box me-2"></i>Add Pack');
  $('#packIsActive').prop('checked', true);
}

function openEditPack(id) {
  var p = allPacks.find(function (x) { return parseInt(x.id) === id; });
  if (!p) return;
  $('#packId').val(p.id);
  $('#packFlavorId').val(p.flavor_id);
  $('#packWeight').val(p.weight_grams);
  $('#packLabel').val(p.label);
  $('#packMrp').val(p.mrp);
  $('#packSelling').val(p.selling_price);
  $('#packCost').val(p.cost_price);
  $('#packSku').val(p.sku || '');
  $('#packIsActive').prop('checked', parseInt(p.is_active) === 1);
  $('#packModalTitle').html('<i class="fas fa-pen me-2"></i>Edit Pack');
  $('#addPackModal').modal('show');
}

function togglePack(id) {
  showLoader('Updating…');
  apiPost('/admin/api/packs/', { action: 'toggle', id: id })
    .done(function (res) {
      showAlertModal(res.message, res.success ? 'success' : 'danger');
      if (res.success) loadPacks();
    })
    .fail(function () { showAlertModal('Request failed.', 'danger'); })
    .always(hideLoader);
}

function deletePack(id) {
  var p = allPacks.find(function (x) { return parseInt(x.id) === id; });
  confirmThen('Delete pack "' + (p ? escHtml(p.label) : id) + '"? This cannot be undone.', function () {
    showLoader('Deleting…');
    apiPost('/admin/api/packs/', { action: 'delete', id: id })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) loadPacks();
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
}
