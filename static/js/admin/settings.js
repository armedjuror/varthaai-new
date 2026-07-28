/* Varthaai Admin — Settings Page */

var _allAdmins = [];
var _allBrands = [];

var PERM_KEYS = {
  all:       'Full Access',
  dashboard: 'Dashboard',
  orders:    'Orders',
  coupons:   'Coupons',
  flavors:   'Flavors',
  stocks:    'Stocks',
  customers: 'Customers',
  reviews:   'Reviews',
  expenses:  'Expenses',
  settings:  'Settings',
  b2b:       'B2B',
  blogs:     'Blogs'
};

$(function () {
  loadSettings();

  /* ── Business form ── */
  $('#businessForm').on('submit', function (e) {
    e.preventDefault();
    var data = {
      business_name:           $('#business_name').val(),
      business_email:          $('#business_email').val(),
      business_phone:          $('#business_phone').val(),
      business_address:        $('#business_address').val(),
      delivery_charge:         $('#delivery_charge').val(),
      free_delivery_threshold: $('#free_delivery_threshold').val(),
      min_order_quantity:      $('#min_order_quantity').val()
    };
    showLoader('Saving…');
    apiPost('/admin/api/settings/', { action: 'update_settings', data: data })
      .done(function (res) { showAlertModal(res.message, res.success ? 'success' : 'danger'); })
      .fail(function ()    { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });

  /* ── Loyalty form ── */
  $('#loyaltyForm').on('submit', function (e) {
    e.preventDefault();
    var data = {
      points_per_rupee: $('#points_per_rupee').val(),
      review_points:    $('#review_points').val(),
      referral_points:  $('#referral_points').val(),
      signup_bonus:     $('#signup_bonus').val()
    };
    showLoader('Saving…');
    apiPost('/admin/api/settings/', { action: 'update_settings', data: data })
      .done(function (res) { showAlertModal(res.message, res.success ? 'success' : 'danger'); })
      .fail(function ()    { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });

  /* ── Change password form ── */
  $('#passwordForm').on('submit', function (e) {
    e.preventDefault();
    var np = $('#newPassword').val();
    var cp = $('#confirmPassword').val();
    if (np !== cp) { showAlertModal('New passwords do not match.', 'warning'); return; }
    showLoader('Changing password…');
    apiPost('/admin/api/settings/', {
      action:           'change_password',
      current_password: $('#currentPassword').val(),
      new_password:     np
    })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) $('#passwordForm')[0].reset();
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });

  /* ── Create admin form ── */
  $('#createAdminForm').on('submit', function (e) {
    e.preventDefault();
    var role = $('#newAdminRole').val();
    showLoader('Creating…');
    apiPost('/admin/api/settings/', {
      action:            'create_admin',
      username:          $('#newAdminUsername').val().trim(),
      name:              $('#newAdminName').val().trim(),
      role:              role,
      password:          $('#newAdminPassword').val(),
      brand_permissions: collectBrandPerms('create')
    })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) { $('#createAdminModal').modal('hide'); $('#createAdminForm')[0].reset(); loadSettings(); }
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });

  /* ── Reset password form ── */
  $('#resetPasswordForm').on('submit', function (e) {
    e.preventDefault();
    var np = $('#resetPwNew').val();
    var cp = $('#resetPwConfirm').val();
    if (np.length < 6) { showAlertModal('Password must be at least 6 characters.', 'warning'); return; }
    if (np !== cp) { showAlertModal('Passwords do not match.', 'warning'); return; }
    showLoader('Resetting…');
    apiPost('/admin/api/settings/', {
      action: 'reset_admin_password',
      id:     parseInt($('#resetPwAdminId').val()),
      new_password: np
    })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) $('#resetPasswordModal').modal('hide');
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });

  /* ── Edit admin form ── */
  $('#editAdminForm').on('submit', function (e) {
    e.preventDefault();
    showLoader('Saving…');
    apiPost('/admin/api/settings/', {
      action:            'update_admin',
      id:                parseInt($('#editAdminId').val()),
      name:              $('#editAdminName').val().trim(),
      role:              $('#editAdminRole').val(),
      brand_permissions: collectBrandPerms('edit')
    })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) { $('#editAdminModal').modal('hide'); loadSettings(); }
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
});

function loadSettings() {
  showLoader('Loading…');
  apiGet('/admin/api/settings/')
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      populateSettings(res.settings || {});
      renderSystemStats(res.stats || {});
      if (res.brands) { _allBrands = res.brands; renderBrandPermsUI('create'); renderBrandPermsUI('edit'); }
      if (res.admins) { _allAdmins = res.admins; renderAdminsTable(_allAdmins); }
    })
    .fail(function () { showAlertModal('Failed to load settings.', 'danger'); })
    .always(hideLoader);
}

