"""
Admin-only Employee Performance dashboard — selector, daily report, period
report (+ CSV export), overall performance. Gated by the project's existing
module-permission mechanism (`permission_module = 'employee_performance'`),
exactly like any other admin screen — so a brand admin can be scoped out
the same way. Field-employee status is itself just a permission
('field_employee', see sessions_tracking.permissions.is_field_employee),
not a separate role, so this list can include ordinary admin/staff accounts
that also do field work.

All metric computation is delegated to services.reporting /
services.overall — nothing here recomputes a number.
"""
import csv
import datetime

from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import render
from rest_framework.views import APIView

from accounts.models import AdminUser
from core.api import HasModulePermission, err, ok
from core.auth import require_module
from sessions_tracking.models import Session
from sessions_tracking.permissions import is_field_employee
from sessions_tracking.services import sessions as session_service
from sessions_tracking.services.overall import build_ranking, conversion_summary, weekly_trend
from sessions_tracking.services.reporting import (
    build_daily_report,
    build_period_report,
    build_period_report_for_users,
)
from sessions_tracking.timeutil import ist_today
from sessions_tracking.views_common import catch_validation, parse_ist_datetime, parse_iso_date


def _employees_qs():
    employees = [u for u in AdminUser.objects.all() if is_field_employee(u)]
    employees.sort(key=lambda u: (u.name or u.username))
    return employees


def resolve_employee(user_id):
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return None
    user = AdminUser.objects.filter(id=user_id).first()
    return user if user and is_field_employee(user) else None


# ── Pages ───────────────────────────────────────────────────────────────

@require_module('employee_performance')
def performance_daily_page(request):
    return render(request, 'admin/sessions/performance_daily.html')


@require_module('employee_performance')
def performance_period_page(request):
    return render(request, 'admin/sessions/performance_period.html')


@require_module('employee_performance')
def performance_overall_page(request):
    return render(request, 'admin/sessions/performance_overall.html')


# ── APIs ────────────────────────────────────────────────────────────────

class EmployeeListAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'employee_performance'

    def get(self, request):
        employees = [
            {'id': u.id, 'name': u.name, 'username': u.username, 'is_active': u.is_active}
            for u in _employees_qs()
        ]
        return ok(employees)


class DailyReportAdminAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'employee_performance'

    @catch_validation
    def get(self, request):
        employee = resolve_employee(request.query_params.get('user_id'))
        if not employee:
            return err('Employee not found.', status=404)
        day = parse_iso_date(request.query_params.get('date'), default=ist_today())
        return ok(build_daily_report(employee, day))


class EditSessionEndTimeAdminAPI(APIView):
    """Admin correction of any employee's session end time — same rule as
    the employee's own edit (required note), but not restricted to the
    requester's own session."""
    permission_classes = [HasModulePermission]
    permission_module = 'employee_performance'

    @catch_validation
    def post(self, request):
        session_id = request.data.get('session_id')
        session = Session.objects.filter(id=session_id).select_related('user').first()
        if not session or not is_field_employee(session.user):
            return err('Session not found.', status=404)
        new_ended_at = parse_ist_datetime(request.data.get('ended_at'))
        session_service.edit_session_end_time(session, new_ended_at, request.data.get('note'))
        return ok(None, 'End time updated.')


def _resolve_range(request):
    preset = request.query_params.get('range')
    today = ist_today()
    if preset == 'this_week':
        start = today - datetime.timedelta(days=today.weekday())
        return start, today
    if preset == 'last_week':
        this_week_start = today - datetime.timedelta(days=today.weekday())
        start = this_week_start - datetime.timedelta(days=7)
        return start, this_week_start - datetime.timedelta(days=1)
    if preset == 'this_month':
        return today.replace(day=1), today
    if preset == 'last_month':
        first_of_this_month = today.replace(day=1)
        last_month_end = first_of_this_month - datetime.timedelta(days=1)
        return last_month_end.replace(day=1), last_month_end
    start = parse_iso_date(request.query_params.get('start'), default=today - datetime.timedelta(days=29))
    end = parse_iso_date(request.query_params.get('end'), default=today)
    if start > end:
        raise ValidationError('start must be on or before end.')
    return start, end


class PeriodReportAdminAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'employee_performance'

    @catch_validation
    def get(self, request):
        start, end = _resolve_range(request)
        user_id = request.query_params.get('user_id')
        if user_id == 'all':
            report = build_period_report_for_users(_employees_qs(), start, end)
        else:
            employee = resolve_employee(user_id)
            if not employee:
                return err('Employee not found.', status=404)
            report = build_period_report(employee, start, end)
        return ok(report)


class PeriodReportCSVAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'employee_performance'

    @catch_validation
    def get(self, request):
        start, end = _resolve_range(request)
        user_id = request.query_params.get('user_id')

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="performance_{start}_{end}.csv"'
        writer = csv.writer(response)

        if user_id == 'all':
            report = build_period_report_for_users(_employees_qs(), start, end)
            writer.writerow(['Date', 'Status counts', 'Employees with session', 'Packs', 'Orders', 'Collection', 'Visits', 'New leads', 'Retargeted leads'])
            for row in report['days']:
                writer.writerow([
                    row['date'], row['status_counts'], row['employees_with_session'], row['packs'],
                    row['orders'], row['collection'], row['visits_count'], row['new_leads'], row['retargeted_leads'],
                ])
        else:
            employee = resolve_employee(user_id)
            if not employee:
                return err('Employee not found.', status=404)
            report = build_period_report(employee, start, end)
            writer.writerow(['Date', 'Status', 'Start', 'Reached home', 'Active min', 'Break min', 'Packs', 'Orders', 'Collection', 'Visits', 'New leads', 'Retargeted leads'])
            for row in report['days']:
                sales = row['sales_session']
                writer.writerow([
                    row['date'], row['day_status'],
                    sales['started_at'].isoformat() if sales else '',
                    row['reached_home_time'].isoformat() if row['reached_home_recorded'] else '',
                    sales['active_minutes'] if sales else '',
                    sales['break_minutes'] if sales else '',
                    row['packs'], row['orders'], row['collection'], row['visits_count'],
                    row['new_leads'], row['retargeted_leads'],
                ])
        return response


class OverallPerformanceAdminAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'employee_performance'

    @catch_validation
    def get(self, request):
        start, end = _resolve_range(request)
        employees = list(_employees_qs())
        return ok({
            'start_date': start,
            'end_date': end,
            'ranking': build_ranking(employees, start, end),
            'trend': weekly_trend(employees, start, end),
            'conversion': conversion_summary(employees, start, end),
        })
