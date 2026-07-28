/* Varthaai Admin — Orders Page */

var ordersState = { page: 1, per_page: 20, filters: {} };
var ordersFlavorData = [];
var ordersPackData = [];
var _filterDebounce = null;

function collectFilters() {
  return {
    status:    $('#filterStatus').val(),
    search:    $('#filterSearch').val(),
    date_from: $('#filterDateFrom').val(),
    date_to:   $('#filterDateTo').val()
  };
}

function applyFilters() {
  ordersState.page = 1;
  ordersState.filters = collectFilters();
  loadOrders();
}

$(function () {
  // Pre-fill status from URL param (e.g. from dashboard link)
  var _usp = new URLSearchParams(window.location.search);
  var urlStatus = _usp.get('status');
  var urlSearch = _usp.get('search');
  if (urlStatus) $('#filterStatus').val(urlStatus);
  if (urlSearch) $('#filterSearch').val(urlSearch);

  ordersState.filters = collectFilters();
  loadOrders();

  // Auto-apply on change (no Filter button needed)
  $('#filterStatus, #filterDateFrom, #filterDateTo').on('change', applyFilters);
  $('#perPageOrders').on('change', function () {
    ordersState.per_page = parseInt(this.value);
    ordersState.page = 1;
    loadOrders();
  });
  $('#filterSearch').on('input', function () {
    clearTimeout(_filterDebounce);
    _filterDebounce = setTimeout(applyFilters, 400);
  });

  // Clear button
  $('#orderFilterForm').on('reset', function () {
    setTimeout(function () {
      ordersState.page = 1;
      ordersState.filters = {};
      loadOrders();
    }, 0);
  });


  $('#selectAllOrders').on('change', function () {
    $('.order-check').prop('checked', this.checked);
  });

  // Load flavors for create order modal
  loadFlavorsForModal();

  // Modal item total updates
  $(document).on('change input', '#createOrderModal select[name="flavor_ids[]"], #createOrderModal select[name="pack_ids[]"], #createOrderModal input[name="quantities[]"], #createOrderModal input[name="delivery_charge"], #createOrderModal select[name="coupon_code"]', updateModalTotal);

  $('#addOrderItemBtn').on('click', addOrderItem);

  $('#createOrderForm').on('submit', function (e) {
    e.preventDefault();
    submitCreateOrder();
  });

  // Reset modal on close
  $('#createOrderModal').on('hidden.bs.modal', function () {
    $('#createOrderForm')[0].reset();
    $('#orderItems').html('');
    addOrderItem();
    updateModalTotal();
  });
});

function loadOrders() {
  showLoader('Loading orders…');
  var params = Object.assign({ page: ordersState.page, per_page: ordersState.per_page }, ordersState.filters);

  apiGet('/admin/api/orders/', params)
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      renderOrdersTable(res.data.orders, res.data.total);
      renderOrdersPagination(res.data.total, res.data.per_page, res.data.page);
    })
    .fail(function () { showAlertModal('Failed to load orders.', 'danger'); })
    .always(hideLoader);
}

