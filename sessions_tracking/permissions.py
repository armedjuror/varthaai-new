"""
Field-employee access is a per-brand module permission ('field_employee'),
not a separate AdminUser role — any super_admin/admin/staff account can be
granted it (see core/permissions.py's MODULE_PERMISSIONS), so the same
account can be both an admin and a field employee (e.g. Saad). Self-service
session-tracking endpoints (sessions_tracking.views_employee) gate on this
the same way as every other admin screen: core.api.HasModulePermission with
permission_module='field_employee'. The admin-only Employee Performance
dashboard (sessions_tracking.views_admin) gates the same way with
permission_module='employee_performance'.

`is_field_employee` below is only needed where we have to resolve WHICH
AdminUsers count as field employees at all — the dashboard's employee
selector and the Telegram report's recipient list — a question
`brand_permissions` (a per-brand JSON blob) can't answer with a plain
queryset filter, so it's a small Python-side check instead.
"""
from core.api import has_module_permission


def is_field_employee(user, brand_id=None):
    """
    Two different questions depending on `brand_id`:

    - brand_id given: may `user` use field-employee session tracking RIGHT
      NOW, for that brand? Ordinary permission-check semantics via
      has_module_permission — super_admin and the 'all' wildcard both pass,
      exactly like any other module. Used to gate the session-tracking
      endpoints and the post-login redirect.

    - brand_id=None (default): does `user` actually hold an explicit
      'field_employee' grant on some brand? Used to decide "is this account
      one of our field employees at all" — the dashboard's employee
      selector, the Telegram report's recipient list. Deliberately does NOT
      treat super_admin or the 'all' wildcard as a yes here: bypassing the
      permission check isn't the same as being a field employee, and a
      full-access admin shouldn't appear in that list (or get the daily
      Telegram report) just because they technically could use the feature.
    """
    if brand_id is not None:
        return has_module_permission(user, brand_id, 'field_employee')
    perms = user.brand_permissions or {}
    return any('field_employee' in granted for granted in perms.values())
