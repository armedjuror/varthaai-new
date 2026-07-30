/* ── Dashboard JS ─────────────────────────────────────────────────────── */

var revenueChart = null;

var _chartPeriod = '30d';
var _dashScope   = 'brand';

$(function () {
  updateGreeting();
  loadDashboard();

  // Period tab switching
  $(document).on('click', '.period-tab', function () {
    $('.period-tab').removeClass('active');
    $(this).addClass('active');
    _chartPeriod = $(this).data('period');
    if (_chartPeriod === 'custom') {
      $('#customDateRow').addClass('show');
      // set sensible defaults: this month
      var now = new Date();
      var y = now.getFullYear(), m = String(now.getMonth() + 1).padStart(2, '0');
      $('#chartFrom').val(y + '-' + m + '-01');
      $('#chartTo').val(now.toISOString().slice(0, 10));
    } else {
      $('#customDateRow').removeClass('show');
      loadChart(_chartPeriod);
    }
  });

  // Scope toggle (brand vs all)
  $(document).on('click', '#scopeToggle .btn', function () {
    $('#scopeToggle .btn').removeClass('active');
    $(this).addClass('active');
    _dashScope = $(this).data('scope');
    loadDashboard();
  });
});

function updateGreeting() {
  var now  = new Date();
  var days = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
  var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  var dateStr = days[now.getDay()] + ', ' + now.getDate() + ' ' + months[now.getMonth()] + ' ' + now.getFullYear();
  $('#dashDate').text(dateStr);
}

function loadDashboard() {
  apiGet('/admin/api/dashboard/', { scope: _dashScope })
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      var d = res.data || {};
      renderKPIs(d);
      renderChart(d.trend);
      renderOrderStatus(d.order_status);
      renderRecentOrders(d.recent_orders);
      renderTopFlavors(d.top_flavors);
      renderLowStock(d.low_stock);
    })
    .fail(function () { showAlertModal('Failed to load dashboard data.', 'danger'); });
}

function loadChart(period, from, to) {
  var params = { period: period, scope: _dashScope };
  if (period === 'range') { params.from = from; params.to = to; }
  var titles = { '30d': 'Revenue — Last 30 Days', 'all': 'Revenue — All Time', 'range': 'Revenue — ' + (from || '') + ' to ' + (to || '') };
  $('#chartTitle').text(titles[period] || 'Revenue');
  apiGet('/admin/api/dashboard/', params)
    .done(function (res) {
      if (res.success) renderChart((res.data || {}).trend);
    });
}

function applyCustomRange() {
  var from = $('#chartFrom').val();
  var to   = $('#chartTo').val();
  if (!from || !to) { showAlertModal('Please select both dates.', 'warning'); return; }
  if (from > to) { showAlertModal('Start date must be before end date.', 'warning'); return; }
  loadChart('range', from, to);
  $('#chartMeta').text(from + ' → ' + to);
}

/* ── KPI Cards ─────────────────────────────────────────────────────────── */

