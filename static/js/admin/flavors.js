/* Varthaai Admin — Flavors Page */

var allFlavors = [];

/* ── Nutrition rows helpers ── */
function addNutritionRow(prefix, key, val) {
  var row = $('<div class="d-flex gap-2 nutrition-row">' +
    '<input type="text" class="form-control form-control-sm nutrition-key" placeholder="e.g. Calories" value="' + escHtml(key) + '">' +
    '<input type="text" class="form-control form-control-sm nutrition-val" placeholder="e.g. 200 kcal" value="' + escHtml(val) + '">' +
    '<button type="button" class="btn btn-sm btn-outline-danger flex-shrink-0" style="padding:2px 8px" onclick="$(this).closest(\'.nutrition-row\').remove()">' +
      '<i class="fas fa-times"></i>' +
    '</button>' +
  '</div>');
  $('#' + prefix + 'NutritionRows').append(row);
}

function collectNutrition(prefix) {
  var obj = {};
  $('#' + prefix + 'NutritionRows .nutrition-row').each(function () {
    var k = $(this).find('.nutrition-key').val().trim();
    var v = $(this).find('.nutrition-val').val().trim();
    if (k) obj[k] = v;
  });
  return Object.keys(obj).length ? JSON.stringify(obj) : '';
}

function populateNutrition(prefix, json) {
  $('#' + prefix + 'NutritionRows').empty();
  if (!json) return;
  try {
    var nf = JSON.parse(json);
    Object.keys(nf).forEach(function (k) { addNutritionRow(prefix, k, nf[k]); });
  } catch (e) {}
}

$(function () {
  loadFlavors();

  // Clear add modal nutrition rows on open
  $('#addFlavorModal').on('show.bs.modal', function () {
    $('#addNutritionRows').empty();
  });

  $('#addFlavorForm').on('submit', function (e) {
    e.preventDefault();
    var data = {
      action:            'add',
      name:              $('#addName').val(),
      description:       $('#addDescription').val(),
      price_per_kg:      parseFloat($('#addPrice').val()),
      sale_price_per_kg: parseFloat($('#addSalePrice').val()),
      reorder_level_kg:  parseFloat($('#addReorderKg').val()) || 5,
      is_active:         $('#addIsActive').is(':checked') ? 1 : 0,
      image:             $('#addImage').val().trim(),
      ingredients:       $('#addIngredients').val().trim(),
      nutrition_fact: collectNutrition('add')
    };
    showLoader('Adding flavor…');
    apiPost('/admin/api/flavors/', data)
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) { $('#addFlavorModal').modal('hide'); $('#addFlavorForm')[0].reset(); loadFlavors(); }
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });

  $('#editFlavorForm').on('submit', function (e) {
    e.preventDefault();
    var data = {
      action:            'edit',
      id:                parseInt($('#editFlavorId').val()),
      name:              $('#editName').val(),
      description:       $('#editDescription').val(),
      price_per_kg:      parseFloat($('#editPrice').val()),
      sale_price_per_kg: parseFloat($('#editSalePrice').val()),
      reorder_level_kg:  parseFloat($('#editReorderKg').val()) || 5,
      is_active:         $('#editIsActive').is(':checked') ? 1 : 0,
      image:             $('#editImage').val().trim(),
      ingredients:       $('#editIngredients').val().trim(),
      nutrition_fact: collectNutrition('edit')
    };
    showLoader('Saving…');
    apiPost('/admin/api/flavors/', data)
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) { $('#editFlavorModal').modal('hide'); loadFlavors(); }
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
});

function loadFlavors() {
  showLoader('Loading flavors…');
  apiGet('/admin/api/flavors/')
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      var d = res.data || {};
      allFlavors = d.flavors || [];
      renderStats(d.stats);
      renderFlavors(allFlavors);
    })
    .fail(function () { showAlertModal('Failed to load flavors.', 'danger'); })
    .always(hideLoader);
}

function renderStats(s) {
  if (!s) return;
  $('#statTotal').text(s.total_flavors || 0);
  $('#statActive').text(s.active_flavors || 0);
  $('#statAvgPrice').text(s.avg_price ? '₹' + Number(s.avg_price).toLocaleString('en-IN') : '—');
  $('#statMaxPrice').text(s.max_price ? '₹' + Number(s.max_price).toLocaleString('en-IN') : '—');
}

