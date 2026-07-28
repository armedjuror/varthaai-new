/* Varthaai Admin — B2B Offers */

var allOffers = [];

$(function () {
  loadOffers();
  loadCompanies();

  $('#offerForm').on('submit', function (e) {
    e.preventDefault();
    saveOffer();
  });
});

function loadCompanies() {
  apiGet('/admin/api/b2b/', { view: 'companies' }).done(function (res) {
    if (!res.success) return;
    var opts = '<option value="">Global (all clients)</option>';
    ((res.data && res.data.companies) || []).forEach(function (c) {
      if (c.stage === 'converted') {
        opts += '<option value="' + c.id + '">' + escHtml(c.company_name) + '</option>';
      }
    });
    $('#offerCompany').html(opts);
  });
}

function loadOffers() {
  apiGet('/admin/api/b2b-offers/')
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      allOffers = res.data || [];
      renderOffers();
    })
    .fail(function () { showAlertModal('Failed to load offers.', 'danger'); });
}

function renderOffers() {
  if (!allOffers.length) {
    $('#offersTableBody').html('<tr><td colspan="8" class="text-center" style="padding:40px;color:var(--gray-400)"><i class="fas fa-gift fa-2x mb-2 d-block"></i>No offers yet</td></tr>');
    return;
  }
  var html = '';
  allOffers.forEach(function (o) {
    var active = parseInt(o.is_active);
    var typeLabel = o.type === 'buy_x_get_y' ? 'Buy X Get Y' : (o.type === 'discount_percent' ? 'Discount %' : 'Flat Discount');
    var details = '';
    if (o.type === 'buy_x_get_y') details = 'Buy ' + o.min_quantity + ', get ' + o.free_quantity + ' free';
    else if (o.type === 'discount_percent') details = o.discount_value + '% off';
    else details = formatCurrency(o.discount_value) + ' off';

    var validity = '—';
    if (o.valid_from || o.valid_to) {
      validity = (o.valid_from ? formatDate(o.valid_from) : '…') + ' to ' + (o.valid_to ? formatDate(o.valid_to) : '…');
    }

    html += '<tr>' +
      '<td style="font-weight:600">' + escHtml(o.name) + '</td>' +
      '<td><span style="font-size:0.78rem;background:var(--gray-100);padding:2px 8px;border-radius:10px">' + typeLabel + '</span></td>' +
      '<td style="font-size:0.85rem">' + details + '</td>' +
      '<td style="font-size:0.85rem">' + (o.company_name ? escHtml(o.company_name) : '<span style="color:var(--gray-400)">Global</span>') + '</td>' +
      '<td style="font-size:0.82rem">' + validity + '</td>' +
      '<td>' + (parseInt(o.is_stackable) ? '<i class="fas fa-check text-success"></i>' : '<i class="fas fa-times text-muted"></i>') + '</td>' +
      '<td><span class="badge ' + (active ? 'badge-success' : 'badge-warning') + '">' + (active ? 'Active' : 'Inactive') + '</span></td>' +
      '<td class="table-actions">' +
        '<button class="btn-icon edit" title="Edit" onclick="editOffer(' + o.id + ')"><i class="fas fa-pen"></i></button>' +
        '<button class="btn-icon" title="' + (active ? 'Deactivate' : 'Activate') + '" onclick="toggleOffer(' + o.id + ')"><i class="fas fa-' + (active ? 'ban' : 'check') + '"></i></button>' +
        '<button class="btn-icon delete" title="Delete" onclick="deleteOffer(' + o.id + ')"><i class="fas fa-trash"></i></button>' +
      '</td>' +
    '</tr>';
  });
  $('#offersTableBody').html(html);
}

function toggleOfferFields() {
  var type = $('#offerType').val();
  if (type === 'buy_x_get_y') {
    $('#minQtyWrap, #freeQtyWrap').show();
    $('#discValWrap').hide();
  } else {
    $('#minQtyWrap, #freeQtyWrap').hide();
    $('#discValWrap').show();
  }
}

function resetOfferForm() {
  $('#offerId').val('');
  $('#offerModalTitle').text('New Offer');
  $('#offerForm')[0].reset();
  $('#offerType').val('buy_x_get_y');
  toggleOfferFields();
}

function editOffer(id) {
  var o = allOffers.find(function (x) { return parseInt(x.id) === id; });
  if (!o) return;
  $('#offerId').val(o.id);
  $('#offerModalTitle').text('Edit Offer');
  $('#offerName').val(o.name);
  $('#offerDesc').val(o.description || '');
  $('#offerType').val(o.type);
  $('#offerCompany').val(o.company_id || '');
  $('#offerMinQty').val(o.min_quantity || '');
  $('#offerFreeQty').val(o.free_quantity || '');
  $('#offerDiscVal').val(o.discount_value || '');
  $('#offerStackable').prop('checked', parseInt(o.is_stackable));
  $('#offerFrom').val(o.valid_from || '');
  $('#offerTo').val(o.valid_to || '');
  toggleOfferFields();
  $('#offerModal').modal('show');
}

function saveOffer() {
  var isEdit = !!$('#offerId').val();
  var data = {
    action:         isEdit ? 'edit' : 'add',
    name:           $('#offerName').val(),
    description:    $('#offerDesc').val(),
    type:           $('#offerType').val(),
    company_id:     $('#offerCompany').val() || null,
    min_quantity:   parseInt($('#offerMinQty').val()) || null,
    free_quantity:  parseInt($('#offerFreeQty').val()) || null,
    discount_value: parseFloat($('#offerDiscVal').val()) || null,
    is_stackable:   $('#offerStackable').is(':checked') ? 1 : 0,
    valid_from:     $('#offerFrom').val() || null,
    valid_to:       $('#offerTo').val() || null
  };
  if (isEdit) data.id = parseInt($('#offerId').val());

  showLoader('Saving…');
  apiPost('/admin/api/b2b-offers/', data)
    .done(function (res) {
      showAlertModal(res.message, res.success ? 'success' : 'danger');
      if (res.success) { $('#offerModal').modal('hide'); loadOffers(); }
    })
    .fail(function () { showAlertModal('Request failed.', 'danger'); })
    .always(hideLoader);
}

function toggleOffer(id) {
  apiPost('/admin/api/b2b-offers/', { action: 'toggle', id: id })
    .done(function (res) {
      if (res.success) loadOffers();
      else showAlertModal(res.message, 'danger');
    });
}

function deleteOffer(id) {
  confirmThen('Delete this offer?', function () {
    showLoader('Deleting…');
    apiPost('/admin/api/b2b-offers/', { action: 'delete', id: id })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) loadOffers();
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
}
