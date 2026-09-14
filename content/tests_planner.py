"""
Tests for Phase 4's "propose changes to an already-approved plan" — the
Planner's run_daily_nudge/propose_change_for_item (content/agents/planner.py)
and ContentCalendarAPI's accept_proposal/reject_proposal (content/views.py).
New file — content/tests.py (empty) and content/tests_copywriter.py/
tests_designer.py are untouched.

All data is disposable (created in setUp, deleted in tearDown) — never
touches the real Varthaai brand or its real plans. No live `claude -p` calls
here: propose_change_for_item's CLI round trip is mocked (content/
tests_copywriter.py already exercises a real live drafting call end-to-end;
this file focuses on the gating/dispatch/API logic Phase 4 actually added).
"""
import json
from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import AdminUser
from core.api import BRAND_SESSION_KEY
from core.models import Brand

from content.agents import planner
from content.models import ActionItem, ContentPlan, ContentSeries, PlanItem, Script, TrendFlag

_BRAND_KIT = "Identity: Varthaai is a premium Kerala banana chips brand."


def _mk_brand(name='Nudge Test Brand'):
    return Brand.objects.create(name=name, order_prefix='NDG', is_active=True, brand_kit=_BRAND_KIT)


def _mk_plan(brand, status=ContentPlan.Status.APPROVED, start=None, end=None):
    today = timezone.localdate()
    start = start or today - timedelta(days=5)
    end = end or today + timedelta(days=25)
    return ContentPlan.objects.create(brand=brand, period_start=start, period_end=end, status=status)


def _mk_item(plan, planned_date=None, content_type='poster_occasion', status=PlanItem.Status.APPROVED,
             working_title='Original title', context_notes='Original context.'):
    return PlanItem.objects.create(
        plan=plan, content_type=content_type, planned_date=planned_date or timezone.localdate() + timedelta(days=10),
        working_title=working_title, context_notes=context_notes, status=status,
    )


class ProposeChangeForItemTests(TestCase):
    """propose_change_for_item — CLI call mocked, exercises the parse/
    has_change branching only (the prompt-building itself is covered
    implicitly by these calls actually running)."""

    def setUp(self):
        self.brand = _mk_brand()
        self.plan = _mk_plan(self.brand)
        self.item = _mk_item(self.plan)

    def tearDown(self):
        self.brand.delete()

    def _mock_result(self, text):
        return {'is_error': False, 'text': text}

    def test_has_change_true_returns_proposal(self):
        with mock.patch.object(planner, 'run_claude_cli') as mock_cli:
            mock_cli.return_value = self._mock_result(json.dumps({
                'has_change': True, 'working_title': 'New angle', 'context_notes': 'A fresh, specific brief.',
            }))
            result = planner.propose_change_for_item(self.item, ['Onam is trending this week.'])
        self.assertEqual(result, {'working_title': 'New angle', 'context_notes': 'A fresh, specific brief.'})

    def test_has_change_false_returns_none(self):
        with mock.patch.object(planner, 'run_claude_cli') as mock_cli:
            mock_cli.return_value = self._mock_result(json.dumps({
                'has_change': False, 'working_title': self.item.working_title, 'context_notes': self.item.context_notes,
            }))
            result = planner.propose_change_for_item(self.item, ['Some unrelated trend.'])
        self.assertIsNone(result)

    def test_empty_working_title_raises(self):
        with mock.patch.object(planner, 'run_claude_cli') as mock_cli:
            mock_cli.return_value = self._mock_result(json.dumps({
                'has_change': True, 'working_title': '', 'context_notes': 'x',
            }))
            with self.assertRaises(RuntimeError):
                planner.propose_change_for_item(self.item, ['trend'])

    def test_unparseable_json_raises(self):
        with mock.patch.object(planner, 'run_claude_cli') as mock_cli:
            mock_cli.return_value = self._mock_result('not json at all')
            with self.assertRaises(RuntimeError):
                planner.propose_change_for_item(self.item, ['trend'])

    def test_cli_error_result_raises(self):
        with mock.patch.object(planner, 'run_claude_cli') as mock_cli:
            mock_cli.return_value = {'is_error': True, 'subtype': 'error', 'text': 'boom'}
            with self.assertRaises(RuntimeError):
                planner.propose_change_for_item(self.item, ['trend'])


