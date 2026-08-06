/* Varthaai Admin — B2B Company Detail Page */

var companyData  = null;
var contactsList = [];
var stages = ['lead', 'contacted', 'negotiation', 'converted', 'lost'];

var typeIcons = {
  call:         { icon: 'fa-phone',          bg: '#3b82f6' },
  meeting:      { icon: 'fa-handshake',      bg: '#8b5cf6' },
  email:        { icon: 'fa-envelope',       bg: '#06b6d4' },
  whatsapp:     { icon: 'fa-comment-dots',   bg: '#25d366' },
  note:         { icon: 'fa-sticky-note',    bg: '#6b7280' },
  stage_change: { icon: 'fa-arrow-right',    bg: '#f59e0b' },
  order:        { icon: 'fa-bag-shopping',   bg: '#16a34a' },
  payment:      { icon: 'fa-indian-rupee-sign', bg: '#16a34a' },
  return:       { icon: 'fa-rotate-left',    bg: '#dc2626' }
};

$(function () {
  loadCompany();
  loadDropdowns();
});

function loadDropdowns() {
  apiGet('/admin/api/b2b/', { view: 'categories' }).done(function (res) {
    if (!res.success) return;
    var opts = '<option value="">Select…</option>';
    (res.data || []).forEach(function (c) {
      opts += '<option value="' + c.id + '">' + escHtml(c.name) + '</option>';
    });
    $('#editCategory').html(opts);
  });
  apiGet('/admin/api/b2b/', { view: 'admins' }).done(function (res) {
    if (!res.success) return;
    var opts = '<option value="">Select…</option>';
    (res.data || []).forEach(function (a) {
      opts += '<option value="' + a.id + '">' + escHtml(a.name) + '</option>';
    });
    $('#editAssignedTo').html(opts);
  });
}

function loadCompany() {
  showLoader('Loading…');
  apiGet('/admin/api/b2b/', { view: 'company', id: COMPANY_ID })
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      companyData  = res.data.company;
      contactsList = res.data.contacts || [];
      renderHeader();
      renderInfo();
      renderStageButtons();
      renderContacts();
      renderTimeline(res.activities || []);
      populateContactDropdown();
    })
    .fail(function () { showAlertModal('Failed to load company.', 'danger'); })
    .always(hideLoader);
}

/* ── Header ── */
function renderHeader() {
  var c = companyData;
  $('#companyTitle').text(c.company_name);
  $('#breadcrumbName').text(c.company_name);
  $('#headerActions').html(
    '<button class="btn btn-sm btn-outline-primary" onclick="openEditCompanyModal()"><i class="fas fa-pen me-1"></i>Edit</button>' +
    '<a href="/admin/b2b/" class="btn btn-sm btn-outline-secondary"><i class="fas fa-arrow-left me-1"></i>Pipeline</a>'
  );
}

/* ── Stage buttons ── */
function renderStageButtons() {
  var html = '';
  stages.forEach(function (s) {
    var active = companyData.stage === s;
    var cls = active ? 'btn-primary' : 'btn-outline-secondary';
    html += '<button class="btn stage-btn ' + cls + '" onclick="changeStage(\'' + s + '\')">' +
      capitalize(s) + '</button>';
  });
  $('#stageBtns').html(html);
}

function changeStage(stage) {
  if (stage === companyData.stage) return;
  showLoader('Updating…');
  apiPost('/admin/api/b2b/', { action: 'change_stage', id: COMPANY_ID, stage: stage })
    .done(function (res) {
      showAlertModal(res.message, res.success ? 'success' : 'danger');
      if (res.success) loadCompany();
    })
    .fail(function () { showAlertModal('Request failed.', 'danger'); })
    .always(hideLoader);
}

