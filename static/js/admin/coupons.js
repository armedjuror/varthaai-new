/* Varthaai Admin — Coupons Page */

var _couponsData = [];

$(function () {
  loadCoupons();

  // Discount type toggle — Add modal
  $('#discountType').on('change', function () {
    var t = $(this).val();
    $('#amountField, #percentageField, #maxDiscountField').hide();
    if (t === 'amount')          { $('#amountField').show(); }
    else if (t === 'percentage') { $('#percentageField, #maxDiscountField').show(); }
  });

  // Discount type toggle — Edit modal
  $('#editDiscountType').on('change', function () {
    var t = $(this).val();
    $('#editAmountField, #editPercentageField, #editMaxDiscountField').hide();
    if (t === 'amount')          { $('#editAmountField').show(); }
    else if (t === 'percentage') { $('#editPercentageField, #editMaxDiscountField').show(); }
  });

  // Default dates when Add modal opens
  $('#addCouponModal').on('show.bs.modal', function () {
    var now = new Date();
    var nextMonth = new Date(now.getTime() + 30 * 24 * 3600000);
    function fmt(d) { return d.toISOString().slice(0, 16); }
    $('#addStartDate').val(fmt(now));
    $('#addExpiryDate').val(fmt(nextMonth));
  });

  // Add form submit
  $('#addCouponForm').on('submit', function (e) {
    e.preventDefault();
    var data = {
      action:              'add',
      code:                $('#addCode').val(),
      discount_amount:     $('#addDiscountAmount').val(),
      discount_percentage: $('#addDiscountPct').val(),
      min_order_value:     parseFloat($('#addMinOrder').val()) || 0,
      min_quantity:        parseFloat($('#addMinQty').val()) || 0,
      max_discount_amount: $('#addMaxDiscount').val(),
      max_uses:            $('#addMaxUses').val(),
      start_date:          $('#addStartDate').val(),
      expiry_date:         $('#addExpiryDate').val(),
      valid_flavors:       $('#addValidFlavors').val()
    };
    showLoader('Adding coupon…');
    apiPost('/admin/api/coupons/', data)
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) { $('#addCouponModal').modal('hide'); $('#addCouponForm')[0].reset(); loadCoupons(); }
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });

  // Edit form submit
  $('#editCouponForm').on('submit', function (e) {
    e.preventDefault();
    var data = {
      action:              'edit',
      id:                  parseInt($('#editCouponId').val()),
      code:                $('#editCode').val(),
      discount_amount:     $('#editDiscountAmount').val(),
      discount_percentage: $('#editDiscountPct').val(),
      min_order_value:     parseFloat($('#editMinOrder').val()) || 0,
      min_quantity:        parseFloat($('#editMinQty').val()) || 0,
      max_discount_amount: $('#editMaxDiscount').val(),
      max_uses:            $('#editMaxUses').val(),
      start_date:          $('#editStartDate').val(),
      expiry_date:         $('#editExpiryDate').val(),
      valid_flavors:       $('#editValidFlavors').val()
    };
    showLoader('Saving coupon…');
    apiPost('/admin/api/coupons/', data)
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) { $('#editCouponModal').modal('hide'); loadCoupons(); }
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
});

function loadCoupons() {
  showLoader('Loading coupons…');
  apiGet('/admin/api/coupons/')
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      _couponsData = res.data.coupons || [];
      renderStats(res.data.stats);
      renderCoupons(_couponsData);
      populateFlavorsSelect(res.data.flavors);
    })
    .fail(function () { showAlertModal('Failed to load coupons.', 'danger'); })
    .always(hideLoader);
}

function renderStats(s) {
  if (!s) return;
  $('#statTotal').text(s.total_coupons || 0);
  $('#statActive').text(s.active_coupons || 0);
  $('#statValid').text(s.valid_coupons || 0);
  $('#statExpired').text(s.expired_coupons || 0);
}

function populateFlavorsSelect(flavors) {
  var html = '';
  (flavors || []).forEach(function (f) {
    html += '<option value="' + f.id + '">' + escHtml(f.name) + '</option>';
  });
  $('#addValidFlavors, #editValidFlavors').html(html);
}