function populateSettings(s) {
  var fields = [
    'business_name', 'business_email', 'business_phone', 'business_address',
    'delivery_charge', 'free_delivery_threshold', 'min_order_quantity',
    'points_per_rupee', 'review_points', 'referral_points', 'signup_bonus'
  ];
  fields.forEach(function (k) {
    if (s[k] !== undefined) $('#' + k).val(s[k]);
  });
}

function renderSystemStats(s) {
  var items = [
    ['Total Users',    s.total_users   || 0],
    ['Total Orders',   s.total_orders  || 0],
    ['Total Flavors',  s.total_flavors || 0],
    ['Total Reviews',  s.total_reviews || 0],
    ['Total Coupons',  s.total_coupons || 0],
  ];
  var html = items.map(function (item) {
    return '<div style="display:flex;justify-content:space-between;padding:9px 0;border-bottom:1px solid var(--gray-100);font-size:0.875rem">' +
      '<span style="color:var(--gray-500)">' + item[0] + '</span>' +
      '<strong>' + Number(item[1]).toLocaleString('en-IN') + '</strong>' +
      '</div>';
  }).join('');
  $('#systemStats').html(html);
}

/* ── Admin Users table ── */
var roleColors = { super_admin: '#dc2626', admin: '#2563eb', staff: '#f59e0b' };
var roleLabels = { super_admin: 'Super Admin', admin: 'Admin', staff: 'Staff' };

function renderAdminsTable(admins) {
  var $tbody = $('#adminsTableBody');
  if (!admins || admins.length === 0) {
    $tbody.html('<tr><td colspan="6" class="text-center" style="padding:40px;color:var(--gray-400)">No admin users found</td></tr>');
    return;
  }
  var html = '';
  admins.forEach(function (a) {
    var isMe    = parseInt(a.id) === CURRENT_ADMIN_ID;
    var color   = roleColors[a.role]  || '#6b7280';
    var label   = roleLabels[a.role]  || a.role;
    var bpHtml  = renderBrandPermsBadges(a.role, a.brand_permissions);

    html += '<tr>' +
      '<td><strong>' + escHtml(a.name) + '</strong>' + (isMe ? ' <span class="badge badge-info" style="font-size:0.7rem">You</span>' : '') + '</td>' +
      '<td style="color:var(--gray-500);font-size:0.85rem">@' + escHtml(a.username) + '</td>' +
      '<td><span class="badge" style="background:' + color + '">' + label + '</span></td>' +
      '<td>' + bpHtml + '</td>' +
      '<td style="color:var(--gray-400);font-size:0.8rem;white-space:nowrap">' + (a.last_login ? formatDate(a.last_login) : '—') + '</td>' +
      '<td><div class="table-actions">' +
        '<button class="btn-icon edit" title="Edit" onclick="openEditAdmin(' + a.id + ')"><i class="fas fa-pen"></i></button>' +
        (!isMe ? '<button class="btn-icon edit" title="Reset Password" onclick="resetAdminPassword(' + a.id + ')"><i class="fas fa-key"></i></button>' : '') +
        (!isMe ? '<button class="btn-icon delete" title="Delete" onclick="deleteAdmin(' + a.id + ')"><i class="fas fa-trash"></i></button>' : '') +
      '</div></td>' +
      '</tr>';
  });
  $tbody.html(html);
}

function renderBrandPermsBadges(role, bp) {
  if (role === 'super_admin' || !bp || !Object.keys(bp).length) {
    return '<span class="badge badge-success">All Brands — Full Access</span>';
  }
  var parts = [];
  Object.keys(bp).forEach(function (brandId) {
    var brand = _allBrands.find(function (b) { return String(b.id) === brandId; });
    var brandName = brand ? brand.name : 'ID:' + brandId;
    var perms = bp[brandId] || [];
    var permLabels = perms.map(function (p) { return PERM_KEYS[p] || p; }).join(', ');
    parts.push(
      '<div style="margin-bottom:2px">' +
      '<span class="badge" style="background:#85AA4E;font-size:0.72rem;margin-right:3px">' + escHtml(brandName) + '</span>' +
      '<span style="font-size:0.75rem;color:var(--gray-500)">' + escHtml(permLabels) + '</span>' +
      '</div>'
    );
  });
  return parts.join('');
}

