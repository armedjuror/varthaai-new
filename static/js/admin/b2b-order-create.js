/* Varthaai Admin — B2B Order Creation */

var formData    = { flavors: [], packs: [], batches: [], companies: [] };
var orderItems  = [];
var offers      = [];
var itemCounter = 0;

$(function () {
  showLoader('Loading…');
  apiGet('api/b2b-orders.php', { view: 'order_form_data' })
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      formData.flavors   = res.flavors   || [];
      formData.packs     = res.packs     || [];
      formData.batches   = res.batches   || [];
      formData.companies = res.companies || [];
      populateCompanies();
      populateFlavors();

      if (EDIT_ORDER_ID) loadEditOrder();
      else if (REPEAT_ORDER_ID) loadRepeatOrder();
      else if (PRESELECT_COMPANY) {
        $('#orderCompany').val(PRESELECT_COMPANY);
        onCompanyChange();
      }
    })
    .fail(function () { showAlertModal('Failed to load form data.', 'danger'); })
    .always(hideLoader);
});

function populateCompanies() {
  var opts = '<option value="">Select a converted company…</option>';
  formData.companies.forEach(function (c) {
    opts += '<option value="' + c.id + '">' + escHtml(c.company_name) + '</option>';
  });
  $('#orderCompany').html(opts);
}

function populateFlavors() {
  var opts = '<option value="">Select…</option>';
  formData.flavors.forEach(function (f) {
    opts += '<option value="' + f.id + '">' + escHtml(f.name) + '</option>';
  });
  $('#itemFlavor').html(opts);
}

function onCompanyChange() {
  var cid = parseInt($('#orderCompany').val());
  var company = formData.companies.find(function (c) { return parseInt(c.id) === cid; });

  // Contacts
  var cOpts = '<option value="">Select…</option>';
  if (company && company.contacts_raw) {
    company.contacts_raw.split('|').forEach(function (s) {
      var parts = s.split(':');
      cOpts += '<option value="' + parts[0] + '">' + escHtml(parts.slice(1).join(':')) + '</option>';
    });
  }
  $('#orderContact').html(cOpts);

  // Pre-fill company discount
  if (company && company.discount_type) {
    $('#discountType').val(company.discount_type);
    $('#discountValue').val(company.discount_value);
  } else {
    $('#discountType').val('');
    $('#discountValue').val('');
  }

  // Auto-fill due date from payment terms
  if (company && parseInt(company.payment_terms_days) > 0) {
    var due = new Date();
    due.setDate(due.getDate() + parseInt(company.payment_terms_days));
    $('#orderDueDate').val(due.toISOString().slice(0, 10));
    $('#dueDateHint').text(company.payment_terms_days + ' days from today');
  } else {
    $('#orderDueDate').val('');
    $('#dueDateHint').text('');
  }

  // Check balance warning
  if (cid) {
    checkBalance(cid);
    loadOffers(cid);
  } else {
    $('#balanceWarning').hide();
    $('#offersCard').hide();
  }
  recalculate();
}

function checkBalance(companyId) {
  apiGet('api/b2b-orders.php', { view: 'company_balance', company_id: companyId })
    .done(function (res) {
      if (!res.success) return;
      var d = res.data;
      if (d.outstanding > 0 || d.advance > 0) {
        var msg = '';
        if (d.outstanding > 0) {
          msg = 'Outstanding: ' + formatCurrency(d.outstanding);
          if (d.overdue > 0) msg += ' · Overdue: ' + formatCurrency(d.overdue);
          if (d.credit_limit > 0 && d.outstanding > d.credit_limit) {
            msg += ' · Exceeds credit limit of ' + formatCurrency(d.credit_limit) + '!';
          }
        }
        if (d.advance > 0) {
          msg += (msg ? ' · ' : '') + 'Advance credit: ' + formatCurrency(d.advance);
        }
        $('#balanceWarningText').text(msg);
        $('#balanceWarning').show();
      } else {
        $('#balanceWarning').hide();
      }
    });
}