function renderOrdersTable(rows, total) {
  $('#ordersCount').text(total || 0);
  var $tbody = $('#ordersTableBody');
  if (!rows || rows.length === 0) {
    $tbody.html('<tr><td colspan="10" class="text-center" style="padding:40px;color:var(--gray-400)"><i class="fas fa-bag-shopping fa-2x mb-2 d-block"></i>No orders found</td></tr>');
    return;
  }
  var html = '';
  rows.forEach(function (o) {
    var discount = parseFloat(o.discount_amount) || 0;
    var delivery = parseFloat(o.delivery_charge) || 0;
    var amountHtml = '<strong>' + formatCurrency(o.total_price) + '</strong>';
    if (discount > 0) amountHtml += '<br><small style="color:#16a34a"><i class="fas fa-tag"></i> -' + formatCurrency(discount) + (o.coupon_code ? ' (' + escHtml(o.coupon_code) + ')' : '') + '</small>';
    if (delivery > 0) amountHtml += '<br><small style="color:var(--gray-400)">+' + formatCurrency(delivery) + ' delivery</small>';

    html += '<tr>' +
      '<td><input type="checkbox" class="order-check" value="' + escHtml(o.id) + '"></td>' +
      '<td><span style="font-family:monospace;font-size:0.76rem;color:var(--gray-400)">' + escHtml(o.id) + '</span></td>' +
      '<td><strong>' + escHtml(o.name || o.user_name || 'Guest') + '</strong>' +
        (o.user_id ? '<br><small style="color:var(--gray-400)">Points: ' + (o.current_loyalty_points || 0) + '</small>' : '') + '</td>' +
      '<td>' + escHtml(o.mobile) + '<br><small style="color:var(--gray-400)">' + escHtml((o.address || '').substring(0, 30)) + '…</small></td>' +
      '<td><small style="color:var(--gray-400)">' + escHtml(o.items || 'No items') + '</small><br><span class="badge badge-info">' + (o.item_count || 0) + ' items</span></td>' +
      '<td>' + amountHtml + '</td>' +
      '<td><div class="dropdown">' +
        '<span class="status-dd-toggle dropdown-toggle" data-bs-toggle="dropdown" data-bs-boundary="viewport">' +
          statusBadge(o.status) +
        '</span>' +
        '<ul class="dropdown-menu dropdown-menu-end">' +
          ['pending','confirmed','processing','shipped','delivered'].map(function(s){ return '<li><a class="dropdown-item" href="#" onclick="updateOrderStatus(\'' + escHtml(o.id) + '\',\'' + s + '\');return false">' + ORDER_STATUS_LABELS[s] + '</a></li>'; }).join('') +
          '<li><hr class="dropdown-divider"></li>' +
          ['cancelled','failed','deleted'].map(function(s){ return '<li><a class="dropdown-item text-danger" href="#" onclick="updateOrderStatus(\'' + escHtml(o.id) + '\',\'' + s + '\');return false">' + ORDER_STATUS_LABELS[s] + '</a></li>'; }).join('') +
        '</ul></div></td>' +
      '<td><div class="dropdown">' +
        '<span class="status-dd-toggle dropdown-toggle" data-bs-toggle="dropdown" data-bs-boundary="viewport">' +
          paymentBadge(o.payment_status) +
        '</span>' +
        '<ul class="dropdown-menu dropdown-menu-end">' +
          Object.keys(PAYMENT_STATUS_LABELS).map(function(s){ return '<li><a class="dropdown-item" href="#" onclick="updatePaymentStatus(\'' + escHtml(o.id) + '\',\'' + s + '\');return false">' + PAYMENT_STATUS_LABELS[s] + '</a></li>'; }).join('') +
        '</ul></div></td>' +
      '<td style="color:var(--gray-400);font-size:0.8rem;white-space:nowrap">' + formatDate(o.order_date) + '<br>' + (new Date(o.order_date)).toTimeString().slice(0,5) + '</td>' +
      '<td><div class="table-actions">' +
        '<a href="/admin/orders/' + encodeURIComponent(o.id) + '/" class="btn-icon view" title="View"><i class="fas fa-eye"></i></a>' +
        '<a href="/admin/orders/' + encodeURIComponent(o.id) + '/edit/" class="btn-icon edit" title="Edit"><i class="fas fa-pen"></i></a>' +
        '<button class="btn-icon print" title="Print" onclick="printInvoice(\'' + escHtml(o.id) + '\')"><i class="fas fa-print"></i></button>' +
      '</div></td>' +
      '</tr>';
  });
  $tbody.html(html);

  // Init dropdowns with fixed strategy so they escape table-responsive overflow
  $('#ordersTableBody [data-bs-toggle="dropdown"]').each(function () {
    new bootstrap.Dropdown(this, { popperConfig: { strategy: 'fixed' } });
  });
}

