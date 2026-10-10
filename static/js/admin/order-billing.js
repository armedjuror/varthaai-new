/* ─────────────────────────────────────────────────
   B2C order page — GST credit note + cancel & reissue
   Requires: utils.js (apiGet/apiPost), Bootstrap 5
───────────────────────────────────────────────── */
(function () {
  var card = document.getElementById('gstCard');
  if (!card) return;
  var orderId = card.getAttribute('data-order-id');
  var API = '/admin/api/gst/order/';
  var lines = [];

  function collect() {
    var out = [];
    $('#cnLinesTable tbody tr').each(function () {
      var qty = parseInt($(this).find('.cn-qty').val(), 10) || 0;
      if (qty > 0) {
        out.push({
          line_id: parseInt($(this).data('line-id'), 10),
          quantity: qty,
          restock: $(this).find('.cn-restock').is(':checked')
        });
      }
    });
    return out;
  }

  function renderLines() {
    var $tb = $('#cnLinesTable tbody').empty();
    lines.forEach(function (ln) {
      var unit = ln.kind === 'SHIPPING' ? '' : ' g';
      var left = ln.remaining_source_quantity;
      $tb.append(
        '<tr data-line-id="' + ln.id + '">' +
          '<td>' + escHtml(ln.description) + (ln.is_free ? ' <span class="badge bg-success">FREE</span>' : '') + '</td>' +
          '<td>' + ln.source_quantity + unit + '</td>' +
          '<td>' + left + unit + '</td>' +
          '<td><input type="number" class="form-control form-control-sm cn-qty" min="0" max="' + left + '" value="0"' +
            (left <= 0 ? ' disabled' : '') + '></td>' +
          '<td>' + (ln.kind === 'GOODS' ? '<input type="checkbox" class="form-check-input cn-restock" checked>' : '—') + '</td>' +
        '</tr>'
      );
    });
    $('#cnPreview').empty();
  }

  $('#btnCreditNote').on('click', function () {
    showLoader('Loading…');
    apiGet(API, { order_id: orderId })
      .done(function (res) {
        if (!res.success) { showAlertModal(res.message, 'danger'); return; }
        lines = res.data.invoice_lines || [];
        renderLines();
        bootstrap.Modal.getOrCreateInstance(document.getElementById('creditNoteModal')).show();
      })
      .fail(function (xhr) { showAlertModal(apiErrorMessage(xhr), 'danger'); })
      .always(hideLoader);
  });

  $('#cnPreviewBtn').on('click', function () {
    apiPost(API, { action: 'credit_note_preview', order_id: orderId, lines: collect() })
      .done(function (res) {
        if (!res.success) { $('#cnPreview').html('<span class="text-danger">' + escHtml(res.message) + '</span>'); return; }
        var d = res.data;
        $('#cnPreview').html(
          '<strong>Credit note total: ' + formatCurrency(d.grand_total) + '</strong>' +
          '<div style="color:var(--gray-400);font-size:0.8rem">Taxable ' + formatCurrency(d.taxable_value) +
          ' · GST ' + formatCurrency(d.tax_total) + '</div>'
        );
      })
      .fail(function (xhr) { $('#cnPreview').html('<span class="text-danger">' + escHtml(apiErrorMessage(xhr)) + '</span>'); });
  });

  $('#cnGenerateBtn').on('click', function () {
    var selected = collect();
    if (!selected.length) { showAlertModal('Enter a quantity on at least one line.', 'warning'); return; }
    confirmThen('Generate the credit note? It is numbered and cannot be edited or deleted afterwards.', function () {
      showLoader('Issuing credit note…');
      apiPost(API, {
        action: 'credit_note', order_id: orderId, lines: selected,
        reason: $('#cnReason').val(), notes: $('#cnNotes').val()
      })
        .done(function (res) {
          showAlertModal(res.message, res.success ? 'success' : 'danger');
          if (res.success) setTimeout(function () { window.location.reload(); }, 1200);
        })
        .fail(function (xhr) { showAlertModal(apiErrorMessage(xhr), 'danger'); })
        .always(hideLoader);
    });
  });

  $('#btnReissue').on('click', function () {
    bootstrap.Modal.getOrCreateInstance(document.getElementById('reissueModal')).show();
  });

  $('#riSubmit').on('click', function () {
    confirmThen('Cancel the current invoice with a credit note and issue a new one?', function () {
      showLoader('Reissuing…');
      apiPost(API, {
        action: 'cancel_reissue', order_id: orderId,
        name: $('#riName').val(), address: $('#riAddress').val(),
        pincode: $('#riPincode').val(), shipping_state_code: $('#riState').val()
      })
        .done(function (res) {
          showAlertModal(res.message, res.success ? 'success' : 'danger');
          if (res.success) setTimeout(function () { window.location.reload(); }, 1200);
        })
        .fail(function (xhr) { showAlertModal(apiErrorMessage(xhr), 'danger'); })
        .always(hideLoader);
    });
  });
})();
