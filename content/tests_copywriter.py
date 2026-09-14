"""
Tests for the Copywriter Agent (content/agents/copywriter.py) and its Script
review API/page (content/views_scripts.py). New file — content/tests.py is
untouched (Django's default test discovery picks up any test*.py module).

Two tiers:
  - Pure DB-logic tests (gate/readiness/status-transition) — fast, no
    external calls, always run.
  - Live `claude -p` round-trip tests — gated on CLAUDE_AVAILABLE (skipped if
    the `claude` CLI isn't on PATH) since they make real subprocess calls
    through core.claude_cli. All data these create (Brand/ContentPlan/
    PlanItem/Script/AdminUser) is disposable — created in setUp, deleted in
    tearDown — never touches the real Varthaai brand or its real plans.

ScriptsAPI tests use `@override_settings(ROOT_URLCONF='content.urlconf_test_scripts')`
rather than touching Varthaai/urls.py (explicitly off-limits for this phase —
owned by a concurrent sibling agent's integration pass) — that test-only
urlconf just includes content/urls_scripts.py's routes.
"""
import shutil
import unittest
from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import AdminUser
from core.api import BRAND_SESSION_KEY
from core.models import Brand

from content.agents import copywriter
from content.models import ActionItem, ContentPlan, ContentSeries, PlanItem, Script

CLAUDE_AVAILABLE = shutil.which('claude') is not None

_BRAND_KIT = (
    "Identity: Varthaai is a premium Kerala banana chips brand, founded by "
    "Ajwad (CEO) and Saad (COO).\n"
    "Audience & voice: warm, personal, first-person, not corporate. Two "
    "registers for posters — warm/heritage for recurring festivals, "
    "meme/trend-jacking for live pop-culture moments.\n"
    "Blog voice: a little more explanatory/SEO-aware than reel copy, still "
    "not corporate; open from a concrete image, not an abstraction.\n"
    "Confidentiality: Varthaai Verdict's reviewed competitor names are "
    "internal-only, never public."
)


def _mk_brand(name='Copywriter Test Brand'):
    """A throwaway Brand per test — never the real Varthaai row — so no live
    call here can ever touch real data. ContentPlan.brand is CASCADE, so
    deleting the brand in tearDown cleans up every Plan/Item/Script it owns."""
    return Brand.objects.create(name=name, order_prefix='CWT', is_active=True, brand_kit=_BRAND_KIT)


def _mk_plan(brand, status=ContentPlan.Status.APPROVED, start=None, end=None):
    today = timezone.localdate()
    start = start or today - timedelta(days=10)
    end = end or today + timedelta(days=20)
    return ContentPlan.objects.create(brand=brand, period_start=start, period_end=end, status=status)