function loadOffers(companyId) {
  apiGet('api/b2b-orders.php', { view: 'offers', company_id: companyId })
    .done(function (res) {
      offers = res.data || [];
      if (!offers.length) { $('#offersCard').hide(); return; }
      var html = '';
      offers.forEach(function (o) {
        var detail = '';
        if (o.type === 'buy_x_get_y') detail = 'Buy ' + o.min_quantity + ' packs, get ' + o.free_quantity + ' free';
        else if (o.type === 'discount_percent') detail = o.discount_value + '% off';
        else detail = formatCurrency(o.discount_value) + ' off';

        html += '<div style="padding:8px 0;border-bottom:1px solid var(--gray-100);font-size:0.85rem">' +
          '<div style="font-weight:600">' + escHtml(o.name) + '</div>' +
          '<div style="font-size:0.78rem;color:var(--gray-500)">' + detail +
            (o.company_name ? ' · ' + escHtml(o.company_name) : ' · Global') +
            (parseInt(o.is_stackable) ? ' · Stackable' : '') +
          '</div>' +
        '</div>';
      });
      $('#offersList').html(html);
      $('#offersCard').show();
    });
}

/* ── Flavor / batch / pack selection ── */
function onFlavorChange() {
  var fid = parseInt($('#itemFlavor').val());

  // Batches for this flavor
  var bOpts = '<option value="">Select…</option>';
  formData.batches.filter(function (b) { return parseInt(b.flavor_id) === fid; }).forEach(function (b) {
    var label = (b.batch_number || 'Batch #' + b.id) + ' — ' + (b.quantity_grams / 1000).toFixed(1) + 'kg avail';
    if (b.expiry_date) label += ' (exp: ' + b.expiry_date + ')';
    bOpts += '<option value="' + b.id + '" data-cost="' + b.cost_price_per_kg + '">' + label + '</option>';
  });
  $('#itemBatch').html(bOpts);

  // Packs for this flavor
  var pOpts = '<option value="custom">Custom Weight</option>';
  formData.packs.filter(function (p) { return parseInt(p.flavor_id) === fid; }).forEach(function (p) {
    pOpts += '<option value="' + p.id + '" data-weight="' + p.weight_grams + '" data-mrp="' + p.mrp + '" data-sp="' + p.selling_price + '" data-cp="' + p.cost_price + '">' +
      escHtml(p.label) + ' — ' + formatCurrency(p.selling_price) + '</option>';
  });
  $('#itemPack').html(pOpts);
  onPackChange();
}

function onPackChange() {
  var $sel = $('#itemPack option:selected');
  if ($sel.val() === 'custom') {
    $('#itemWeight').prop('disabled', false).val('');
  } else {
    $('#itemWeight').val($sel.data('weight')).prop('disabled', true);
  }
}

/* ── Add item ── */
function addItem() {
  var fid = parseInt($('#itemFlavor').val());
  if (!fid) { showAlertModal('Select a flavor.', 'warning'); return; }

  var batchId = parseInt($('#itemBatch').val());
  if (!batchId) { showAlertModal('Select a batch.', 'warning'); return; }

  var packVal = $('#itemPack').val();
  var qty     = parseInt($('#itemQty').val()) || 1;
  var weight  = parseInt($('#itemWeight').val()) || 0;
  if (!weight) { showAlertModal('Enter weight.', 'warning'); return; }

  var flavor = formData.flavors.find(function (f) { return parseInt(f.id) === fid; });
  var packId = null, mrp = null, sp = 0, cp = null, packLabel = null;

  if (packVal !== 'custom') {
    var $pOpt = $('#itemPack option:selected');
    packId    = parseInt(packVal);
    mrp       = parseFloat($pOpt.data('mrp')) || 0;
    sp        = parseFloat($pOpt.data('sp')) || 0;
    cp        = parseFloat($pOpt.data('cp')) || 0;
    packLabel = $pOpt.text().split(' — ')[0];
  } else {
    // Custom weight: price from flavor's sale_price_per_kg
    sp = Math.round((weight / 1000) * parseFloat(flavor.sale_price_per_kg) * 100) / 100;
  }

  // Cost price from batch if not from pack
  if (cp === null || cp === 0) {
    var batchCost = parseFloat($('#itemBatch option:selected').data('cost')) || 0;
    cp = Math.round((weight / 1000) * batchCost * 100) / 100;
  }

  itemCounter++;
  orderItems.push({
    _id:           itemCounter,
    flavor_id:     fid,
    flavor_pack_id: packId,
    stock_id:      batchId,
    quantity:      qty,
    weight_grams:  weight,
    mrp:           mrp,
    selling_price: sp,
    cost_price:    cp,
    is_free_item:  0,
    offer_id:      null,
    flavor_name:   flavor.name,
    pack_label:    packLabel
  });

  renderItems();
  recalculate();
}

