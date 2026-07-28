/* Varthaai Admin — B2B Orders List */

var currentPage  = 1;
var searchTimer  = null;

$(function () {
  loadOrders();

  $('#filterSearch').on('input', function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () { currentPage = 1; loadOrders(); }, 300);
  });

  $('#filterStatus, #filterPayment, #filterPerPage').on('change', function () {
    currentPage = 1;
    loadOrders();
  });

  $('#paymentForm').on('submit', function (e) {
    e.preventDefault();
    recordPayment();
  });

  $('#payDate').val(new Date().toISOString().slice(0, 10));
});

function resetFilters() {
  $('#filterSearch').val('');
  $('#filterStatus').val('');
  $('#filterPayment').val('');
  currentPage = 1;
  loadOrders();
}

function loadOrders() {
  var params = {
    page:     currentPage,
    per_page: parseInt($('#filterPerPage').val()) || 25
  };
  var status = $('#filterStatus').val();
  var payment = $('#filterPayment').val();
  var search = $('#filterSearch').val().trim();
  if (status)  params.status = status;
  if (payment) params.payment_status = payment;
  if (search)  params.search = search;

  apiGet('/admin/api/b2b-orders/', params)
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      $('#ordersCount').text(res.total);
      renderOrders(res.data || []);
      renderPagination(res.total, res.page, res.per_page);
    })
    .fail(function () { showAlertModal('Failed to load orders.', 'danger'); });
}

function renderOrders(orders) {
  if (!orders.length) {
    $('#ordersTableBody').html('<tr><td colspan="11" class="text-center" style="padding:40px;color:var(--gray-400)"><i class="fas fa-bag-shopping fa-2x mb-2 d-block"></i>No orders found</td></tr>');
    return;
  }
  var html = '';
  orders.forEach(function (o) {
    html += '<tr style="cursor:pointer" onclick="viewOrder(\'' + escHtml(o.id) + '\')">' +
      '<td style="font-weight:600;font-size:0.85rem">' + escHtml(o.id) + '</td>' +
      '<td>' + escHtml(o.company_name) + '</td>' +
      '<td>' + (o.flavors_count || 0) + '</td>' +
      '<td>' + (parseInt(o.packs_count || 0) + parseInt(o.custom_count || 0)) + '</td>' +
      '<td style="font-weight:600">' + formatCurrency(o.total_amount) + '</td>' +
      '<td>' + formatCurrency(o.paid_amount) + '</td>' +
      '<td style="' + (parseFloat(o.balance_amount) > 0 ? 'color:#dc2626;font-weight:600' : '') + '">' + formatCurrency(o.balance_amount) + '</td>' +
      '<td><span class="b2b-status b2b-status-' + o.status + '">' + capitalize(o.status) + '</span></td>' +
      '<td><span class="pay-status pay-status-' + o.payment_status + '">' + capitalize(o.payment_status) + '</span></td>' +
      '<td style="font-size:0.82rem">' + formatDate(o.order_date) + '</td>' +
      '<td class="table-actions" onclick="event.stopPropagation()">' +
        '<button class="btn-icon edit" title="View" onclick="viewOrder(\'' + escHtml(o.id) + '\')"><i class="fas fa-eye"></i></button>' +
        (o.status === 'draft' ? '<a class="btn-icon edit" title="Edit" href="/admin/b2b-orders/create/?edit=' + encodeURIComponent(o.id) + '"><i class="fas fa-pen"></i></a>' : '') +
        (o.status === 'draft' ? '<button class="btn-icon edit" title="Confirm" onclick="confirmOrder(\'' + escHtml(o.id) + '\')"><i class="fas fa-check"></i></button>' : '') +
        (o.status === 'draft' ? '<button class="btn-icon delete" title="Delete" onclick="deleteOrder(\'' + escHtml(o.id) + '\')"><i class="fas fa-trash"></i></button>' : '') +
        '<button class="btn-icon edit" title="Collect Payment" onclick="openPaymentModal(\'' + escHtml(o.id) + '\',' + o.company_id + ',' + o.balance_amount + ')"><i class="fas fa-indian-rupee-sign"></i></button>' +
        '<a class="btn-icon edit" title="Invoice" href="/admin/b2b-orders/' + encodeURIComponent(o.id) + '/invoice/" target="_blank"><i class="fas fa-print"></i></a>' +
      '</td>' +
    '</tr>';
  });
  $('#ordersTableBody').html(html);
}

