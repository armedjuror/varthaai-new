/* Varthaai Admin — Customers Page */

var customersState = { page: 1, per_page: 25, filters: {} };
var _custFilterDebounce = null;

function collectCustomerFilters() {
  return {
    search:    $('#filterSearch').val(),
    date_from: $('#filterDateFrom').val(),
    date_to:   $('#filterDateTo').val()
  };
}

function applyCustomerFilters() {
  customersState.page = 1;
  customersState.filters = collectCustomerFilters();
  loadCustomers();
}

$(function () {
  loadCustomers();

  // Auto-apply filters
  $('#filterDateFrom, #filterDateTo').on('change', applyCustomerFilters);
  $('#perPageCustomers').on('change', function () {
    customersState.per_page = parseInt(this.value);
    customersState.page = 1;
    loadCustomers();
  });
  $('#filterSearch').on('input', function () {
    clearTimeout(_custFilterDebounce);
    _custFilterDebounce = setTimeout(applyCustomerFilters, 400);
  });

  $('#customerFilterForm').on('reset', function () {
    setTimeout(function () {
      customersState.page = 1;
      customersState.filters = {};
      loadCustomers();
    }, 0);
  });

  $('#selectAllCustomers').on('change', function () {
    $('.customer-check').prop('checked', this.checked);
  });

  // Bulk points modal — update selected count when opened
  $('#bulkPointsModal').on('show.bs.modal', function () {
    updateBulkPreview();
  });

  $('#pointsValue').on('input', updateBulkPreview);

  $('#bulkPointsForm').on('submit', function (e) {
    e.preventDefault();
    var ids = $('.customer-check:checked').map(function () { return parseInt(this.value); }).get();
    if (!ids.length) { showAlertModal('Select at least one customer.', 'warning'); return; }
    var action = $('#pointsAction').val();
    var value  = parseInt($('#pointsValue').val());
    if (!value || value <= 0) { showAlertModal('Enter a valid points value.', 'warning'); return; }
    confirmThen((action === 'add' ? 'Add ' : 'Subtract ') + value + ' points for ' + ids.length + ' customer(s)?', function () {
      showLoader('Updating points…');
      apiPost('/admin/api/customers/', { action: 'bulk_update_points', user_ids: ids, points_action: action, points_value: value })
        .done(function (res) {
          showAlertModal(res.message, res.success ? 'success' : 'danger');
          if (res.success) { $('#bulkPointsModal').modal('hide'); loadCustomers(); }
        })
        .fail(function () { showAlertModal('Request failed.', 'danger'); })
        .always(hideLoader);
    });
  });

  // Create customer
  $('#createCustomerForm').on('submit', function (e) {
    e.preventDefault();
    var mobile = $('#newMobile').val().trim();
    if (!mobile) { showAlertModal('Mobile number is required.', 'warning'); return; }
    showLoader('Creating customer…');
    apiPost('/admin/api/customers/', {
      action:      'create',
      mobile:      mobile,
      name:        $('#newName').val().trim(),
      address:     $('#newAddress').val().trim(),
      pincode:     $('#newPincode').val().trim(),
      designation: $('#newDesignation').val().trim()
    })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) {
          $('#createCustomerModal').modal('hide');
          $('#createCustomerForm')[0].reset();
          loadCustomers();
        }
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
});

function loadCustomers() {
  showLoader('Loading customers…');
  var params = Object.assign({ page: customersState.page, per_page: customersState.per_page }, customersState.filters);

  apiGet('/admin/api/customers/', params)
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      var d = res.data || {};
      renderCustomersTable(d.items, d.total);
      renderCustomersPagination(d.total, d.per_page, d.page);
    })
    .fail(function () { showAlertModal('Failed to load customers.', 'danger'); })
    .always(hideLoader);
}