function removeItem(id) {
  orderItems = orderItems.filter(function (i) { return i._id !== id; });
  renderItems();
  recalculate();
}

function addFreeItem(offerId) {
  // Prompt: pick flavor + pack for the free item
  var offer = offers.find(function (o) { return parseInt(o.id) === offerId; });
  if (!offer) return;

  // Check min quantity met
  var totalPacks = 0;
  orderItems.filter(function (i) { return !i.is_free_item; }).forEach(function (i) { totalPacks += i.quantity; });
  if (totalPacks < parseInt(offer.min_quantity)) {
    showAlertModal('Need at least ' + offer.min_quantity + ' packs to use this offer. Currently: ' + totalPacks + '.', 'warning');
    return;
  }

  // Find min selling price in order items
  var minPrice = Infinity;
  orderItems.filter(function (i) { return !i.is_free_item; }).forEach(function (i) {
    if (i.selling_price < minPrice) minPrice = i.selling_price;
  });

  // Show flavor/pack picker via simple prompt approach using existing dropdowns
  var fid = parseInt($('#itemFlavor').val());
  var packVal = $('#itemPack').val();
  var batchId = parseInt($('#itemBatch').val());

  if (!fid || !batchId) {
    showAlertModal('Select a flavor and batch in the item form first, then click this button to add it as a free item.', 'warning');
    return;
  }

  var weight = parseInt($('#itemWeight').val()) || 0;
  if (!weight) { showAlertModal('Enter weight for the free item.', 'warning'); return; }

  var flavor = formData.flavors.find(function (f) { return parseInt(f.id) === fid; });
  var packId = null, mrp = null, sp = 0, cp = null, packLabel = null;

  if (packVal !== 'custom') {
    var $pOpt = $('#itemPack option:selected');
    packId    = parseInt(packVal);
    mrp       = parseFloat($pOpt.data('mrp')) || 0;
    sp        = parseFloat($pOpt.data('sp')) || 0;
    cp        = parseFloat($pOpt.data('cp')) || 0;
    packLabel = $pOpt.text().split(' — ')[0];
  } else {
    sp = Math.round((weight / 1000) * parseFloat(flavor.sale_price_per_kg) * 100) / 100;
  }

  // Validate: free item price <= cheapest item
  if (sp > minPrice) {
    showAlertModal('Free item price (' + formatCurrency(sp) + ') cannot exceed the cheapest item in the order (' + formatCurrency(minPrice) + ').', 'warning');
    return;
  }

  if (cp === null || cp === 0) {
    var batchCost = parseFloat($('#itemBatch option:selected').data('cost')) || 0;
    cp = Math.round((weight / 1000) * batchCost * 100) / 100;
  }

  for (var fi = 0; fi < parseInt(offer.free_quantity); fi++) {
    itemCounter++;
    orderItems.push({
      _id:           itemCounter,
      flavor_id:     fid,
      flavor_pack_id: packId,
      stock_id:      batchId,
      quantity:      1,
      weight_grams:  weight,
      mrp:           mrp,
      selling_price: sp,
      cost_price:    cp,
      is_free_item:  1,
      offer_id:      offerId,
      flavor_name:   flavor.name,
      pack_label:    packLabel
    });
  }

  renderItems();
  recalculate();
}