function renderKPIs(data) {
  var tm = data.this_month;

  var cards = [
    {
      icon: 'fa-indian-rupee-sign', iconCls: 'green',
      value: formatINR(data.today.revenue),
      label: "Today's Revenue",
      sub: data.today.orders + ' order' + (data.today.orders !== 1 ? 's' : '') + ' today',
      trend: null, href: null,
    },
    {
      icon: 'fa-calendar-check', iconCls: 'teal',
      value: formatINR(tm.revenue),
      label: 'This Month Revenue',
      sub: tm.orders + ' orders',
      trend: tm.revenue_vs_last, trendLabel: 'vs last month',
      href: null,
    },
    {
      icon: 'fa-clock', iconCls: 'amber',
      value: (data.order_status.pending || 0) + '',
      label: 'Pending Orders',
      sub: 'Awaiting action',
      trend: null,
      href: '/admin/orders/?status=pending',
      badge: data.order_status.pending || 0,
      badgeCls: 'urgent',
    },
    {
      icon: 'fa-star-half-stroke', iconCls: 'purple',
      value: data.pending_reviews + '',
      label: 'Pending Reviews',
      sub: 'Awaiting moderation',
      trend: null,
      href: '/admin/reviews/?status=pending',
      badge: data.pending_reviews,
      badgeCls: 'danger',
    },
  ];

  var html = '';
  cards.forEach(function (c) {
    var trendHtml = '';
    if (c.trend !== null) {
      var cls = c.trend > 0 ? 'trend-up' : (c.trend < 0 ? 'trend-down' : 'trend-flat');
      var arrow = c.trend > 0 ? 'fa-arrow-trend-up' : (c.trend < 0 ? 'fa-arrow-trend-down' : 'fa-minus');
      trendHtml = '<div class="kpi-trend ' + cls + '"><i class="fas ' + arrow + '"></i>' +
        Math.abs(c.trend) + '% ' + c.trendLabel + '</div>';
    }
    var clickable = c.href ? ' kpi-clickable' : '';
    var onclick   = c.href ? ' onclick="window.location=\'' + c.href + '\'"' : '';
    html +=
      '<div class="col-sm-6 col-xl-3">' +
        '<div class="kpi-card' + clickable + '"' + onclick + '>' +
          '<div class="kpi-icon ' + c.iconCls + '"><i class="fas ' + c.icon + '"></i></div>' +
          '<div class="kpi-value">' + escHtml(c.value) + '</div>' +
          '<div class="kpi-label">' + c.label + '</div>' +
          '<div class="kpi-sub">' + escHtml(c.sub) + '</div>' +
          trendHtml +
        '</div>' +
      '</div>';
  });

  // Secondary metrics row
  var sec = [
    { icon: 'fa-indian-rupee-sign', iconCls: 'green',  value: formatINR(data.all_time_revenue), label: 'All-Time Revenue'    },
    { icon: 'fa-circle-check',      iconCls: 'teal',   value: data.completed_orders + '',        label: 'Completed Orders'   },
    { icon: 'fa-users',             iconCls: 'indigo', value: data.total_users + '',             label: 'Registered Users'   },
    { icon: 'fa-receipt',           iconCls: 'blue',   value: formatINR(data.avg_order_value),   label: 'Avg Order Value'    },
  ];
  var secHtml = '';
  sec.forEach(function (c) {
    secHtml +=
      '<div class="col-sm-6 col-xl-3">' +
        '<div class="kpi-card kpi-card-sm">' +
          '<div style="display:flex;align-items:center;gap:10px">' +
            '<div class="kpi-icon kpi-icon-sm ' + c.iconCls + '"><i class="fas ' + c.icon + '"></i></div>' +
            '<div>' +
              '<div class="kpi-value kpi-value-sm">' + escHtml(c.value) + '</div>' +
              '<div class="kpi-label">' + c.label + '</div>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>';
  });

  $('#kpiPrimary').html(html);
  $('#kpiSecondary').html(secHtml);
}

/* ── Revenue Chart ─────────────────────────────────────────────────────── */

