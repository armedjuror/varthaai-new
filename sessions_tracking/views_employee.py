"""
Employee-facing API + the "My Performance" self-service page.

Field-employee access is a per-brand module permission ('field_employee'),
not a separate AdminUser role — any super_admin/admin/staff account can be
granted it (see core/permissions.py), so the same person can be an admin
and a field employee at once (e.g. Saad). Gated the same way as every other
admin screen: HasModulePermission + permission_module.

Every endpoint here acts only on `request.user` — none accepts another
user's id — which rules out IDOR for this half of the API by construction
(the one exception, SessionEditEndTimeAPI, still re-filters by
`user=request.user` so a foreign session id 404s rather than trusting a
client-supplied owner).

Session start/break/resume/end is driven from the persistent topbar widget
(static/js/admin/session-widget.js, included on every admin page) rather
than a dedicated page — see admin/base.html. "My Performance" is this
app's only page here: the employee's own daily/period report, session
logs, and leave/week-off self-service (the admin-wide equivalent,
Employee Performance, lives in views_admin.py).
"""
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView

from core.api import HasModulePermission, err, ok
from core.auth import require_module
from products.models import Flavor
from sessions_tracking.models import Leave, Session
from sessions_tracking.services import leave as leave_service
from sessions_tracking.services import sessions as session_service
from sessions_tracking.services.reporting import build_daily_report, build_period_report
from sessions_tracking.timeutil import ist_today
from sessions_tracking.views_common import (
    catch_validation, parse_iso_date, parse_ist_datetime, resolve_date_range,
)


@require_module('field_employee')
@ensure_csrf_cookie
def my_performance_page(request):
    return render(request, 'admin/sessions/my_performance.html')


def _serialize_open_session(session):
    if not session:
        return None
    return {
        'id': session.id, 'type': session.type, 'status': session.status,
        'area': session.area, 'started_at': session.started_at,
    }


class SessionStateAPI(APIView):
    """Current open session (if any) + today's numbers — drives which of
    Start/Break/Resume/End the UI shows."""
    permission_classes = [HasModulePermission]
    permission_module = 'field_employee'

    def get(self, request):
        session = session_service.get_open_session(request.user)
        return ok({
            'open_session': _serialize_open_session(session),
            'today': build_daily_report(request.user, ist_today()),
        })


class SessionStartAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'field_employee'

    @catch_validation
    def post(self, request):
        session = session_service.start_session(
            request.user, request.data.get('type'), area=request.data.get('area'),
        )
        return ok(
            {'id': session.id, 'type': session.type, 'status': session.status, 'area': session.area},
            'Session started.',
        )


class SessionBreakAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'field_employee'

    @catch_validation
    def post(self, request):
        session_service.break_session(request.user, request.data.get('reason'))
        return ok(None, 'On break.')


class SessionResumeAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'field_employee'

    @catch_validation
    def post(self, request):
        session_service.resume_session(request.user)
        return ok(None, 'Resumed.')


class SessionEndAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'field_employee'

    @catch_validation
    def post(self, request):
        items = request.data.get('items')
        session = session_service.end_session(request.user, packing_items=items)
        return ok({'id': session.id, 'status': session.status}, 'Session ended.')


class SessionEditEndTimeAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'field_employee'

    @catch_validation
    def post(self, request):
        session = Session.objects.filter(id=request.data.get('session_id'), user=request.user).first()
        if not session:
            return err('Session not found.', status=404)
        new_ended_at = parse_ist_datetime(request.data.get('ended_at'))
        session_service.edit_session_end_time(session, new_ended_at, request.data.get('note'))
        return ok(None, 'End time updated.')


class DailyReportAPI(APIView):
    """The employee's own numbers for a given date (default: today)."""
    permission_classes = [HasModulePermission]
    permission_module = 'field_employee'

    @catch_validation
    def get(self, request):
        day = parse_iso_date(request.query_params.get('date'), default=ist_today())
        return ok(build_daily_report(request.user, day))


class FlavorPickerAPI(APIView):
    """Minimal flavour list (id + name only) for the packing end screen."""
    permission_classes = [HasModulePermission]
    permission_module = 'field_employee'

    def get(self, request):
        return ok(list(Flavor.objects.filter(is_active=True).order_by('name').values('id', 'name')))


class PeriodReportAPI(APIView):
    """The employee's own period totals/averages — same metrics as the admin
    Employee Performance period view, scoped to request.user only."""
    permission_classes = [HasModulePermission]
    permission_module = 'field_employee'

    @catch_validation
    def get(self, request):
        start, end = resolve_date_range(request)
        return ok(build_period_report(request.user, start, end))


class WeeklyOffAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'field_employee'

    def get(self, request):
        today = ist_today()
        return ok({
            'effective_weekday': leave_service.effective_weekly_off_weekday(request.user, today),
            'default_weekday': leave_service.get_or_create_settings(request.user).weekly_off_weekday,
        })

    @catch_validation
    def post(self, request):
        change = leave_service.set_weekly_off(request.user, request.data.get('weekday'), ist_today())
        return ok(
            {'new_weekday': change.new_weekday, 'effective_from': change.effective_from},
            'Weekly off will change from Monday, ' + change.effective_from.isoformat(),
        )


class LeaveAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'field_employee'

    def get(self, request):
        year = int(request.query_params.get('year') or ist_today().year)
        days = list(
            Leave.objects.filter(user=request.user, date__year=year).order_by('date').values('date', 'note'),
        )
        return ok({'balance': leave_service.leave_balance(request.user, year), 'days': days})

    @catch_validation
    def post(self, request):
        day = parse_iso_date(request.data.get('date'))
        leave = leave_service.add_leave(request.user, day, request.data.get('note'))
        return ok({'date': leave.date}, 'Leave recorded.')

    @catch_validation
    def delete(self, request):
        day = parse_iso_date(request.query_params.get('date'))
        leave_service.remove_leave(request.user, day)
        return ok(None, 'Leave removed.')