function renderPagination(total, page, perPage) {
  var pages = Math.ceil(total / perPage);
  if (pages <= 1) { $('#ordersPagination').html(''); return; }
  var html = '<nav><ul class="pagination pagination-sm mb-0 justify-content-center">';
  for (var i = 1; i <= pages; i++) {
    html += '<li class="page-item' + (i === page ? ' active' : '') + '">' +
      '<a class="page-link" href="#" onclick="goPage(' + i + ');return false">' + i + '</a></li>';
  }
  html += '</ul></nav>';
  $('#ordersPagination').html(html);
}

function goPage(p) { currentPage = p; loadOrders(); }

/* ── Order detail modal ── */
function viewOrder(orderId) {
  $('#detailOrderId').text(orderId);
  $('#orderDetailBody').html('<div class="text-center py-4"><div class="admin-loader-spinner" style="margin:0 auto 10px"></div>Loading…</div>');
  $('#orderDetailModal').modal('show');

  apiGet('/admin/api/b2b-orders/', { view: 'order', id: orderId })
    .done(function (res) {
      if (!res.success) { $('#orderDetailBody').html('<div class="text-danger">' + res.message + '</div>'); return; }
      renderOrderDetail(res.data.order, res.data.items, res.data.payments);
    });
}

function renderOrderDetail(order, items, payments) {
  var statusActions = '';
  if (order.status === 'draft') {
    statusActions = '<button class="btn btn-sm btn-primary me-2" onclick="confirmOrder(\'' + escHtml(order.id) + '\')"><i class="fas fa-check me-1"></i>Confirm & Deduct Stock</button>';
  } else if (order.status === 'confirmed') {
    statusActions = '<button class="btn btn-sm btn-outline-primary me-2" onclick="updateStatus(\'' + escHtml(order.id) + '\',\'dispatched\')"><i class="fas fa-truck me-1"></i>Dispatched</button>';
  } else if (order.status === 'dispatched') {
    statusActions = '<button class="btn btn-sm btn-outline-success me-2" onclick="updateStatus(\'' + escHtml(order.id) + '\',\'delivered\')"><i class="fas fa-check-double me-1"></i>Delivered</button>';
  }
  if (order.status === 'draft') {
    statusActions += '<a href="/admin/b2b-orders/create/?edit=' + encodeURIComponent(order.id) + '" class="btn btn-sm btn-outline-secondary me-2"><i class="fas fa-pen me-1"></i>Edit</a>';
    statusActions += '<button class="btn btn-sm btn-outline-danger me-2" onclick="deleteOrder(\'' + escHtml(order.id) + '\')"><i class="fas fa-trash me-1"></i>Delete</button>';
  }
  if (order.status !== 'cancelled' && order.status !== 'delivered' && order.status !== 'draft') {
    statusActions += '<button class="btn btn-sm btn-outline-danger me-2" onclick="updateStatus(\'' + escHtml(order.id) + '\',\'cancelled\')"><i class="fas fa-ban me-1"></i>Cancel</button>';
  }
  if (order.status === 'delivered') {
    statusActions += '<a href="/admin/b2b-orders/create/?repeat=' + encodeURIComponent(order.id) + '" class="btn btn-sm btn-outline-primary me-2"><i class="fas fa-redo me-1"></i>Repeat Order</a>';
  }
  // Print invoice (always available)
  statusActions += '<a href="/admin/b2b-orders/' + encodeURIComponent(order.id) + '/invoice/" target="_blank" class="btn btn-sm btn-outline-secondary me-2"><i class="fas fa-print me-1"></i>Invoice</a>';

  var html = '<div class="row g-4">';

  // Order info
  html += '<div class="col-md-6">' +
    '<h6 style="font-size:0.82rem;color:var(--gray-400);text-transform:uppercase;margin-bottom:8px">Order Info</h6>' +
    '<div style="font-size:0.875rem">' +
      '<div class="mb-1"><strong>Company:</strong> <a href="/admin/b2b/' + order.company_id + '/">' + escHtml(order.company_name) + '</a></div>' +
      (order.contact_name ? '<div class="mb-1"><strong>Contact:</strong> ' + escHtml(order.contact_name) + '</div>' : '') +
      '<div class="mb-1"><strong>Date:</strong> ' + formatDateTime(order.order_date) + '</div>' +
      (order.due_date ? '<div class="mb-1"><strong>Due:</strong> ' + formatDate(order.due_date) + '</div>' : '') +
      '<div class="mb-1"><strong>Status:</strong> <span class="b2b-status b2b-status-' + order.status + '">' + capitalize(order.status) + '</span></div>' +
      '<div class="mb-1"><strong>Payment:</strong> <span class="pay-status pay-status-' + order.payment_status + '">' + capitalize(order.payment_status) + '</span></div>' +
      (order.source_order_id ? '<div class="mb-1"><strong>Repeated from:</strong> ' + escHtml(order.source_order_id) + '</div>' : '') +
      (order.notes ? '<div class="mb-1"><strong>Notes:</strong> ' + escHtml(order.notes) + '</div>' : '') +
    '</div>' +
    '<div class="mt-3">' + statusActions + '</div>' +
  '</div>';

  // Summary
  html += '<div class="col-md-6">' +
    '<h6 style="font-size:0.82rem;color:var(--gray-400);text-transform:uppercase;margin-bottom:8px">Summary</h6>' +
    '<div style="background:var(--gray-50);border-radius:8px;padding:14px;font-size:0.875rem">' +
      '<div style="display:flex;justify-content:space-between;margin-bottom:6px"><span>Subtotal</span><span>' + formatCurrency(order.subtotal) + '</span></div>' +
      (parseFloat(order.offer_discount_amount) > 0 ? '<div style="display:flex;justify-content:space-between;margin-bottom:6px;color:#16a34a"><span>Free items</span><span>-' + formatCurrency(order.offer_discount_amount) + '</span></div>' : '') +
      (parseFloat(order.discount_amount) > 0 ? '<div style="display:flex;justify-content:space-between;margin-bottom:6px;color:#3b82f6"><span>Discount' + (order.discount_type === 'percentage' ? ' (' + order.discount_value + '%)' : '') + '</span><span>-' + formatCurrency(order.discount_amount) + '</span></div>' : '') +
      '<div style="display:flex;justify-content:space-between;font-weight:700;font-size:1rem;border-top:2px solid var(--gray-200);padding-top:8px;margin-top:8px"><span>Total</span><span>' + formatCurrency(order.total_amount) + '</span></div>' +
      '<div style="display:flex;justify-content:space-between;margin-top:6px"><span>Paid</span><span style="color:#16a34a">' + formatCurrency(order.paid_amount) + '</span></div>' +
      '<div style="display:flex;justify-content:space-between;font-weight:700;' + (parseFloat(order.balance_amount) > 0 ? 'color:#dc2626' : '') + '"><span>Balance</span><span>' + formatCurrency(order.balance_amount) + '</span></div>' +
    '</div>' +
    '<button class="btn btn-sm btn-outline-primary mt-3" onclick="openPaymentModal(\'' + escHtml(order.id) + '\',' + order.company_id + ',' + order.balance_amount + ')"><i class="fas fa-indian-rupee-sign me-1"></i>Record Payment</button>' +
  '</div>';

  // Items table
  html += '<div class="col-12">' +
    '<h6 style="font-size:0.82rem;color:var(--gray-400);text-transform:uppercase;margin-bottom:8px">Items (' + items.reduce(function(s, it) { return s + (it.flavor_pack_id ? parseInt(it.quantity) : 1); }, 0) + ')</h6>' +
    '<div class="table-responsive"><table class="table table-sm" style="font-size:0.85rem"><thead><tr>' +
    '<th>Flavor</th><th>Pack</th><th>Qty</th><th>Weight</th><th>MRP</th><th>Price</th><th>Line Total</th>' +
    '</tr></thead><tbody>';
  items.forEach(function (it) {
    var free = parseInt(it.is_free_item);
    var lineTotal = free ? 0 : (parseFloat(it.selling_price) * parseInt(it.quantity));
    html += '<tr' + (free ? ' style="background:#f0fdf4"' : '') + '>' +
      '<td>' + escHtml(it.flavor_name) + (free ? ' <span style="font-size:0.7rem;background:#dcfce7;color:#16a34a;padding:1px 6px;border-radius:8px">FREE</span>' : '') + '</td>' +
      '<td>' + (it.pack_label || 'Custom') + '</td>' +
      '<td>' + it.quantity + '</td>' +
      '<td>' + it.total_weight_grams + 'g</td>' +
      '<td>' + (it.mrp ? formatCurrency(it.mrp) : '—') + '</td>' +
      '<td>' + formatCurrency(it.selling_price) + '</td>' +
      '<td style="font-weight:600">' + formatCurrency(lineTotal) + '</td>' +
    '</tr>';
  });
  html += '</tbody></table></div></div>';

  // Payments
  if (payments.length) {
    html += '<div class="col-12">' +
      '<h6 style="font-size:0.82rem;color:var(--gray-400);text-transform:uppercase;margin-bottom:8px">Payments (' + payments.length + ')</h6>' +
      '<div class="table-responsive"><table class="table table-sm" style="font-size:0.85rem"><thead><tr>' +
      '<th>Date</th><th>Amount</th><th>Type</th><th>Method</th><th>Ref</th><th>By</th>' +
      '</tr></thead><tbody>';
    payments.forEach(function (p) {
      html += '<tr>' +
        '<td>' + formatDate(p.payment_date) + '</td>' +
        '<td style="font-weight:600">' + formatCurrency(p.amount) + '</td>' +
        '<td>' + capitalize(p.payment_type) + '</td>' +
        '<td>' + capitalize(p.payment_method.replace(/_/g, ' ')) + '</td>' +
        '<td>' + (p.reference_number || '—') + '</td>' +
        '<td>' + escHtml(p.created_by_name) + '</td>' +
      '</tr>';
    });
    html += '</tbody></table></div></div>';
  }

  html += '</div>';
  $('#orderDetailBody').html(html);
}