function renderChart(trend) {
  var labels   = trend.map(function (t) { return t.label; });
  var revenues = trend.map(function (t) { return t.revenue; });
  var orders   = trend.map(function (t) { return t.orders; });

  var total30d = revenues.reduce(function (a, b) { return a + b; }, 0);
  $('#chartMeta').text(formatINR(total30d) + ' in 30 days');

  if (revenueChart) { revenueChart.destroy(); }

  var ctx = document.getElementById('revenueChart').getContext('2d');

  revenueChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'Revenue',
          data: revenues,
          borderColor: '#85AA4E',
          borderWidth: 2.5,
          fill: true,
          backgroundColor: function (context) {
            var chart = context.chart;
            if (!chart.chartArea) return 'transparent';
            var grad = chart.ctx.createLinearGradient(0, chart.chartArea.top, 0, chart.chartArea.bottom);
            grad.addColorStop(0, 'rgba(133,170,78,0.22)');
            grad.addColorStop(1, 'rgba(133,170,78,0.02)');
            return grad;
          },
          tension: 0.4,
          pointRadius: 3,
          pointHoverRadius: 7,
          pointBackgroundColor: '#85AA4E',
          pointBorderColor: '#fff',
          pointBorderWidth: 2,
          yAxisID: 'y',
        },
        {
          label: 'Orders',
          data: orders,
          type: 'bar',
          backgroundColor: 'rgba(59,130,246,0.12)',
          borderColor: 'rgba(59,130,246,0.3)',
          borderWidth: 1,
          borderRadius: 4,
          yAxisID: 'y2',
        },
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
          displayColors: true,
          callbacks: {
            label: function (ctx) {
              if (ctx.datasetIndex === 0) return '  Revenue: ' + formatINR(ctx.parsed.y);
              return '  Orders: ' + ctx.parsed.y;
            },
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          border: { display: false },
          ticks: {
            color: '#94a3b8',
            font: { size: 11 },
            maxTicksLimit: 10,
            maxRotation: 0,
          },
        },
        y: {
          position: 'left',
          grid: { color: '#f1f5f9' },
          border: { display: false, dash: [4, 4] },
          ticks: {
            color: '#94a3b8',
            font: { size: 11 },
            callback: function (v) {
              return v >= 1000 ? '₹' + (v / 1000).toFixed(1) + 'k' : '₹' + v;
            },
          },
        },
        y2: {
          position: 'right',
          grid: { display: false },
          border: { display: false },
          ticks: {
            color: '#cbd5e1',
            font: { size: 10 },
            stepSize: 1,
            maxTicksLimit: 5,
          },
        },
      },
    },
  });
}

/* ── Order Status List (vertical) ──────────────────────────────────────── */

function renderOrderStatus(status) {
  var items = [
    { key: 'pending',    label: 'Pending',    color: '#d97706' },
    { key: 'confirmed',  label: 'Confirmed',  color: '#2563eb' },
    { key: 'processing', label: 'Processing', color: '#7c3aed' },
    { key: 'shipped',    label: 'Shipped',    color: '#0891b2' },
    { key: 'delivered',  label: 'Delivered',  color: '#16a34a' },
    { key: 'cancelled',  label: 'Cancelled',  color: '#dc2626' },
  ];
  var total = items.reduce(function (sum, i) { return sum + (status[i.key] || 0); }, 0);
  var html = '';
  items.forEach(function (item) {
    var count = status[item.key] || 0;
    var pct   = total > 0 ? Math.round((count / total) * 100) : 0;
    html +=
      '<div class="os-row" onclick="window.location=\'/admin/orders/?status=' + item.key + '\'">' +
        '<div class="os-dot" style="background:' + item.color + '"></div>' +
        '<span class="os-label">' + item.label + '</span>' +
        '<div class="os-bar-wrap">' +
          '<div class="os-bar" style="width:' + pct + '%;background:' + item.color + '"></div>' +
        '</div>' +
        '<span class="os-count">' + count + '</span>' +
        '<i class="fas fa-chevron-right os-arrow"></i>' +
      '</div>';
  });
  $('#orderStatusList').html(html);
}

/* ── Recent Orders ─────────────────────────────────────────────────────── */

function renderRecentOrders(orders) {
  if (!orders || !orders.length) {
    $('#recentOrdersContainer').html('<div class="empty-state"><i class="fas fa-bag-shopping"></i>No orders yet</div>');
    return;
  }

  var STATUS_COLOR = {
    pending: '#d97706', confirmed: '#2563eb', processing: '#7c3aed',
    shipped: '#0891b2', delivered: '#16a34a', cancelled: '#dc2626',
  };

  var html = '<div class="recent-orders-list">';
  orders.forEach(function (o) {
    var initials = (o.customer_name || 'G').slice(0, 2).toUpperCase();
    var color    = avatarColor(o.customer_name);
    var scColor  = STATUS_COLOR[o.status] || '#64748b';
    var ago      = timeAgo(o.order_date);
    var items    = o.items ? o.items.split(',').slice(0, 2).join(', ') + (o.items.split(',').length > 2 ? '…' : '') : '—';

    html +=
      '<div class="ro-row">' +
        '<div class="ro-avatar" style="background:' + color + '">' + initials + '</div>' +
        '<div class="ro-info">' +
          '<div class="ro-name">' + escHtml(o.customer_name) + '</div>' +
          '<div class="ro-items">' + escHtml(items) + '</div>' +
        '</div>' +
        '<div class="ro-right">' +
          '<div class="ro-amount">' + formatINR(o.total) + '</div>' +
          '<span class="ro-status" style="color:' + scColor + ';background:' + scColor + '18">' +
            ucFirst(o.status) + '</span>' +
        '</div>' +
        '<div class="ro-meta">' +
          '<span class="ro-time">' + ago + '</span>' +
          '<a href="/admin/orders/' + encodeURIComponent(o.id) + '/" class="btn-icon view" title="View">' +
            '<i class="fas fa-eye"></i></a>' +
        '</div>' +
      '</div>';
  });
  html += '</div>';
  $('#recentOrdersContainer').html(html);
}

