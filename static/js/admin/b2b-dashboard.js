/* Varthaai Admin — B2B Dashboard */

var b2bRevenueChart = null;

$(function () {
  apiGet('/admin/api/b2b-dashboard/')
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      var d = res.data || {};
      renderRevenueKpis(d.revenue_stats || {});
      renderStats(d.order_stats, d.pipeline);
      renderPipeline(d.pipeline);
      renderFollowUps(d.follow_ups || []);
      renderOverdue(d.overdue_payments || []);
      renderPendingOrders(d.pending_orders || []);
      renderTopCompanies(d.top_companies || []);
      renderRevenueChart(d.revenue_trend || []);
    })
    .fail(function () { showAlertModal('Failed to load dashboard.', 'danger'); });
});

function renderRevenueKpis(rev) {
  var tm = rev.this_month || {};
  var trend = tm.revenue_vs_last;
  var trendHtml = '';
  if (trend !== undefined && trend !== null) {
    var cls = trend > 0 ? 'trend-up' : (trend < 0 ? 'trend-down' : 'trend-flat');
    var arrow = trend > 0 ? 'fa-arrow-trend-up' : (trend < 0 ? 'fa-arrow-trend-down' : 'fa-minus');
    trendHtml = '<div class="kpi-trend ' + cls + '"><i class="fas ' + arrow + '"></i>' +
      Math.abs(trend) + '% vs last month</div>';
  }

  var cards = [
    {
      icon: 'fa-indian-rupee-sign', iconCls: 'green',
      value: formatCurrency((rev.today || {}).revenue || 0),
      label: "Today's Revenue",
      sub: ((rev.today || {}).orders || 0) + ' order' + (((rev.today || {}).orders || 0) !== 1 ? 's' : '') + ' today',
      trend: '',
    },
    {
      icon: 'fa-calendar-check', iconCls: 'teal',
      value: formatCurrency(tm.revenue || 0),
      label: 'This Month Revenue',
      sub: (tm.orders || 0) + ' orders',
      trend: trendHtml,
    },
    {
      icon: 'fa-coins', iconCls: 'blue',
      value: formatCurrency(rev.all_time_revenue || 0),
      label: 'All-Time Revenue',
      sub: 'Since inception',
      trend: '',
    },
    {
      icon: 'fa-receipt', iconCls: 'amber',
      value: formatCurrency(rev.avg_order_value || 0),
      label: 'Avg Order Value',
      sub: 'Across all orders',
      trend: '',
    },
  ];

  var html = '';
  cards.forEach(function (c) {
    html +=
      '<div class="col-sm-6 col-xl-3">' +
        '<div class="kpi-card">' +
          '<div class="kpi-icon ' + c.iconCls + '"><i class="fas ' + c.icon + '"></i></div>' +
          '<div class="kpi-value">' + c.value + '</div>' +
          '<div class="kpi-label">' + c.label + '</div>' +
          '<div class="kpi-sub">' + escHtml(c.sub) + '</div>' +
          c.trend +
        '</div>' +
      '</div>';
  });
  $('#revenueKpis').html(html);
}

function renderRevenueChart(trend) {
  var labels  = trend.map(function (t) { return t.label; });
  var paid    = trend.map(function (t) { return t.paid; });
  var pending = trend.map(function (t) { return t.pending; });

  if (b2bRevenueChart) { b2bRevenueChart.destroy(); }
  var ctx = document.getElementById('b2bRevenueChart').getContext('2d');

  b2bRevenueChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [
        { label: 'Paid', data: paid, backgroundColor: '#16a34a', borderRadius: 4, stack: 'rev' },
        { label: 'Pending', data: pending, backgroundColor: '#f59e0b', borderRadius: 4, stack: 'rev' },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1e293b',
          titleColor: '#94a3b8',
          bodyColor: '#f8fafc',
          padding: 12,
          cornerRadius: 10,
          callbacks: {
            label: function (ctx) { return '  ' + ctx.dataset.label + ': ' + formatCurrency(ctx.parsed.y); },
          },
        },
      },
      scales: {
        x: { stacked: true, grid: { display: false }, border: { display: false }, ticks: { color: '#94a3b8', font: { size: 11 }, maxTicksLimit: 10, maxRotation: 0 } },
        y: {
          stacked: true,
          grid: { color: '#f1f5f9' },
          border: { display: false },
          ticks: {
            color: '#94a3b8', font: { size: 11 },
            callback: function (v) { return v >= 1000 ? '₹' + (v / 1000).toFixed(1) + 'k' : '₹' + v; },
          },
        },
      },
    },
  });
}