function renderCoupons(coupons) {
  var $grid = $('#couponsGrid');
  if (!coupons || coupons.length === 0) {
    $grid.html('<div class="col-12"><div class="empty-state"><i class="fas fa-ticket"></i>No coupons found</div></div>');
    return;
  }
  var now = new Date();
  var html = '';
  coupons.forEach(function (c) {
    var active  = parseInt(c.is_active) === 1;
    var expired = c.expiry_date && new Date(c.expiry_date) < now;
    var discountText = c.discount_amount
      ? '₹' + Number(c.discount_amount).toLocaleString('en-IN') + ' off'
      : (c.discount_percentage ? c.discount_percentage + '% off' : '—');

    var statusClass = expired ? 'badge-danger' : (active ? 'badge-success' : 'badge-warning');
    var statusLabel = expired ? 'Expired' : (active ? 'Active' : 'Inactive');
    var borderColor = active && !expired ? 'var(--green)' : 'var(--gray-300)';

    html += '<div class="col-sm-6 col-lg-4">' +
      '<div class="data-card" style="padding:0;overflow:hidden">' +
        '<div style="padding:16px 20px;border-left:4px solid ' + borderColor + '">' +
          '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">' +
            '<div style="font-family:monospace;font-size:1.1rem;font-weight:800;color:var(--gray-900);letter-spacing:1px">' + escHtml(c.code) + '</div>' +
            '<div style="display:flex;gap:6px;align-items:center">' +
              '<span class="badge ' + statusClass + '">' + statusLabel + '</span>' +
              '<button class="btn-icon edit" title="Edit" onclick="editCoupon(' + c.id + ')"><i class="fas fa-pen"></i></button>' +
              '<button class="btn-icon view" title="' + (active ? 'Deactivate' : 'Activate') + '" onclick="toggleCoupon(' + c.id + ')"><i class="fas fa-' + (active ? 'pause' : 'play') + '"></i></button>' +
              '<button class="btn-icon delete" title="Delete" onclick="deleteCoupon(' + c.id + ')"><i class="fas fa-trash"></i></button>' +
            '</div>' +
          '</div>' +
          '<div style="font-size:1.5rem;font-weight:800;color:var(--green);margin-bottom:6px">' + discountText + '</div>' +
          '<div style="font-size:0.78rem;color:var(--gray-400);display:flex;flex-wrap:wrap;gap:10px">' +
            (c.min_order_value > 0 ? '<span><i class="fas fa-receipt me-1"></i>Min ₹' + Number(c.min_order_value).toLocaleString('en-IN') + '</span>' : '') +
            (c.min_quantity > 0 ? '<span><i class="fas fa-weight me-1"></i>Min ' + c.min_quantity + 'g</span>' : '') +
            (c.max_uses ? '<span><i class="fas fa-users me-1"></i>Max ' + c.max_uses + ' uses</span>' : '') +
          '</div>' +
        '</div>' +
        '<div style="padding:10px 20px;background:var(--gray-50);border-top:1px solid var(--gray-100);display:flex;justify-content:space-between;font-size:0.78rem;color:var(--gray-400)">' +
          '<span><i class="fas fa-chart-bar me-1"></i>' + (c.total_uses || 0) + ' uses</span>' +
          '<span><i class="fas fa-calendar me-1"></i>' + (c.expiry_date ? formatDate(c.expiry_date) : 'No expiry') + '</span>' +
        '</div>' +
      '</div></div>';
  });
  $grid.html(html);
}

function editCoupon(id) {
  var c = _couponsData.find(function (x) { return parseInt(x.id) === id; });
  if (!c) return;

  $('#editCouponId').val(c.id);
  $('#editCode').val(c.code);
  $('#editMinOrder').val(c.min_order_value || 0);
  $('#editMinQty').val(c.min_quantity || 0);
  $('#editMaxUses').val(c.max_uses || '');

  function toDatetimeLocal(s) { return s ? s.replace(' ', 'T').slice(0, 16) : ''; }
  $('#editStartDate').val(toDatetimeLocal(c.start_date));
  $('#editExpiryDate').val(toDatetimeLocal(c.expiry_date));

  $('#editDiscountAmount').val('');
  $('#editDiscountPct').val('');
  $('#editMaxDiscount').val('');
  if (c.discount_amount) {
    $('#editDiscountType').val('amount').trigger('change');
    $('#editDiscountAmount').val(c.discount_amount);
  } else if (c.discount_percentage) {
    $('#editDiscountType').val('percentage').trigger('change');
    $('#editDiscountPct').val(c.discount_percentage);
    $('#editMaxDiscount').val(c.max_discount_amount || '');
  } else {
    $('#editDiscountType').val('').trigger('change');
  }

  if (c.valid_flavors) {
    try {
      $('#editValidFlavors').val(JSON.parse(c.valid_flavors).map(String));
    } catch (e) { $('#editValidFlavors').val([]); }
  } else {
    $('#editValidFlavors').val([]);
  }

  $('#editCouponModal').modal('show');
}

function toggleCoupon(id) {
  showLoader('Updating…');
  apiPost('/admin/api/coupons/', { action: 'toggle', id: id })
    .done(function (res) {
      showAlertModal(res.message, res.success ? 'success' : 'danger');
      if (res.success) loadCoupons();
    })
    .fail(function () { showAlertModal('Request failed.', 'danger'); })
    .always(hideLoader);
}

function deleteCoupon(id) {
  confirmThen('Delete this coupon? This cannot be undone.', function () {
    showLoader('Deleting…');
    apiPost('/admin/api/coupons/', { action: 'delete', id: id })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) loadCoupons();
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
}