class DuePlanItemsGateTests(TestCase):
    """The core admin-approval gate (module docstring, §26): item APPROVED
    alone — plan.status is NOT checked (item-wise approval, not plan-wise) —
    and never an item with an already-approved Script. No claude CLI calls
    in this class."""

    def setUp(self):
        self.brand = _mk_brand()
        self.series = ContentSeries.objects.get(slug='varthaanm')  # seeded, prep_lead_days=5

    def tearDown(self):
        self.brand.delete()

    def _mk_item(self, plan, planned_date, content_type='reel_varthaanm', series=None,
                 status=PlanItem.Status.APPROVED, context_notes='A concrete, usable brief with real specifics.'):
        return PlanItem.objects.create(
            plan=plan, series=series, content_type=content_type, planned_date=planned_date,
            working_title='Test item', context_notes=context_notes, status=status,
        )

    def test_item_due_when_plan_and_item_approved_and_deadline_passed(self):
        plan = _mk_plan(self.brand, status=ContentPlan.Status.APPROVED)
        today = timezone.localdate()
        # prep_lead_days=5 -> deadline = planned_date - 5 = today - 2 <= today.
        item = self._mk_item(plan, planned_date=today + timedelta(days=3), series=self.series)
        due_ids = list(copywriter.due_plan_items(brand=self.brand).values_list('id', flat=True))
        self.assertIn(item.id, due_ids)

    def test_item_not_due_before_deadline(self):
        plan = _mk_plan(self.brand)
        today = timezone.localdate()
        item = self._mk_item(plan, planned_date=today + timedelta(days=20), series=self.series)
        due_ids = list(copywriter.due_plan_items(brand=self.brand).values_list('id', flat=True))
        self.assertNotIn(item.id, due_ids)

    def test_item_due_even_when_plan_not_approved(self):
        """Reversed decision, §26: "we don't need plan-wise approval, we need
        item-wise approval" — an individually-toggled-approved item inside a
        still-NEEDS_REVIEW plan MUST be drafted; the item's own approval is a
        complete, standalone decision, not provisional on the rest of the
        plan being reviewed."""
        plan = _mk_plan(self.brand, status=ContentPlan.Status.NEEDS_REVIEW)
        today = timezone.localdate()
        item = self._mk_item(plan, planned_date=today, series=self.series, status=PlanItem.Status.APPROVED)
        due_ids = list(copywriter.due_plan_items(brand=self.brand).values_list('id', flat=True))
        self.assertIn(item.id, due_ids)

    def test_reel_verdict_included_once_approved_and_due(self):
        """Phase 5: reel_verdict is no longer hard-excluded from
        due_plan_items — it goes through the exact same plan/item-approval
        and deadline gate as every other content_type; VerdictIntake
        completeness is a separate _is_ready() check, not a due_plan_items
        exclusion (see content/tests_verdict.py for that branch)."""
        plan = _mk_plan(self.brand)
        today = timezone.localdate()
        item = self._mk_item(plan, planned_date=today, content_type='reel_verdict')
        due_ids = list(copywriter.due_plan_items(brand=self.brand).values_list('id', flat=True))
        self.assertIn(item.id, due_ids)

    def test_item_with_approved_script_excluded(self):
        """Stops an approved-script item from being redrafted every day —
        the item's own status may still read APPROVED (post-script, per
        ScriptsAPI.approve), but an APPROVED Script already exists for it."""
        plan = _mk_plan(self.brand)
        today = timezone.localdate()
        item = self._mk_item(plan, planned_date=today, series=self.series)
        Script.objects.create(plan_item=item, skill_used='x', status=Script.Status.APPROVED, version=1)
        due_ids = list(copywriter.due_plan_items(brand=self.brand).values_list('id', flat=True))
        self.assertNotIn(item.id, due_ids)

    def test_fallback_lead_days_used_when_no_series_configured(self):
        """poster_occasion here has series=None -> DEFAULT_PREP_LEAD_DAYS (3)."""
        plan = _mk_plan(self.brand)
        today = timezone.localdate()
        item = self._mk_item(
            plan, planned_date=today + timedelta(days=2), content_type='poster_occasion',
            series=None, context_notes='Onam poster idea',
        )
        due_ids = list(copywriter.due_plan_items(brand=self.brand).values_list('id', flat=True))
        self.assertIn(item.id, due_ids)

    def test_other_brand_not_included(self):
        other_brand = _mk_brand(name='Other Brand')
        try:
            plan = _mk_plan(other_brand)
            today = timezone.localdate()
            item = self._mk_item(plan, planned_date=today, series=self.series)
            due_ids = list(copywriter.due_plan_items(brand=self.brand).values_list('id', flat=True))
            self.assertNotIn(item.id, due_ids)
        finally:
            other_brand.delete()


