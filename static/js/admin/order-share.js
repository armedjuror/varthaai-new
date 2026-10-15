/* ─────────────────────────────────────────────────
   Order share link + one-click WhatsApp (B2C order page, B2B order modal,
   B2B dashboard overdue list). Requires utils.js.
   WhatsApp only opens with the message ready — the sender still taps Send.
───────────────────────────────────────────────── */
var OrderShare = (function () {
  function api(kind) { return kind === 'b2b' ? '/admin/api/b2b-orders/' : '/admin/api/gst/order/'; }

  function sendWhatsApp(kind, orderId, msgKind, onDone) {
    // Open the tab synchronously (inside the click) so popup blockers allow it.
    var win = window.open('about:blank', '_blank');
    apiPost(api(kind), { action: 'whatsapp', order_id: orderId, kind: msgKind })
      .done(function (res) {
        if (!res.success) { if (win) win.close(); showAlertModal(res.message, 'danger'); return; }
        if (win) win.location.href = res.data.url; else window.location.href = res.data.url;
        if (onDone) onDone(res.data);
      })
      .fail(function (xhr) { if (win) win.close(); showAlertModal(apiErrorMessage(xhr), 'danger'); });
  }

  function linkAction(kind, orderId, action, onDone) {
    apiPost(api(kind), { action: action, order_id: orderId })
      .done(function (res) {
        if (!res.success) { showAlertModal(res.message, 'danger'); return; }
        if (onDone) onDone(res.data);
      })
      .fail(function (xhr) { showAlertModal(apiErrorMessage(xhr), 'danger'); });
  }

  function copy(text) {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(function () { showAlertModal('Link copied.', 'success'); });
    } else {
      var ta = document.createElement('textarea');
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); showAlertModal('Link copied.', 'success'); } catch (e) { window.prompt('Copy the link:', text); }
      document.body.removeChild(ta);
    }
  }

  /* Render the share panel into `el`. `share` is the API's share payload. */
  function render(el, kind, orderId, share) {
    var $el = $(el);
    var disabled = !share.whatsapp_number;
    var reason = escHtml(share.whatsapp_disabled_reason || '');
    var html = '<div class="d-flex flex-wrap gap-2 align-items-center">' +
      '<button class="btn btn-sm btn-success os-wa" data-kind="delivery"' + (disabled ? ' disabled title="' + reason + '"' : '') + '><i class="fab fa-whatsapp me-1"></i>Send on WhatsApp</button>' +
      (share.amount_due > 0 ? '<button class="btn btn-sm btn-outline-success os-wa" data-kind="reminder"' + (disabled ? ' disabled title="' + reason + '"' : '') + '><i class="fas fa-bell me-1"></i>Send reminder (' + formatCurrency(share.amount_due) + ' due)</button>' : '') +
      (share.link
        ? '<button class="btn btn-sm btn-outline-secondary os-copy"><i class="fas fa-link me-1"></i>Copy link</button>' +
          '<button class="btn btn-sm btn-outline-secondary os-regen" title="Revokes the old link"><i class="fas fa-rotate me-1"></i>New link</button>'
        : '<button class="btn btn-sm btn-outline-secondary os-create"><i class="fas fa-link me-1"></i>Create link</button>') +
      '</div>';
    if (disabled) html += '<div style="font-size:0.75rem;color:#b45309;margin-top:6px">' + reason + '</div>';
    if (share.link) {
      html += '<div style="font-size:0.75rem;color:var(--gray-400);margin-top:6px;word-break:break-all">' + escHtml(share.link) +
        ' · viewed ' + share.view_count + 'x' + (share.last_viewed_at ? ', last ' + formatDateTime(share.last_viewed_at) : '') + '</div>';
    }
    if ((share.messages || []).length) {
      html += '<div style="margin-top:8px;font-size:0.78rem">' + share.messages.slice(0, 5).map(function (m) {
        return '<div><i class="fab fa-whatsapp me-1" style="color:#16a34a"></i>' + escHtml(m.label) + ' to ' + escHtml(m.recipient) +
          ' · ' + formatDateTime(m.at) + (m.by ? ' · ' + escHtml(m.by) : '') + '</div>';
      }).join('') + '</div>';
    }
    $el.html(html);
    var refresh = function (data) { render(el, kind, orderId, data); };
    $el.find('.os-wa').on('click', function () {
      var msgKind = $(this).data('kind');
      sendWhatsApp(kind, orderId, msgKind, function () { linkAction(kind, orderId, 'share_link', refresh); });
    });
    $el.find('.os-copy').on('click', function () { copy(share.link); });
    $el.find('.os-create').on('click', function () { linkAction(kind, orderId, 'share_link', refresh); });
    $el.find('.os-regen').on('click', function () {
      confirmThen('Create a new link? The old link stops working.', function () {
        linkAction(kind, orderId, 'regenerate_link', refresh);
      });
    });
  }

  return { render: render, sendWhatsApp: sendWhatsApp };
})();