function renderCustomersTable(rows, total) {
  $('#customersCount').text(total || 0);
  var $tbody = $('#customersTableBody');
  if (!rows || rows.length === 0) {
    $tbody.html('<tr><td colspan="9" class="text-center" style="padding:40px;color:var(--gray-400)"><i class="fas fa-users fa-2x mb-2 d-block"></i>No customers found</td></tr>');
    return;
  }
  var html = '';
  rows.forEach(function (u) {
    html += '<tr>' +
      '<td><input type="checkbox" class="customer-check" value="' + u.id + '"></td>' +
      '<td><strong>' + escHtml(u.name || '—') + '</strong>' +
        (u.designation ? '<br><small style="color:var(--gray-400)">' + escHtml(u.designation) + '</small>' : '') + '</td>' +
      '<td>' + escHtml(u.mobile) + '</td>' +
      '<td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:0.8rem;color:var(--gray-500)">' + escHtml(u.address || '—') + '</td>' +
      '<td><span class="badge badge-info" style="font-size:0.85rem">' + (u.loyalty_points || 0) + ' pts</span></td>' +
      '<td><strong>' + (u.total_orders || 0) + '</strong></td>' +
      '<td><strong>' + formatCurrency(u.total_spent) + '</strong></td>' +
      '<td style="color:var(--gray-400);font-size:0.8rem;white-space:nowrap">' + formatDate(u.created_at) + '</td>' +
      '<td><div class="table-actions">' +
        '<a href="/admin/customers/' + u.id + '/" class="btn-icon view" title="View"><i class="fas fa-eye"></i></a>' +
        '<a href="/admin/customers/' + u.id + '/edit/" class="btn-icon edit" title="Edit"><i class="fas fa-pen"></i></a>' +
      '</div></td>' +
      '</tr>';
  });
  $tbody.html(html);
}

function renderCustomersPagination(total, perPage, current) {
  var totalPages = Math.ceil(total / perPage);
  var $p = $('#customersPagination');
  if (totalPages <= 1) { $p.html(''); return; }

  function pi(disabled, onclick, label) {
    return '<li class="page-item' + (disabled ? ' disabled' : '') + '">' +
      '<a class="page-link" href="#" onclick="' + onclick + ';return false">' + label + '</a></li>';
  }

  var html = '<nav><ul class="pagination justify-content-center mb-0 flex-wrap">';
  html += pi(current <= 1, 'customersGoPage(1)',                   '<i class="fas fa-angles-left"></i>');
  html += pi(current <= 1, 'customersGoPage(' + (current-1) + ')', '<i class="fas fa-angle-left"></i>');
  for (var i = Math.max(1, current - 2); i <= Math.min(totalPages, current + 2); i++) {
    html += '<li class="page-item' + (i === current ? ' active' : '') + '">' +
      '<a class="page-link" href="#" onclick="customersGoPage(' + i + ');return false">' + i + '</a></li>';
  }
  html += pi(current >= totalPages, 'customersGoPage(' + (current+1) + ')',  '<i class="fas fa-angle-right"></i>');
  html += pi(current >= totalPages, 'customersGoPage(' + totalPages + ')',   '<i class="fas fa-angles-right"></i>');
  html += '</ul></nav>';
  $p.html(html);
}

function customersGoPage(p) { customersState.page = p; loadCustomers(); return false; }

function setBulkAction(action) {
  $('#pointsAction').val(action);
  if (action === 'add') {
    $('#actionAdd').removeClass('btn-outline-primary').addClass('btn-primary');
    $('#actionSubtract').removeClass('btn-danger').addClass('btn-outline-secondary');
  } else {
    $('#actionSubtract').removeClass('btn-outline-secondary').addClass('btn-danger');
    $('#actionAdd').removeClass('btn-primary').addClass('btn-outline-primary');
  }
  updateBulkPreview();
}

function updateBulkPreview() {
  var count  = $('.customer-check:checked').length;
  var value  = parseInt($('#pointsValue').val()) || 0;
  var action = $('#pointsAction').val();

  $('#bulkSelectedLabel').text(
    count === 0 ? 'No customers selected' :
    count === 1 ? '1 customer selected' : count + ' customers selected'
  );

  if (count > 0 && value > 0) {
    var verb = action === 'add' ? 'Add' : 'Subtract';
    $('#bulkPreview').text(verb + ' ' + value + ' pts ' + (action === 'add' ? 'to' : 'from') + ' ' + count + ' customer(s)').show();
  } else {
    $('#bulkPreview').hide();
  }
}
