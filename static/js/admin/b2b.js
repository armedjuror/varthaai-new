/* Varthaai Admin — B2B Pipeline Page */

var currentStage = '';
var currentPage  = 1;
var searchTimer  = null;

$(function () {
  loadDropdowns();
  loadPipelineStats();
  loadCompanies();
  loadFollowUps();

  $('#b2bSearch').on('input', function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () { currentPage = 1; loadCompanies(); }, 300);
  });

  $('#addCompanyForm').on('submit', function (e) {
    e.preventDefault();
    var fd = new FormData();
    fd.append('action',              'add_company');
    fd.append('company_name',        $('#addCompanyName').val());
    fd.append('category_id',         $('#addCategory').val());
    fd.append('source',              $('#addSource').val());
    fd.append('address',             $('#addAddress').val());
    fd.append('city',                $('#addCity').val());
    fd.append('state',               $('#addState').val());
    fd.append('pincode',             $('#addPincode').val());
    fd.append('gst_number',          $('#addGst').val());
    fd.append('location_url',        $('#addLocationUrl').val());
    fd.append('assigned_to',         $('#addAssignedTo').val());
    fd.append('notes',               $('#addNotes').val());
    fd.append('contact_name',        $('#addContactName').val());
    fd.append('contact_phone',       $('#addContactPhone').val());
    fd.append('contact_designation', $('#addContactDesignation').val());
    var addPhoto = $('#addPhoto')[0].files[0];
    if (addPhoto) fd.append('photo', addPhoto);
    showLoader('Adding lead…');
    apiPostForm('/admin/api/b2b/', fd)
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) {
          $('#addCompanyModal').modal('hide');
          $('#addCompanyForm')[0].reset();
          loadPipelineStats();
          loadCompanies();
        }
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
});

function loadDropdowns() {
  apiGet('/admin/api/b2b/', { view: 'categories' }).done(function (res) {
    if (!res.success) return;
    var opts = '<option value="">Select…</option>';
    (res.data || []).forEach(function (c) {
      opts += '<option value="' + c.id + '">' + escHtml(c.name) + '</option>';
    });
    $('#addCategory').html(opts);
  });

  apiGet('/admin/api/b2b/', { view: 'admins' }).done(function (res) {
    if (!res.success) return;
    var opts = '<option value="">Select…</option>';
    (res.data || []).forEach(function (a) {
      opts += '<option value="' + a.id + '">' + escHtml(a.name) + '</option>';
    });
    $('#addAssignedTo').html(opts);
  });
}

function loadPipelineStats() {
  apiGet('/admin/api/b2b/', { view: 'pipeline_stats' }).done(function (res) {
    if (!res.success) return;
    var s = res.data;
    $('#statTotal').text(s.total || 0);
    $('#statLead').text(s.lead || 0);
    $('#statContacted').text(s.contacted || 0);
    $('#statNegotiation').text(s.negotiation || 0);
    $('#statConverted').text(s.converted || 0);
    $('#statLost').text(s.lost || 0);
  });
}

function filterStage(stage) {
  currentStage = stage;
  currentPage = 1;
  $('.stage-card').removeClass('active');
  if (stage) {
    $('.stage-card[data-stage="' + stage + '"]').addClass('active');
  } else {
    $('.stage-card[data-stage=""]').addClass('active');
  }
  loadCompanies();
}

function loadCompanies() {
  var params = { page: currentPage, per_page: 25 };
  if (currentStage) params.stage = currentStage;
  var search = $('#b2bSearch').val().trim();
  if (search) params.search = search;

  apiGet('/admin/api/b2b/', params)
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      $('#companiesCount').text(res.total);
      renderCompanies(res.data || []);
      renderPagination(res.total, res.page, res.per_page);
    })
    .fail(function () { showAlertModal('Failed to load companies.', 'danger'); });
}