function renderItems() {
  if (!orderItems.length) {
    $('#itemsList').html('<div style="text-align:center;color:var(--gray-400);padding:20px"><i class="fas fa-box-open fa-2x mb-2 d-block"></i>No items added yet</div>');
    return;
  }
  var html = '';
  orderItems.forEach(function (it) {
    var lineTotal = it.is_free_item ? 0 : (it.selling_price * it.quantity);
    html += '<div class="item-row' + (it.is_free_item ? ' free-item' : '') + '">' +
      '<div class="d-flex justify-content-between align-items-center">' +
        '<div>' +
          '<div style="font-weight:600;font-size:0.875rem">' +
            escHtml(it.flavor_name) +
            (it.pack_label ? ' <span style="font-size:0.75rem;color:var(--gray-400)">(' + escHtml(it.pack_label) + ')</span>' : '') +
            (it.is_free_item ? ' <span style="font-size:0.72rem;background:#f0fdf4;color:#16a34a;padding:1px 8px;border-radius:10px">FREE</span>' : '') +
          '</div>' +
          '<div style="font-size:0.78rem;color:var(--gray-500)">' +
            it.quantity + ' × ' + it.weight_grams + 'g = ' + (it.quantity * it.weight_grams) + 'g' +
            (it.mrp ? ' · MRP: ' + formatCurrency(it.mrp) : '') +
            ' · Price: ' + formatCurrency(it.selling_price) +
          '</div>' +
        '</div>' +
        '<div class="d-flex align-items-center gap-3">' +
          '<span style="font-weight:700">' + formatCurrency(lineTotal) + '</span>' +
          '<button class="btn-icon delete" onclick="removeItem(' + it._id + ')" title="Remove"><i class="fas fa-times"></i></button>' +
        '</div>' +
      '</div>' +
    '</div>';
  });

  // Show applicable buy_x_get_y offers as buttons
  var buyOffers = offers.filter(function (o) { return o.type === 'buy_x_get_y'; });
  if (buyOffers.length) {
    html += '<div class="mt-2">';
    buyOffers.forEach(function (o) {
      html += '<button class="btn btn-sm btn-outline-success me-2" onclick="addFreeItem(' + o.id + ')">' +
        '<i class="fas fa-gift me-1"></i>' + escHtml(o.name) +
      '</button>';
    });
    html += '</div>';
  }

  $('#itemsList').html(html);
}

function recalculate() {
  var subtotal = 0;
  var offerDisc = 0;

  orderItems.forEach(function (it) {
    if (it.is_free_item) {
      offerDisc += it.selling_price * it.quantity;
    } else {
      subtotal += it.selling_price * it.quantity;
    }
  });

  var discType = $('#discountType').val();
  var discVal  = parseFloat($('#discountValue').val()) || 0;
  var discAmt  = 0;
  if (discType === 'percentage' && discVal > 0) discAmt = Math.round(subtotal * discVal / 100 * 100) / 100;
  else if (discType === 'amount' && discVal > 0) discAmt = Math.min(discVal, subtotal);

  var total = Math.max(0, subtotal - discAmt);

  $('#summSubtotal').text(formatCurrency(subtotal));

  if (offerDisc > 0) {
    $('#summOfferRow').show();
    $('#summOfferDisc').text('-' + formatCurrency(offerDisc));
  } else {
    $('#summOfferRow').hide();
  }

  if (discAmt > 0) {
    $('#summDiscRow').show();
    $('#summDiscount').text('-' + formatCurrency(discAmt));
  } else {
    $('#summDiscRow').hide();
  }

  $('#summTotal').text(formatCurrency(total));
}

/* ── Submit ── */
function submitOrder() {
  var companyId = parseInt($('#orderCompany').val());
  if (!companyId) { showAlertModal('Select a company.', 'warning'); return; }
  if (!orderItems.length) { showAlertModal('Add at least one item.', 'warning'); return; }

  var isEdit = !!EDIT_ORDER_ID;
  var data = {
    action:       isEdit ? 'update_order' : 'create_order',
    company_id:   companyId,
    contact_id:   $('#orderContact').val() || null,
    discount_type:  $('#discountType').val() || null,
    discount_value: parseFloat($('#discountValue').val()) || null,
    notes:        $('#orderNotes').val() || null,
    due_date:     $('#orderDueDate').val() || null,
    source_order_id: REPEAT_ORDER_ID || null,
    items: orderItems.map(function (it) {
      return {
        flavor_id:      it.flavor_id,
        flavor_pack_id: it.flavor_pack_id,
        stock_id:       it.stock_id,
        quantity:        it.quantity,
        weight_grams:   it.weight_grams,
        mrp:            it.mrp,
        selling_price:  it.selling_price,
        cost_price:     it.cost_price,
        is_free_item:   it.is_free_item,
        offer_id:       it.offer_id,
        flavor_name:    it.flavor_name,
        pack_label:     it.pack_label
      };
    })
  };
  if (isEdit) data.order_id = EDIT_ORDER_ID;

  showLoader(isEdit ? 'Saving…' : 'Creating order…');
  apiPost('api/b2b-orders.php', data)
    .done(function (res) {
      if (res.success) {
        showAlertModal(res.message, 'success');
        setTimeout(function () { window.location.href = 'b2b-orders.php'; }, 1200);
      } else {
        showAlertModal(res.message, 'danger');
      }
    })
    .fail(function () { showAlertModal('Request failed.', 'danger'); })
    .always(hideLoader);
}

