/* Varthaai Admin — Reviews Page */

var reviewsState = { page: 1, per_page: 20, filters: {}, total: 0 };
var reviewsData = [];
var _reviewFilterDebounce = null;

function collectReviewFilters() {
  return {
    status:    $('#filterStatus').val(),
    rating:    $('#filterRating').val(),
    search:    $('#filterSearch').val(),
    date_from: $('#filterDateFrom').val(),
    date_to:   $('#filterDateTo').val()
  };
}

function applyReviewFilters() {
  reviewsState.page = 1;
  reviewsState.filters = collectReviewFilters();
  loadReviews();
}

$(function () {
  loadReviews();

  $('#filterStatus, #filterRating, #filterDateFrom, #filterDateTo').on('change', applyReviewFilters);

  $('#filterSearch').on('input', function () {
    clearTimeout(_reviewFilterDebounce);
    _reviewFilterDebounce = setTimeout(applyReviewFilters, 400);
  });

  $('#perPageReviews').on('change', function () {
    reviewsState.per_page = parseInt(this.value);
    reviewsState.page = 1;
    loadReviews();
  });

  $('#reviewFilterForm').on('reset', function () {
    setTimeout(function () {
      reviewsState.page = 1;
      reviewsState.filters = {};
      loadReviews();
    }, 0);
  });

  $('#selectAllReviews').on('change', function () {
    $('.review-check').prop('checked', this.checked);
  });

  $(document).on('change', '.review-check', function () {
    $('#selectAllReviews').prop('checked',
      $('.review-check').length === $('.review-check:checked').length);
  });
});

function loadReviews() {
  showLoader('Loading reviews…');
  var params = Object.assign({ page: reviewsState.page, per_page: reviewsState.per_page }, reviewsState.filters);

  apiGet('/admin/api/reviews/', params)
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      var d = res.data || {};
      renderStats(d.stats);
      reviewsData = d.reviews || [];
      renderTable(d.reviews, d.total);
      renderPagination(d.total, d.per_page, d.page);
      reviewsState.total = d.total;
    })
    .fail(function () { showAlertModal('Failed to load reviews.', 'danger'); })
    .always(hideLoader);
}

function renderStats(s) {
  if (!s) return;
  $('#statTotal').text(s.total || 0);
  $('#statApproved').text(s.approved || 0);
  $('#statPending').text(s.pending || 0);
  $('#statAvgRating').text(s.avg_rating || '—');
}

function renderTable(rows, total) {
  $('#reviewsCount').text(total || 0);
  var $tbody = $('#reviewsTableBody');
  if (!rows || rows.length === 0) {
    $tbody.html('<tr><td colspan="7" class="text-center" style="padding:40px;color:var(--gray-400)"><i class="fas fa-star fa-2x mb-2 d-block"></i>No reviews found</td></tr>');
    return;
  }
  var html = '';
  rows.forEach(function (r) {
    var approved = parseInt(r.is_approved) === 1;
    var stars = '';
    for (var i = 1; i <= 5; i++) {
      stars += '<i class="' + (i <= r.rating ? 'fas' : 'far') + ' fa-star" style="color:#f59e0b;font-size:0.75rem"></i>';
    }
    html += '<tr>' +
      '<td><input type="checkbox" class="review-check" value="' + r.id + '"></td>' +
      '<td><strong>' + escHtml(r.user_name || 'Anonymous') + '</strong></td>' +
      '<td>' + stars + '</td>' +
      '<td style="max-width:300px;color:var(--gray-500);font-size:0.82rem;cursor:pointer" onclick="viewReview(' + r.id + ')" title="Click to read full review">' +
        '<div style="display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">' + escHtml(r.review) + '</div></td>' +
      '<td><span class="badge ' + (approved ? 'badge-success' : 'badge-warning') + '">' + (approved ? 'Approved' : 'Pending') + '</span></td>' +
      '<td style="color:var(--gray-400);font-size:0.8rem;white-space:nowrap">' + formatDate(r.created_at) + '</td>' +
      '<td><div class="table-actions">' +
        (approved
          ? '<button class="btn-icon edit" title="Reject" onclick="reviewAction(' + r.id + ',\'reject\')"><i class="fas fa-times"></i></button>'
          : '<button class="btn-icon view" title="Approve" onclick="reviewAction(' + r.id + ',\'approve\')"><i class="fas fa-check"></i></button>') +
        '<button class="btn-icon delete" title="Delete" onclick="reviewAction(' + r.id + ',\'delete\')"><i class="fas fa-trash"></i></button>' +
      '</div></td>' +
      '</tr>';
  });
  $tbody.html(html);
}

