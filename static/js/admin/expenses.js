/* ─────────────────────────────────────────────────
   Varthaai Admin — Expenses, Investments & Income
───────────────────────────────────────────────── */

var expenseCategories = [];
var expenseBrands     = [];
var stockBatches      = [];
var editingExpenseId  = null;
var editingInvestId   = null;
var expenseChartInstance  = null;
var categoryChartInstance = null;
var overviewChartInstance = null;

/* ═══════════════════════════════ Init ═══════════════════════════════ */

$(document).ready(function () {
  loadCategories();
  loadBrands();
  loadStats();
  loadExpenses(1);
  loadOverview();

  // Tab change handlers
  $('#tabInvestmentsBtn').on('shown.bs.tab', function () { loadInvestments(1); });
  $('#tabIncomeBtn').on('shown.bs.tab', function () { loadIncome(); });

  // Forms
  $('#addExpenseForm').on('submit', function (e) { e.preventDefault(); saveExpense(); });
  $('#addInvestmentForm').on('submit', function (e) { e.preventDefault(); saveInvestment(); });

  // Recurring toggle
  $('input[name="is_recurring"]').on('change', function () {
    $('select[name="recurring_period"]').prop('disabled', !this.checked);
  });

  // Stock mapping toggle
  $('select[name="category_id"]').on('change', function () {
    toggleStockMapping();
  });

  // Set default date
  var today = new Date().toISOString().split('T')[0];
  $('input[name="expense_date"]').val(today);
  $('input[name="investment_date"]').val(today);
});

/* ═══════════════════════════════ Loaders ═══════════════════════════════ */

function loadCategories() {
  apiGet('/admin/api/expenses/', { action: 'categories' }).done(function (res) {
    if (!res.success) return;
    expenseCategories = res.data;
    var opts = '<option value="">Select Category</option>';
    res.data.forEach(function (c) {
      opts += '<option value="' + c.id + '">' + escHtml(c.name) + '</option>';
    });
    $('select[name="category_id"]').html(opts);
    $('#filterCategory').html('<option value="">All Categories</option>' + res.data.map(function (c) {
      return '<option value="' + c.id + '">' + escHtml(c.name) + '</option>';
    }).join(''));
  });
}

function loadBrands() {
  apiGet('/admin/api/expenses/', { action: 'brands' }).done(function (res) {
    if (!res.success) return;
    expenseBrands = res.data;
    var opts = '<option value="">No Brand (Global)</option>';
    res.data.forEach(function (b) {
      opts += '<option value="' + b.id + '">' + escHtml(b.name) + '</option>';
    });
    $('select[name="brand_id"]').html(opts);
    $('#filterBrand').html('<option value="">All Brands</option><option value="0">Global (No Brand)</option>' +
      res.data.map(function (b) {
        return '<option value="' + b.id + '">' + escHtml(b.name) + '</option>';
      }).join(''));
    $('#incomeFilterBrand').html('<option value="">All Brands</option>' +
      res.data.map(function (b) {
        return '<option value="' + b.id + '">' + escHtml(b.name) + '</option>';
      }).join(''));
  });
}

function loadStockBatches(brandId) {
  apiGet('/admin/api/expenses/', { action: 'stock_batches', brand_id: brandId || '' }).done(function (res) {
    if (!res.success) return;
    stockBatches = res.data;
    var opts = '<option value="">Select Stock Batch</option>';
    res.data.forEach(function (s) {
      opts += '<option value="' + s.id + '">' +
        escHtml(s.flavor_name) + ' — Batch ' + escHtml(s.batch_number) +
        ' (' + s.quantity_kg + ' kg, ' + formatCurrency(s.cost_price_per_kg) + '/kg)' +
        '</option>';
    });
    $('select[name="stock_id"]').html(opts);
  });
}

function toggleStockMapping() {
  var catName = $('select[name="category_id"] option:selected').text().toLowerCase();
  var isStock = catName.indexOf('stock') !== -1 || catName.indexOf('raw material') !== -1;
  if (isStock) {
    $('#stockMappingGroup').show();
    var brandVal = $('select[name="brand_id"]').val();
    loadStockBatches(brandVal);
  } else {
    $('#stockMappingGroup').hide();
    $('select[name="stock_id"]').val('');
  }
}

/* ═══════════════════════════════ Stats ═══════════════════════════════ */