class ReadinessCheckTests(TestCase):
    """content-generator-plan.md §13's per-content_type input-readiness
    check — no DB writes, no CLI calls."""

    def test_blank_context_notes_not_ready_for_varthaanm(self):
        item = PlanItem(content_type='reel_varthaanm', context_notes='', working_title='x')
        ready, reason = copywriter._is_ready(item)
        self.assertFalse(ready)
        self.assertTrue(reason)

    def test_short_context_notes_not_ready_for_inside(self):
        item = PlanItem(content_type='reel_inside', context_notes='hi', working_title='x')
        ready, _ = copywriter._is_ready(item)
        self.assertFalse(ready)

    def test_concrete_context_notes_ready_for_learn(self):
        item = PlanItem(
            content_type='poster_learn', working_title='x',
            context_notes='Teach the Kannada word "ರುಚಿ" (ruchi) meaning taste.',
        )
        ready, _ = copywriter._is_ready(item)
        self.assertTrue(ready)

    def test_poster_occasion_never_blocks(self):
        item = PlanItem(content_type='poster_occasion', context_notes='', working_title='Onam')
        ready, _ = copywriter._is_ready(item)
        self.assertTrue(ready)

    def test_blog_never_blocks(self):
        item = PlanItem(content_type='blog', context_notes='', working_title='Why banana chips')
        ready, _ = copywriter._is_ready(item)
        self.assertTrue(ready)

    def test_poster_event_without_date_not_ready(self):
        item = PlanItem(
            content_type='poster_event', working_title='x',
            context_notes='Come visit our stall, it will be great fun for everyone.',
        )
        ready, reason = copywriter._is_ready(item)
        self.assertFalse(ready)
        self.assertTrue(reason)

    def test_poster_event_with_date_ready(self):
        item = PlanItem(
            content_type='poster_event', working_title='x',
            context_notes='Pop-up at Indiranagar on 20 Sept, 5-8pm, RSVP via the link in bio.',
        )
        ready, _ = copywriter._is_ready(item)
        self.assertTrue(ready)


class ParsingHelperTests(TestCase):
    """Deterministic, no-CLI tests for the free-form-output parsers §6's
    recap continuity and the generic-content structured parse depend on —
    the live tests exercise these too, but only non-deterministically (they'd
    pass even if these returned nothing); these lock the actual behavior."""

    def test_extract_recap_line_found(self):
        text = (
            "VARTHAAI VARTHAANM — EPISODE 2\n[~140 words]\n\nSome VO line.\n\n"
            "RECAP_FOR_NEXT_EPISODE: We rejected a batch and fixed the recipe."
        )
        self.assertEqual(
            copywriter._extract_recap_line(text),
            'We rejected a batch and fixed the recipe.',
        )

    def test_extract_recap_line_missing(self):
        self.assertEqual(copywriter._extract_recap_line('No recap line here at all.'), '')

    def test_parse_generic_structured_with_header(self):
        text = 'CONTENT_TYPE: poster_occasion\nREGISTER: warm/heritage\n\nHappy Onam!\nEnjoy the harvest.'
        data = copywriter._parse_generic_structured(text)
        self.assertEqual(data['content_type'], 'poster_occasion')
        self.assertEqual(data['register'], 'warm/heritage')
        self.assertEqual(data['body'], 'Happy Onam!\nEnjoy the harvest.')

    def test_parse_generic_structured_without_header_returns_empty(self):
        self.assertEqual(copywriter._parse_generic_structured('Just some prose with no header.'), {})

    def test_latest_varthaanm_recap_reads_most_recent_approved_only(self):
        brand = _mk_brand(name='Recap Read Test Brand')
        try:
            plan = _mk_plan(brand)
            series = ContentSeries.objects.get(slug='varthaanm')
            today = timezone.localdate()

            older = PlanItem.objects.create(
                plan=plan, series=series, content_type='reel_varthaanm',
                planned_date=today - timedelta(days=14), working_title='Old ep',
                context_notes='x' * 20, status=PlanItem.Status.APPROVED,
            )
            Script.objects.create(
                plan_item=older, skill_used='varthaai-varthaanm-script',
                status=Script.Status.APPROVED, version=1, recap_summary='Stale recap — should not win.',
            )

            newer = PlanItem.objects.create(
                plan=plan, series=series, content_type='reel_varthaanm',
                planned_date=today - timedelta(days=7), working_title='Recent ep',
                context_notes='x' * 20, status=PlanItem.Status.APPROVED,
            )
            Script.objects.create(
                plan_item=newer, skill_used='varthaai-varthaanm-script',
                status=Script.Status.APPROVED, version=1, recap_summary='The real recap to carry forward.',
            )
            # A draft-status script on an even more recent item must be ignored —
            # recap continuity only ever reads APPROVED scripts (§6).
            unapproved_recent = PlanItem.objects.create(
                plan=plan, series=series, content_type='reel_varthaanm',
                planned_date=today - timedelta(days=1), working_title='Unreviewed ep',
                context_notes='x' * 20, status=PlanItem.Status.NEEDS_APPROVAL,
            )
            Script.objects.create(
                plan_item=unapproved_recent, skill_used='varthaai-varthaanm-script',
                status=Script.Status.NEEDS_REVIEW, version=1, recap_summary='Draft recap — should not win either.',
            )

            target_item = PlanItem.objects.create(
                plan=plan, series=series, content_type='reel_varthaanm',
                planned_date=today, working_title='Today ep',
                context_notes='x' * 20, status=PlanItem.Status.APPROVED,
            )
            self.assertEqual(
                copywriter._latest_varthaanm_recap(target_item),
                'The real recap to carry forward.',
            )
            self.assertEqual(copywriter._next_varthaanm_episode_number(target_item), 3)
        finally:
            brand.delete()

    def test_latest_varthaanm_recap_empty_when_no_approved_script_exists(self):
        brand = _mk_brand(name='No Recap Test Brand')
        try:
            plan = _mk_plan(brand)
            series = ContentSeries.objects.get(slug='varthaanm')
            item = PlanItem.objects.create(
                plan=plan, series=series, content_type='reel_varthaanm',
                planned_date=timezone.localdate(), working_title='First ep',
                context_notes='x' * 20, status=PlanItem.Status.APPROVED,
            )
            self.assertEqual(copywriter._latest_varthaanm_recap(item), '')
            self.assertEqual(copywriter._next_varthaanm_episode_number(item), 1)
        finally:
            brand.delete()


