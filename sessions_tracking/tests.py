import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import AdminUser
from core.api import BRAND_SESSION_KEY
from core.models import Brand
from crm.models import B2BCompany
from orders.models import B2BOrder, B2BOrderItem, B2BPayment
from products.models import Flavor
from sessions_tracking.models import (
    Leave, PackingItem, Session, SessionEvent, TelegramReportLog, UserSetting, Visit, WeeklyOffChange,
)
from sessions_tracking.services import leave as leave_service
from sessions_tracking.services import sessions as session_service
from sessions_tracking.services import visits as visit_service
from sessions_tracking.services.autoclose import autoclose_open_sessions
from sessions_tracking.services.reporting import build_daily_report, build_period_report
from sessions_tracking.services.telegram import send_daily_report
from sessions_tracking.timeutil import IST, cutoff_at, next_monday_strictly_after


def ist_dt(y, m, d, h=10, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=IST)


def login_with_brand(client, username, password, brand_id):
    """client.login() bypasses accounts.views.login_view, so the active-brand
    session key (set by establish_admin_session) never gets written — admin
    (non-super) endpoints gated by HasModulePermission need it. Set it
    directly, mirroring what a real login does."""
    client.login(username=username, password=password)
    session = client.session
    session[BRAND_SESSION_KEY] = brand_id
    session.save()


