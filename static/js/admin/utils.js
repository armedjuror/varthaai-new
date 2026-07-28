/* ─────────────────────────────────────────────────
   Varthaai Admin — Shared Utilities
   Requires: jQuery 3.6+, alert-modal.js, Bootstrap 5
───────────────────────────────────────────────── */

/* ── Loader ── */

function showLoader(label) {
  var el = document.getElementById('adminLoader');
  if (!el) return;
  var lbl = el.querySelector('.admin-loader-label');
  if (lbl) lbl.textContent = label || 'Loading…';
  el.classList.add('show');
}

function hideLoader() {
  var el = document.getElementById('adminLoader');
  if (el) el.classList.remove('show');
}

/* ── AJAX helpers ── */

function apiPost(url, data) {
  return $.ajax({
    url: url,
    type: 'POST',
    contentType: 'application/json',
    data: JSON.stringify(data),
    dataType: 'json',
    statusCode: {
      401: function () { window.location.href = '/admin/'; }
    }
  });
}

function apiGet(url, params) {
  return $.ajax({
    url: url,
    type: 'GET',
    data: params || {},
    dataType: 'json',
    statusCode: {
      401: function () { window.location.href = '/admin/'; }
    }
  });
}

/* ── Confirm then run ── */

function confirmThen(message, onConfirm) {
  showAlertModal(message, 'warning', 'Confirm', function () {
    onConfirm();
  }, null);
}

/* ── Format helpers ── */

function formatCurrency(amount) {
  return '₹' + Number(amount || 0).toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

function formatDate(dateStr) {
  if (!dateStr) return '—';
  var d = new Date(dateStr);
  var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return ('0' + d.getDate()).slice(-2) + ' ' + months[d.getMonth()] + ' ' + d.getFullYear();
}

function formatDateTime(dateStr) {
  if (!dateStr) return '—';
  var d = new Date(dateStr);
  return formatDate(dateStr) + ' ' + ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
}

/* ── Badge helpers ── */

var ORDER_STATUS_LABELS = {
  pending: 'Pending', confirmed: 'Confirmed', processing: 'Processing',
  shipped: 'Shipped', delivered: 'Delivered', cancelled: 'Cancelled',
  failed: 'Failed', deleted: 'Deleted'
};

var PAYMENT_STATUS_LABELS = {
  pending: 'Pending', paid: 'Paid', failed: 'Failed',
  refund_initiated: 'Refund Initiated', refunded: 'Refunded', cancelled: 'Cancelled'
};

function statusBadge(status) {
  var label = ORDER_STATUS_LABELS[status] || status;
  return '<span class="status status-' + status + '">' + label + '</span>';
}

function paymentBadge(status) {
  var label = PAYMENT_STATUS_LABELS[status] || status;
  return '<span class="status payment-' + status + '">' + label + '</span>';
}

function escHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