class RunDailyInputNeededTests(TestCase):
    """The INPUT_NEEDED path — no claude CLI calls (readiness is checked
    before any drafting attempt)."""

    def setUp(self):
        self.brand = _mk_brand()

    def tearDown(self):
        self.brand.delete()

    def test_blank_context_notes_files_action_item_and_sets_needs_input(self):
        plan = _mk_plan(self.brand)
        series = ContentSeries.objects.get(slug='varthaanm')
        today = timezone.localdate()
        item = PlanItem.objects.create(
            plan=plan, series=series, content_type='reel_varthaanm', planned_date=today,
            working_title='Blank brief episode', context_notes='',
            status=PlanItem.Status.APPROVED,
        )

        summary = copywriter.run_daily(brand=self.brand)
        item.refresh_from_db()

        self.assertEqual(item.status, PlanItem.Status.NEEDS_INPUT)
        self.assertTrue(ActionItem.objects.filter(
            plan_item=item, kind=ActionItem.Kind.INPUT_NEEDED, status=ActionItem.Status.OPEN).exists())
        self.assertEqual(summary['needs_input'], 1)
        self.assertEqual(summary['drafted'], 0)

        # Re-running while still blank: NEEDS_INPUT is now included in
        # due_plan_items' gate (self-healing — see its docstring), so the
        # item is re-checked, still fails, and needs_input is reported again
        # — but the get_or_create guard means no duplicate ActionItem.
        summary2 = copywriter.run_daily(brand=self.brand)
        self.assertEqual(
            ActionItem.objects.filter(plan_item=item, kind=ActionItem.Kind.INPUT_NEEDED).count(), 1)
        self.assertEqual(summary2['needs_input'], 1)

        # Now the admin supplies the missing input (same as update_item would
        # do) — the next run should self-heal: draft successfully and close
        # out the INPUT_NEEDED item, without needing anything special to
        # move it out of NEEDS_INPUT first.
        item.context_notes = 'The story of the first batch a distributor rejected for not being crunchy enough.'
        item.save(update_fields=['context_notes'])
        with mock.patch.object(copywriter, 'run_claude_cli') as mock_cli:
            mock_cli.return_value = {
                'is_error': False,
                'text': 'A fine episode script.\nRECAP_FOR_NEXT_EPISODE: The first batch got rejected.',
            }
            summary3 = copywriter.run_daily(brand=self.brand)
        item.refresh_from_db()
        self.assertEqual(summary3['drafted'], 1)
        self.assertEqual(summary3['needs_input'], 0)
        self.assertEqual(item.status, PlanItem.Status.NEEDS_APPROVAL)
        self.assertFalse(ActionItem.objects.filter(
            plan_item=item, kind=ActionItem.Kind.INPUT_NEEDED, status=ActionItem.Status.OPEN).exists())
        self.assertTrue(ActionItem.objects.filter(
            plan_item=item, kind=ActionItem.Kind.SCRIPT_REVIEW, status=ActionItem.Status.OPEN).exists())

    def test_poster_event_missing_date_files_input_needed(self):
        plan = _mk_plan(self.brand)
        today = timezone.localdate()
        item = PlanItem.objects.create(
            plan=plan, series=None, content_type='poster_event', planned_date=today,
            working_title='Mystery event', context_notes='We should do something fun soon.',
            status=PlanItem.Status.APPROVED,
        )
        summary = copywriter.run_daily(brand=self.brand)
        item.refresh_from_db()
        self.assertEqual(item.status, PlanItem.Status.NEEDS_INPUT)
        self.assertEqual(summary['needs_input'], 1)