/* ── Edit order ── */
function loadEditOrder() {
  showLoader('Loading order…');
  apiPost('api/b2b-orders.php', { action: 'repeat_order', source_order_id: EDIT_ORDER_ID })
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }

      $('#orderCompany').val(res.company_id);
      onCompanyChange();
      if (res.contact_id) $('#orderContact').val(res.contact_id);
      if (res.discount_type) { $('#discountType').val(res.discount_type); $('#discountValue').val(res.discount_value); }
      if (res.notes) $('#orderNotes').val(res.notes);

      (res.items || []).forEach(function (it) {
        itemCounter++;
        orderItems.push({
          _id:            itemCounter,
          flavor_id:      parseInt(it.flavor_id),
          flavor_pack_id: it.flavor_pack_id ? parseInt(it.flavor_pack_id) : null,
          stock_id:       it.stock_id ? parseInt(it.stock_id) : null,
          quantity:        parseInt(it.quantity),
          weight_grams:   parseInt(it.weight_grams),
          mrp:            it.mrp !== null ? parseFloat(it.mrp) : null,
          selling_price:  parseFloat(it.selling_price),
          cost_price:     it.cost_price !== null ? parseFloat(it.cost_price) : null,
          is_free_item:   parseInt(it.is_free_item),
          offer_id:       it.offer_id ? parseInt(it.offer_id) : null,
          flavor_name:    it.flavor_name,
          pack_label:     it.pack_label
        });
      });

      renderItems();
      recalculate();
    })
    .fail(function () { showAlertModal('Failed to load order.', 'danger'); })
    .always(hideLoader);
}

/* ── Repeat order ── */
function loadRepeatOrder() {
  showLoader('Loading order…');
  apiPost('api/b2b-orders.php', { action: 'repeat_order', source_order_id: REPEAT_ORDER_ID })
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }

      $('#orderCompany').val(res.company_id);
      onCompanyChange();
      if (res.contact_id) $('#orderContact').val(res.contact_id);
      if (res.discount_type) { $('#discountType').val(res.discount_type); $('#discountValue').val(res.discount_value); }
      if (res.notes) $('#orderNotes').val(res.notes);

      // Add items (re-map with fresh prices where possible)
      (res.items || []).forEach(function (it) {
        itemCounter++;
        orderItems.push({
          _id:            itemCounter,
          flavor_id:      parseInt(it.flavor_id),
          flavor_pack_id: it.flavor_pack_id ? parseInt(it.flavor_pack_id) : null,
          stock_id:       it.stock_id ? parseInt(it.stock_id) : null,
          quantity:        parseInt(it.quantity),
          weight_grams:   parseInt(it.weight_grams),
          mrp:            it.mrp !== null ? parseFloat(it.mrp) : null,
          selling_price:  parseFloat(it.selling_price),
          cost_price:     it.cost_price !== null ? parseFloat(it.cost_price) : null,
          is_free_item:   parseInt(it.is_free_item),
          offer_id:       it.offer_id ? parseInt(it.offer_id) : null,
          flavor_name:    it.flavor_name,
          pack_label:     it.pack_label
        });
      });

      renderItems();
      recalculate();
    })
    .fail(function () { showAlertModal('Failed to load order.', 'danger'); })
    .always(hideLoader);
}
