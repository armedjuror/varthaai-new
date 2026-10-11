"""
Single source of truth for brand-scoped module permission keys.

Every admin view guards itself with `permission_module = '<key>'`
(core.api.HasModulePermission) or `@require_module('<key>')` (core.auth,
for plain HTML views). Whenever a new guarded view is added, register its
key + a human-readable label here — nowhere else.

core.settings_views.SettingsAPI.get() serialises MODULE_PERMISSIONS into the
Settings payload, and static/js/admin/settings.js builds the Admin Users
permission checkboxes from that response, so a new entry here appears in the
UI automatically. core.tests enforces the other direction: it scans the
codebase for `permission_module`/`require_module` keys and fails if any of
them aren't registered here.
"""

# (key, label) — 'all' is the wildcard matched in has_module_permission();
# it isn't a real permission_module any view declares, but is listed first
# as "Full Access" since it's a selectable checkbox in the UI.
MODULE_PERMISSIONS = [
    ('all', 'Full Access'),
    ('dashboard', 'Dashboard'),
    ('orders', 'Orders'),
    ('coupons', 'Coupons'),
    ('flavors', 'Flavors'),
    ('packs', 'Packs'),
    ('stocks', 'Stocks'),
    ('customers', 'Customers'),
    ('reviews', 'Reviews'),
    ('blogs', 'Blogs'),
    ('expenses', 'Expenses'),
    ('settings', 'Settings'),
    ('b2b', 'B2B'),
    ('gst_records', 'GST Records (read-only, for the CA)'),
    ('field_employee', 'Field Employee (sessions)'),
    ('employee_performance', 'Employee Performance'),
    ('content_dashboard', 'Content: Dashboard'),
    ('content_calendar', 'Content: Calendar'),
    ('content_tasks', 'Content: Tasks'),
    ('content_verdict', 'Content: Verdict'),
    ('content_scripts', 'Content: Scripts'),
    ('content_posters', 'Content: Posters'),
]

MODULE_KEYS = frozenset(key for key, _ in MODULE_PERMISSIONS)