@unittest.skipUnless(CLAUDE_AVAILABLE, 'claude CLI not available in this environment')
class LiveDraftScriptTests(TestCase):
    """Real `claude -p` round trips. All data disposable (see _mk_brand)."""

    def setUp(self):
        self.brand = _mk_brand()

    def tearDown(self):
        self.brand.delete()

    def test_reel_varthaanm_live_draft_with_recap_continuity(self):
        plan = _mk_plan(self.brand)
        series = ContentSeries.objects.get(slug='varthaanm')
        today = timezone.localdate()

        item1 = PlanItem.objects.create(
            plan=plan, series=series, content_type='reel_varthaanm',
            planned_date=today - timedelta(days=7), working_title='Ep1',
            context_notes=(
                "Tell the story of packing the first 50 bags of chips by hand in Ajwad's "
                "kitchen at 2am the night before the first market stall, running out of "
                "packing tape and using a stapler instead."
            ),
            status=PlanItem.Status.APPROVED,
        )
        script1 = copywriter.draft_script_for_item(item1)
        self.assertTrue(script1.raw_output)
        self.assertEqual(script1.status, Script.Status.NEEDS_REVIEW)
        self.assertEqual(script1.skill_used, 'varthaai-varthaanm-script')
        item1.refresh_from_db()
        self.assertEqual(item1.status, PlanItem.Status.NEEDS_APPROVAL)
        self.assertTrue(ActionItem.objects.filter(
            plan_item=item1, kind=ActionItem.Kind.SCRIPT_REVIEW, status=ActionItem.Status.OPEN).exists())

        # Simulate the admin approving episode 1 so episode 2's recap read
        # actually finds something (§6 — recap only carries forward from an
        # APPROVED script, never a draft).
        script1.status = Script.Status.APPROVED
        script1.save(update_fields=['status'])

        item2 = PlanItem.objects.create(
            plan=plan, series=series, content_type='reel_varthaanm',
            planned_date=today, working_title='Ep2',
            context_notes=(
                "Tell the story of the first time a distributor rejected an entire batch "
                "for not being crunchy enough, and what changed in the recipe after."
            ),
            status=PlanItem.Status.APPROVED,
        )
        script2 = copywriter.draft_script_for_item(item2)
        self.assertTrue(script2.raw_output)
        self.assertEqual(script2.version, 1)
        self.assertTrue(script2.recap_summary, 'expected a parsed RECAP_FOR_NEXT_EPISODE line')
        item2.refresh_from_db()
        self.assertEqual(item2.status, PlanItem.Status.NEEDS_APPROVAL)

    def test_generic_content_poster_occasion_live_draft(self):
        plan = _mk_plan(self.brand)
        today = timezone.localdate()
        item = PlanItem.objects.create(
            plan=plan, series=None, content_type='poster_occasion', planned_date=today,
            working_title='Onam poster',
            context_notes=(
                "Onam falls this week — warm/heritage register, tie it to the Kerala "
                "harvest festival and the banana-leaf sadya tradition."
            ),
            status=PlanItem.Status.APPROVED,
        )
        script = copywriter.draft_script_for_item(item)
        self.assertTrue(script.raw_output)
        self.assertEqual(script.skill_used, 'varthaai-generic-content')
        self.assertIn('content_type', script.structured_json)
        item.refresh_from_db()
        self.assertEqual(item.status, PlanItem.Status.NEEDS_APPROVAL)