class RunDailyNudgeTests(TestCase):
    """The gating logic: which items are even considered, and the
    open-TrendFlags short-circuit."""

    def setUp(self):
        self.brand = _mk_brand()

    def tearDown(self):
        self.brand.delete()

    def _flag(self, text='A new trend worth reacting to.'):
        return TrendFlag.objects.create(source_text=text, considered=False)

    def _mock_change(self, title='Proposed title', notes='Proposed notes.'):
        return mock.patch.object(
            planner, 'propose_change_for_item',
            return_value={'working_title': title, 'context_notes': notes},
        )

    def test_no_open_trend_flags_is_a_pure_noop(self):
        plan = _mk_plan(self.brand)
        _mk_item(plan)
        with mock.patch.object(planner, 'propose_change_for_item') as mock_propose:
            summary = planner.run_daily_nudge(brand=self.brand)
        mock_propose.assert_not_called()
        self.assertEqual(summary, {'proposed': 0, 'unchanged': 0, 'failed': 0})

    def test_open_trend_flags_but_no_eligible_items_leaves_flags_unconsidered(self):
        flag = self._flag()
        # Item not gate-approved (still PLANNED) -> no eligible items at all
        # (item-wise-only gate, §26/§27 — plan.status is never checked).
        plan = _mk_plan(self.brand)
        _mk_item(plan, status=PlanItem.Status.PLANNED)
        with mock.patch.object(planner, 'propose_change_for_item') as mock_propose:
            summary = planner.run_daily_nudge(brand=self.brand)
        mock_propose.assert_not_called()
        flag.refresh_from_db()
        self.assertFalse(flag.considered)
        self.assertEqual(summary, {'proposed': 0, 'unchanged': 0, 'failed': 0})

    def test_eligible_item_gets_proposal_written_and_action_item_filed(self):
        self._flag()
        plan = _mk_plan(self.brand)
        item = _mk_item(plan)
        with self._mock_change():
            summary = planner.run_daily_nudge(brand=self.brand)
        item.refresh_from_db()
        self.assertEqual(summary['proposed'], 1)
        self.assertEqual(item.proposed_changes, {'working_title': 'Proposed title', 'context_notes': 'Proposed notes.'})
        self.assertTrue(ActionItem.objects.filter(
            plan_item=item, kind=ActionItem.Kind.ITEM_CHANGE_PROPOSED, status=ActionItem.Status.OPEN).exists())

    def test_trend_flags_marked_considered_after_a_real_attempt(self):
        flag = self._flag()
        plan = _mk_plan(self.brand)
        item = _mk_item(plan)
        with mock.patch.object(planner, 'propose_change_for_item', return_value=None):
            planner.run_daily_nudge(brand=self.brand)
        flag.refresh_from_db()
        self.assertTrue(flag.considered)

    def test_unchanged_recommendation_leaves_item_untouched(self):
        self._flag()
        plan = _mk_plan(self.brand)
        item = _mk_item(plan)
        with mock.patch.object(planner, 'propose_change_for_item', return_value=None):
            summary = planner.run_daily_nudge(brand=self.brand)
        item.refresh_from_db()
        self.assertEqual(summary['unchanged'], 1)
        self.assertEqual(item.proposed_changes, {})
        self.assertFalse(ActionItem.objects.filter(plan_item=item, kind=ActionItem.Kind.ITEM_CHANGE_PROPOSED).exists())

    def test_failed_proposal_is_caught_and_counted(self):
        self._flag()
        plan = _mk_plan(self.brand)
        _mk_item(plan)
        with mock.patch.object(planner, 'propose_change_for_item', side_effect=RuntimeError('boom')):
            summary = planner.run_daily_nudge(brand=self.brand)
        self.assertEqual(summary['failed'], 1)

    def test_reel_verdict_excluded(self):
        self._flag()
        plan = _mk_plan(self.brand)
        _mk_item(plan, content_type='reel_verdict')
        with self._mock_change():
            summary = planner.run_daily_nudge(brand=self.brand)
        self.assertEqual(summary, {'proposed': 0, 'unchanged': 0, 'failed': 0})

    def test_past_due_item_excluded(self):
        self._flag()
        plan = _mk_plan(self.brand)
        _mk_item(plan, planned_date=timezone.localdate() - timedelta(days=1))
        with self._mock_change():
            summary = planner.run_daily_nudge(brand=self.brand)
        self.assertEqual(summary, {'proposed': 0, 'unchanged': 0, 'failed': 0})

    def test_item_with_existing_script_excluded(self):
        self._flag()
        plan = _mk_plan(self.brand)
        item = _mk_item(plan)
        Script.objects.create(plan_item=item, skill_used='x', raw_output='y', status=Script.Status.NEEDS_REVIEW)
        with self._mock_change():
            summary = planner.run_daily_nudge(brand=self.brand)
        self.assertEqual(summary, {'proposed': 0, 'unchanged': 0, 'failed': 0})

    def test_item_with_pending_proposal_excluded(self):
        self._flag()
        plan = _mk_plan(self.brand)
        item = _mk_item(plan)
        item.proposed_changes = {'working_title': 'Already proposed'}
        item.save(update_fields=['proposed_changes'])
        with self._mock_change():
            summary = planner.run_daily_nudge(brand=self.brand)
        self.assertEqual(summary, {'proposed': 0, 'unchanged': 0, 'failed': 0})

    def test_brand_scoping(self):
        self._flag()
        other_brand = _mk_brand(name='Other Nudge Brand')
        try:
            other_plan = _mk_plan(other_brand)
            _mk_item(other_plan)
            plan = _mk_plan(self.brand)
            item = _mk_item(plan)
            with self._mock_change():
                summary = planner.run_daily_nudge(brand=self.brand)
            item.refresh_from_db()
            self.assertEqual(summary['proposed'], 1)
        finally:
            other_brand.delete()