/* ── Top Flavors ───────────────────────────────────────────────────────── */

function renderTopFlavors(flavors) {
  if (!flavors || !flavors.length) {
    $('#topFlavorsContainer').html(
      '<div class="empty-state"><i class="fas fa-pepper-hot"></i>No sales this month</div>'
    );
    return;
  }

  var maxGrams = Math.max.apply(null, flavors.map(function (f) { return f.total_grams; }));
  var html = '<div style="padding:8px 0">';
  flavors.forEach(function (f, idx) {
    var pct = maxGrams > 0 ? Math.round((f.total_grams / maxGrams) * 100) : 0;
    var kg  = (f.total_grams / 1000).toFixed(1);
    html +=
      '<div class="tf-row">' +
        '<div class="tf-rank">' + (idx + 1) + '</div>' +
        '<div class="tf-body">' +
          '<div class="tf-name-row">' +
            '<span class="tf-name">' + escHtml(f.name) + '</span>' +
            '<span class="tf-rev">' + formatINR(f.revenue) + '</span>' +
          '</div>' +
          '<div class="tf-bar-wrap">' +
            '<div class="tf-bar" style="width:' + pct + '%"></div>' +
          '</div>' +
          '<div class="tf-meta">' + kg + ' kg &middot; ' + f.order_count + ' orders</div>' +
        '</div>' +
      '</div>';
  });
  html += '</div>';
  $('#topFlavorsContainer').html(html);
}

/* ── Low Stock ─────────────────────────────────────────────────────────── */

function renderLowStock(items) {
  if (!items || !items.length) {
    $('#lowStockCard').hide();
    return;
  }

  var html = '<div style="padding:4px 0">';
  items.forEach(function (s) {
    var pct = Math.round((s.quantity_grams / s.reorder_level_grams) * 100);
    var barCls = pct <= 25 ? 'ls-bar-critical' : 'ls-bar-low';
    html +=
      '<div class="ls-row">' +
        '<div class="ls-info">' +
          '<span class="ls-name">' + escHtml(s.name) + '</span>' +
          '<span class="ls-qty">' + s.qty_kg + ' kg left</span>' +
        '</div>' +
        '<div class="ls-bar-wrap">' +
          '<div class="ls-bar ' + barCls + '" style="width:' + Math.min(pct, 100) + '%"></div>' +
        '</div>' +
      '</div>';
  });
  html += '</div>';
  $('#lowStockContainer').html(html);
  $('#lowStockCard').show();
}

/* ── Helpers ───────────────────────────────────────────────────────────── */

function formatINR(amount) {
  return '₹' + Math.round(amount).toLocaleString('en-IN');
}

function ucFirst(s) {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : '';
}

function timeAgo(dateStr) {
  var diff = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
  if (diff < 60)      return 'just now';
  if (diff < 3600)    return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400)   return Math.floor(diff / 3600) + 'h ago';
  if (diff < 604800)  return Math.floor(diff / 86400) + 'd ago';
  var d = new Date(dateStr);
  return d.getDate() + ' ' + ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getMonth()];
}

var _avatarColors = ['#85AA4E','#3b82f6','#8b5cf6','#f59e0b','#ec4899','#0ea5e9','#14b8a6'];
function avatarColor(name) {
  var hash = 0;
  for (var i = 0; i < (name || '').length; i++) hash += name.charCodeAt(i);
  return _avatarColors[hash % _avatarColors.length];
}