/* ── Actions ── */
function confirmOrder(orderId) {
  confirmThen('Confirm this order? Stock will be deducted from selected batches.', function () {
    showLoader('Confirming…');
    apiPost('/admin/api/b2b-orders/', { action: 'confirm_order', order_id: orderId })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) { $('#orderDetailModal').modal('hide'); loadOrders(); }
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
}

function updateStatus(orderId, status) {
  var label = status === 'cancelled' ? 'Cancel this order?' : 'Mark order as ' + status + '?';
  confirmThen(label, function () {
    showLoader('Updating…');
    apiPost('/admin/api/b2b-orders/', { action: 'update_status', order_id: orderId, status: status })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) { $('#orderDetailModal').modal('hide'); loadOrders(); }
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
}

function deleteOrder(orderId) {
  confirmThen('Delete this order? Stock deductions will be reverted. This cannot be undone.', function () {
    showLoader('Deleting…');
    apiPost('/admin/api/b2b-orders/', { action: 'delete_order', order_id: orderId })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) { $('#orderDetailModal').modal('hide'); loadOrders(); }
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
}

function openPaymentModal(orderId, companyId, balance) {
  $('#payOrderId').val(orderId);
  $('#payCompanyId').val(companyId);
  $('#payAmount').val(balance > 0 ? balance : '');
  $('#payRef, #payNotes').val('');
  $('#payDate').val(new Date().toISOString().slice(0, 10));
  $('#paymentModal').modal('show');
}

function recordPayment() {
  var data = {
    action:           'add_payment',
    company_id:       parseInt($('#payCompanyId').val()),
    order_id:         $('#payOrderId').val(),
    amount:           parseFloat($('#payAmount').val()),
    payment_type:     'payment',
    payment_method:   $('#payMethod').val(),
    reference_number: $('#payRef').val(),
    notes:            $('#payNotes').val(),
    payment_date:     $('#payDate').val()
  };

  showLoader('Recording…');
  apiPost('/admin/api/b2b-orders/', data)
    .done(function (res) {
      showAlertModal(res.message, res.success ? 'success' : 'danger');
      if (res.success) {
        $('#paymentModal').modal('hide');
        // Refresh order detail if open
        var oid = $('#payOrderId').val();
        if (oid) viewOrder(oid);
        loadOrders();
      }
    })
    .fail(function () { showAlertModal('Request failed.', 'danger'); })
    .always(hideLoader);
}

function capitalize(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ''; }
