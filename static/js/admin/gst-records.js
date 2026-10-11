/* Varthaai Admin — GST Records (read-only; CA login) */
var currentTab = 'invoices';
var currentPage = 1;
var searchTimer = null;

$(function () {
  var now = new Date();
  $('#periodMonth').val(now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0'));
  var fyStart = now.getMonth() >= 3 ? now.getFullYear() : now.getFullYear() - 1;
  var fyOpts = '';
  for (var y = fyStart; y >= 2026; y--) fyOpts += '<option value="' + y + '-' + String((y + 1) % 100).padStart(2, '0') + '">' + y + '-' + String((y + 1) % 100).padStart(2, '0') + '</option>';
  $('#periodFy').html(fyOpts);

  $('#periodType').on('change', function () {
    $('.period').addClass('d-none');
    $('.period-' + this.value).removeClass('d-none');
    reload();
  });
  $('#periodMonth, #periodFy, #periodFrom, #periodTo, #supplyType').on('change', reload);
  $('#search').on('input', function () { clearTimeout(searchTimer); searchTimer = setTimeout(reload, 300); });
  $('#recordTabs').on('click', 'a', function (e) {
    e.preventDefault();
    $('#recordTabs a').removeClass('active');
    $(this).addClass('active');
    currentTab = $(this).data('tab');
    reload();
  });
  load();
});

function reload() { currentPage = 1; load(); }

function filters() {
  var p = { supply_type: $('#supplyType').val(), search: $('#search').val().trim() };
  var t = $('#periodType').val();
  if (t === 'month') p.month = $('#periodMonth').val();
  else if (t === 'fy') p.fy = $('#periodFy').val();
  else if (t === 'custom') { p.from = $('#periodFrom').val(); p.to = $('#periodTo').val(); }
  return p;
}

function load() {
  var params = $.extend({ tab: currentTab, page: currentPage }, filters());
  apiGet('/admin/api/gst/records/', params)
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      var d = res.data;
      $('#pendingCount').text(d.pending_count || '');
      $('#missingCount').text(d.missing_count || '');
      if (d.gaps && d.gaps.length) {
        $('#gapAlert').removeClass('d-none').text('Numbering gap found: ' + d.gaps.map(function (g) {
          return g.series + ' missing ' + g.count + ' (' + g.missing.join(', ') + ')';
        }).join('; '));
      } else {
        $('#gapAlert').addClass('d-none');
      }
      render(d);
    })
    .fail(function (xhr) { showAlertModal(apiErrorMessage(xhr, 'Failed to load records.'), 'danger'); });
}

function money(v) { return formatCurrency(v); }

function render(d) {
  var head, rows, foot = '';
  if (currentTab === 'invoices' || currentTab === 'credit_notes') {
    var isCn = currentTab === 'credit_notes';
    head = '<tr><th>Number</th><th>Date</th>' + (isCn ? '<th>Invoice</th><th>Reason</th>' : '<th>Order</th>') +
      '<th>Customer</th><th>GSTIN</th><th>Place of supply</th><th class="text-end">Taxable</th><th class="text-end">CGST</th>' +
      '<th class="text-end">SGST</th><th class="text-end">IGST</th><th class="text-end">Total</th>' + (isCn ? '' : '<th>Status</th>') + '<th></th></tr>';
    rows = d.rows.map(function (r) {
      return '<tr>' +
        '<td style="font-weight:600">' + escHtml(r.number) + '</td><td>' + formatDate(r.date) + '</td>' +
        (isCn ? '<td>' + escHtml(r.invoice_number) + '</td><td>' + escHtml(r.reason) + '</td>' : '<td style="font-size:0.78rem">' + escHtml(r.order_id) + '</td>') +
        '<td>' + escHtml(r.customer) + '</td><td>' + escHtml(r.gstin || '—') + '</td><td>' + escHtml(r.place_of_supply) + '</td>' +
        '<td class="text-end">' + money(r.taxable) + '</td><td class="text-end">' + money(r.cgst) + '</td>' +
        '<td class="text-end">' + money(r.sgst) + '</td><td class="text-end">' + money(r.igst) + '</td>' +
        '<td class="text-end" style="font-weight:600">' + money(r.total) + '</td>' +
        (isCn ? '' : '<td>' + (r.status === 'active' ? 'Active' : '<span class="text-danger">Cancelled</span>') + '</td>') +
        '<td class="table-actions"><a class="btn-icon view" target="_blank" title="View" href="' + r.print_url + '"><i class="fas fa-eye"></i></a>' +
        '<a class="btn-icon edit" target="_blank" title="PDF" href="' + r.pdf_url + '"><i class="fas fa-file-pdf"></i></a></td></tr>';
    }).join('');
    var t = d.totals || {};
    foot = '<tr style="font-weight:700"><td colspan="' + (isCn ? 6 : 5) + '">Total for period (' + d.count + ')</td>' +
      '<td class="text-end">' + money(t.taxable) + '</td><td class="text-end">' + money(t.cgst) + '</td>' +
      '<td class="text-end">' + money(t.sgst) + '</td><td class="text-end">' + money(t.igst) + '</td>' +
      '<td class="text-end">' + money(t.total) + '</td><td colspan="' + (isCn ? 1 : 2) + '"></td></tr>';
  } else if (currentTab === 'pending') {
    head = '<tr><th>Order</th><th>Company</th><th>Returned on</th><th class="text-end">Credit note value</th><th></th></tr>';
    rows = d.rows.map(function (r) {
      return '<tr><td>' + escHtml(r.order_id) + '</td><td>' + escHtml(r.company) + '</td><td>' + formatDate(r.date) + '</td>' +
        '<td class="text-end">' + money(r.amount) + '</td><td><a href="' + r.url + '">Open order</a></td></tr>';
    }).join('');
  } else {
    head = '<tr><th>Type</th><th>Order</th><th>Customer</th><th>Status</th><th>Order date</th><th></th></tr>';
    rows = d.rows.map(function (r) {
      return '<tr><td>' + r.kind + '</td><td>' + escHtml(r.order_id) + '</td><td>' + escHtml(r.name) + '</td><td>' + escHtml(r.status) + '</td>' +
        '<td>' + formatDate(r.order_date) + '</td><td><a href="' + r.url + '">Open order</a></td></tr>';
    }).join('');
    if (d.rows.length) {
      foot = '<tr><td colspan="6" style="font-size:0.8rem;color:var(--gray-400)">Orders dispatched before GST billing was live are expected here until the backfill (manage.py backfill_invoices) has run.</td></tr>';
    }
  }
  $('#recordsHead').html(head);
  $('#recordsBody').html(rows || '<tr><td colspan="14" class="text-center" style="padding:30px;color:var(--gray-400)">Nothing for this period.</td></tr>');
  $('#recordsFoot').html(foot);
  renderPagination(d);
}

function renderPagination(d) {
  var pages = Math.ceil((d.count || 0) / (d.per_page || 50));
  if (pages <= 1 || (currentTab !== 'invoices' && currentTab !== 'credit_notes')) { $('#recordsPagination').html(''); return; }
  var html = '<nav><ul class="pagination pagination-sm mb-0 justify-content-center">';
  for (var i = 1; i <= pages; i++) {
    html += '<li class="page-item' + (i === d.page ? ' active' : '') + '"><a class="page-link" href="#" onclick="goPage(' + i + ');return false">' + i + '</a></li>';
  }
  $('#recordsPagination').html(html + '</ul></nav>');
}

function goPage(p) { currentPage = p; load(); }

function exportZip() {
  var p = filters();
  delete p.search;
  window.location.href = '/admin/gst/records/export/?' + $.param(p);
}