function renderPagination(total, perPage, current) {
  var totalPages = Math.ceil(total / perPage);
  var $p = $('#reviewsPagination');
  if (totalPages <= 1) { $p.html(''); return; }

  function pi(disabled, onclick, label) {
    return '<li class="page-item' + (disabled ? ' disabled' : '') + '">' +
      '<a class="page-link" href="#" onclick="' + onclick + ';return false">' + label + '</a></li>';
  }

  var html = '<nav><ul class="pagination justify-content-center mb-0 flex-wrap">';
  html += pi(current <= 1, 'reviewsGoPage(1)',                     '<i class="fas fa-angles-left"></i>');
  html += pi(current <= 1, 'reviewsGoPage(' + (current - 1) + ')', '<i class="fas fa-angle-left"></i>');
  for (var i = Math.max(1, current - 2); i <= Math.min(totalPages, current + 2); i++) {
    html += '<li class="page-item' + (i === current ? ' active' : '') + '">' +
      '<a class="page-link" href="#" onclick="reviewsGoPage(' + i + ');return false">' + i + '</a></li>';
  }
  html += pi(current >= totalPages, 'reviewsGoPage(' + (current + 1) + ')', '<i class="fas fa-angle-right"></i>');
  html += pi(current >= totalPages, 'reviewsGoPage(' + totalPages + ')',     '<i class="fas fa-angles-right"></i>');
  html += '</ul></nav>';
  $p.html(html);
}

function reviewsGoPage(p) { reviewsState.page = p; loadReviews(); return false; }

function reviewAction(id, action) {
  var labels = { approve: 'approve', reject: 'reject', delete: 'delete' };
  confirmThen('Are you sure you want to ' + labels[action] + ' this review?', function () {
    showLoader();
    apiPost('/admin/api/reviews/', { action: action, id: id })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) loadReviews();
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
}

function viewReview(id) {
  var r = reviewsData.find(function (x) { return parseInt(x.id) === id; });
  if (!r) return;
  var approved = parseInt(r.is_approved) === 1;
  var stars = '';
  for (var i = 1; i <= 5; i++) {
    stars += '<i class="' + (i <= r.rating ? 'fas' : 'far') + ' fa-star" style="color:#f59e0b"></i>';
  }
  var actions = approved
    ? '<button class="btn btn-sm btn-outline-danger" onclick="reviewAction(' + r.id + ',\'reject\');$(\'#reviewModal\').modal(\'hide\')"><i class="fas fa-times me-1"></i>Reject</button>'
    : '<button class="btn btn-sm btn-success" onclick="reviewAction(' + r.id + ',\'approve\');$(\'#reviewModal\').modal(\'hide\')"><i class="fas fa-check me-1"></i>Approve</button>';

  var html = '<div style="margin-bottom:16px">' +
    '<div style="font-weight:700;font-size:1rem;margin-bottom:4px">' + escHtml(r.user_name || 'Anonymous') + '</div>' +
    '<div style="margin-bottom:4px">' + stars + ' <span style="font-size:0.82rem;color:var(--gray-400);margin-left:6px">' + r.rating + '/5</span></div>' +
    '<div style="font-size:0.78rem;color:var(--gray-400)">' + formatDate(r.created_at) + ' · ' +
      '<span class="badge ' + (approved ? 'badge-success' : 'badge-warning') + '">' + (approved ? 'Approved' : 'Pending') + '</span>' +
    '</div>' +
  '</div>' +
  '<div style="font-size:0.9rem;line-height:1.7;white-space:pre-wrap;background:var(--gray-50);border-radius:8px;padding:16px">' + escHtml(r.review) + '</div>' +
  '<div class="mt-3 d-flex gap-2">' + actions +
    '<button class="btn btn-sm btn-outline-danger" onclick="reviewAction(' + r.id + ',\'delete\');$(\'#reviewModal\').modal(\'hide\')"><i class="fas fa-trash me-1"></i>Delete</button>' +
  '</div>';

  $('#reviewModalBody').html(html);
  $('#reviewModal').modal('show');
}

function bulkApprove() {
  var ids = $('.review-check:checked').map(function () { return parseInt(this.value); }).get();
  if (!ids.length) { showAlertModal('Select at least one review.', 'warning'); return; }
  confirmThen('Approve ' + ids.length + ' selected review(s)?', function () {
    showLoader();
    apiPost('/admin/api/reviews/', { action: 'bulk_approve', ids: ids })
      .done(function (res) {
        showAlertModal(res.message, res.success ? 'success' : 'danger');
        if (res.success) loadReviews();
      })
      .fail(function () { showAlertModal('Request failed.', 'danger'); })
      .always(hideLoader);
  });
}
