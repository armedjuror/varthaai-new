/* Varthaai Admin — B2B Dashboard */

var b2bRevenueChart = null;
var trendData = [];
var flavorNames = [];

var METRIC_DEFS = {
  revenue:       { label: 'Revenue',       kind: 'money' },
  orders:        { label: 'Orders',        kind: 'count', color: '#2563eb' },
  leads_created: { label: 'Leads Created', kind: 'count', color: '#8b5cf6' },
  converted:     { label: 'Converted',     kind: 'count', color: '#16a34a' },
  packs:         { label: 'Packs per Flavour', kind: 'count' },
};
var DAILY_METRIC_ORDER = ['revenue', 'orders', 'leads_created', 'converted', 'packs'];
var activeMetrics = ['revenue'];
var FLAVOR_COLORS = ['#0ea5e9', '#f97316', '#ec4899', '#14b8a6', '#a855f7', '#eab308', '#ef4444', '#22c55e'];
var chartType = 'line';

var DASH_LISTS = {};   // full (uncapped) lists, keyed by section name — used by "Show All" modal
var CARD_ROW_CAP = 5;

$(function () {
  apiGet('/admin/api/b2b-dashboard/')
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      var d = res.data || {};
      renderRevenueKpis(d.revenue_stats || {});
      renderStats(d.order_stats, d.pipeline);
      renderPipeline(d.pipeline);
      renderCappedSection('followups', d.follow_ups || [], renderFollowUps, '#followUpsBody');
      renderCappedSection('overdue', d.overdue_payments || [], renderOverdue, '#overdueBody');
      renderCappedSection('pendingOrders', d.pending_orders || [], renderPendingOrders, '#pendingOrdersBody');
      renderCappedSection('topCompanies', d.top_companies || [], renderTopCompanies, '#topCompaniesBody');

      applyTrendData(d);
      var range = d.trend_range || {};
      $('#trendStartDate').val(range.start || '');
      $('#trendEndDate').val(range.end || '');
      renderMetricToggle();
      renderTrendChart(trendData);
    })
    .fail(function () { showAlertModal('Failed to load dashboard.', 'danger'); });

  $('#trendStartDate, #trendEndDate').on('change', function () { loadTrend(); });
});

function applyTrendData(d) {
  trendData = d.revenue_trend || [];
  flavorNames = d.flavor_names || [];
}

function loadTrend(params) {
  params = params || {
    start_date: $('#trendStartDate').val(),
    end_date: $('#trendEndDate').val(),
  };
  apiGet('/admin/api/b2b-dashboard/', params)
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      var d = res.data || {};
      applyTrendData(d);
      var range = d.trend_range || {};
      $('#trendStartDate').val(range.start || '');
      $('#trendEndDate').val(range.end || '');
      renderTrendChart(trendData);
    })
    .fail(function () { showAlertModal('Failed to load trend data.', 'danger'); });
}

function resetTrendRange() {
  $('#trendStartDate').val('');
  $('#trendEndDate').val('');
  loadTrend({});
}

/* ── Fixed-height list sections + "Show All" modal ─────────────────────── */

function renderCappedSection(name, items, renderFn, targetSel) {
  DASH_LISTS[name] = items;
  renderFn(items.slice(0, CARD_ROW_CAP), targetSel);
  var $btn = $('#' + name + 'ShowAllBtn');
  if (items.length > CARD_ROW_CAP) {
    $btn.text('Show All (' + items.length + ')').show();
  } else {
    $btn.hide();
  }
}

function openDashListModal(name, title, renderFn) {
  $('#dashListModalTitle').text(title);
  renderFn(DASH_LISTS[name] || [], '#dashListModalBody');
  $('#dashListModal').modal('show');
}

/* ── KPI cards ──────────────────────────────────────────────────────────── */

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

/* ── Trend chart: metric toggle (Revenue / Orders / Leads / Converted / Packs per Flavour) ──
   Everything renders as a line, all metrics plotted day-wise on the same 30-day x-axis. Pills
   are clubbable — click to add/remove a metric, at least one stays selected. */