function renderStats(stats, pipeline) {
  var totalCompanies = 0;
  for (var k in pipeline) totalCompanies += pipeline[k];

  var cards = [
    { icon: 'fa-building',           bg: '#eff6ff', color: '#3b82f6', value: totalCompanies, label: 'Companies' },
    { icon: 'fa-file-invoice',       bg: '#f0fdf4', color: '#16a34a', value: parseInt(stats.pending_orders) || 0, label: 'Active Orders' },
    { icon: 'fa-indian-rupee-sign',  bg: '#fffbeb', color: '#b45309', value: formatCurrency(stats.total_outstanding), label: 'Outstanding' },
    { icon: 'fa-clock',              bg: '#fef2f2', color: '#dc2626', value: formatCurrency(stats.total_overdue), label: 'Overdue' },
    { icon: 'fa-wallet',             bg: '#f0fdf4', color: '#16a34a', value: formatCurrency(stats.total_advance), label: 'Advance Credit' }
  ];

  var html = '';
  cards.forEach(function (c) {
    html += '<div class="col-6 col-lg"><div class="b2b-stat">' +
      '<div class="d-flex justify-content-between align-items-start">' +
        '<div><div class="stat-value">' + c.value + '</div><div class="stat-label">' + c.label + '</div></div>' +
        '<div class="stat-icon" style="background:' + c.bg + ';color:' + c.color + '"><i class="fas ' + c.icon + '"></i></div>' +
      '</div>' +
    '</div></div>';
  });
  $('#statCards').html(html);
}

function renderPipeline(pipeline) {
  var stages = [
    { key: 'lead',        label: 'Lead',        color: '#9ca3af' },
    { key: 'contacted',   label: 'Contacted',   color: '#3b82f6' },
    { key: 'negotiation', label: 'Negotiation', color: '#f59e0b' },
    { key: 'converted',   label: 'Converted',   color: '#16a34a' },
    { key: 'lost',        label: 'Lost',        color: '#dc2626' }
  ];

  var total = 0;
  stages.forEach(function (s) { total += (pipeline[s.key] || 0); });
  if (!total) return;

  var labelsHtml = '';
  var barHtml = '';
  stages.forEach(function (s) {
    var cnt = pipeline[s.key] || 0;
    if (!cnt) return;
    var pct = Math.max(5, (cnt / total) * 100);
    labelsHtml += '<div style="text-align:center;font-size:0.75rem"><div style="font-weight:600">' + cnt + '</div><div style="color:var(--gray-400)">' + s.label + '</div></div>';
    barHtml += '<span style="flex:' + pct + ';background:' + s.color + '"></span>';
  });

  $('#pipelineLabels').html(labelsHtml);
  $('#pipelineBar').html(barHtml);
  $('#pipelineCard').show();
}

function renderFollowUps(items) {
  if (!items.length) {
    $('#followUpsBody').html('<div class="text-center py-4" style="color:var(--gray-400)"><i class="fas fa-check-circle fa-2x mb-2 d-block" style="color:#16a34a"></i>No pending follow-ups</div>');
    return;
  }
  var html = '<div class="table-responsive"><table class="table table-sm dash-table mb-0"><thead><tr><th></th><th>Company</th><th>Subject</th><th>Due</th><th></th></tr></thead><tbody>';
  items.forEach(function (fu) {
    var typeClass = 'fu-type fu-type-' + fu.type;
    var icons = { call: 'fa-phone', meeting: 'fa-handshake', email: 'fa-envelope', whatsapp: 'fa-brands fa-whatsapp', note: 'fa-note-sticky' };
    var isOverdue = fu.follow_up_date < new Date().toISOString().slice(0, 10);
    html += '<tr>' +
      '<td><span class="' + typeClass + '"><i class="fas ' + (icons[fu.type] || 'fa-note-sticky') + '"></i></span></td>' +
      '<td><a href="/admin/b2b/' + fu.company_id + '/">' + escHtml(fu.company_name) + '</a></td>' +
      '<td>' + escHtml(fu.subject || fu.description || '—') + '</td>' +
      '<td>' + (isOverdue ? '<span class="overdue-badge">' + formatDate(fu.follow_up_date) + '</span>' : formatDate(fu.follow_up_date)) + '</td>' +
      '<td><button class="btn btn-sm btn-outline-success" onclick="markFollowUpDone(' + fu.id + ', this)" title="Mark done"><i class="fas fa-check"></i></button></td>' +
    '</tr>';
  });
  html += '</tbody></table></div>';
  $('#followUpsBody').html(html);
}