/* ── Per-brand permissions UI ── */
function renderBrandPermsUI(prefix) {
  var $container = $('#' + prefix + 'BrandPermsContainer');
  $container.empty();
  if (!_allBrands.length) {
    $container.html('<div class="text-muted">No active brands found.</div>');
    return;
  }
  _allBrands.forEach(function (b) {
    var brandKey = prefix + '_bp_' + b.id;
    var enableId = brandKey + '_enable';
    var html =
      '<div class="border rounded p-3 mb-2" data-brand-id="' + b.id + '" data-prefix="' + prefix + '">' +
        '<div class="form-check mb-2">' +
          '<input class="form-check-input ' + prefix + '-brand-enable" type="checkbox" id="' + enableId + '" value="' + b.id + '" onchange="toggleBrandPerms(this)">' +
          '<label class="form-check-label fw-semibold" for="' + enableId + '">' + escHtml(b.name) + '</label>' +
        '</div>' +
        '<div class="d-flex flex-wrap gap-3 ms-4 ' + prefix + '-brand-perms-' + b.id + '" style="display:none!important">';
    Object.keys(PERM_KEYS).forEach(function (key) {
      var cbId = brandKey + '_' + key;
      html +=
        '<div class="form-check">' +
          '<input class="form-check-input ' + prefix + '-bperm-' + b.id + '" type="checkbox" value="' + key + '" id="' + cbId + '">' +
          '<label class="form-check-label" for="' + cbId + '">' + PERM_KEYS[key] + '</label>' +
        '</div>';
    });
    html += '</div></div>';
    $container.append(html);
  });
}

function toggleBrandPerms(checkbox) {
  var $wrap = $(checkbox).closest('[data-brand-id]');
  var brandId = $wrap.data('brand-id');
  var prefix  = $wrap.data('prefix');
  var $perms  = $wrap.find('.' + prefix + '-brand-perms-' + brandId);
  if (checkbox.checked) {
    $perms.css('display', '').removeClass('d-none');
  } else {
    $perms.css('display', 'none').addClass('d-none');
    $perms.find('input[type=checkbox]').prop('checked', false);
  }
}

function collectBrandPerms(prefix) {
  var result = {};
  $('#' + prefix + 'BrandPermsContainer [data-brand-id]').each(function () {
    var brandId = String($(this).data('brand-id'));
    var $enable = $(this).find('.' + prefix + '-brand-enable');
    if (!$enable.is(':checked')) return;
    var perms = [];
    $(this).find('.' + prefix + '-bperm-' + brandId + ':checked').each(function () {
      perms.push(this.value);
    });
    if (perms.length) result[brandId] = perms;
  });
  return Object.keys(result).length ? result : null;
}

function setBrandPerms(prefix, bp) {
  // Reset all
  $('#' + prefix + 'BrandPermsContainer .' + prefix + '-brand-enable').prop('checked', false).each(function () {
    toggleBrandPerms(this);
  });
  if (!bp || typeof bp !== 'object') return;
  Object.keys(bp).forEach(function (brandId) {
    var $enable = $('#' + prefix + '_bp_' + brandId + '_enable');
    if (!$enable.length) return;
    $enable.prop('checked', true);
    toggleBrandPerms($enable[0]);
    var perms = bp[brandId] || [];
    perms.forEach(function (p) {
      $('#' + prefix + '_bp_' + brandId + '_' + p).prop('checked', true);
    });
  });
}

function toggleBrandPermsSection(prefix) {
  var role = $('#' + (prefix === 'create' ? 'newAdminRole' : 'editAdminRole')).val();
  var $section = $('#' + prefix + 'BrandPermsSection');
  role === 'super_admin' ? $section.hide() : $section.show();
}

function openEditAdmin(id) {
  var a = _allAdmins.find(function (x) { return parseInt(x.id) === id; });
  if (!a) return;
  $('#editAdminId').val(a.id);
  $('#editAdminName').val(a.name);
  $('#editAdminRole').val(a.role).trigger('change');
  setBrandPerms('edit', a.brand_permissions);
  toggleBrandPermsSection('edit');
  $('#editAdminModal').modal('show');
}

function resetAdminPassword(id) {
  var a = _allAdmins.find(function (x) { return parseInt(x.id) === id; });
  var name = a ? a.name : 'this admin';
  $('#resetPwAdminId').val(id);
  $('#resetPwAdminName').text(name);
  $('#resetPwNew').val('');
  $('#resetPwConfirm').val('');
  $('#resetPasswordModal').modal('show');
}

function deleteAdmin(id) {
  var a = _allAdmins.find(function (x) { return parseInt(x.id) === id; });
  confirmThen('Delete admin "' + (a ? escHtml(a.name) : id) + '"? This cannot be undone.', function () {
    showLoader('Deleting…');
    apiPost('/admin/api/settings/', { action: 'delete_admin', id: id })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) loadSettings();
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
}