class AcceptRejectProposalAPITests(TestCase):
    """content/views.py's ContentCalendarAPI accept_proposal/reject_proposal
    — proposals only ever arise on an APPROVED plan's item (run_daily_nudge
    only considers those), so these actions are exercised against that
    plan status here."""

    def setUp(self):
        self.brand = _mk_brand()
        self.admin = AdminUser.objects.create_user(
            username='ndg_test_admin', password='x',
            role=AdminUser.Role.SUPER_ADMIN, is_staff=True, is_superuser=True,
        )
        self.client = Client()
        self.client.force_login(self.admin)
        session = self.client.session
        session[BRAND_SESSION_KEY] = self.brand.id
        session.save()

    def tearDown(self):
        self.admin.delete()
        self.brand.delete()

    def _mk_proposed_item(self):
        plan = _mk_plan(self.brand, status=ContentPlan.Status.APPROVED)
        item = _mk_item(plan, working_title='Old title', context_notes='Old notes.')
        item.proposed_changes = {'working_title': 'New title', 'context_notes': 'New notes.'}
        item.save(update_fields=['proposed_changes'])
        ActionItem.objects.create(
            plan_item=item, kind=ActionItem.Kind.ITEM_CHANGE_PROPOSED, status=ActionItem.Status.OPEN,
            title='Proposed change: Old title',
        )
        return item

    def test_accept_applies_fields_and_resolves_action_item(self):
        item = self._mk_proposed_item()
        res = self.client.post(
            '/admin/api/content/calendar/',
            {'action': 'accept_proposal', 'item_id': item.id}, content_type='application/json')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()['success'])
        item.refresh_from_db()
        self.assertEqual(item.working_title, 'New title')
        self.assertEqual(item.context_notes, 'New notes.')
        self.assertEqual(item.proposed_changes, {})
        action_item = ActionItem.objects.get(plan_item=item, kind=ActionItem.Kind.ITEM_CHANGE_PROPOSED)
        self.assertEqual(action_item.status, ActionItem.Status.DONE)
        self.assertEqual(action_item.resolved_by, self.admin)

    def test_reject_clears_proposal_and_dismisses_action_item_without_changing_fields(self):
        item = self._mk_proposed_item()
        res = self.client.post(
            '/admin/api/content/calendar/',
            {'action': 'reject_proposal', 'item_id': item.id}, content_type='application/json')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()['success'])
        item.refresh_from_db()
        self.assertEqual(item.working_title, 'Old title')
        self.assertEqual(item.context_notes, 'Old notes.')
        self.assertEqual(item.proposed_changes, {})
        action_item = ActionItem.objects.get(plan_item=item, kind=ActionItem.Kind.ITEM_CHANGE_PROPOSED)
        self.assertEqual(action_item.status, ActionItem.Status.DISMISSED)

    def test_accept_with_no_pending_proposal_errors(self):
        plan = _mk_plan(self.brand, status=ContentPlan.Status.APPROVED)
        item = _mk_item(plan)
        res = self.client.post(
            '/admin/api/content/calendar/',
            {'action': 'accept_proposal', 'item_id': item.id}, content_type='application/json')
        self.assertFalse(res.json()['success'])

    def test_accept_proposal_works_regardless_of_plan_status(self):
        """Approval is item-wise only now — there's no plan-level gate left
        to be an exception to, so accept_proposal just needs to keep working
        on an item whose parent plan happens to be APPROVED."""
        item = self._mk_proposed_item()
        self.assertEqual(item.plan.status, ContentPlan.Status.APPROVED)
        res = self.client.post(
            '/admin/api/content/calendar/',
            {'action': 'accept_proposal', 'item_id': item.id}, content_type='application/json')
        self.assertTrue(res.json()['success'])