/* ── Company info ── */
function renderInfo() {
  var c = companyData;
  var rows = [
    ['Stage', '<span class="stage-badge stage-badge-' + c.stage + '">' + capitalize(c.stage) + '</span>'],
    ['Category', escHtml(c.category_name || '—')],
    ['City', escHtml([c.city, c.state].filter(Boolean).join(', ') || '—')],
    ['Pincode', escHtml(c.pincode || '—')],
    ['GST', escHtml(c.gst_number || '—')],
    ['Location', c.location_url
      ? '<a href="' + encodeURI(c.location_url) + '" target="_blank" rel="noopener"><i class="fas fa-map-marker-alt me-1"></i>View</a>'
      : '—'],
    ['Source', capitalize(c.source || '—')],
    ['Assigned To', escHtml(c.assigned_name || '—')],
    ['Credit Limit', c.credit_limit > 0 ? formatCurrency(c.credit_limit) : '—'],
    ['Payment Terms', c.payment_terms_days > 0 ? c.payment_terms_days + ' days' : 'Immediate'],
    ['Discount', c.discount_type ? (c.discount_type === 'percentage' ? c.discount_value + '%' : formatCurrency(c.discount_value)) : '—'],
    ['Created', formatDate(c.created_at)]
  ];
  if (c.converted_at) rows.push(['Converted', formatDate(c.converted_at)]);

  var html = '';
  rows.forEach(function (r) {
    html += '<div class="info-row"><span class="info-label">' + r[0] + '</span><span class="info-val">' + r[1] + '</span></div>';
  });
  if (c.address) {
    html += '<div style="margin-top:10px;font-size:0.82rem;color:var(--gray-500)"><strong>Address:</strong> ' + escHtml(c.address) + '</div>';
  }
  if (c.notes) {
    html += '<div style="margin-top:8px;font-size:0.82rem;color:var(--gray-500)"><strong>Notes:</strong> ' + escHtml(c.notes) + '</div>';
  }
  if (c.photo) {
    html += '<div style="margin-top:10px"><a href="' + encodeURI(c.photo) + '" target="_blank" rel="noopener">' +
      '<img src="' + encodeURI(c.photo) + '" alt="Lead photo" style="max-width:100%;border-radius:10px;border:1px solid var(--gray-200)"></a></div>';
  }
  $('#companyInfoBody').html(html);
}

/* ── Contacts ── */
function renderContacts() {
  if (!contactsList.length) {
    $('#contactsList').html('<div style="text-align:center;color:var(--gray-400);padding:20px"><i class="fas fa-user-slash fa-2x mb-2 d-block"></i>No contacts yet</div>');
    return;
  }
  var html = '';
  contactsList.forEach(function (ct) {
    var primary = parseInt(ct.is_primary) ? ' <span style="font-size:0.7rem;background:#eff6ff;color:#3b82f6;padding:1px 8px;border-radius:10px">Primary</span>' : '';
    html += '<div class="contact-card">' +
      '<div class="d-flex justify-content-between align-items-start">' +
        '<div>' +
          '<div style="font-weight:600;font-size:0.875rem">' + escHtml(ct.name) + primary + '</div>' +
          '<div style="font-size:0.78rem;color:var(--gray-400);margin-top:2px">' +
            (ct.designation ? escHtml(ct.designation) + ' · ' : '') +
            (ct.phone ? '<a href="tel:' + escHtml(ct.phone) + '">' + escHtml(ct.phone) + '</a>' : '') +
            (ct.email ? ' · ' + escHtml(ct.email) : '') +
          '</div>' +
        '</div>' +
        '<div class="contact-actions d-flex gap-1">' +
          '<button class="btn-icon edit" title="Edit" onclick="openEditContactModal(' + ct.id + ')"><i class="fas fa-pen"></i></button>' +
          '<button class="btn-icon delete" title="Delete" onclick="deleteContact(' + ct.id + ')"><i class="fas fa-trash"></i></button>' +
        '</div>' +
      '</div>' +
    '</div>';
  });
  $('#contactsList').html(html);
}

function populateContactDropdown() {
  var opts = '<option value="">No contact</option>';
  contactsList.forEach(function (ct) {
    opts += '<option value="' + ct.id + '">' + escHtml(ct.name) + '</option>';
  });
  $('#activityContact').html(opts);
}

function openAddContactModal() {
  $('#contactModalTitle').text('Add Contact');
  $('#contactId').val('');
  $('#contactForm')[0].reset();
  $('#contactModal').modal('show');
}

function openEditContactModal(id) {
  var ct = contactsList.find(function (c) { return parseInt(c.id) === id; });
  if (!ct) return;
  $('#contactModalTitle').text('Edit Contact');
  $('#contactId').val(ct.id);
  $('#contactName').val(ct.name);
  $('#contactPhone').val(ct.phone || '');
  $('#contactEmail').val(ct.email || '');
  $('#contactDesignation').val(ct.designation || '');
  $('#contactPrimary').prop('checked', parseInt(ct.is_primary));
  $('#contactModal').modal('show');
}