function renderFlavors(flavors) {
  var $grid = $('#flavorsGrid');
  if (!flavors || flavors.length === 0) {
    $grid.html('<div class="col-12"><div class="empty-state"><i class="fas fa-pepper-hot"></i>No flavors found</div></div>');
    return;
  }
  var html = '';
  flavors.forEach(function (f) {
    var active = parseInt(f.is_active) === 1;

    // Image — resolve relative paths against site root; data-card overflow:hidden clips to border-radius
    var imgSrc = f.image
      ? (/^(https?:)?\/\//i.test(f.image) ? f.image : '/' + f.image.replace(/^\//, ''))
      : null;
    var thumb = imgSrc
      ? '<div style="height:120px;background:url(\'' + imgSrc.replace(/'/g, '%27') + '\') center/cover no-repeat;flex-shrink:0"></div>'
      : '';

    // Compact nutrition facts — up to 3 entries
    var nutritionHtml = '';
    if (f.nutrition_fact) {
      try {
        var nf = JSON.parse(f.nutrition_fact);
        var keys = Object.keys(nf).slice(0, 3);
        if (keys.length) {
          nutritionHtml = '<div style="font-size:0.72rem;color:var(--gray-400);margin-top:6px;border-top:1px solid var(--gray-100);padding-top:6px">' +
            keys.map(function (k) {
              return '<span style="white-space:nowrap"><span style="color:var(--gray-500)">' + escHtml(k) + ':</span> <strong>' + escHtml(String(nf[k])) + '</strong></span>';
            }).join(' &nbsp;·&nbsp; ') +
          '</div>';
        }
      } catch (e) {}
    }

    html += '<div class="col-sm-6 col-lg-4 col-xl-3">' +
      '<div class="data-card h-100" style="padding:0;display:flex;flex-direction:column">' +
        thumb +
        '<div style="padding:20px;flex:1;display:flex;flex-direction:column">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">' +
          '<div>' +
            '<div style="font-weight:700;font-size:0.9rem;color:var(--gray-900)">' + escHtml(f.name) + '</div>' +
            '<span class="badge ' + (active ? 'badge-success' : 'badge-warning') + '">' + (active ? 'Active' : 'Inactive') + '</span>' +
          '</div>' +
          '<div class="table-actions">' +
            '<button class="btn-icon edit" title="Edit" onclick="openEditFlavor(' + f.id + ')"><i class="fas fa-pen"></i></button>' +
            '<button class="btn-icon delete" title="Delete" onclick="deleteFlavor(' + f.id + ')"><i class="fas fa-trash"></i></button>' +
          '</div>' +
        '</div>' +
        '<div style="font-size:0.8rem;color:var(--gray-500);margin-bottom:10px;min-height:36px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">' +
          (f.description ? escHtml(f.description) : '<em>No description</em>') +
        '</div>' +
        '<div style="border-top:1px solid var(--gray-100);padding-top:10px">' +
          '<div style="display:flex;justify-content:space-between;margin-bottom:4px">' +
            '<span style="font-size:0.75rem;color:var(--gray-400)">MRP</span>' +
            '<span style="font-weight:600;font-size:0.85rem">₹' + Number(f.price_per_kg).toLocaleString('en-IN') + '/kg</span>' +
          '</div>' +
          '<div style="display:flex;justify-content:space-between;margin-bottom:4px">' +
            '<span style="font-size:0.75rem;color:var(--gray-400)">Sale Price</span>' +
            '<span style="font-weight:700;color:var(--green);font-size:0.9rem">₹' + Number(f.sale_price_per_kg).toLocaleString('en-IN') + '/kg</span>' +
          '</div>' +
          '<div style="display:flex;justify-content:space-between">' +
            '<span style="font-size:0.75rem;color:var(--gray-400)">Sold</span>' +
            '<span style="font-size:0.8rem;font-weight:600">' + (f.total_quantity_sold || 0) + ' kg</span>' +
          '</div>' +
          nutritionHtml +
        '</div>' +
        '<div class="d-flex gap-2 mt-3">' +
          '<button class="btn btn-sm flex-fill btn-outline-primary" onclick="event.stopPropagation();openPacks(' + f.id + ')">' +
            '<i class="fas fa-box me-1"></i>Packs' +
          '</button>' +
          '<button class="btn btn-sm flex-fill ' + (active ? 'btn-outline-secondary' : 'btn-outline-primary') + '" onclick="toggleFlavor(' + f.id + ')">' +
            '<i class="fas fa-' + (active ? 'ban' : 'check') + ' me-1"></i>' + (active ? 'Deactivate' : 'Activate') +
          '</button>' +
        '</div>' +
      '</div>' + // close inner padding div
      '</div></div>';
  });
  $grid.html(html);
}

function openEditFlavor(id) {
  var f = allFlavors.find(function (x) { return parseInt(x.id) === id; });
  if (!f) return;
  $('#editFlavorId').val(f.id);
  $('#editName').val(f.name);
  $('#editDescription').val(f.description || '');
  $('#editPrice').val(f.price_per_kg);
  $('#editSalePrice').val(f.sale_price_per_kg);
  $('#editReorderKg').val(f.reorder_level_grams ? (f.reorder_level_grams / 1000) : 5);
  $('#editIsActive').prop('checked', parseInt(f.is_active) === 1);
  $('#editImage').val(f.image || '');
  $('#editIngredients').val(f.ingredients || '');
  populateNutrition('edit', f.nutrition_fact);
  $('#editFlavorModal').modal('show');
}

function toggleFlavor(id) {
  showLoader('Updating…');
  apiPost('/admin/api/flavors/', { action: 'toggle', id: id })
    .done(function (res) {
      showAlertModal(res.message, res.success ? 'success' : 'danger');
      if (res.success) loadFlavors();
    })
    .fail(function () { showAlertModal('Request failed.', 'danger'); })
    .always(hideLoader);
}

function deleteFlavor(id) {
  var f = allFlavors.find(function (x) { return parseInt(x.id) === id; });
  confirmThen('Delete flavor "' + (f ? escHtml(f.name) : id) + '"? This cannot be undone.', function () {
    showLoader('Deleting…');
    apiPost('/admin/api/flavors/', { action: 'delete', id: id })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) loadFlavors();
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
}

/* ── Packs ── */
var packsFlavorId = null;

function openPacks(flavorId) {
  packsFlavorId = flavorId;
  var f = allFlavors.find(function (x) { return parseInt(x.id) === flavorId; });
  $('#packsFlavorName').text(f ? f.name : '#' + flavorId);
  $('#packWeight, #packLabel, #packMrp, #packSelling, #packCost').val('');
  loadPacks();
  $('#packsModal').modal('show');
}

function loadPacks() {
  apiGet('/admin/api/flavors/', { flavor_packs: packsFlavorId })
    .done(function (res) {
      if (!res.success) return;
      renderPacks(res.data || []);
    });
}

function renderPacks(packs) {
  if (!packs.length) {
    $('#packsTableBody').html('<tr><td colspan="8" class="text-center" style="color:var(--gray-400);padding:20px">No packs yet. Add one above.</td></tr>');
    return;
  }
  var html = '';
  packs.forEach(function (p) {
    var active = parseInt(p.is_active);
    html += '<tr>' +
      '<td>' + p.weight_grams + 'g</td>' +
      '<td>' + escHtml(p.label) + '</td>' +
      '<td>' + formatCurrency(p.mrp) + '</td>' +
      '<td>' + formatCurrency(p.selling_price) + '</td>' +
      '<td>' + formatCurrency(p.cost_price) + '</td>' +
      '<td>' + (p.sku ? escHtml(p.sku) : '—') + '</td>' +
      '<td><span class="badge ' + (active ? 'badge-success' : 'badge-warning') + '">' + (active ? 'Active' : 'Inactive') + '</span></td>' +
      '<td class="table-actions">' +
        '<button class="btn-icon delete" title="Delete" onclick="deletePack(' + p.id + ')"><i class="fas fa-trash"></i></button>' +
      '</td>' +
    '</tr>';
  });
  $('#packsTableBody').html(html);
}

function addPack() {
  var weight = parseInt($('#packWeight').val());
  var label  = $('#packLabel').val().trim();
  if (!weight || !label) { showAlertModal('Weight and label are required.', 'warning'); return; }

  showLoader('Adding pack…');
  apiPost('/admin/api/flavors/', {
    action:        'add_pack',
    flavor_id:     packsFlavorId,
    weight_grams:  weight,
    label:         label,
    mrp:           parseFloat($('#packMrp').val()) || 0,
    selling_price: parseFloat($('#packSelling').val()) || 0,
    cost_price:    parseFloat($('#packCost').val()) || 0
  })
    .done(function (res) {
      showAlertModal(res.message, res.success ? 'success' : 'danger');
      if (res.success) {
        $('#packWeight, #packLabel, #packMrp, #packSelling, #packCost').val('');
        loadPacks();
      }
    })
    .fail(function () { showAlertModal('Request failed.', 'danger'); })
    .always(hideLoader);
}

function deletePack(id) {
  confirmThen('Delete this pack?', function () {
    showLoader('Deleting…');
    apiPost('/admin/api/flavors/', { action: 'delete_pack', id: id })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) loadPacks();
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
}