class BaseFixtures(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(name='Varthaai Test')
        self.super_admin = AdminUser.objects.create_user(
            username='root', password='pw123456', name='Root', role=AdminUser.Role.SUPER_ADMIN,
        )
        self.admin_with_perm = AdminUser.objects.create_user(
            username='admin1', password='pw123456', name='Admin One', role=AdminUser.Role.ADMIN,
            brand_permissions={str(self.brand.id): ['employee_performance']},
        )
        self.admin_without_perm = AdminUser.objects.create_user(
            username='admin2', password='pw123456', name='Admin Two', role=AdminUser.Role.ADMIN,
            brand_permissions={str(self.brand.id): ['orders']},
        )
        # Field-employee is a permission ('field_employee'), not a role — an
        # ordinary staff/admin account gets granted it, exactly like any
        # other module. Mirrors the real Saad: an `admin`/`staff` account
        # that also does field work.
        self.employee1 = AdminUser.objects.create_user(
            username='saad', password='pw123456', name='Saad', role=AdminUser.Role.STAFF,
            brand_permissions={str(self.brand.id): ['field_employee']},
        )
        self.employee2 = AdminUser.objects.create_user(
            username='priya', password='pw123456', name='Priya', role=AdminUser.Role.STAFF,
            brand_permissions={str(self.brand.id): ['field_employee']},
        )
        self.flavor = Flavor.objects.create(name='Classic Banana Chips')
        self.flavor2 = Flavor.objects.create(name='Spicy Banana Chips')
        self.company = B2BCompany.objects.create(brand=self.brand, company_name='ABC Mart', stage=B2BCompany.Stage.LEAD)


# ─────────────────────────── Session state machine ───────────────────────────

class SessionStateMachineTests(BaseFixtures):
    @patch('sessions_tracking.services.sessions.ist_now')
    def test_start_creates_active_session(self, mock_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        session = session_service.start_session(self.employee1, Session.Type.SALES)
        self.assertEqual(session.status, Session.Status.ACTIVE)
        self.assertEqual(SessionEvent.objects.filter(session=session, event=SessionEvent.Event.START).count(), 1)

    @patch('sessions_tracking.services.sessions.ist_now')
    def test_cannot_start_second_session_of_any_type(self, mock_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        session_service.start_session(self.employee1, Session.Type.SALES)
        with self.assertRaisesMessage(ValidationError, 'End your sales session first.'):
            session_service.start_session(self.employee1, Session.Type.PACKING)

    @patch('sessions_tracking.services.sessions.ist_now')
    def test_cannot_start_after_cutoff(self, mock_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 19, 45)
        with self.assertRaisesMessage(ValidationError, 'cannot be started after 7:30 PM IST'):
            session_service.start_session(self.employee1, Session.Type.SALES)

    @patch('sessions_tracking.services.sessions.ist_now')
    def test_break_then_resume_then_end(self, mock_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        session = session_service.start_session(self.employee1, Session.Type.SALES)

        mock_now.return_value = ist_dt(2026, 6, 1, 13, 0)
        session_service.break_session(self.employee1, 'lunch_break')
        session.refresh_from_db()
        self.assertEqual(session.status, Session.Status.ON_BREAK)

        mock_now.return_value = ist_dt(2026, 6, 1, 13, 30)
        session_service.resume_session(self.employee1)
        session.refresh_from_db()
        self.assertEqual(session.status, Session.Status.ACTIVE)

        mock_now.return_value = ist_dt(2026, 6, 1, 18, 0)
        ended = session_service.end_session(self.employee1)
        self.assertEqual(ended.status, Session.Status.ENDED)
        self.assertEqual(ended.ended_at, ist_dt(2026, 6, 1, 18, 0))

    @patch('sessions_tracking.services.sessions.ist_now')
    def test_resume_without_break_rejected(self, mock_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        session_service.start_session(self.employee1, Session.Type.SALES)
        with self.assertRaisesMessage(ValidationError, 'not on break'):
            session_service.resume_session(self.employee1)

    @patch('sessions_tracking.services.sessions.ist_now')
    def test_break_twice_rejected(self, mock_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        session_service.start_session(self.employee1, Session.Type.SALES)
        session_service.break_session(self.employee1, 'lunch_break')
        with self.assertRaisesMessage(ValidationError, 'not active'):
            session_service.break_session(self.employee1, 'lunch_break')

    @patch('sessions_tracking.services.sessions.ist_now')
    def test_end_while_on_break_inserts_resume_event(self, mock_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        session_service.start_session(self.employee1, Session.Type.SALES)
        mock_now.return_value = ist_dt(2026, 6, 1, 13, 0)
        session_service.break_session(self.employee1, 'lunch_break')
        mock_now.return_value = ist_dt(2026, 6, 1, 18, 0)
        session = session_service.end_session(self.employee1)
        events = list(session.events.values_list('event', flat=True))
        self.assertEqual(events, ['start', 'break', 'resume', 'end'])

    @patch('sessions_tracking.services.sessions.ist_now')
    def test_end_packing_requires_items(self, mock_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        session_service.start_session(self.employee1, Session.Type.PACKING)
        with self.assertRaisesMessage(ValidationError, 'at least one flavour'):
            session_service.end_session(self.employee1, packing_items=[])

    @patch('sessions_tracking.services.sessions.ist_now')
    def test_end_packing_rejects_zero_packs(self, mock_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        session_service.start_session(self.employee1, Session.Type.PACKING)
        with self.assertRaisesMessage(ValidationError, 'greater than zero'):
            session_service.end_session(self.employee1, packing_items=[{'flavor_id': self.flavor.id, 'packs': 0}])

    @patch('sessions_tracking.services.sessions.ist_now')
    def test_end_packing_rejects_duplicate_flavor(self, mock_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        session_service.start_session(self.employee1, Session.Type.PACKING)
        items = [{'flavor_id': self.flavor.id, 'packs': 5}, {'flavor_id': self.flavor.id, 'packs': 3}]
        with self.assertRaisesMessage(ValidationError, 'only appear once'):
            session_service.end_session(self.employee1, packing_items=items)

    @patch('sessions_tracking.services.sessions.ist_now')
    def test_end_packing_rejects_unknown_flavor(self, mock_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        session_service.start_session(self.employee1, Session.Type.PACKING)
        with self.assertRaisesMessage(ValidationError, 'Unknown flavour'):
            session_service.end_session(self.employee1, packing_items=[{'flavor_id': 999999, 'packs': 5}])

    @patch('sessions_tracking.services.sessions.ist_now')
    def test_end_packing_creates_items(self, mock_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        session_service.start_session(self.employee1, Session.Type.PACKING)
        mock_now.return_value = ist_dt(2026, 6, 1, 17, 0)
        items = [{'flavor_id': self.flavor.id, 'packs': 10}, {'flavor_id': self.flavor2.id, 'packs': 5}]
        session = session_service.end_session(self.employee1, packing_items=items)
        self.assertEqual(PackingItem.objects.filter(session=session).count(), 2)
        self.assertEqual(sum(PackingItem.objects.filter(session=session).values_list('packs', flat=True)), 15)


# ───────────────────────────────── Break-time math ─────────────────────────────────

class BreakTimeCalculationTests(BaseFixtures):
    @patch('sessions_tracking.services.sessions.ist_now')
    def test_break_and_active_minutes(self, mock_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        session_service.start_session(self.employee1, Session.Type.SALES)
        mock_now.return_value = ist_dt(2026, 6, 1, 13, 0)
        session_service.break_session(self.employee1, 'lunch_break')
        mock_now.return_value = ist_dt(2026, 6, 1, 13, 30)
        session_service.resume_session(self.employee1)
        mock_now.return_value = ist_dt(2026, 6, 1, 16, 0)
        session_service.break_session(self.employee1, 'evening_break')
        mock_now.return_value = ist_dt(2026, 6, 1, 16, 15)
        session_service.resume_session(self.employee1)
        mock_now.return_value = ist_dt(2026, 6, 1, 18, 0)
        session_service.end_session(self.employee1)

        report = build_daily_report(self.employee1, date(2026, 6, 1))
        sales = report['sales_session']
        self.assertEqual(sales['total_minutes'], 540)  # 9:00 -> 18:00
        self.assertEqual(sales['break_minutes'], 45)    # 30 + 15
        self.assertEqual(sales['break_minutes_lunch'], 30)
        self.assertEqual(sales['break_minutes_evening'], 15)
        self.assertEqual(sales['active_minutes'], 495)


# ───────────────────────────────── Auto-close ─────────────────────────────────

class AutoCloseTests(BaseFixtures):
    @patch('sessions_tracking.services.sessions.ist_now')
    def test_autoclose_active_session(self, mock_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        session = session_service.start_session(self.employee1, Session.Type.SALES)

        closed = autoclose_open_sessions(date(2026, 6, 1))
        self.assertEqual(closed, [session.id])
        session.refresh_from_db()
        self.assertTrue(session.auto_closed)
        self.assertEqual(session.ended_at, cutoff_at(date(2026, 6, 1)))
        self.assertEqual(list(session.events.values_list('event', flat=True)), ['start', 'end'])

    @patch('sessions_tracking.services.sessions.ist_now')
    def test_autoclose_closes_open_break_first(self, mock_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        session = session_service.start_session(self.employee1, Session.Type.PACKING)
        mock_now.return_value = ist_dt(2026, 6, 1, 15, 0)
        session_service.break_session(self.employee1, 'evening_break')

        autoclose_open_sessions(date(2026, 6, 1))
        session.refresh_from_db()
        self.assertEqual(list(session.events.values_list('event', flat=True)), ['start', 'break', 'resume', 'end'])
        self.assertTrue(session.auto_closed)

    @patch('sessions_tracking.services.sessions.ist_now')
    def test_reached_home_not_recorded_when_auto_closed(self, mock_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        session_service.start_session(self.employee1, Session.Type.SALES)
        autoclose_open_sessions(date(2026, 6, 1))

        report = build_daily_report(self.employee1, date(2026, 6, 1))
        self.assertFalse(report['reached_home_recorded'])
        self.assertIsNone(report['reached_home_time'])
        self.assertTrue(report['flags']['auto_closed_sales'])

    @patch('sessions_tracking.services.sessions.ist_now')
    def test_autoclosed_packing_with_no_items_is_flagged(self, mock_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        session_service.start_session(self.employee1, Session.Type.PACKING)
        autoclose_open_sessions(date(2026, 6, 1))

        report = build_daily_report(self.employee1, date(2026, 6, 1))
        self.assertEqual(report['packing_session']['total_packs'], 0)
        self.assertTrue(report['flags']['packing_no_items'])


# ───────────────────────────────── Leave / weekly off ─────────────────────────────────

class LeaveTests(BaseFixtures):
    def test_add_leave_and_balance(self):
        leave_service.add_leave(self.employee1, date(2026, 1, 10))
        balance = leave_service.leave_balance(self.employee1, 2026)
        self.assertEqual(balance['used'], 1)
        self.assertEqual(balance['remaining'], 14)

    def test_duplicate_leave_rejected(self):
        leave_service.add_leave(self.employee1, date(2026, 1, 10))
        with self.assertRaisesMessage(ValidationError, 'already recorded'):
            leave_service.add_leave(self.employee1, date(2026, 1, 10))

    def test_leave_allowance_enforced(self):
        for day in range(1, 16):
            leave_service.add_leave(self.employee1, date(2026, 1, day))
        with self.assertRaisesMessage(ValidationError, 'exhausted'):
            leave_service.add_leave(self.employee1, date(2026, 1, 16))

    def test_remove_leave(self):
        leave_service.add_leave(self.employee1, date(2026, 1, 10))
        leave_service.remove_leave(self.employee1, date(2026, 1, 10))
        self.assertEqual(Leave.objects.filter(user=self.employee1).count(), 0)

    def test_day_status_leave(self):
        leave_service.add_leave(self.employee1, date(2026, 1, 10))
        self.assertEqual(leave_service.day_status(self.employee1, date(2026, 1, 10)), 'leave')


class WeeklyOffTests(BaseFixtures):
    def test_next_monday_strictly_after_mid_week(self):
        # Wednesday 2026-06-03 -> next Monday is 2026-06-08
        self.assertEqual(next_monday_strictly_after(date(2026, 6, 3)), date(2026, 6, 8))

    def test_next_monday_strictly_after_monday_rolls_to_next_week(self):
        # Monday itself can't be the effective date — rolls a full week.
        self.assertEqual(next_monday_strictly_after(date(2026, 6, 8)), date(2026, 6, 15))

    def test_set_weekly_off_effective_from_next_monday(self):
        change = leave_service.set_weekly_off(self.employee1, 2, date(2026, 6, 3))  # Wed -> Wednesday off
        self.assertEqual(change.effective_from, date(2026, 6, 8))

    def test_effective_weekday_before_and_after_change(self):
        UserSetting.objects.create(user=self.employee1, weekly_off_weekday=6)  # default Sunday
        leave_service.set_weekly_off(self.employee1, 2, date(2026, 6, 3))  # effective 2026-06-08
        self.assertEqual(leave_service.effective_weekly_off_weekday(self.employee1, date(2026, 6, 5)), 6)
        self.assertEqual(leave_service.effective_weekly_off_weekday(self.employee1, date(2026, 6, 8)), 2)


# ───────────────────────────────── Reporting ─────────────────────────────────

class ReportingTests(BaseFixtures):
    def _deliver_order(self, user, delivered_at, packs=4, amount=Decimal('500')):
        order = B2BOrder.objects.create(
            id=f'VO_{delivered_at.isoformat()}', brand=self.brand, company=self.company,
            status=B2BOrder.Status.DELIVERED, created_by=user, delivered_at=delivered_at,
            total_amount=amount, paid_amount=amount,
        )
        B2BOrderItem.objects.create(
            b2b_order=order, flavor=self.flavor, quantity=packs, weight_grams=150,
            total_weight_grams=150 * packs, selling_price=Decimal('100'), flavor_name=self.flavor.name,
        )
        return order

    def test_orders_packs_collection_attributed_by_created_by(self):
        self._deliver_order(self.employee1, ist_dt(2026, 6, 1, 14, 0), packs=6)
        self._deliver_order(self.employee2, ist_dt(2026, 6, 1, 15, 0), packs=9)  # other employee — must not count
        B2BPayment.objects.create(
            company=self.company, amount=Decimal('250'), payment_method='cash',
            payment_date=date(2026, 6, 1), created_by=self.employee1,
        )
        report = build_daily_report(self.employee1, date(2026, 6, 1))
        self.assertEqual(report['orders'], 1)
        self.assertEqual(report['packs'], 6)
        self.assertEqual(report['collection'], Decimal('250'))
        self.assertEqual(report['first_delivery_time'], ist_dt(2026, 6, 1, 14, 0))

    @patch('sessions_tracking.services.visits.ist_now')
    @patch('sessions_tracking.services.sessions.ist_now')
    def test_new_leads_and_last_meeting(self, mock_now, mock_visit_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        session_service.start_session(self.employee1, Session.Type.SALES)
        mock_visit_now.return_value = ist_dt(2026, 6, 1, 11, 0)
        visit_service.create_visit(self.employee1, {
            'company_id': self.company.id, 'purpose': 'new_lead', 'outcome': 'none', 'notes': '',
        })
        mock_visit_now.return_value = ist_dt(2026, 6, 1, 16, 0)
        other = B2BCompany.objects.create(brand=self.brand, company_name='Later Visit Co')
        visit_service.create_visit(self.employee1, {
            'company_id': other.id, 'purpose': 'audit', 'outcome': 'none', 'notes': '',
        })

        report = build_daily_report(self.employee1, date(2026, 6, 1))
        self.assertEqual(report['new_leads'], 1)
        self.assertEqual(report['last_meeting_time'], ist_dt(2026, 6, 1, 16, 0))
        self.assertEqual(report['visits_count'], 2)

    @patch('sessions_tracking.services.visits.ist_now')
    @patch('sessions_tracking.services.sessions.ist_now')
    def test_retargeted_excludes_existing_customer_and_same_day_company(self, mock_now, mock_visit_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        session_service.start_session(self.employee1, Session.Type.SALES)

        # Company created on an earlier day, no order -> counts as retargeted.
        old_lead = B2BCompany.objects.create(brand=self.brand, company_name='Old Lead')
        B2BCompany.objects.filter(id=old_lead.id).update(created_at=ist_dt(2026, 5, 1, 9, 0))

        # Company created today -> must NOT count.
        today_lead = B2BCompany.objects.create(brand=self.brand, company_name='Today Lead')

        # self.company already has a delivered order (existing customer) -> must NOT count.
        self._existing_customer_order()

        mock_visit_now.return_value = ist_dt(2026, 6, 1, 11, 0)
        visit_service.create_visit(self.employee1, {'company_id': old_lead.id, 'purpose': 'retarget', 'outcome': 'none', 'notes': ''})
        mock_visit_now.return_value = ist_dt(2026, 6, 1, 12, 0)
        visit_service.create_visit(self.employee1, {'company_id': today_lead.id, 'purpose': 'retarget', 'outcome': 'none', 'notes': ''})
        mock_visit_now.return_value = ist_dt(2026, 6, 1, 13, 0)
        visit_service.create_visit(self.employee1, {'company_id': self.company.id, 'purpose': 'retarget', 'outcome': 'none', 'notes': ''})
        mock_visit_now.return_value = ist_dt(2026, 6, 1, 14, 0)
        visit_service.create_visit(self.employee1, {'company_id': self.company.id, 'purpose': 'reorder_push', 'outcome': 'none', 'notes': ''})

        report = build_daily_report(self.employee1, date(2026, 6, 1))
        self.assertEqual(report['retargeted_leads'], 1)

    def _existing_customer_order(self):
        order = B2BOrder.objects.create(
            id='VO_existing', brand=self.brand, company=self.company,
            status=B2BOrder.Status.DELIVERED, created_by=self.employee2,
            delivered_at=ist_dt(2026, 5, 15, 10, 0), total_amount=100, paid_amount=100,
        )
        return order


class PeriodReportTests(BaseFixtures):
    @patch('sessions_tracking.services.sessions.ist_now')
    def test_averages_exclude_off_leave_and_no_session_days(self, mock_now):
        # Day 1: worked (active 480 min). Day 2: leave. Day 3: working status but no session.
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        session_service.start_session(self.employee1, Session.Type.SALES)
        mock_now.return_value = ist_dt(2026, 6, 1, 17, 0)
        session_service.end_session(self.employee1)

        leave_service.add_leave(self.employee1, date(2026, 6, 2))
        # 2026-06-03 (Wednesday): no session, no leave, not the default weekly off (Sunday) -> no_session_day.

        report = build_period_report(self.employee1, date(2026, 6, 1), date(2026, 6, 3))
        self.assertEqual(report['counts']['days_worked'], 1)
        self.assertEqual(report['counts']['leave_days'], 1)
        self.assertEqual(report['counts']['no_session_days'], 1)
        # Average must divide by the 1 day actually worked, not by 3.
        self.assertEqual(report['averages']['active_minutes_per_day'], 480)


# ───────────────────────────────── Telegram report ─────────────────────────────────

@override_settings(TELEGRAM_BOT_TOKEN='test-token', TELEGRAM_GROUP_CHAT_ID='12345')
class TelegramReportTests(BaseFixtures):
    @patch('sessions_tracking.services.telegram.requests.post')
    def test_send_is_idempotent(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None
        status1, _ = send_daily_report(date(2026, 6, 1))
        self.assertEqual(status1, TelegramReportLog.Status.SENT)
        self.assertEqual(mock_post.call_count, 1)

        status2, detail2 = send_daily_report(date(2026, 6, 1))
        self.assertEqual(status2, TelegramReportLog.Status.SKIPPED)
        self.assertEqual(mock_post.call_count, 1)  # not called again
        self.assertEqual(TelegramReportLog.objects.filter(report_date=date(2026, 6, 1)).count(), 1)

    @patch('sessions_tracking.services.telegram.requests.post')
    def test_force_resend_calls_telegram_again(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None
        send_daily_report(date(2026, 6, 1))
        send_daily_report(date(2026, 6, 1), force=True)
        self.assertEqual(mock_post.call_count, 2)

    @patch('sessions_tracking.services.telegram.requests.post')
    def test_dry_run_never_sends_or_records(self, mock_post):
        status, text = send_daily_report(date(2026, 6, 1), dry_run=True)
        self.assertEqual(status, TelegramReportLog.Status.SKIPPED)
        mock_post.assert_not_called()
        self.assertEqual(TelegramReportLog.objects.count(), 0)


@override_settings(TELEGRAM_BOT_TOKEN='', TELEGRAM_GROUP_CHAT_ID='')
class TelegramMissingConfigTests(BaseFixtures):
    @patch('sessions_tracking.services.telegram.requests.post')
    def test_missing_config_fails_loudly_without_raising(self, mock_post):
        status, _ = send_daily_report(date(2026, 6, 1))
        self.assertEqual(status, TelegramReportLog.Status.FAILED)
        mock_post.assert_not_called()


class SendDailyReportCommandTests(BaseFixtures):
    def test_dry_run_management_command(self):
        call_command('send_daily_report', '--date', '2026-06-01', '--dry-run')
        self.assertEqual(TelegramReportLog.objects.count(), 0)


# ───────────────────────────────── delivered_at hook ─────────────────────────────────

class DeliveredAtHookTests(BaseFixtures):
    def setUp(self):
        super().setUp()
        login_with_brand(self.client, 'root', 'pw123456', self.brand.id)
        self.order = B2BOrder.objects.create(
            id='VO_hook1', brand=self.brand, company=self.company,
            status=B2BOrder.Status.CONFIRMED, created_by=self.employee1, stock_deducted=True,
        )

    def test_delivered_at_set_on_status_change(self):
        resp = self.client.post(
            reverse('orders:b2b_orders_api'),
            data=json.dumps({'action': 'update_status', 'order_id': self.order.id, 'status': 'delivered'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.delivered_at)

    def test_delivered_at_not_overwritten_on_repeat(self):
        self.order.status = B2BOrder.Status.DELIVERED
        self.order.delivered_at = ist_dt(2026, 1, 1, 10, 0)
        self.order.save()
        self.client.post(
            reverse('orders:b2b_orders_api'),
            data=json.dumps({'action': 'update_status', 'order_id': self.order.id, 'status': 'cancelled'}),
            content_type='application/json',
        )
        self.client.post(
            reverse('orders:b2b_orders_api'),
            data=json.dumps({'action': 'update_status', 'order_id': self.order.id, 'status': 'delivered'}),
            content_type='application/json',
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.delivered_at, ist_dt(2026, 1, 1, 10, 0))


# ───────────────────────────────── Isolation / permissions ─────────────────────────────────

class EmployeeIsolationTests(BaseFixtures):
    def setUp(self):
        super().setUp()
        login_with_brand(self.client, 'saad', 'pw123456', self.brand.id)

    def test_employee_login_redirects_to_my_day(self):
        # saad has no 'dashboard' permission, only 'field_employee' — a
        # fresh login (not the already-authenticated branch) must land him
        # on My Day, not 403 on the dashboard.
        self.client.logout()
        resp = self.client.post(reverse('accounts:login'), {'username': 'saad', 'password': 'pw123456'})
        self.assertRedirects(resp, reverse('sessions_tracking:my_day'))

    def test_my_day_page_loads_for_employee(self):
        resp = self.client.get(reverse('sessions_tracking:my_day'))
        self.assertEqual(resp.status_code, 200)

    def test_employee_cannot_reach_admin_dashboard_page(self):
        resp = self.client.get(reverse('sessions_tracking:performance'))
        self.assertEqual(resp.status_code, 403)

    def test_employee_cannot_reach_admin_dashboard_api(self):
        resp = self.client.get(reverse('sessions_tracking:performance_employees_api'))
        self.assertIn(resp.status_code, (403, 404))

    def test_employee_cannot_reach_unrelated_admin_module(self):
        resp = self.client.get(reverse('finance:expenses'))
        self.assertEqual(resp.status_code, 403)

    @patch('sessions_tracking.services.sessions.ist_now')
    def test_employee_cannot_edit_another_employees_session(self, mock_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        other_session = session_service.start_session(self.employee2, Session.Type.SALES)
        mock_now.return_value = ist_dt(2026, 6, 1, 17, 0)
        session_service.end_session(self.employee2)

        resp = self.client.post(
            reverse('sessions_tracking:session_edit_end_time_api'),
            data=json.dumps({'session_id': other_session.id, 'ended_at': '2026-06-01T18:00', 'note': 'hack'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 404)
        other_session.refresh_from_db()
        self.assertFalse(other_session.end_edited)

    @patch('sessions_tracking.services.sessions.ist_now')
    def test_employee_report_endpoint_only_returns_own_data(self, mock_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        session_service.start_session(self.employee2, Session.Type.SALES)  # other employee's session

        resp = self.client.get(reverse('sessions_tracking:session_state_api'))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()['data']['open_session'])  # saad has no open session of his own


class DualCapabilityTests(BaseFixtures):
    """field_employee is a permission, not a role — the same AdminUser can
    be a normal dashboard admin AND use My Day (the real Saad)."""

    def setUp(self):
        super().setUp()
        self.dual = AdminUser.objects.create_user(
            username='dual', password='pw123456', name='Dual Role', role=AdminUser.Role.ADMIN,
            brand_permissions={str(self.brand.id): ['dashboard', 'field_employee']},
        )

    def test_login_lands_on_dashboard_when_dashboard_permission_present(self):
        resp = self.client.post(reverse('accounts:login'), {'username': 'dual', 'password': 'pw123456'})
        self.assertRedirects(resp, reverse('core:dashboard'))

    def test_can_still_reach_my_day(self):
        login_with_brand(self.client, 'dual', 'pw123456', self.brand.id)
        resp = self.client.get(reverse('sessions_tracking:my_day'))
        self.assertEqual(resp.status_code, 200)

    @patch('sessions_tracking.services.sessions.ist_now')
    def test_can_use_own_session_endpoints(self, mock_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        login_with_brand(self.client, 'dual', 'pw123456', self.brand.id)
        resp = self.client.post(
            reverse('sessions_tracking:session_start_api'),
            data=json.dumps({'type': 'sales'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])


class AdminDashboardPermissionTests(BaseFixtures):
    def test_admin_with_module_permission_gets_200(self):
        login_with_brand(self.client, 'admin1', 'pw123456', self.brand.id)
        resp = self.client.get(reverse('sessions_tracking:performance_employees_api'))
        self.assertEqual(resp.status_code, 200)

    def test_admin_without_module_permission_gets_403(self):
        login_with_brand(self.client, 'admin2', 'pw123456', self.brand.id)
        resp = self.client.get(reverse('sessions_tracking:performance_employees_api'))
        self.assertEqual(resp.status_code, 403)

    def test_super_admin_bypasses_module_permission(self):
        login_with_brand(self.client, 'root', 'pw123456', self.brand.id)
        resp = self.client.get(reverse('sessions_tracking:performance_employees_api'))
        self.assertEqual(resp.status_code, 200)

    @patch('sessions_tracking.services.sessions.ist_now')
    def test_admin_can_view_daily_report_for_any_employee(self, mock_now):
        mock_now.return_value = ist_dt(2026, 6, 1, 9, 0)
        session_service.start_session(self.employee1, Session.Type.SALES)
        mock_now.return_value = ist_dt(2026, 6, 1, 17, 0)
        session_service.end_session(self.employee1)

        login_with_brand(self.client, 'admin1', 'pw123456', self.brand.id)
        resp = self.client.get(
            reverse('sessions_tracking:performance_daily_api'),
            {'user_id': self.employee1.id, 'date': '2026-06-01'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['data']['user_id'], self.employee1.id)

    def test_csv_export(self):
        login_with_brand(self.client, 'admin1', 'pw123456', self.brand.id)
        resp = self.client.get(
            reverse('sessions_tracking:performance_period_csv_api'),
            {'user_id': self.employee1.id, 'range': 'this_week'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'text/csv')

    def test_period_report_for_employee_with_no_sessions(self):
        login_with_brand(self.client, 'admin1', 'pw123456', self.brand.id)
        resp = self.client.get(
            reverse('sessions_tracking:performance_period_api'),
            {'user_id': self.employee2.id, 'range': 'this_week'},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['data']
        self.assertEqual(data['counts']['days_worked'], 0)
        for key, value in data['averages'].items():
            self.assertIsNone(value)

    def test_dashboard_pages_render(self):
        login_with_brand(self.client, 'admin1', 'pw123456', self.brand.id)
        for name in ('performance', 'performance_period', 'performance_overall'):
            resp = self.client.get(reverse(f'sessions_tracking:{name}'))
            self.assertEqual(resp.status_code, 200, f'{name} page did not render')