function renderCompanies(companies) {
  var $body = $('#companiesListBody');
  if (!companies.length) {
    $body.html('<div style="padding:40px;text-align:center;color:var(--gray-400)"><i class="fas fa-briefcase fa-2x mb-2 d-block"></i>No companies found</div>');
    return;
  }

  var html = '';
  companies.forEach(function (c) {
    var initial = c.company_name.charAt(0).toUpperCase();
    html += '<div class="company-row" onclick="window.location.href=\'/admin/b2b/' + c.id + '/\'">' +
      '<div class="d-flex align-items-center gap-3">' +
        '<div style="width:40px;height:40px;border-radius:50%;background:var(--primary-color);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:1rem;flex-shrink:0">' + initial + '</div>' +
        '<div>' +
          '<div style="font-weight:600;font-size:0.9rem">' + escHtml(c.company_name) + '</div>' +
          '<div style="font-size:0.78rem;color:var(--gray-400)">' +
            (c.category_name ? escHtml(c.category_name) : '') +
            (c.city ? (c.category_name ? ' · ' : '') + escHtml(c.city) : '') +
            ' · ' + (c.contact_count || 0) + ' contact' + (c.contact_count == 1 ? '' : 's') +
          '</div>' +
        '</div>' +
      '</div>' +
      '<div class="d-flex align-items-center gap-3">' +
        (c.assigned_name ? '<span style="font-size:0.75rem;color:var(--gray-400)"><i class="fas fa-user me-1"></i>' + escHtml(c.assigned_name) + '</span>' : '') +
        '<span class="stage-badge stage-badge-' + c.stage + '">' + capitalize(c.stage) + '</span>' +
      '</div>' +
    '</div>';
  });
  $body.html(html);
}

function renderPagination(total, page, perPage) {
  var pages = Math.ceil(total / perPage);
  if (pages <= 1) { $('#companiesPagination').html(''); return; }
  var html = '<nav><ul class="pagination pagination-sm mb-0 justify-content-center">';
  for (var i = 1; i <= pages; i++) {
    html += '<li class="page-item' + (i === page ? ' active' : '') + '">' +
      '<a class="page-link" href="#" onclick="goPage(' + i + ');return false">' + i + '</a></li>';
  }
  html += '</ul></nav>';
  $('#companiesPagination').html(html);
}

function goPage(p) { currentPage = p; loadCompanies(); }

function loadFollowUps() {
  apiGet('/admin/api/b2b/', { view: 'follow_ups' }).done(function (res) {
    if (!res.success) return;
    var items = res.data || [];
    if (!items.length) {
      $('#followUpsList').html('<div style="text-align:center;color:var(--gray-400);padding:20px"><i class="fas fa-check-circle fa-2x mb-2 d-block"></i>No pending follow-ups</div>');
      return;
    }
    var today = new Date().toISOString().slice(0, 10);
    var html = '';
    items.forEach(function (a) {
      var isOverdue = a.follow_up_date < today;
      html += '<div class="followup-item' + (isOverdue ? ' overdue' : '') + '">' +
        '<div class="d-flex justify-content-between align-items-start">' +
          '<div>' +
            '<a href="/admin/b2b/' + a.company_id + '/" style="font-weight:600;font-size:0.85rem">' + escHtml(a.company_name) + '</a>' +
            '<div style="font-size:0.78rem;color:var(--gray-500);margin-top:2px">' +
              escHtml(a.subject || a.type) +
            '</div>' +
          '</div>' +
          '<div style="text-align:right">' +
            '<div style="font-size:0.75rem;font-weight:600;color:' + (isOverdue ? '#dc2626' : '#b45309') + '">' +
              formatDate(a.follow_up_date) +
            '</div>' +
            '<button class="btn btn-outline-success btn-sm mt-1" style="font-size:0.7rem;padding:1px 8px" onclick="event.stopPropagation();markFollowUpDone(' + a.id + ')">' +
              '<i class="fas fa-check me-1"></i>Done' +
            '</button>' +
          '</div>' +
        '</div>' +
      '</div>';
    });
    $('#followUpsList').html(html);
  });
}

function markFollowUpDone(id) {
  apiPost('/admin/api/b2b/', { action: 'complete_follow_up', id: id })
    .done(function (res) {
      if (res.success) loadFollowUps();
      else showAlertModal(res.message, 'danger');
    });
}

function capitalize(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ''; }
