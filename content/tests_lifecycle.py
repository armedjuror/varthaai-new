"""
Tests for the derived 5-stage content lifecycle (content/lifecycle.py),
the ContentCalendarAPI mark_generated/mark_approved/mark_published actions
(content/views.py), and the plan-level Regenerate (content/agents/
planner.py's regenerate_plan + content/views.py's RegeneratePlanAPI).

All data is disposable (created in setUp, deleted in tearDown). No live
`claude -p` calls — regenerate_plan's underlying generate_plan call is
mocked the same way content/tests_planner.py mocks run_claude_cli.
"""
import json
from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import AdminUser
from core.api import BRAND_SESSION_KEY
from core.models import Brand

from content import lifecycle
from content.agents import planner
from content.models import ContentPlan, PlanItem, PosterAsset, Script

_BRAND_KIT = "Identity: Varthaai is a premium Kerala banana chips brand."


def _mk_brand(name='Lifecycle Test Brand'):
    return Brand.objects.create(name=name, order_prefix='LFC', is_active=True, brand_kit=_BRAND_KIT)


def _mk_plan(brand, start=None, end=None, status=ContentPlan.Status.NEEDS_REVIEW):
    today = timezone.localdate()
    start = start or today - timedelta(days=5)
    end = end or today + timedelta(days=25)
    return ContentPlan.objects.create(brand=brand, period_start=start, period_end=end, status=status)


def _mk_item(plan, content_type='poster_occasion', status=PlanItem.Status.APPROVED,
             planned_date=None, working_title='Item', context_notes='Context.'):
    return PlanItem.objects.create(
        plan=plan, content_type=content_type, planned_date=planned_date or timezone.localdate() + timedelta(days=10),
        working_title=working_title, context_notes=context_notes, status=status,
    )


class LifecycleStageTests(TestCase):
    """Pure computation, no CLI/API involved — content/lifecycle.py."""

    def setUp(self):
        self.brand = _mk_brand()
        self.plan = _mk_plan(self.brand)

    def tearDown(self):
        self.brand.delete()

    def test_not_gate_approved_is_none(self):
        item = _mk_item(self.plan, status=PlanItem.Status.PLANNED)
        self.assertIsNone(lifecycle.lifecycle_stage(item, False, False, False, False))

    def test_skipped_is_none(self):
        item = _mk_item(self.plan, status=PlanItem.Status.SKIPPED)
        self.assertIsNone(lifecycle.lifecycle_stage(item, False, False, False, False))

    def test_posted_is_published_regardless_of_other_signals(self):
        item = _mk_item(self.plan, status=PlanItem.Status.POSTED)
        self.assertEqual(lifecycle.lifecycle_stage(item, False, False, False, False), 'published')

    def test_gate_approved_no_artifacts_is_planned(self):
        item = _mk_item(self.plan, status=PlanItem.Status.APPROVED)
        self.assertEqual(lifecycle.lifecycle_stage(item, False, False, False, False), 'planned')

    def test_poster_progression(self):
        item = _mk_item(self.plan, content_type='poster_learn', status=PlanItem.Status.APPROVED)
        self.assertEqual(lifecycle.lifecycle_stage(item, True, False, False, False), 'scripted')
        self.assertEqual(lifecycle.lifecycle_stage(item, True, True, True, False), 'generated')
        self.assertEqual(lifecycle.lifecycle_stage(item, True, True, True, True), 'approved')

    def test_blog_scripted_is_generated_no_separate_step(self):
        item = _mk_item(self.plan, content_type='blog', status=PlanItem.Status.APPROVED)
        self.assertEqual(lifecycle.lifecycle_stage(item, True, False, False, False), 'scripted')
        self.assertEqual(lifecycle.lifecycle_stage(item, True, True, False, False), 'approved')

    def test_reel_progression_uses_manual_milestones_not_script_approval(self):
        """The bug an earlier draft of this design had: a reel's script is
        normally approved BEFORE filming, so has_approved_script must NOT
        by itself advance the stage past 'scripted' — only the manual
        milestones (manually_generated_at/manually_approved_at) do."""
        item = _mk_item(self.plan, content_type='reel_varthaanm', status=PlanItem.Status.APPROVED)
        # Script drafted and even approved, but not yet filmed — still 'scripted'.
        self.assertEqual(lifecycle.lifecycle_stage(item, True, True, False, False), 'scripted')

        item.manually_generated_at = timezone.now()
        self.assertEqual(lifecycle.lifecycle_stage(item, True, True, False, False), 'generated')

        item.manually_approved_at = timezone.now()
        self.assertEqual(lifecycle.lifecycle_stage(item, True, True, False, False), 'approved')