function renderMetricToggle() {
  var html = '';
  DAILY_METRIC_ORDER.forEach(function (key) {
    var active = activeMetrics.indexOf(key) !== -1;
    html += '<span class="metric-pill' + (active ? ' active' : '') + '" data-metric="' + key + '" onclick="toggleMetric(\'' + key + '\')">' +
      escHtml(METRIC_DEFS[key].label) + '</span>';
  });
  $('#trendMetricToggle').html(html);
}

function toggleMetric(key) {
  var idx = activeMetrics.indexOf(key);
  if (idx !== -1) {
    if (activeMetrics.length > 1) activeMetrics.splice(idx, 1); // keep at least one selected
  } else {
    activeMetrics.push(key);
  }
  renderMetricToggle();
  renderTrendChart(trendData);
}

function setChartType(type) {
  chartType = type;
  $('.chart-type-btn').removeClass('active');
  $('.chart-type-btn[data-type="' + type + '"]').addClass('active');
  renderTrendChart(trendData);
}

function renderTrendChart(trend) {
  var labels = trend.map(function (t) { return t.label; });
  var hasMoney = activeMetrics.indexOf('revenue') !== -1;
  var hasOrders = activeMetrics.indexOf('orders') !== -1;
  var hasPacks = activeMetrics.indexOf('packs') !== -1;
  var hasLeads = activeMetrics.indexOf('leads_created') !== -1;
  var hasConverted = activeMetrics.indexOf('converted') !== -1;
  var isBar = chartType === 'bar';

  var datasets = [];
  var legendHtml = '';

  function addLegend(color, label) {
    legendHtml += '<span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:' + color + ';margin-right:5px"></span>' + escHtml(label) + '</span>';
  }

  // Revenue: Paid + Pending always sum to the true total, so this stack is
  // already "total height + paid/pending split" in both bar and line mode.
  if (hasMoney) {
    datasets.push({
      label: 'Paid', data: trend.map(function (t) { return t.paid; }),
      borderColor: '#16a34a', backgroundColor: '#16a34a', pointRadius: 2, tension: 0.3,
      borderRadius: isBar ? 4 : 0, stack: 'rev',
      yAxisID: 'money', _metricKey: 'revenue',
    });
    datasets.push({
      label: 'Pending', data: trend.map(function (t) { return t.pending; }),
      borderColor: '#f59e0b', backgroundColor: '#f59e0b', pointRadius: 2, tension: 0.3,
      borderRadius: isBar ? 4 : 0, stack: 'rev',
      yAxisID: 'money', _metricKey: 'revenue',
    });
    addLegend('#16a34a', 'Paid');
    addLegend('#f59e0b', 'Pending');
  }

  var countAxisId = hasMoney ? 'count' : 'money'; // reuse 'money' scale key as the sole left axis when revenue isn't shown

  if (isBar) {
    // Bar mode: Orders and Packs per Flavour are separate bars, each showing
    // its own real total (no rescaling one to fit the other).
    if (hasOrders) {
      datasets.push({
        label: 'Orders', data: trend.map(function (t) { return t.orders; }),
        borderColor: METRIC_DEFS.orders.color, backgroundColor: METRIC_DEFS.orders.color, borderRadius: 4,
        stack: 'orders', yAxisID: countAxisId, _metricKey: 'orders',
      });
      addLegend(METRIC_DEFS.orders.color, 'Orders');
    }
    if (hasPacks) {
      flavorNames.forEach(function (flavor, i) {
        var color = FLAVOR_COLORS[i % FLAVOR_COLORS.length];
        datasets.push({
          label: flavor, data: trend.map(function (t) { return (t.packs_by_flavor || {})[flavor] || 0; }),
          borderColor: color, backgroundColor: color, borderRadius: 4,
          stack: 'packs', yAxisID: countAxisId, _metricKey: 'packs',
        });
        addLegend(color, flavor + ' (packs)');
      });
      if (!flavorNames.length) {
        legendHtml += '<span style="color:var(--gray-400)">No pack sales in the last 30 days</span>';
      }
    }

    if (hasLeads && hasConverted) {
      // Total height = Leads Created. Colors = Converted share vs the rest.
      var convertedSeg = trend.map(function (t) { return Math.min(t.converted, t.leads_created); });
      datasets.push({
        label: 'Converted', data: convertedSeg,
        borderColor: '#16a34a', backgroundColor: '#16a34a', borderRadius: 4,
        stack: 'leads_converted', yAxisID: countAxisId, _metricKey: 'converted',
        _rawData: trend.map(function (t) { return t.converted; }),
      });
      datasets.push({
        label: 'Leads (Not Converted)', data: trend.map(function (t, idx) { return Math.max(t.leads_created - convertedSeg[idx], 0); }),
        borderColor: '#8b5cf6', backgroundColor: '#8b5cf6', borderRadius: 4,
        stack: 'leads_converted', yAxisID: countAxisId, _metricKey: 'leads_created',
      });
      addLegend('#16a34a', 'Converted');
      addLegend('#8b5cf6', 'Leads (Not Converted)');
    } else if (hasLeads) {
      datasets.push({
        label: 'Leads Created', data: trend.map(function (t) { return t.leads_created; }),
        borderColor: METRIC_DEFS.leads_created.color, backgroundColor: METRIC_DEFS.leads_created.color, borderRadius: 4,
        stack: 'leads_converted', yAxisID: countAxisId, _metricKey: 'leads_created',
      });
      addLegend(METRIC_DEFS.leads_created.color, 'Leads Created');
    } else if (hasConverted) {
      datasets.push({
        label: 'Converted', data: trend.map(function (t) { return t.converted; }),
        borderColor: METRIC_DEFS.converted.color, backgroundColor: METRIC_DEFS.converted.color, borderRadius: 4,
        stack: 'leads_converted', yAxisID: countAxisId, _metricKey: 'converted',
      });
      addLegend(METRIC_DEFS.converted.color, 'Converted');
    }
  } else {
    // Line mode: plot each metric's real value independently — proportional
    // envelopes only make sense as stacked-bar segments.
    var countMetrics = activeMetrics.filter(function (m) { return m !== 'revenue' && m !== 'packs'; });
    countMetrics.forEach(function (m) {
      var def = METRIC_DEFS[m];
      datasets.push({
        label: def.label, data: trend.map(function (t) { return t[m]; }),
        borderColor: def.color, backgroundColor: def.color, pointRadius: 2, tension: 0.3,
        yAxisID: countAxisId, _metricKey: m,
      });
      addLegend(def.color, def.label);
    });
    if (hasPacks) {
      flavorNames.forEach(function (flavor, i) {
        var color = FLAVOR_COLORS[i % FLAVOR_COLORS.length];
        datasets.push({
          label: flavor, data: trend.map(function (t) { return (t.packs_by_flavor || {})[flavor] || 0; }),
          borderColor: color, backgroundColor: color, pointRadius: 2, tension: 0.3,
          yAxisID: countAxisId, _metricKey: 'packs',
        });
        addLegend(color, flavor + ' (packs)');
      });
      if (!flavorNames.length) {
        legendHtml += '<span style="color:var(--gray-400)">No pack sales in the last 30 days</span>';
      }
    }
  }

  $('#trendChartLegend').html(legendHtml);

  if (b2bRevenueChart) { b2bRevenueChart.destroy(); }
  var ctx = document.getElementById('b2bRevenueChart').getContext('2d');

  var hasCounts = hasOrders || hasPacks || hasLeads || hasConverted;
  var scales = {
    x: { stacked: isBar, grid: { display: false }, border: { display: false }, ticks: { color: '#94a3b8', font: { size: 11 }, maxTicksLimit: 10, maxRotation: 0 } },
  };
  if (hasMoney) {
    scales.money = {
      stacked: isBar, position: 'left', grid: { color: '#f1f5f9' }, border: { display: false },
      ticks: { color: '#94a3b8', font: { size: 11 }, callback: function (v) { return v >= 1000 ? '₹' + (v / 1000).toFixed(1) + 'k' : '₹' + v; } },
    };
    if (hasCounts) {
      scales.count = {
        stacked: isBar, position: 'right', grid: { display: false }, border: { display: false },
        ticks: { color: '#94a3b8', font: { size: 11 }, precision: 0 },
      };
    }
  } else if (hasCounts) {
    scales.money = {
      stacked: isBar, position: 'left', grid: { color: '#f1f5f9' }, border: { display: false },
      ticks: { color: '#94a3b8', font: { size: 11 }, precision: 0 },
    };
  }

  b2bRevenueChart = new Chart(ctx, {
    type: chartType,
    data: { labels: labels, datasets: datasets },
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
          footerColor: '#f8fafc',
          padding: 12,
          cornerRadius: 10,
          callbacks: {
            label: function (ctx) {
              var isMoney = ctx.dataset._metricKey === 'revenue';
              var raw = ctx.dataset._rawData ? ctx.dataset._rawData[ctx.dataIndex] : ctx.parsed.y;
              return '  ' + ctx.dataset.label + ': ' + (isMoney ? formatCurrency(raw) : raw);
            },
            footer: function (items) {
              if (!items.length) return '';
              var t = trend[items[0].dataIndex];
              var lines = [];
              if (hasMoney) lines.push('Total Revenue: ' + formatCurrency(t.revenue));
              if (isBar && hasOrders) lines.push('Total Orders: ' + t.orders);
              if (isBar && hasPacks) {
                var totalPacks = flavorNames.reduce(function (sum, f) { return sum + ((t.packs_by_flavor || {})[f] || 0); }, 0);
                lines.push('Total Packs Sold: ' + totalPacks);
              }
              if (isBar && hasLeads && hasConverted) lines.push('Total Leads: ' + t.leads_created + ' (Converted: ' + t.converted + ')');
              return lines;
            },
          },
        },
      },
      scales: scales,
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

function renderFollowUps(items, target) {
  target = target || '#followUpsBody';
  if (!items.length) {
    $(target).html('<div class="text-center py-4" style="color:var(--gray-400)"><i class="fas fa-check-circle fa-2x mb-2 d-block" style="color:#16a34a"></i>No pending follow-ups</div>');
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
  $(target).html(html);
}

function renderOverdue(items, target) {
  target = target || '#overdueBody';
  if (!items.length) {
    $(target).html('<div class="text-center py-4" style="color:var(--gray-400)"><i class="fas fa-check-circle fa-2x mb-2 d-block" style="color:#16a34a"></i>No overdue payments</div>');
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
  $(target).html(html);
}

function renderPendingOrders(items, target) {
  target = target || '#pendingOrdersBody';
  if (!items.length) {
    $(target).html('<div class="text-center py-4" style="color:var(--gray-400)"><i class="fas fa-bag-shopping fa-2x mb-2 d-block"></i>No active orders</div>');
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
  $(target).html(html);
}

function renderTopCompanies(items, target) {
  target = target || '#topCompaniesBody';
  var filtered = items.filter(function (c) { return parseFloat(c.revenue) || parseInt(c.order_count); });
  if (!filtered.length) {
    $(target).html('<div class="text-center py-4" style="color:var(--gray-400)">No data yet</div>');
    return;
  }
  var html = '<div class="table-responsive"><table class="table table-sm dash-table mb-0"><thead><tr><th>Company</th><th>Orders</th><th>Revenue</th><th>Outstanding</th></tr></thead><tbody>';
  filtered.forEach(function (c) {
    html += '<tr>' +
      '<td><a href="/admin/b2b/' + c.id + '/">' + escHtml(c.company_name) + '</a></td>' +
      '<td>' + (c.order_count || 0) + '</td>' +
      '<td style="font-weight:600">' + formatCurrency(c.revenue) + '</td>' +
      '<td style="' + (parseFloat(c.outstanding) > 0 ? 'color:#dc2626;font-weight:600' : '') + '">' + formatCurrency(c.outstanding) + '</td>' +
    '</tr>';
  });
  html += '</tbody></table></div>';
  $(target).html(html);
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
