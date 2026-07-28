/**
 * Varthaai Admin — Shared JS
 * Handles: sidebar toggle, active nav, table search
 */
(function () {
  'use strict';

  var sidebar  = document.querySelector('.sidebar');
  var backdrop = document.querySelector('.sidebar-backdrop');
  var toggle   = document.getElementById('toggle-sidebar');

  function openSidebar() {
    if (sidebar)  sidebar.classList.add('show');
    if (backdrop) backdrop.classList.add('show');
  }

  function closeSidebar() {
    if (sidebar)  sidebar.classList.remove('show');
    if (backdrop) backdrop.classList.remove('show');
  }

  if (toggle)   toggle.addEventListener('click', function () {
    sidebar && sidebar.classList.contains('show') ? closeSidebar() : openSidebar();
  });

  if (backdrop) backdrop.addEventListener('click', closeSidebar);

  window.addEventListener('resize', function () {
    if (window.innerWidth > 991) closeSidebar();
  });

  // Table search via #admin-search input
  var searchInput = document.getElementById('admin-search');
  if (searchInput) {
    searchInput.addEventListener('input', function () {
      var term = this.value.toLowerCase().trim();
      document.querySelectorAll('.table tbody tr').forEach(function (row) {
        row.style.display = (!term || row.textContent.toLowerCase().includes(term)) ? '' : 'none';
      });
    });
  }

})();
