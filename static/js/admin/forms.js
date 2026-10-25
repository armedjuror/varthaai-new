/* Varthaai Admin — Forms list */

var STATUS_BADGE = { draft: 'bg-warning', live: 'bg-success', closed: 'bg-danger' };

$(function () { loadForms(); });

function loadForms() {
  apiGet('/admin/api/forms/')
    .done(function (res) {
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      var rows = res.data.forms || [];
      $('#formsCount').text(rows.length);
      if (!rows.length) {
        $('#formsBody').html('<tr><td colspan="5" class="text-center" style="padding:40px;color:var(--gray-400)">No forms yet. Create your first one.</td></tr>');
        return;
      }
      $('#formsBody').html(rows.map(function (f) {
        return '<tr>' +
          '<td><a href="/admin/forms/' + f.id + '/"><strong>' + escHtml(f.title) + '</strong></a></td>' +
          '<td><span class="badge ' + (STATUS_BADGE[f.status] || '') + '">' + escHtml(f.status) + '</span></td>' +
          '<td>' + f.response_count + '</td>' +
          '<td>' + formatDate(f.created_at) + '</td>' +
          '<td class="table-actions">' +
            '<a class="btn-icon edit" title="Open" href="/admin/forms/' + f.id + '/"><i class="fas fa-pen"></i></a> ' +
            '<button class="btn-icon delete" title="Delete" onclick="deleteForm(' + f.id + ',' + f.response_count + ')"><i class="fas fa-trash"></i></button>' +
          '</td></tr>';
      }).join(''));
    })
    .fail(function () { showAlertModal('Failed to load forms.', 'danger'); });
}

function openCreateForm() {
  $('#newFormTitle, #newFormDescription').val('');
  new bootstrap.Modal('#createFormModal').show();
}

function createForm() {
  var title = $('#newFormTitle').val().trim();
  if (!title) { showAlertModal('Please enter a title.', 'danger'); return; }
  showLoader('Creating…');
  apiPost('/admin/api/forms/', { action: 'create', title: title, description: $('#newFormDescription').val() })
    .done(function (res) {
      hideLoader();
      if (!res.success) { showAlertModal(res.message, 'danger'); return; }
      window.location.href = '/admin/forms/' + res.data.id + '/';
    })
    .fail(function (xhr) { hideLoader(); showAlertModal(apiErrorMessage(xhr, 'Failed to create form.'), 'danger'); });
}

function deleteForm(id, responses) {
  var msg = responses
    ? 'This form has ' + responses + ' response(s). Deleting it removes all of them permanently. Continue?'
    : 'Delete this form?';
  confirmThen(msg, function () {
    apiPost('/admin/api/forms/', { action: 'delete', id: id })
      .done(function (res) {
        if (!res.success) { showAlertModal(res.message, 'danger'); return; }
        loadForms();
      })
      .fail(function (xhr) { showAlertModal(apiErrorMessage(xhr, 'Failed to delete.'), 'danger'); });
  });
}