function loadStats() {
  apiGet('/admin/api/expenses/', { action: 'stats' }).done(function (res) {
    if (!res.success) return;
    $('#statThisMonth').text(formatCurrency(res.this_month));
    $('#statPending').text(formatCurrency(res.pending));
    $('#statCategories').text(res.category_count);
    $('#statTotal').text(res.total_count);
    renderExpenseChart(res.trend);
    renderCategoryChart(res.category_breakdown);
  });
}

function renderExpenseChart(trend) {
  var ctx = document.getElementById('expenseChart');
  if (!ctx) return;
  if (expenseChartInstance) expenseChartInstance.destroy();
  expenseChartInstance = new Chart(ctx.getContext('2d'), {
    type: 'line',
    data: {
      labels: trend.map(function (t) { return t.label; }),
      datasets: [{
        label: 'Monthly Expenses',
        data: trend.map(function (t) { return parseFloat(t.total); }),
        borderColor: '#dc3545',
        backgroundColor: 'rgba(220,53,69,0.1)',
        tension: 0.4, fill: true
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, ticks: { callback: function (v) { return formatCurrency(v); } } } }
    }
  });
}

function renderCategoryChart(cats) {
  var ctx = document.getElementById('categoryChart');
  if (!ctx) return;
  if (categoryChartInstance) categoryChartInstance.destroy();
  categoryChartInstance = new Chart(ctx.getContext('2d'), {
    type: 'doughnut',
    data: {
      labels: cats.map(function (c) { return c.name; }),
      datasets: [{
        data: cats.map(function (c) { return parseFloat(c.total); }),
        backgroundColor: cats.map(function (c) { return c.color || '#6c757d'; })
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom' } }
    }
  });
}

/* ═══════════════════════════════ Overview ═══════════════════════════════ */

function loadOverview() {
  apiGet('/admin/api/expenses/', { action: 'overview' }).done(function (res) {
    if (!res.success) return;
    $('#overviewIncome').text(formatCurrency(res.total_income));
    $('#overviewExpenses').text(formatCurrency(res.total_expenses));
    $('#overviewInvestments').text(formatCurrency(res.total_investments));
    var profit = res.net_profit;
    $('#overviewProfit').text(formatCurrency(profit))
      .removeClass('text-success text-danger')
      .addClass(profit >= 0 ? 'text-success' : 'text-danger');
    renderOverviewChart(res.monthly);
  });
}

function renderOverviewChart(monthly) {
  var ctx = document.getElementById('overviewChart');
  if (!ctx) return;
  if (overviewChartInstance) overviewChartInstance.destroy();
  overviewChartInstance = new Chart(ctx.getContext('2d'), {
    type: 'bar',
    data: {
      labels: monthly.map(function (m) { return m.label; }),
      datasets: [
        { label: 'Income', data: monthly.map(function (m) { return m.income; }), backgroundColor: '#28a745' },
        { label: 'Expenses', data: monthly.map(function (m) { return m.expenses; }), backgroundColor: '#dc3545' },
        { label: 'Investments', data: monthly.map(function (m) { return m.investments; }), backgroundColor: '#007bff' }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: { y: { beginAtZero: true, ticks: { callback: function (v) { return formatCurrency(v); } } } },
      plugins: { legend: { position: 'bottom' } }
    }
  });
}

/* ═══════════════════════════════ Expenses List ═══════════════════════════════ */

function loadExpenses(page) {
  var params = {
    action: 'list', page: page || 1,
    per_page: $('#filterPerPage').val() || 20,
    category_id: $('#filterCategory').val() || '',
    status: $('#filterStatus').val() || '',
    date_from: $('#filterDateFrom').val() || '',
    date_to: $('#filterDateTo').val() || '',
    search: $('#filterSearch').val() || '',
    brand_id: $('#filterBrand').val() || ''
  };

  apiGet('/admin/api/expenses/', params).done(function (res) {
    if (!res.success) { $('#expensesTableBody').html('<tr><td colspan="8" class="text-center" style="padding:40px;color:var(--gray-400)">Error loading expenses.</td></tr>'); return; }
    if (!res.data.length) {
      $('#expensesTableBody').html('<tr><td colspan="8" class="text-center" style="padding:40px;color:var(--gray-400)">No expenses found.</td></tr>');
      $('#expensesPagination').html('');
      return;
    }

    var html = '';
    res.data.forEach(function (e) {
      var catBadge = '<span class="badge" style="background-color:' + escHtml(e.category_color || '#6c757d') + '">' + escHtml(e.category_name) + '</span>';
      var statusCls = 'status-' + e.payment_status;
      var statusLabel = e.payment_status.charAt(0).toUpperCase() + e.payment_status.slice(1);
      var brandLabel = e.brand_name ? '<span class="badge bg-secondary">' + escHtml(e.brand_name) + '</span>' : '<span class="text-muted small">Global</span>';
      var stockLabel = e.stock_id ? '<br><small class="text-info"><i class="fas fa-link me-1"></i>' + escHtml(e.stock_flavor_name) + ' — ' + escHtml(e.batch_number) + '</small>' : '';

      html += '<tr>' +
        '<td>' + escHtml(e.title) + stockLabel + '</td>' +
        '<td>' + catBadge + '</td>' +
        '<td>' + brandLabel + '</td>' +
        '<td>' + escHtml(e.vendor_name || '—') + '</td>' +
        '<td class="text-end">' + formatCurrency(e.amount) +
          (parseFloat(e.tax_amount) > 0 ? '<br><small class="text-muted">+' + formatCurrency(e.tax_amount) + ' tax</small>' : '') + '</td>' +
        '<td>' + formatDate(e.expense_date) + '</td>' +
        '<td><span class="payment-status ' + statusCls + '">' + statusLabel + '</span></td>' +
        '<td>' +
          '<button class="btn btn-outline-primary btn-sm me-1" onclick="editExpense(' + e.id + ')" title="Edit"><i class="fas fa-edit"></i></button>' +
          '<button class="btn btn-outline-danger btn-sm" onclick="deleteExpense(' + e.id + ')" title="Delete"><i class="fas fa-trash"></i></button>' +
        '</td>' +
        '</tr>';
    });
    $('#expensesTableBody').html(html);
    renderPagination('#expensesPagination', res.page, res.per_page, res.total, 'loadExpenses');
  });
}

function filterExpenses() { loadExpenses(1); }
function resetFilters() {
  $('#filterCategory, #filterStatus, #filterBrand, #filterSearch, #filterDateFrom, #filterDateTo').val('');
  loadExpenses(1);
}

/* ═══════════════════════════════ Expense CRUD ═══════════════════════════════ */

function openAddExpenseModal() {
  editingExpenseId = null;
  $('#addExpenseForm')[0].reset();
  $('#stockMappingGroup').hide();
  $('#expenseModalTitle').text('Add New Expense');
  var today = new Date().toISOString().split('T')[0];
  $('input[name="expense_date"]').val(today);
  $('select[name="recurring_period"]').prop('disabled', true);
  new bootstrap.Modal(document.getElementById('addExpenseModal')).show();
}

function editExpense(id) {
  showLoader('Loading expense...');
  apiGet('/admin/api/expenses/', { action: 'get', id: id }).done(function (res) {
    hideLoader();
    if (!res.success) { showAlertModal(res.message, 'danger'); return; }
    var e = res.data;
    editingExpenseId = e.id;
    $('#expenseModalTitle').text('Edit Expense');
    $('select[name="category_id"]').val(e.category_id);
    $('input[name="title"]').val(e.title);
    $('textarea[name="description"]').val(e.description);
    $('input[name="amount"]').val(e.amount);
    $('input[name="tax_amount"]').val(e.tax_amount);
    $('input[name="expense_date"]').val(e.expense_date);
    $('input[name="vendor_name"]').val(e.vendor_name);
    $('input[name="vendor_contact"]').val(e.vendor_contact);
    $('select[name="payment_method"]').val(e.payment_method);
    $('select[name="payment_status"]').val(e.payment_status);
    $('input[name="invoice_number"]').val(e.invoice_number);
    $('input[name="is_recurring"]').prop('checked', !!parseInt(e.is_recurring));
    $('select[name="recurring_period"]').prop('disabled', !parseInt(e.is_recurring)).val(e.recurring_period || '');
    $('input[name="tags"]').val(e.tags);
    $('textarea[name="notes"]').val(e.notes);
    $('select[name="brand_id"]').val(e.brand_id || '');
    toggleStockMapping();
    if (e.stock_id) {
      setTimeout(function () { $('select[name="stock_id"]').val(e.stock_id); }, 500);
    }
    new bootstrap.Modal(document.getElementById('addExpenseModal')).show();
  });
}

function saveExpense() {
  var data = {
    action:           editingExpenseId ? 'update' : 'create',
    category_id:      $('select[name="category_id"]').val(),
    title:            $('input[name="title"]').val(),
    description:      $('textarea[name="description"]').val(),
    amount:           $('input[name="amount"]').val(),
    tax_amount:       $('input[name="tax_amount"]').val() || 0,
    expense_date:     $('input[name="expense_date"]').val(),
    vendor_name:      $('input[name="vendor_name"]').val(),
    vendor_contact:   $('input[name="vendor_contact"]').val(),
    payment_method:   $('select[name="payment_method"]').val(),
    payment_status:   $('select[name="payment_status"]').val(),
    invoice_number:   $('input[name="invoice_number"]').val(),
    is_recurring:     $('input[name="is_recurring"]').is(':checked') ? 1 : 0,
    recurring_period: $('select[name="recurring_period"]').val(),
    tags:             $('input[name="tags"]').val(),
    notes:            $('textarea[name="notes"]').val(),
    brand_id:         $('select[name="brand_id"]').val(),
    stock_id:         $('select[name="stock_id"]').val()
  };
  if (editingExpenseId) data.id = editingExpenseId;

  if (!data.category_id || !data.title || !data.amount || !data.expense_date) {
    showAlertModal('Please fill in all required fields.', 'warning'); return;
  }

  showLoader('Saving...');
  apiPost('/admin/api/expenses/', data).done(function (res) {
    hideLoader();
    if (res.success) {
      showAlertModal(res.message, 'success');
      bootstrap.Modal.getInstance(document.getElementById('addExpenseModal')).hide();
      loadExpenses(1);
      loadStats();
      loadOverview();
    } else {
      showAlertModal(res.message, 'danger');
    }
  }).fail(function () { hideLoader(); showAlertModal('Network error.', 'danger'); });
}

function deleteExpense(id) {
  confirmThen('Are you sure you want to delete this expense?', function () {
    showLoader('Deleting...');
    apiPost('/admin/api/expenses/', { action: 'delete', id: id }).done(function (res) {
      hideLoader();
      showAlertModal(res.message, res.success ? 'success' : 'danger');
      if (res.success) { loadExpenses(1); loadStats(); loadOverview(); }
    });
  });
}

/* ═══════════════════════════════ Investments ═══════════════════════════════ */

function loadInvestments(page) {
  var partner = $('#investPartnerFilter').val() || '';
  apiGet('/admin/api/expenses/', { action: 'investments', page: page || 1, partner: partner }).done(function (res) {
    if (!res.success) return;

    // Partner summary cards
    var summaryHtml = '';
    res.partner_totals.forEach(function (p) {
      summaryHtml += '<div class="col-sm-6 col-lg-3"><div class="data-card" style="padding:16px">' +
        '<div style="font-size:0.78rem;color:var(--gray-500)">' + escHtml(p.partner_name) + '</div>' +
        '<div style="font-size:1.5rem;font-weight:700;color:#007bff">' + formatCurrency(p.total) + '</div>' +
        '</div></div>';
    });
    summaryHtml += '<div class="col-sm-6 col-lg-3"><div class="data-card" style="padding:16px;border-left:3px solid #28a745">' +
      '<div style="font-size:0.78rem;color:var(--gray-500)">Total Investment</div>' +
      '<div style="font-size:1.5rem;font-weight:700;color:#28a745">' + formatCurrency(res.grand_total) + '</div>' +
      '</div></div>';
    $('#investmentSummary').html(summaryHtml);

    // Partner filter dropdown
    if (!$('#investPartnerFilter option').length || $('#investPartnerFilter option').length <= 1) {
      var filterOpts = '<option value="">All Partners</option>';
      res.partner_totals.forEach(function (p) {
        filterOpts += '<option value="' + escHtml(p.partner_name) + '">' + escHtml(p.partner_name) + '</option>';
      });
      $('#investPartnerFilter').html(filterOpts);
    }

    // Table
    if (!res.data.length) {
      $('#investmentsTableBody').html('<tr><td colspan="7" class="text-center" style="padding:40px;color:var(--gray-400)">No investments found.</td></tr>');
      return;
    }
    var html = '';
    res.data.forEach(function (inv) {
      html += '<tr>' +
        '<td><strong>' + escHtml(inv.partner_name) + '</strong></td>' +
        '<td class="text-end" style="font-weight:600">' + formatCurrency(inv.amount) + '</td>' +
        '<td>' + formatDate(inv.investment_date) + '</td>' +
        '<td>' + escHtml(inv.description || '—') + '</td>' +
        '<td>' + escHtml((inv.payment_method || '').replace(/_/g, ' ')) + '</td>' +
        '<td>' + escHtml(inv.reference_number || '—') + '</td>' +
        '<td>' +
          '<button class="btn btn-outline-primary btn-sm me-1" onclick="editInvestment(' + inv.id + ')" title="Edit"><i class="fas fa-edit"></i></button>' +
          '<button class="btn btn-outline-danger btn-sm" onclick="deleteInvestment(' + inv.id + ')" title="Delete"><i class="fas fa-trash"></i></button>' +
        '</td>' +
        '</tr>';
    });
    $('#investmentsTableBody').html(html);
    renderPagination('#investmentsPagination', res.page, res.per_page, res.total, 'loadInvestments');
  });
}

function openAddInvestmentModal() {
  editingInvestId = null;
  $('#addInvestmentForm')[0].reset();
  $('#investmentModalTitle').text('Add Investment');
  $('input[name="investment_date"]').val(new Date().toISOString().split('T')[0]);
  new bootstrap.Modal(document.getElementById('addInvestmentModal')).show();
}

function editInvestment(id) {
  // Fetch from current table data (simpler than API call)
  apiGet('/admin/api/expenses/', { action: 'investments' }).done(function (res) {
    if (!res.success) return;
    var inv = res.data.find(function (i) { return parseInt(i.id) === id; });
    if (!inv) { showAlertModal('Investment not found.', 'danger'); return; }
    editingInvestId = inv.id;
    $('#investmentModalTitle').text('Edit Investment');
    $('input[name="partner_name"]').val(inv.partner_name);
    $('input[name="inv_amount"]').val(inv.amount);
    $('input[name="investment_date"]').val(inv.investment_date);
    $('textarea[name="inv_description"]').val(inv.description);
    $('select[name="inv_payment_method"]').val(inv.payment_method);
    $('input[name="reference_number"]').val(inv.reference_number);
    $('textarea[name="inv_notes"]').val(inv.notes);
    new bootstrap.Modal(document.getElementById('addInvestmentModal')).show();
  });
}

function saveInvestment() {
  var data = {
    action:           editingInvestId ? 'update_investment' : 'create_investment',
    partner_name:     $('input[name="partner_name"]').val(),
    amount:           $('input[name="inv_amount"]').val(),
    investment_date:  $('input[name="investment_date"]').val(),
    description:      $('textarea[name="inv_description"]').val(),
    payment_method:   $('select[name="inv_payment_method"]').val(),
    reference_number: $('input[name="reference_number"]').val(),
    notes:            $('textarea[name="inv_notes"]').val()
  };
  if (editingInvestId) data.id = editingInvestId;

  if (!data.partner_name || !data.amount || !data.investment_date) {
    showAlertModal('Partner name, amount and date are required.', 'warning'); return;
  }

  showLoader('Saving...');
  apiPost('/admin/api/expenses/', data).done(function (res) {
    hideLoader();
    if (res.success) {
      showAlertModal(res.message, 'success');
      bootstrap.Modal.getInstance(document.getElementById('addInvestmentModal')).hide();
      loadInvestments(1);
      loadOverview();
    } else {
      showAlertModal(res.message, 'danger');
    }
  }).fail(function () { hideLoader(); showAlertModal('Network error.', 'danger'); });
}

function deleteInvestment(id) {
  confirmThen('Delete this investment record?', function () {
    showLoader('Deleting...');
    apiPost('/admin/api/expenses/', { action: 'delete_investment', id: id }).done(function (res) {
      hideLoader();
      showAlertModal(res.message, res.success ? 'success' : 'danger');
      if (res.success) { loadInvestments(1); loadOverview(); }
    });
  });
}

/* ═══════════════════════════════ Income ═══════════════════════════════ */

function loadIncome() {
  var params = {
    action: 'income',
    date_from: $('#incomeDateFrom').val() || '',
    date_to: $('#incomeDateTo').val() || '',
    brand_id: $('#incomeFilterBrand').val() || ''
  };
  apiGet('/admin/api/expenses/', params).done(function (res) {
    if (!res.success) return;

    $('#incomeOrderCount').text(res.summary.order_count);
    $('#incomeTotalRevenue').text(formatCurrency(res.summary.total_revenue));

    // Brand breakdown
    var brandHtml = '';
    res.by_brand.forEach(function (b) {
      brandHtml += '<tr>' +
        '<td>' + escHtml(b.brand_name || 'Unknown') + '</td>' +
        '<td class="text-end">' + b.orders + '</td>' +
        '<td class="text-end">' + formatCurrency(b.revenue) + '</td>' +
        '</tr>';
    });
    $('#incomeBrandBody').html(brandHtml || '<tr><td colspan="3" class="text-center text-muted">No data</td></tr>');

    // Monthly breakdown
    var monthHtml = '';
    res.monthly.forEach(function (m) {
      monthHtml += '<tr>' +
        '<td>' + escHtml(m.label) + '</td>' +
        '<td class="text-end">' + m.orders + '</td>' +
        '<td class="text-end">' + formatCurrency(m.revenue) + '</td>' +
        '</tr>';
    });
    $('#incomeMonthlyBody').html(monthHtml || '<tr><td colspan="3" class="text-center text-muted">No data</td></tr>');
  });
}

function filterIncome() { loadIncome(); }

/* ═══════════════════════════════ Export ═══════════════════════════════ */

function exportExpenses() {
  var params = {
    action: 'list', page: 1, per_page: 50,
    category_id: $('#filterCategory').val() || '',
    status: $('#filterStatus').val() || '',
    date_from: $('#filterDateFrom').val() || '',
    date_to: $('#filterDateTo').val() || '',
    search: $('#filterSearch').val() || '',
    brand_id: $('#filterBrand').val() || ''
  };

  showLoader('Exporting...');
  apiGet('/admin/api/expenses/', params).done(function (res) {
    hideLoader();
    if (!res.success || !res.data.length) { showAlertModal('No data to export.', 'warning'); return; }

    var csv = [['Title', 'Category', 'Brand', 'Amount', 'Tax', 'Total', 'Date', 'Vendor', 'Status', 'Invoice'].join(',')];
    res.data.forEach(function (e) {
      csv.push([
        '"' + (e.title || '').replace(/"/g, '""') + '"',
        '"' + (e.category_name || '') + '"',
        '"' + (e.brand_name || 'Global') + '"',
        e.amount, e.tax_amount,
        (parseFloat(e.amount) + parseFloat(e.tax_amount || 0)).toFixed(2),
        e.expense_date,
        '"' + (e.vendor_name || '') + '"',
        e.payment_status,
        '"' + (e.invoice_number || '') + '"'
      ].join(','));
    });

    var blob = new Blob([csv.join('\n')], { type: 'text/csv' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'expenses_' + new Date().toISOString().split('T')[0] + '.csv';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
  });
}

/* ═══════════════════════════════ Pagination ═══════════════════════════════ */

function renderPagination(selector, page, perPage, total, fnName) {
  var totalPages = Math.ceil(total / perPage);
  if (totalPages <= 1) { $(selector).html(''); return; }

  var html = '<nav><ul class="pagination pagination-sm justify-content-center mb-0">';
  html += '<li class="page-item' + (page <= 1 ? ' disabled' : '') + '">' +
    '<a class="page-link" href="#" onclick="' + fnName + '(' + (page - 1) + ');return false">Prev</a></li>';

  var start = Math.max(1, page - 2);
  var end = Math.min(totalPages, page + 2);
  for (var i = start; i <= end; i++) {
    html += '<li class="page-item' + (i === page ? ' active' : '') + '">' +
      '<a class="page-link" href="#" onclick="' + fnName + '(' + i + ');return false">' + i + '</a></li>';
  }

  html += '<li class="page-item' + (page >= totalPages ? ' disabled' : '') + '">' +
    '<a class="page-link" href="#" onclick="' + fnName + '(' + (page + 1) + ');return false">Next</a></li>';
  html += '</ul></nav>';
  $(selector).html(html);
}