$('#contactForm').on('submit', function (e) {
  e.preventDefault();
  var isEdit = !!$('#contactId').val();
  var data = {
    action:      isEdit ? 'edit_contact' : 'add_contact',
    company_id:  COMPANY_ID,
    name:        $('#contactName').val(),
    phone:       $('#contactPhone').val(),
    email:       $('#contactEmail').val(),
    designation: $('#contactDesignation').val(),
    is_primary:  $('#contactPrimary').is(':checked') ? 1 : 0
  };
  if (isEdit) data.id = parseInt($('#contactId').val());

  showLoader('Saving…');
  apiPost('/admin/api/b2b/', data)
    .done(function (res) {
      showAlertModal(res.message, res.success ? 'success' : 'danger');
      if (res.success) { $('#contactModal').modal('hide'); loadCompany(); }
    })
    .fail(function () { showAlertModal('Request failed.', 'danger'); })
    .always(hideLoader);
});

function deleteContact(id) {
  var ct = contactsList.find(function (c) { return parseInt(c.id) === id; });
  confirmThen('Delete contact "' + (ct ? escHtml(ct.name) : id) + '"?', function () {
    showLoader('Deleting…');
    apiPost('/admin/api/b2b/', { action: 'delete_contact', id: id })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) loadCompany();
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
}

/* ── Activity logging ── */
function logActivity() {
  var data = {
    action:       'add_activity',
    company_id:   COMPANY_ID,
    type:         $('#activityType').val(),
    contact_id:   $('#activityContact').val(),
    subject:      $('#activitySubject').val(),
    description:  $('#activityDesc').val(),
    follow_up_date: $('#activityFollowUp').val()
  };
  if (!data.subject && !data.description) {
    showAlertModal('Please enter a subject or description.', 'warning');
    return;
  }
  showLoader('Logging…');
  apiPost('/admin/api/b2b/', data)
    .done(function (res) {
      showAlertModal(res.message, res.success ? 'success' : 'danger');
      if (res.success) {
        $('#activitySubject').val('');
        $('#activityDesc').val('');
        $('#activityFollowUp').val('');
        loadCompany();
      }
    })
    .fail(function () { showAlertModal('Request failed.', 'danger'); })
    .always(hideLoader);
}

/* ── Timeline ── */
function renderTimeline(activities) {
  if (!activities.length) {
    $('#timelineBody').html('<div style="text-align:center;color:var(--gray-400);padding:30px"><i class="fas fa-clock-rotate-left fa-2x mb-2 d-block"></i>No activity yet</div>');
    return;
  }

  var html = '';
  activities.forEach(function (a) {
    var ti = typeIcons[a.type] || typeIcons.note;
    var stageInfo = '';
    if (a.type === 'stage_change' && a.old_stage && a.new_stage) {
      stageInfo = '<div style="margin-top:4px">' +
        '<span class="stage-badge stage-badge-' + a.old_stage + '">' + capitalize(a.old_stage) + '</span>' +
        ' <i class="fas fa-arrow-right mx-1" style="font-size:0.7rem;color:var(--gray-400)"></i> ' +
        '<span class="stage-badge stage-badge-' + a.new_stage + '">' + capitalize(a.new_stage) + '</span>' +
      '</div>';
    }

    var followUpTag = '';
    if (a.follow_up_date) {
      var done = parseInt(a.is_follow_up_done);
      var today = new Date().toISOString().slice(0, 10);
      var overdue = !done && a.follow_up_date < today;
      followUpTag = '<span style="font-size:0.72rem;padding:2px 8px;border-radius:10px;' +
        (done ? 'background:#f0fdf4;color:#16a34a' : (overdue ? 'background:#fef2f2;color:#dc2626' : 'background:#fffbeb;color:#b45309')) + '">' +
        '<i class="fas fa-' + (done ? 'check' : 'bell') + ' me-1"></i>' +
        'Follow-up: ' + formatDate(a.follow_up_date) +
        (done ? ' (done)' : '') +
      '</span>';
      if (!done) {
        followUpTag += ' <button class="btn btn-outline-success" style="font-size:0.68rem;padding:1px 6px;border-radius:10px" onclick="markFollowUpDone(' + a.id + ')"><i class="fas fa-check"></i></button>';
      }
    }

    html += '<div class="timeline-item">' +
      '<div class="timeline-dot timeline-dot-' + a.type + '"></div>' +
      '<div style="display:flex;align-items:flex-start;gap:10px">' +
        '<div class="type-icon" style="background:' + ti.bg + ';margin-top:2px"><i class="fas ' + ti.icon + '"></i></div>' +
        '<div style="flex:1;min-width:0">' +
          '<div style="display:flex;justify-content:space-between;align-items:center">' +
            '<div style="font-weight:600;font-size:0.85rem">' + escHtml(a.subject || capitalize(a.type)) + '</div>' +
            '<span style="font-size:0.72rem;color:var(--gray-400);flex-shrink:0;margin-left:8px">' + formatDateTime(a.created_at) + '</span>' +
          '</div>' +
          (a.description ? '<div style="font-size:0.82rem;color:var(--gray-500);margin-top:2px;white-space:pre-wrap">' + escHtml(a.description) + '</div>' : '') +
          stageInfo +
          '<div style="margin-top:4px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">' +
            '<span style="font-size:0.72rem;color:var(--gray-400)"><i class="fas fa-user me-1"></i>' + escHtml(a.admin_name) + '</span>' +
            (a.contact_name ? '<span style="font-size:0.72rem;color:var(--gray-400)"><i class="fas fa-address-book me-1"></i>' + escHtml(a.contact_name) + '</span>' : '') +
            followUpTag +
          '</div>' +
        '</div>' +
      '</div>' +
    '</div>';
  });
  $('#timelineBody').html(html);
}

function markFollowUpDone(id) {
  apiPost('/admin/api/b2b/', { action: 'complete_follow_up', id: id })
    .done(function (res) {
      if (res.success) loadCompany();
      else showAlertModal(res.message, 'danger');
    });
}

/* ── Edit company ── */
function openEditCompanyModal() {
  var c = companyData;
  $('#editCompanyName').val(c.company_name);
  $('#editCategory').val(c.category_id || '');
  $('#editSource').val(c.source || '');
  $('#editAddress').val(c.address || '');
  $('#editCity').val(c.city || '');
  $('#editState').val(c.state || '');
  $('#editPincode').val(c.pincode || '');
  $('#editGst').val(c.gst_number || '');
  $('#editAssignedTo').val(c.assigned_to || '');
  $('#editCreditLimit').val(c.credit_limit || '');
  $('#editPaymentTerms').val(c.payment_terms_days || '');
  $('#editDiscountType').val(c.discount_type || '');
  $('#editDiscountValue').val(c.discount_value || '');
  $('#editLocationUrl').val(c.location_url || '');
  // Photo is a file input — can't be pre-filled. Reset it and preview current.
  $('#editPhoto').val('');
  $('#editRemovePhoto').prop('checked', false);
  if (c.photo) {
    $('#editPhotoPreview').attr('src', c.photo).show();
    $('#editRemovePhotoWrap').show();
  } else {
    $('#editPhotoPreview').attr('src', '').hide();
    $('#editRemovePhotoWrap').hide();
  }
  $('#editNotes').val(c.notes || '');
  $('#editCompanyModal').modal('show');
}

$('#editCompanyForm').on('submit', function (e) {
  e.preventDefault();
  var fd = new FormData();
  fd.append('action',             'edit_company');
  fd.append('id',                 COMPANY_ID);
  fd.append('company_name',       $('#editCompanyName').val());
  fd.append('category_id',        $('#editCategory').val());
  fd.append('source',             $('#editSource').val());
  fd.append('address',            $('#editAddress').val());
  fd.append('city',               $('#editCity').val());
  fd.append('state',              $('#editState').val());
  fd.append('pincode',            $('#editPincode').val());
  fd.append('gst_number',         $('#editGst').val());
  fd.append('location_url',       $('#editLocationUrl').val());
  fd.append('assigned_to',        $('#editAssignedTo').val());
  fd.append('credit_limit',       $('#editCreditLimit').val());
  fd.append('payment_terms_days', $('#editPaymentTerms').val());
  fd.append('discount_type',      $('#editDiscountType').val());
  fd.append('discount_value',     $('#editDiscountValue').val());
  fd.append('notes',              $('#editNotes').val());
  var editPhoto = $('#editPhoto')[0].files[0];
  if (editPhoto) fd.append('photo', editPhoto);
  if ($('#editRemovePhoto').is(':checked')) fd.append('remove_photo', 1);
  showLoader('Saving…');
  apiPostForm('/admin/api/b2b/', fd)
    .done(function (res) {
      showAlertModal(res.message, res.success ? 'success' : 'danger');
      if (res.success) { $('#editCompanyModal').modal('hide'); loadCompany(); }
    })
    .fail(function () { showAlertModal('Request failed.', 'danger'); })
    .always(hideLoader);
});

/* ── Company Orders tab ── */
var companyOrdersLoaded = false;

function loadCompanyOrders() {
  if (companyOrdersLoaded) return;
  companyOrdersLoaded = true;

  apiGet('/admin/api/b2b-orders/', { company_id: COMPANY_ID, per_page: 50 })
    .done(function (res) {
      if (!res.success) return;
      var orders = res.data || [];
      if (!orders.length) {
        $('#companyOrdersList').html('<div style="text-align:center;color:var(--gray-400);padding:20px"><i class="fas fa-file-invoice fa-2x mb-2 d-block"></i>No orders yet</div>');
        return;
      }
      var html = '<div class="table-responsive"><table class="table table-sm" style="font-size:0.85rem"><thead><tr>' +
        '<th>Order ID</th><th>Total</th><th>Balance</th><th>Status</th><th>Payment</th><th>Date</th><th></th>' +
        '</tr></thead><tbody>';
      orders.forEach(function (o) {
        html += '<tr>' +
          '<td style="font-weight:600">' + escHtml(o.id) + '</td>' +
          '<td>' + formatCurrency(o.total_amount) + '</td>' +
          '<td style="' + (parseFloat(o.balance_amount) > 0 ? 'color:#dc2626;font-weight:600' : '') + '">' + formatCurrency(o.balance_amount) + '</td>' +
          '<td><span class="stage-badge stage-badge-' + (o.status === 'delivered' || o.status === 'confirmed' ? 'converted' : (o.status === 'cancelled' ? 'lost' : 'lead')) + '">' + capitalize(o.status) + '</span></td>' +
          '<td><span class="stage-badge stage-badge-' + (o.payment_status === 'paid' ? 'converted' : (o.payment_status === 'partial' ? 'negotiation' : 'lead')) + '">' + capitalize(o.payment_status) + '</span></td>' +
          '<td>' + formatDate(o.order_date) + '</td>' +
          '<td>' +
            (o.status === 'delivered' ? '<a href="/admin/b2b-orders/create/?repeat=' + encodeURIComponent(o.id) + '" class="btn btn-sm btn-outline-primary" style="font-size:0.72rem;padding:1px 8px"><i class="fas fa-redo me-1"></i>Repeat</a>' : '') +
          '</td>' +
        '</tr>';
      });
      html += '</tbody></table></div>';
      $('#companyOrdersList').html(html);
    });
}

/* ── Company Payments tab ── */
var companyPaymentsLoaded = false;

function loadCompanyPayments() {
  companyPaymentsLoaded = true;

  // Balance summary
  apiGet('/admin/api/b2b-orders/', { view: 'company_balance', company_id: COMPANY_ID })
    .done(function (res) {
      if (!res.success) return;
      var d = res.data;
      var html = '<div class="d-flex gap-4 flex-wrap">' +
        '<div><span style="font-size:0.78rem;color:var(--gray-400)">Outstanding</span><div style="font-weight:700;font-size:1.1rem;' + (d.outstanding > 0 ? 'color:#dc2626' : '') + '">' + formatCurrency(d.outstanding) + '</div></div>' +
        '<div><span style="font-size:0.78rem;color:var(--gray-400)">Overdue</span><div style="font-weight:700;font-size:1.1rem;color:#dc2626">' + formatCurrency(d.overdue) + '</div></div>' +
        (d.advance > 0 ? '<div><span style="font-size:0.78rem;color:var(--gray-400)">Advance Credit</span><div style="font-weight:700;font-size:1.1rem;color:#16a34a">' + formatCurrency(d.advance) + '</div></div>' : '') +
        '<div><span style="font-size:0.78rem;color:var(--gray-400)">Credit Limit</span><div style="font-weight:700;font-size:1.1rem">' + (d.credit_limit > 0 ? formatCurrency(d.credit_limit) : '—') + '</div></div>' +
      '</div>';
      $('#balanceSummary').html(html);
    });

  // Payments list — fetch all orders for this company and their payments
  apiGet('/admin/api/b2b-orders/', { company_id: COMPANY_ID, per_page: 100 })
    .done(function (res) {
      if (!res.success) return;
      var orders = res.data || [];
      // For each order with balance, show payment info
      // We'll load a simpler view — all payments for this company
      // The API doesn't have a direct company payments list, so we show orders with payment status
      var unpaid = orders.filter(function (o) { return parseFloat(o.balance_amount) > 0 && o.status !== 'cancelled'; });
      if (!unpaid.length) {
        $('#companyPaymentsList').html('<div style="text-align:center;color:var(--gray-400);padding:20px"><i class="fas fa-check-circle fa-2x mb-2 d-block" style="color:#16a34a"></i>All orders are fully paid</div>');
        return;
      }

      // Populate the order dropdown in general payment modal
      var selectOpts = '<option value="">General payment</option>';
      unpaid.forEach(function (o) {
        selectOpts += '<option value="' + escHtml(o.id) + '">Order ' + escHtml(o.id) + ' (bal: ' + formatCurrency(o.balance_amount) + ')</option>';
      });
      $('#gpOrderId').html(selectOpts);

      var html = '<div class="table-responsive"><table class="table table-sm" style="font-size:0.85rem"><thead><tr>' +
        '<th>Order</th><th>Total</th><th>Paid</th><th>Balance</th><th>Due Date</th><th></th>' +
        '</tr></thead><tbody>';
      unpaid.forEach(function (o) {
        var overdue = o.due_date && o.due_date < new Date().toISOString().slice(0, 10);
        html += '<tr>' +
          '<td style="font-weight:600">' + escHtml(o.id) + '</td>' +
          '<td>' + formatCurrency(o.total_amount) + '</td>' +
          '<td style="color:#16a34a">' + formatCurrency(o.paid_amount) + '</td>' +
          '<td style="color:#dc2626;font-weight:600">' + formatCurrency(o.balance_amount) + '</td>' +
          '<td style="' + (overdue ? 'color:#dc2626;font-weight:600' : '') + '">' + (o.due_date ? formatDate(o.due_date) : '—') + (overdue ? ' (overdue)' : '') + '</td>' +
          '<td><button class="btn btn-sm btn-outline-primary" style="font-size:0.72rem;padding:1px 8px" onclick="quickPay(\'' + escHtml(o.id) + '\',' + o.balance_amount + ')"><i class="fas fa-indian-rupee-sign me-1"></i>Pay</button></td>' +
        '</tr>';
      });
      html += '</tbody></table></div>';
      $('#companyPaymentsList').html(html);
    });
}

function openGeneralPaymentModal() {
  $('#gpAmount, #gpRef, #gpNotes').val('');
  $('#gpDate').val(new Date().toISOString().slice(0, 10));
  $('#gpType').val('payment');
  $('#generalPaymentModal').modal('show');
}

function quickPay(orderId, balance) {
  $('#gpOrderId').val(orderId);
  $('#gpAmount').val(balance);
  $('#gpDate').val(new Date().toISOString().slice(0, 10));
  $('#gpRef, #gpNotes').val('');
  $('#gpType').val('payment');
  $('#generalPaymentModal').modal('show');
}

$('#generalPaymentForm').on('submit', function (e) {
  e.preventDefault();
  var data = {
    action:           'add_payment',
    company_id:       COMPANY_ID,
    order_id:         $('#gpOrderId').val() || null,
    amount:           parseFloat($('#gpAmount').val()),
    payment_type:     $('#gpType').val(),
    payment_method:   $('#gpMethod').val(),
    reference_number: $('#gpRef').val(),
    notes:            $('#gpNotes').val(),
    payment_date:     $('#gpDate').val()
  };

  showLoader('Recording…');
  apiPost('/admin/api/b2b-orders/', data)
    .done(function (res) {
      showAlertModal(res.message, res.success ? 'success' : 'danger');
      if (res.success) {
        $('#generalPaymentModal').modal('hide');
        companyPaymentsLoaded = false;
        loadCompanyPayments();
        companyOrdersLoaded = false;
      }
    })
    .fail(function () { showAlertModal('Request failed.', 'danger'); })
    .always(hideLoader);
});

function capitalize(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, ' ') : ''; }