class MarkMilestoneAPITests(TestCase):
    """ContentCalendarAPI's mark_generated/mark_approved/mark_published actions."""

    def setUp(self):
        self.brand = _mk_brand()
        self.plan = _mk_plan(self.brand)
        self.admin = AdminUser.objects.create_user(
            username='lfc_test_admin', password='x',
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

    def _post(self, **data):
        return self.client.post('/admin/api/content/calendar/', data, content_type='application/json')

    def test_mark_generated_rejected_for_non_reel(self):
        item = _mk_item(self.plan, content_type='poster_learn')
        res = self._post(action='mark_generated', item_id=item.id)
        self.assertFalse(res.json()['success'])

    def test_mark_generated_and_undo_toggles(self):
        item = _mk_item(self.plan, content_type='reel_inside')
        res = self._post(action='mark_generated', item_id=item.id)
        self.assertTrue(res.json()['success'])
        item.refresh_from_db()
        self.assertIsNotNone(item.manually_generated_at)
        self.assertEqual(item.manually_generated_by, self.admin)

        res2 = self._post(action='mark_generated', item_id=item.id)
        self.assertTrue(res2.json()['success'])
        item.refresh_from_db()
        self.assertIsNone(item.manually_generated_at)
        self.assertIsNone(item.manually_generated_by)

    def test_mark_approved_requires_generated_first(self):
        item = _mk_item(self.plan, content_type='reel_verdict')
        res = self._post(action='mark_approved', item_id=item.id)
        self.assertFalse(res.json()['success'])

    def test_mark_approved_after_generated(self):
        item = _mk_item(self.plan, content_type='reel_varthaanm')
        self._post(action='mark_generated', item_id=item.id)
        res = self._post(action='mark_approved', item_id=item.id)
        self.assertTrue(res.json()['success'])
        item.refresh_from_db()
        self.assertIsNotNone(item.manually_approved_at)

    def test_mark_published_requires_approved_stage(self):
        item = _mk_item(self.plan, content_type='poster_learn')
        res = self._post(action='mark_published', item_id=item.id)
        self.assertFalse(res.json()['success'])

    def test_mark_published_and_undo(self):
        item = _mk_item(self.plan, content_type='poster_learn')
        Script.objects.create(plan_item=item, skill_used='x', status=Script.Status.APPROVED)
        PosterAsset.objects.create(plan_item=item, status=PosterAsset.Status.APPROVED)

        res = self._post(action='mark_published', item_id=item.id)
        self.assertTrue(res.json()['success'])
        item.refresh_from_db()
        self.assertEqual(item.status, PlanItem.Status.POSTED)
        self.assertIsNotNone(item.published_at)

        res2 = self._post(action='mark_published', item_id=item.id)
        self.assertTrue(res2.json()['success'])
        item.refresh_from_db()
        self.assertEqual(item.status, PlanItem.Status.APPROVED)
        self.assertIsNone(item.published_at)

    def test_toggle_approve_rejected_once_posted(self):
        item = _mk_item(self.plan, content_type='poster_learn', status=PlanItem.Status.POSTED)
        res = self._post(action='toggle_item_approve', item_id=item.id)
        self.assertFalse(res.json()['success'])


class RegeneratePlanTests(TestCase):
    """planner.regenerate_plan — deletes untouched (PLANNED, no script/
    poster) items and re-runs generate_plan; everything else must survive
    completely untouched."""

    def setUp(self):
        self.brand = _mk_brand()
        self.plan = _mk_plan(self.brand)

    def tearDown(self):
        self.brand.delete()

    def _mock_result(self, items):
        return {'is_error': False, 'text': json.dumps(items)}

    def test_untouched_planned_item_is_deleted_and_replaced(self):
        stale = _mk_item(
            self.plan, content_type='poster_occasion', status=PlanItem.Status.PLANNED,
            planned_date=timezone.localdate() + timedelta(days=8), working_title='Stale idea',
        )
        with mock.patch.object(planner, 'run_claude_cli') as mock_cli:
            mock_cli.return_value = self._mock_result([{
                'planned_date': (timezone.localdate() + timedelta(days=8)).isoformat(),
                'series_slug': None, 'content_type': 'poster_occasion',
                'working_title': 'Fresh idea', 'context_notes': 'A concrete new angle.',
            }])
            result = planner.regenerate_plan(self.plan)

        self.assertEqual(result['deleted'], 1)
        self.assertEqual(result['created'], 1)
        self.assertFalse(PlanItem.objects.filter(id=stale.id).exists())
        fresh = PlanItem.objects.get(plan=self.plan)
        self.assertEqual(fresh.working_title, 'Fresh idea')

    def test_approved_item_never_touched(self):
        approved = _mk_item(self.plan, status=PlanItem.Status.APPROVED, working_title='Keep me')
        with mock.patch.object(planner, 'run_claude_cli') as mock_cli:
            mock_cli.return_value = self._mock_result([])
            planner.regenerate_plan(self.plan)
        approved.refresh_from_db()
        self.assertEqual(approved.working_title, 'Keep me')
        self.assertEqual(approved.status, PlanItem.Status.APPROVED)

    def test_skipped_item_never_touched(self):
        skipped = _mk_item(self.plan, status=PlanItem.Status.SKIPPED, working_title='Keep me too')
        with mock.patch.object(planner, 'run_claude_cli') as mock_cli:
            mock_cli.return_value = self._mock_result([])
            planner.regenerate_plan(self.plan)
        skipped.refresh_from_db()
        self.assertEqual(skipped.working_title, 'Keep me too')

    def test_planned_item_with_script_never_touched(self):
        """Regression for the exact bug the advisor flagged: only items
        with literally nothing produced yet are eligible for replacement."""
        drafted = _mk_item(self.plan, status=PlanItem.Status.PLANNED, working_title='Has a script')
        Script.objects.create(plan_item=drafted, skill_used='x')
        with mock.patch.object(planner, 'run_claude_cli') as mock_cli:
            mock_cli.return_value = self._mock_result([])
            result = planner.regenerate_plan(self.plan)
        self.assertEqual(result['deleted'], 0)
        self.assertTrue(PlanItem.objects.filter(id=drafted.id).exists())

    def test_instruction_reaches_the_prompt(self):
        with mock.patch.object(planner, 'run_claude_cli') as mock_cli:
            mock_cli.return_value = self._mock_result([])
            planner.regenerate_plan(self.plan, instruction='Add an extra blog post about Onam.')
        prompt_arg = mock_cli.call_args[0][0]
        self.assertIn('Add an extra blog post about Onam.', prompt_arg)


class RegeneratePlanAPITests(TestCase):
    def setUp(self):
        self.brand = _mk_brand()
        self.plan = _mk_plan(self.brand)
        self.admin = AdminUser.objects.create_user(
            username='regen_test_admin', password='x',
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

    def test_rejects_when_already_generating(self):
        self.plan.status = ContentPlan.Status.GENERATING
        self.plan.save(update_fields=['status'])
        res = self.client.post(
            '/admin/api/content/planner/regenerate/',
            {'plan_id': self.plan.id}, content_type='application/json')
        self.assertFalse(res.json()['success'])

    def test_dispatches_and_sets_generating(self):
        with mock.patch('content.tasks.run_planner_regenerate_task.delay') as mock_delay:
            res = self.client.post(
                '/admin/api/content/planner/regenerate/',
                {'plan_id': self.plan.id, 'instruction': 'add something'}, content_type='application/json')
        self.assertTrue(res.json()['success'])
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, ContentPlan.Status.GENERATING)
        mock_delay.assert_called_once_with(self.plan.id, instruction='add something')