function updateOrderStatus(orderId, status) {
  confirmThen('Change order status to "' + (ORDER_STATUS_LABELS[status] || status) + '"?', function () {
    showLoader('Updating…');
    apiPost('/admin/api/orders/', { action: 'update_status', order_id: orderId, status: status })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) loadOrders();
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
}

function updatePaymentStatus(orderId, paymentStatus) {
  confirmThen('Change payment status to "' + (PAYMENT_STATUS_LABELS[paymentStatus] || paymentStatus) + '"?', function () {
    showLoader('Updating…');
    apiPost('/admin/api/orders/', { action: 'update_payment_status', order_id: orderId, payment_status: paymentStatus })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) loadOrders();
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
}

function bulkStatusUpdate(status) {
  var ids = $('.order-check:checked').map(function () { return this.value; }).get();
  if (!ids.length) { showAlertModal('Select at least one order.', 'warning'); return; }
  confirmThen('Update ' + ids.length + ' orders to "' + (ORDER_STATUS_LABELS[status] || status) + '"?', function () {
    showLoader('Updating…');
    apiPost('/admin/api/orders/', { action: 'bulk_update', order_ids: ids, status: status })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) loadOrders();
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
}

function renderOrdersPagination(total, perPage, current) {
  var totalPages = Math.ceil(total / perPage);
  var $p = $('#ordersPagination');
  if (totalPages <= 1) { $p.html(''); return; }

  function pi(disabled, onclick, label) {
    return '<li class="page-item' + (disabled ? ' disabled' : '') + '">' +
      '<a class="page-link" href="#" onclick="' + onclick + ';return false">' + label + '</a></li>';
  }

  var html = '<nav><ul class="pagination justify-content-center mb-0 flex-wrap">';
  html += pi(current <= 1, 'ordersGoPage(1)',             '<i class="fas fa-angles-left"></i>');
  html += pi(current <= 1, 'ordersGoPage(' + (current - 1) + ')', '<i class="fas fa-angle-left"></i>');

  for (var i = Math.max(1, current - 2); i <= Math.min(totalPages, current + 2); i++) {
    html += '<li class="page-item' + (i === current ? ' active' : '') + '">' +
      '<a class="page-link" href="#" onclick="ordersGoPage(' + i + ');return false">' + i + '</a></li>';
  }

  html += pi(current >= totalPages, 'ordersGoPage(' + (current + 1) + ')', '<i class="fas fa-angle-right"></i>');
  html += pi(current >= totalPages, 'ordersGoPage(' + totalPages + ')',    '<i class="fas fa-angles-right"></i>');
  html += '</ul></nav>';
  $p.html(html);
}

function ordersGoPage(p) { ordersState.page = p; loadOrders(); return false; }

function printInvoice(orderId) {
  window.open('/admin/orders/' + encodeURIComponent(orderId) + '/invoice/', '_blank', 'width=800,height=600');
}

/* ── Create Order Modal ── */

function loadFlavorsForModal() {
  apiGet('/admin/api/orders/', { view: 'order_form_data' })
    .done(function (res) {
      if (res.success) {
        ordersFlavorData = res.data.flavors || [];
        ordersPackData = res.data.packs || [];
        addOrderItem();
      }
    });
}

function buildFlavorOptions() {
  var opts = '<option value="">Select Flavor</option>';
  ordersFlavorData.forEach(function (f) {
    opts += '<option value="' + f.id + '" data-price="' + f.sale_price_per_kg + '">' +
      escHtml(f.name) + ' — ₹' + Number(f.sale_price_per_kg).toLocaleString('en-IN') + '/kg</option>';
  });
  return opts;
}

function buildPackOptions(flavorId) {
  var opts = '<option value="">Custom Weight</option>';
  if (!flavorId) return opts;
  ordersPackData.filter(function (p) { return parseInt(p.flavor_id) === parseInt(flavorId); }).forEach(function (p) {
    opts += '<option value="' + p.id + '" data-weight="' + p.weight_grams + '" data-price="' + p.selling_price + '" data-label="' + escHtml(p.label) + '">' +
      escHtml(p.label) + ' — ' + formatCurrency(p.selling_price) + '</option>';
  });
  return opts;
}

function onOrderFlavorChange(sel) {
  var row = $(sel).closest('.item-row');
  var fid = $(sel).val();
  row.find('select[name="pack_ids[]"]').html(buildPackOptions(fid));
  row.find('input[name="quantities[]"]').prop('disabled', false).val('');
  updateModalTotal();
}

function onOrderPackChange(sel) {
  var row = $(sel).closest('.item-row');
  var opt = $(sel).find(':selected');
  var qtyInput = row.find('input[name="quantities[]"]');
  if (opt.val()) {
    qtyInput.val(opt.data('weight')).prop('disabled', true);
  } else {
    qtyInput.val('').prop('disabled', false);
  }
  updateModalTotal();
}

function addOrderItem() {
  var html = '<div class="item-row mb-2" style="display:flex;gap:8px;align-items:center;padding:10px;background:var(--gray-50);border:1px solid var(--gray-200);border-radius:8px">' +
    '<select class="form-select" name="flavor_ids[]" style="flex:2" onchange="onOrderFlavorChange(this)">' + buildFlavorOptions() + '</select>' +
    '<select class="form-select" name="pack_ids[]" style="flex:1.5" onchange="onOrderPackChange(this)"><option value="">Custom Weight</option></select>' +
    '<input type="number" class="form-control" name="quantities[]" placeholder="Qty (grams)" min="1" style="flex:1">' +
    '<button type="button" class="btn-icon delete flex-shrink-0" onclick="removeOrderItem(this)" title="Remove"><i class="fas fa-times"></i></button>' +
    '</div>';
  $('#orderItems').append(html);
  updateModalTotal();
}

function removeOrderItem(btn) {
  if ($('.item-row').length <= 1) { showAlertModal('At least one item is required.', 'warning'); return; }
  $(btn).closest('.item-row').remove();
  updateModalTotal();
}

function updateModalTotal() {
  var subtotal = 0;
  $('.item-row').each(function () {
    var packSel = $(this).find('select[name="pack_ids[]"]');
    var packOpt = packSel.find(':selected');
    var qty = parseFloat($(this).find('input[name="quantities[]"]').val()) || 0;

    if (packOpt.val()) {
      // Pack selected: use pack selling_price
      subtotal += parseFloat(packOpt.data('price')) || 0;
    } else {
      // Custom weight: use per-kg pricing
      var flavorPrice = parseFloat($(this).find('select[name="flavor_ids[]"] :selected').data('price')) || 0;
      if (qty > 0 && flavorPrice > 0) subtotal += (qty / 1000) * flavorPrice;
    }
  });
  var delivery = parseFloat($('input[name="delivery_charge"]').val()) || 0;
  var discount = parseFloat($('select[name="coupon_code"]').find(':selected').data('discount')) || 0;
  var total = subtotal + delivery - discount;
  $('#modalSubtotal').text('₹' + subtotal.toFixed(2));
  $('#modalDelivery').text('₹' + delivery.toFixed(2));
  $('#modalDiscount').text('-₹' + discount.toFixed(2));
  $('#modalTotal').text('₹' + total.toFixed(2));
}

function submitCreateOrder() {
  var items = [];
  var valid = true;
  $('.item-row').each(function () {
    var fid = $(this).find('select[name="flavor_ids[]"]').val();
    var qty = parseInt($(this).find('input[name="quantities[]"]').val());
    var packSel = $(this).find('select[name="pack_ids[]"]');
    var packOpt = packSel.find(':selected');
    var packId = packOpt.val() ? parseInt(packOpt.val()) : null;
    var packLabel = packOpt.val() ? (packOpt.data('label') || '') : null;
    if (fid && qty > 0) items.push({ flavor_id: parseInt(fid), quantity: qty, flavor_pack_id: packId, pack_label: packLabel });
    else if (fid || ($(this).find('input[name="quantities[]"]').val())) valid = false;
  });
  if (!items.length) { showAlertModal('Add at least one valid item.', 'warning'); return; }

  var data = {
    name: $('[name="name"]').val(),
    mobile: $('[name="mobile"]').val(),
    address: $('[name="address"]').val(),
    pincode: $('[name="pincode"]').val(),
    delivery_charge: parseFloat($('[name="delivery_charge"]').val()) || 0,
    coupon_code: $('[name="coupon_code"]').val(),
    referral_code: $('[name="referral_code"]').val(),
    items: items
  };

  showLoader('Creating order…');
  apiPost('/admin/api/orders/create/', data)
    .done(function (res) {
      showAlertModal(res.message, res.success ? 'success' : 'danger');
      if (res.success) {
        $('#createOrderModal').modal('hide');
        loadOrders();
      }
    })
    .fail(function () { showAlertModal('Request failed.', 'danger'); })
    .always(hideLoader);
}