function renderOverdue(items) {
  if (!items.length) {
    $('#overdueBody').html('<div class="text-center py-4" style="color:var(--gray-400)"><i class="fas fa-check-circle fa-2x mb-2 d-block" style="color:#16a34a"></i>No overdue payments</div>');
    return;
  }
  var html = '<div class="table-responsive"><table class="table table-sm dash-table mb-0"><thead><tr><th>Order</th><th>Company</th><th>Balance</th><th>Due</th></tr></thead><tbody>';
  items.forEach(function (o) {
    var dueTd = '';
    if (o.due_date) {
      var daysOverdue = Math.floor((Date.now() - new Date(o.due_date).getTime()) / 86400000);
      dueTd = '<span class="overdue-badge">' + daysOverdue + 'd overdue</span>';
    } else {
      dueTd = '<span style="font-size:0.75rem;color:var(--gray-400)">No due date</span>';
    }
    html += '<tr>' +
      '<td style="font-family:monospace;font-size:0.8rem">' + escHtml(o.id) + '</td>' +
      '<td><a href="/admin/b2b/' + o.company_id + '/">' + escHtml(o.company_name) + '</a></td>' +
      '<td style="font-weight:600;color:#dc2626">' + formatCurrency(o.balance) + '</td>' +
      '<td>' + dueTd + '</td>' +
    '</tr>';
  });
  html += '</tbody></table></div>';
  $('#overdueBody').html(html);
}

function renderPendingOrders(items) {
  if (!items.length) {
    $('#pendingOrdersBody').html('<div class="text-center py-4" style="color:var(--gray-400)"><i class="fas fa-bag-shopping fa-2x mb-2 d-block"></i>No active orders</div>');
    return;
  }
  var html = '<div class="table-responsive"><table class="table table-sm dash-table mb-0"><thead><tr><th>Order</th><th>Company</th><th>Amount</th><th>Status</th><th>Payment</th></tr></thead><tbody>';
  items.forEach(function (o) {
    html += '<tr>' +
      '<td style="font-family:monospace;font-size:0.8rem">' + escHtml(o.id) + '</td>' +
      '<td><a href="/admin/b2b/' + o.company_id + '/">' + escHtml(o.company_name) + '</a></td>' +
      '<td style="font-weight:600">' + formatCurrency(o.total_amount) + '</td>' +
      '<td><span class="status-badge status-' + o.status + '">' + capitalize(o.status) + '</span></td>' +
      '<td><span class="status-badge status-' + o.payment_status + '">' + capitalize(o.payment_status) + '</span></td>' +
    '</tr>';
  });
  html += '</tbody></table></div>';
  $('#pendingOrdersBody').html(html);
}

function renderTopCompanies(items) {
  if (!items.length) {
    $('#topCompaniesBody').html('<div class="text-center py-4" style="color:var(--gray-400)">No data yet</div>');
    return;
  }
  var html = '<div class="table-responsive"><table class="table table-sm dash-table mb-0"><thead><tr><th>Company</th><th>Orders</th><th>Revenue</th><th>Outstanding</th></tr></thead><tbody>';
  items.forEach(function (c) {
    if (!parseFloat(c.revenue) && !parseInt(c.order_count)) return;
    html += '<tr>' +
      '<td><a href="/admin/b2b/' + c.id + '/">' + escHtml(c.company_name) + '</a></td>' +
      '<td>' + (c.order_count || 0) + '</td>' +
      '<td style="font-weight:600">' + formatCurrency(c.revenue) + '</td>' +
      '<td style="' + (parseFloat(c.outstanding) > 0 ? 'color:#dc2626;font-weight:600' : '') + '">' + formatCurrency(c.outstanding) + '</td>' +
    '</tr>';
  });
  html += '</tbody></table></div>';
  $('#topCompaniesBody').html(html);
}

function markFollowUpDone(activityId, btn) {
  apiPost('/admin/api/b2b/', { action: 'complete_follow_up', id: activityId })
    .done(function (res) {
      if (res.success) {
        $(btn).closest('tr').fadeOut(300, function () { $(this).remove(); });
      } else {
        showAlertModal(res.message, 'danger');
      }
    });
}

function capitalize(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ''; }
