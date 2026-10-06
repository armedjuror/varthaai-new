"""
Employee-facing API + the mobile "My Day" page.

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
"""
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView

from core.api import HasModulePermission, current_brand_id, err, ok
from core.auth import require_module
from products.models import Flavor
from sessions_tracking.models import Leave, Session
from sessions_tracking.services import leave as leave_service
from sessions_tracking.services import orders as order_service
from sessions_tracking.services import sessions as session_service
from sessions_tracking.services import visits as visit_service
from sessions_tracking.services.reporting import build_daily_report
from sessions_tracking.timeutil import ist_today
from sessions_tracking.views_common import catch_validation, parse_iso_date, parse_ist_datetime


@require_module('field_employee')
@ensure_csrf_cookie
def my_day_page(request):
    return render(request, 'admin/sessions/my_day.html')


def _serialize_open_session(session):
    if not session:
        return None
    return {
        'id': session.id, 'type': session.type, 'status': session.status,
        'started_at': session.started_at,
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
        session = session_service.start_session(request.user, request.data.get('type'))
        return ok({'id': session.id, 'type': session.type, 'status': session.status}, 'Session started.')


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


class CompanyPickerAPI(APIView):
    """Minimal company fields for the visit-logging picker — never a full
    B2B record (see sessions_tracking.services.visits.company_picker_results)."""
    permission_classes = [HasModulePermission]
    permission_module = 'field_employee'

    def get(self, request):
        return ok(visit_service.company_picker_results(request.query_params.get('q', '')))


class VisitCreateAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'field_employee'

    @catch_validation
    def post(self, request):
        visit = visit_service.create_visit(request.user, request.data)
        return ok({'id': visit.id, 'company_id': visit.company_id}, 'Visit logged.')


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


def _order_row(o):
    """Balance is always total_amount - paid_amount, never the stored
    balance_amount column (project-wide rule, see orders/views_b2b.py)."""
    return {
        'id': o.id,
        'company_id': o.company_id,
        'company_name': o.company.company_name,
        'status': o.status,
        'payment_status': o.payment_status,
        'total_amount': float(o.total_amount),
        'paid_amount': float(o.paid_amount),
        'balance': float(o.total_amount - o.paid_amount),
        'order_date': o.order_date,
        'due_date': o.due_date,
    }


class MyOrdersAPI(APIView):
    """The employee's own B2B orders — reuses orders.views_b2b.B2BOrdersAPI's
    status-update/payment handlers rather than re-implementing stock
    deduction or income sync (see sessions_tracking.services.orders).
    Requires 'b2b' module access, same as the desktop B2B Orders screen —
    'field_employee' alone only grants session tracking, not order data."""
    permission_classes = [HasModulePermission]
    permission_module = 'b2b'

    def get(self, request):
        brand_id = current_brand_id(request)
        status = request.query_params.get('status', 'open')
        orders = order_service.my_orders(request.user, brand_id, status=status)
        return ok([_order_row(o) for o in orders])


class MyOrderUpdateStatusAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'b2b'

    @catch_validation
    def post(self, request):
        brand_id = current_brand_id(request)
        order_service.update_order_status(
            request, brand_id, request.data.get('order_id'), request.data.get('status'),
        )
        return ok(None, 'Order status updated.')


class MyOrderPaymentAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'b2b'

    @catch_validation
    def post(self, request):
        brand_id = current_brand_id(request)
        order_service.record_order_payment(request, brand_id, request.data.get('order_id'), request.data)
        return ok(None, 'Payment recorded.')
