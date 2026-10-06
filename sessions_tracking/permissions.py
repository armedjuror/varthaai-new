"""
Permission model for sessions_tracking.

Deliberately independent of core.api.HasModulePermission / brand_permissions:
employees have none of that — `role == EMPLOYEE` is a separate, restricted
tier (see accounts.models.AdminUser.Role) with no brand context at all.

Employee-facing endpoints never take a target user id — they always act on
`request.user`, which structurally rules out IDOR for that half of the API.
Admin dashboard endpoints accept an explicit employee id and must combine
IsAdminRole with an explicit `role=EMPLOYEE` filter when resolving it (see
sessions_tracking.views_admin.resolve_employee).
"""
from rest_framework.permissions import BasePermission


class IsEmployee(BasePermission):
    """Employee-only endpoints — all scoped implicitly to request.user."""

    message = 'This endpoint is for employee accounts only.'

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_employee)


class IsAdminRole(BasePermission):
    """
    Admin-dashboard endpoints. Any authenticated non-employee AdminUser
    (super_admin/admin/staff) passes this check; combine with
    core.api.HasModulePermission (permission_module='employee_performance')
    on views that should additionally respect the brand_permissions module
    list, per the project's existing permission mechanism.
    """

    message = 'Admin access required.'

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return not user.is_employee