@override_settings(ROOT_URLCONF='content.urlconf_test_scripts')
class ScriptsAPITests(TestCase):
    """Approve/request_changes are pure status-flip logic (no CLI calls);
    regenerate makes a real claude CLI call and is skipped if unavailable."""

    def setUp(self):
        self.brand = _mk_brand()
        self.admin = AdminUser.objects.create_user(
            username='cwt_test_admin', password='x',
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

    def _mk_script(self, content_type='poster_occasion', status=Script.Status.NEEDS_REVIEW, version=1,
                   context_notes='Onam is coming up — warm/heritage angle.'):
        plan = _mk_plan(self.brand)
        item = PlanItem.objects.create(
            plan=plan, content_type=content_type, planned_date=timezone.localdate(),
            working_title='API test item', context_notes=context_notes,
            status=PlanItem.Status.NEEDS_APPROVAL,  # post-draft state, as draft_script_for_item leaves it
        )
        return Script.objects.create(
            plan_item=item, skill_used='varthaai-generic-content',
            raw_output='CONTENT_TYPE: poster_occasion\nREGISTER: warm/heritage\n\nHappy Onam from Varthaai!',
            status=status, version=version,
        )

    def test_list_and_detail(self):
        script = self._mk_script()
        res = self.client.get('/admin/api/content/scripts/')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        self.assertTrue(any(i['id'] == script.id for i in data['data']['items']))

        res2 = self.client.get('/admin/api/content/scripts/', {'id': script.id})
        self.assertTrue(res2.json()['success'])
        self.assertEqual(res2.json()['data']['raw_output'], script.raw_output)

    def test_status_filter(self):
        self._mk_script(status=Script.Status.NEEDS_REVIEW)
        approved = self._mk_script(status=Script.Status.APPROVED)
        res = self.client.get('/admin/api/content/scripts/', {'status': 'approved'})
        ids = [i['id'] for i in res.json()['data']['items']]
        self.assertEqual(ids, [approved.id])

    def test_approve_sets_script_and_plan_item_status_and_resolves_action_item(self):
        script = self._mk_script()
        ActionItem.objects.create(
            plan_item=script.plan_item, kind=ActionItem.Kind.SCRIPT_REVIEW,
            status=ActionItem.Status.OPEN, title='Review',
        )
        res = self.client.post('/admin/api/content/scripts/', {'action': 'approve', 'script_id': script.id})
        self.assertTrue(res.json()['success'], res.json())
        script.refresh_from_db()
        script.plan_item.refresh_from_db()
        self.assertEqual(script.status, Script.Status.APPROVED)
        self.assertEqual(script.plan_item.status, PlanItem.Status.APPROVED)
        self.assertEqual(ActionItem.objects.get(plan_item=script.plan_item).status, ActionItem.Status.DONE)

        res2 = self.client.post('/admin/api/content/scripts/', {'action': 'approve', 'script_id': script.id})
        self.assertFalse(res2.json()['success'])

    def test_approve_rejects_stale_version(self):
        script = self._mk_script(version=1)
        Script.objects.create(
            plan_item=script.plan_item, skill_used='x', raw_output='v2',
            status=Script.Status.NEEDS_REVIEW, version=2,
        )
        res = self.client.post('/admin/api/content/scripts/', {'action': 'approve', 'script_id': script.id})
        self.assertFalse(res.json()['success'])

    def test_request_changes(self):
        script = self._mk_script()
        res = self.client.post('/admin/api/content/scripts/', {
            'action': 'request_changes', 'script_id': script.id, 'review_notes': 'too long',
        })
        self.assertTrue(res.json()['success'])
        script.refresh_from_db()
        self.assertEqual(script.status, Script.Status.CHANGES_REQUESTED)
        self.assertEqual(script.review_notes, 'too long')
        script.plan_item.refresh_from_db()
        self.assertEqual(script.plan_item.status, PlanItem.Status.NEEDS_APPROVAL)  # left untouched

    def test_regenerate_rejects_stale_version(self):
        script = self._mk_script(version=1)
        Script.objects.create(
            plan_item=script.plan_item, skill_used='x', raw_output='v2',
            status=Script.Status.NEEDS_REVIEW, version=2,
        )
        res = self.client.post('/admin/api/content/scripts/', {'action': 'regenerate', 'script_id': script.id})
        self.assertFalse(res.json()['success'])

    @unittest.skipUnless(CLAUDE_AVAILABLE, 'claude CLI not available in this environment')
    def test_regenerate_live_creates_new_version_and_resets_plan_item_status(self):
        script = self._mk_script(
            content_type='poster_occasion',
            context_notes='Diwali is coming up — warm/heritage register, tie it to lights and family gatherings.',
        )
        # Pretend the admin had approved it, then asked for a regeneration —
        # confirms regenerate resets status regardless of the prior state.
        script.status = Script.Status.APPROVED
        script.save(update_fields=['status'])
        script.plan_item.status = PlanItem.Status.APPROVED
        script.plan_item.save(update_fields=['status'])

        res = self.client.post('/admin/api/content/scripts/', {
            'action': 'regenerate', 'script_id': script.id,
            'instruction': 'Make it about Diwali night snacking specifically.',
        })
        self.assertTrue(res.json()['success'], res.json())
        data = res.json()['data']
        self.assertEqual(data['version'], 2)
        script.plan_item.refresh_from_db()
        self.assertEqual(script.plan_item.status, PlanItem.Status.NEEDS_APPROVAL)


@override_settings(ROOT_URLCONF='content.urlconf_test_scripts')
class ContentScriptsPageTests(TestCase):
    def setUp(self):
        self.brand = _mk_brand()
        self.admin = AdminUser.objects.create_user(
            username='cwt_page_admin', password='x',
            role=AdminUser.Role.SUPER_ADMIN, is_staff=True, is_superuser=True,
        )

    def tearDown(self):
        self.admin.delete()
        self.brand.delete()

    def test_super_admin_can_load_page(self):
        client = Client()
        client.force_login(self.admin)
        session = client.session
        session[BRAND_SESSION_KEY] = self.brand.id
        session.save()
        res = client.get('/admin/content/scripts/')
        self.assertEqual(res.status_code, 200)

    def test_restricted_admin_gets_403(self):
        restricted = AdminUser.objects.create_user(
            username='cwt_restricted_admin', password='x', role=AdminUser.Role.ADMIN,
            brand_permissions={str(self.brand.id): ['content_dashboard']},
        )
        try:
            client = Client()
            client.force_login(restricted)
            session = client.session
            session[BRAND_SESSION_KEY] = self.brand.id
            session.save()
            res = client.get('/admin/content/scripts/')
            self.assertEqual(res.status_code, 403)
        finally:
            restricted.delete()
