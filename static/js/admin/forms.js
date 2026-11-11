/* Varthaai Admin — Forms list */

var STATUS_BADGE = { draft: 'bg-warning', live: 'bg-success', closed: 'bg-danger' };

$(function () {
  loadForms();
  // Keep each colour picker and its hex box in step.
  [['#brPrimaryPick', '#brPrimary'], ['#brAccentPick', '#brAccent'], ['#brBgPick', '#brBg']].forEach(function (p) {
    $(p[0]).on('input', function () { $(p[1]).val(this.value.toUpperCase()); });
    $(p[1]).on('input', function () { if (/^#[0-9A-Fa-f]{6}$/.test(this.value)) $(p[0]).val(this.value); });
  });
});

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

/* ── per-brand branding of public form pages ── */

var BRANDING_API = '/admin/api/forms/branding/';

function openBranding() {
  apiGet(BRANDING_API).done(function (res) {
    if (!res.success) { showAlertModal(res.message, 'danger'); return; }
    var b = res.data;
    [['#brPrimary', '#brPrimaryPick', b.primary_color, '#447337'], ['#brAccent', '#brAccentPick', b.accent_color, '#E9C202'],
     ['#brBg', '#brBgPick', b.background_color, '#EDE8D6']].forEach(function (x) {
      $(x[0]).val(x[2]); $(x[1]).val(x[2] || x[3]);
    });
    $('#brFooter').val(b.footer_text); $('#brHours').val(b.default_download_hours);
    $('#brLogo').val(''); $('#brRemoveLogo').prop('checked', false);
    if (b.logo_url) { $('#brLogoImg').attr('src', b.logo_url); $('#brLogoCurrent').attr('style', ''); }
    else $('#brLogoCurrent').attr('style', 'display:none!important');
    new bootstrap.Modal('#brandingModal').show();
  }).fail(function (xhr) { showAlertModal(apiErrorMessage(xhr, 'Failed to load branding.'), 'danger'); });
}

function saveBranding() {
  var fd = new FormData();
  fd.append('primary_color', $('#brPrimary').val().trim());
  fd.append('accent_color', $('#brAccent').val().trim());
  fd.append('background_color', $('#brBg').val().trim());
  fd.append('footer_text', $('#brFooter').val());
  fd.append('default_download_hours', $('#brHours').val());
  var logo = $('#brLogo')[0].files[0];
  if (logo) fd.append('logo', logo);
  else if ($('#brRemoveLogo').is(':checked')) fd.append('remove_logo', '1');
  showLoader('Saving…');
  apiPostForm(BRANDING_API, fd).done(function (res) {
    hideLoader();
    if (!res.success) { showAlertModal(res.message, 'danger'); return; }
    bootstrap.Modal.getInstance('#brandingModal').hide();
    showAlertModal('Branding saved. It applies to all of this brand\'s forms.', 'success');
  }).fail(function (xhr) { hideLoader(); showAlertModal(apiErrorMessage(xhr, 'Failed to save branding.'), 'danger'); });
}
