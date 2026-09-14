"""
Designer Agent tests — Phase 3 (content-generator-plan.md §13, poster-
generation-plan.md §7). New file per this phase's brief (content/tests.py
is a shared file owned elsewhere and is currently empty — kept untouched).

generate_poster_brief (a `claude -p` call) and generate_poster_image (a
Gemini call) are mocked throughout this file — this is the permanent,
fast, deterministic automated suite. A real end-to-end run against live
claude/Gemini was performed separately (disposable data, created and torn
down in a one-off shell session, not committed here as an automated test
since it needs live credentials and takes real wall-clock time) — see this
task's final report for what that run showed.
"""
import shutil
import sys
import tempfile
import types
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import path

from accounts.models import AdminUser
from content.agents import designer
from content.models import (
    ActionItem, ContentPlan, ContentSeries, PlanItem, PosterAsset, PosterFormat,
    PosterInspiration, Script,
)
from core.api import BRAND_SESSION_KEY
from core.models import Brand


# --------------------------------------------------------------------------- #
# A dynamically-registered urlconf so django.test.Client can hit
# PostersAPI/content_posters_page without Varthaai/urls.py being wired up
# yet — that include() is the orchestrator's job (content/urls_posters.py's
# own docstring). Registered once at import time under a private module
# name, not written to disk, so nothing here touches Varthaai/urls.py.
# --------------------------------------------------------------------------- #
from content import views_posters as _views_posters  # noqa: E402
from Varthaai import urls as _real_urls  # noqa: E402  (full site urlconf — base.html needs e.g. accounts:*)

_test_urlconf = types.ModuleType('content._test_urlconf_posters')
_test_urlconf.urlpatterns = list(_real_urls.urlpatterns) + [
    path('admin/content/posters/', _views_posters.content_posters_page, name='content_posters'),
    path('admin/api/content/posters/', _views_posters.PostersAPI.as_view(), name='content_posters_api'),
]
sys.modules['content._test_urlconf_posters'] = _test_urlconf
POSTERS_PAGE_URL = '/admin/content/posters/'
POSTERS_API_URL = '/admin/api/content/posters/'

# _save_poster_image() genuinely writes to FileSystemStorage — Django's
# TestCase wraps the DB in a rollback-able transaction but does NOT sandbox
# MEDIA_ROOT, so without this every run of a test that exercises the real
# run_daily()/regenerate_poster() code path (even with generate_poster_image
# mocked to return fake bytes) would leave real files behind under the
# project's actual media/content/posters/ directory. Route those tests at
# a throwaway temp dir instead, removed in tearDownModule below.
_TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix='varthaai_test_media_')


def tearDownModule():
    shutil.rmtree(_TEST_MEDIA_ROOT, ignore_errors=True)


def _fake_brief(*args, **kwargs):
    return 'scene: a warm kitchen counter\nheadline: Test Headline\nsubheadline: Test Subheadline'


def _fake_image_ok(*args, **kwargs):
    return b'FAKEJPEGBYTES', {'mime_type': 'image/jpeg', 'model': 'fake'}


def _fake_image_fail(*args, **kwargs):
    return None, {'error': 'Gemini returned no image data'}


class GenerateBriefApprovedCopyTests(TestCase):
    """generate_poster_brief's new `approved_copy` param — the Script->
    Designer contract (content-generator-plan.md): when given, the prompt
    must tell the brief-writer to use the exact approved text, not invent
    its own headline/subheadline, and must stay 100% backward compatible
    when omitted (DesignerTestAPI's existing calls never pass it)."""

    def setUp(self):
        self.brand = Brand.objects.create(name='Test Brand', brand_kit='Some brand kit text.')

    def tearDown(self):
        self.brand.delete()

    @mock.patch('content.agents.designer.run_claude_cli')
    @mock.patch('content.agents.designer.shutil.which', return_value='/usr/bin/claude')
    def test_approved_copy_spliced_into_prompt(self, _which, mock_cli):
        mock_cli.return_value = {'is_error': False, 'text': 'headline: x\nsubheadline: y'}
        designer.generate_poster_brief(
            self.brand, 'Diwali greeting', None,
            approved_copy='Headline: ರುಚಿ (Ruchi)\nSubline: means taste',
        )
        prompt = mock_cli.call_args[0][0]
        self.assertIn('APPROVED COPY', prompt)
        self.assertIn('ರುಚಿ', prompt)
        self.assertIn('ALREADY APPROVED', prompt)
        self.assertIn('never rephrase or invent', prompt)

    @mock.patch('content.agents.designer.run_claude_cli')
    @mock.patch('content.agents.designer.shutil.which', return_value='/usr/bin/claude')
    def test_no_approved_copy_is_backward_compatible(self, _which, mock_cli):
        mock_cli.return_value = {'is_error': False, 'text': 'headline: x\nsubheadline: y'}
        designer.generate_poster_brief(self.brand, 'Diwali greeting', None)
        prompt = mock_cli.call_args[0][0]
        self.assertNotIn('APPROVED COPY', prompt)
        self.assertNotIn('never rephrase or invent', prompt)


class FormatResolutionTests(TestCase):
    """_resolve_poster_format's documented default rule: poster_learn/
    poster_occasion always square; poster_event defaults square unless
    context_notes names a different shape."""

    def setUp(self):
        self.brand = Brand.objects.create(name='Test Brand 2')
        self.plan = ContentPlan.objects.create(
            brand=self.brand, period_start='2026-09-01', period_end='2026-09-30')

    def tearDown(self):
        self.brand.delete()

    def _item(self, content_type, context_notes=''):
        return PlanItem.objects.create(
            plan=self.plan, content_type=content_type, planned_date='2026-09-15',
            working_title='Test item', context_notes=context_notes)

    def test_poster_learn_is_always_square(self):
        item = self._item(ContentSeries.ContentType.POSTER_LEARN, context_notes='9:16 story format please')
        self.assertEqual(designer._resolve_poster_format(item), PosterFormat.SQUARE)

    def test_poster_occasion_is_always_square(self):
        item = self._item(ContentSeries.ContentType.POSTER_OCCASION, context_notes='portrait 4:5')
        self.assertEqual(designer._resolve_poster_format(item), PosterFormat.SQUARE)

    def test_poster_event_defaults_square(self):
        item = self._item(ContentSeries.ContentType.POSTER_EVENT, context_notes='Join us at the store on Friday')
        self.assertEqual(designer._resolve_poster_format(item), PosterFormat.SQUARE)

    def test_poster_event_story_hint(self):
        item = self._item(ContentSeries.ContentType.POSTER_EVENT, context_notes='Post as an Instagram story, 9:16')
        self.assertEqual(designer._resolve_poster_format(item), PosterFormat.STORY)

    def test_poster_event_portrait_hint(self):
        item = self._item(ContentSeries.ContentType.POSTER_EVENT, context_notes='Use a 4:5 portrait layout')
        self.assertEqual(designer._resolve_poster_format(item), PosterFormat.PORTRAIT)


@override_settings(MEDIA_ROOT=_TEST_MEDIA_ROOT)
class RunDailyGatingTests(TestCase):
    """run_daily's eligibility + "already attempted" gating rule (content-
    generator-plan.md §13 Phase 3 task brief)."""

    def setUp(self):
        self.brand = Brand.objects.create(name='Test Brand 3')
        self.other_brand = Brand.objects.create(name='Other Brand')
        self.plan = ContentPlan.objects.create(
            brand=self.brand, period_start='2026-09-01', period_end='2026-09-30')

    def tearDown(self):
        self.brand.delete()
        self.other_brand.delete()

    def _item(self, **kwargs):
        defaults = dict(
            plan=self.plan, content_type=ContentSeries.ContentType.POSTER_LEARN,
            planned_date='2026-09-15', working_title='Kannada word of the day',
        )
        defaults.update(kwargs)
        return PlanItem.objects.create(**defaults)

    def _approved_script(self, item, version=1):
        return Script.objects.create(
            plan_item=item, skill_used='learn-with-varthaai-script',
            raw_output='Headline: test\nSubline: test', status=Script.Status.APPROVED,
            version=version)

    @mock.patch('content.agents.designer.select_poster_inspiration', return_value=None)
    @mock.patch('content.agents.designer.generate_poster_image', side_effect=_fake_image_ok)
    @mock.patch('content.agents.designer.generate_poster_brief', side_effect=_fake_brief)
    def test_generates_for_eligible_item(self, mock_brief, mock_image, _select):
        item = self._item()
        self._approved_script(item)
        summary = designer.run_daily(brand=self.brand)
        self.assertEqual(summary, {'generated': 1, 'failed': 0, 'skipped': 0})
        poster = PosterAsset.objects.get(plan_item=item)
        self.assertEqual(poster.version, 1)
        self.assertEqual(poster.status, PosterAsset.Status.NEEDS_REVIEW)
        self.assertTrue(poster.image)
        self.assertEqual(poster.brief, {'text': _fake_brief()})
        self.assertTrue(
            ActionItem.objects.filter(
                plan_item=item, kind=ActionItem.Kind.POSTER_REVIEW, status=ActionItem.Status.OPEN,
            ).exists())
        mock_brief.assert_called_once()
        self.assertEqual(mock_brief.call_args.kwargs.get('approved_copy'), 'Headline: test\nSubline: test')

    def test_skips_item_with_no_approved_script(self):
        self._item()  # no Script at all
        summary = designer.run_daily(brand=self.brand)
        self.assertEqual(summary, {'generated': 0, 'failed': 0, 'skipped': 0})
        self.assertFalse(PosterAsset.objects.exists())

    def test_ignores_non_poster_content_types(self):
        item = self._item(content_type=ContentSeries.ContentType.REEL_VARTHAANM)
        self._approved_script(item)
        summary = designer.run_daily(brand=self.brand)
        self.assertEqual(summary, {'generated': 0, 'failed': 0, 'skipped': 0})

    @mock.patch('content.agents.designer.select_poster_inspiration', return_value=None)
    @mock.patch('content.agents.designer.generate_poster_image', side_effect=_fake_image_ok)
    @mock.patch('content.agents.designer.generate_poster_brief', side_effect=_fake_brief)
    def test_skips_item_already_needs_review(self, mock_brief, mock_image, _select):
        item = self._item()
        self._approved_script(item)
        PosterAsset.objects.create(
            plan_item=item, format=PosterFormat.SQUARE, version=1,
            status=PosterAsset.Status.NEEDS_REVIEW)
        summary = designer.run_daily(brand=self.brand)
        self.assertEqual(summary, {'generated': 0, 'failed': 0, 'skipped': 1})
        mock_brief.assert_not_called()
        self.assertEqual(PosterAsset.objects.filter(plan_item=item).count(), 1)

    @mock.patch('content.agents.designer.select_poster_inspiration', return_value=None)
    @mock.patch('content.agents.designer.generate_poster_image', side_effect=_fake_image_ok)
    @mock.patch('content.agents.designer.generate_poster_brief', side_effect=_fake_brief)
    def test_skips_item_already_approved(self, mock_brief, mock_image, _select):
        item = self._item()
        self._approved_script(item)
        PosterAsset.objects.create(
            plan_item=item, format=PosterFormat.SQUARE, version=1,
            status=PosterAsset.Status.APPROVED)
        summary = designer.run_daily(brand=self.brand)
        self.assertEqual(summary, {'generated': 0, 'failed': 0, 'skipped': 1})
        mock_brief.assert_not_called()

    @mock.patch('content.agents.designer.select_poster_inspiration', return_value=None)
    @mock.patch('content.agents.designer.generate_poster_image', side_effect=_fake_image_ok)
    @mock.patch('content.agents.designer.generate_poster_brief', side_effect=_fake_brief)
    def test_failed_attempt_counts_as_attempted_and_is_skipped(self, mock_brief, mock_image, _select):
        item = self._item()
        self._approved_script(item)
        # A prior failed attempt — no image, status left at whatever it was
        # (NEEDS_REVIEW is realistic: run_daily itself always sets that on
        # a failed attempt, per its own code path below).
        PosterAsset.objects.create(
            plan_item=item, format=PosterFormat.SQUARE, version=1,
            status=PosterAsset.Status.NEEDS_REVIEW, image=None)
        summary = designer.run_daily(brand=self.brand)
        self.assertEqual(summary, {'generated': 0, 'failed': 0, 'skipped': 1})
        mock_brief.assert_not_called()
        self.assertEqual(PosterAsset.objects.filter(plan_item=item).count(), 1)

    @mock.patch('content.agents.designer.select_poster_inspiration', return_value=None)
    @mock.patch('content.agents.designer.generate_poster_image', side_effect=_fake_image_ok)
    @mock.patch('content.agents.designer.generate_poster_brief', side_effect=_fake_brief)
    def test_changes_requested_allows_a_fresh_attempt(self, mock_brief, mock_image, _select):
        item = self._item()
        self._approved_script(item)
        PosterAsset.objects.create(
            plan_item=item, format=PosterFormat.SQUARE, version=1,
            status=PosterAsset.Status.CHANGES_REQUESTED, image='content/posters/fake.jpg')
        summary = designer.run_daily(brand=self.brand)
        self.assertEqual(summary, {'generated': 1, 'failed': 0, 'skipped': 0})
        mock_brief.assert_called_once()
        latest = PosterAsset.objects.filter(plan_item=item).order_by('-version').first()
        self.assertEqual(latest.version, 2)

    @mock.patch('content.agents.designer.select_poster_inspiration', return_value=None)
    @mock.patch('content.agents.designer.generate_poster_image', side_effect=_fake_image_fail)
    @mock.patch('content.agents.designer.generate_poster_brief', side_effect=_fake_brief)
    def test_image_generation_failure_still_persists_a_poster_asset(self, mock_brief, mock_image, _select):
        item = self._item()
        self._approved_script(item)
        summary = designer.run_daily(brand=self.brand)
        self.assertEqual(summary, {'generated': 0, 'failed': 1, 'skipped': 0})
        poster = PosterAsset.objects.get(plan_item=item)
        self.assertFalse(poster.image)
        self.assertEqual(poster.generation_metadata.get('error'), 'Gemini returned no image data')

    @mock.patch('content.agents.designer.select_poster_inspiration', return_value=None)
    @mock.patch(
        'content.agents.designer.generate_poster_brief',
        side_effect=RuntimeError('brief generation failed (subtype=error): boom'))
    def test_brief_generation_failure_still_persists_a_poster_asset(self, mock_brief, _select):
        item = self._item()
        self._approved_script(item)
        summary = designer.run_daily(brand=self.brand)
        self.assertEqual(summary, {'generated': 0, 'failed': 1, 'skipped': 0})
        poster = PosterAsset.objects.get(plan_item=item)
        self.assertFalse(poster.image)
        self.assertIn('brief generation failed', poster.generation_metadata.get('error', ''))

    @mock.patch('content.agents.designer.select_poster_inspiration', return_value=None)
    @mock.patch('content.agents.designer.generate_poster_image', side_effect=_fake_image_ok)
    @mock.patch('content.agents.designer.generate_poster_brief', side_effect=_fake_brief)
    def test_brand_scoping(self, mock_brief, mock_image, _select):
        other_plan = ContentPlan.objects.create(
            brand=self.other_brand, period_start='2026-09-01', period_end='2026-09-30')
        other_item = PlanItem.objects.create(
            plan=other_plan, content_type=ContentSeries.ContentType.POSTER_LEARN,
            planned_date='2026-09-15', working_title='Other brand item')
        self._approved_script(other_item)
        summary = designer.run_daily(brand=self.brand)
        self.assertEqual(summary, {'generated': 0, 'failed': 0, 'skipped': 0})
        self.assertFalse(PosterAsset.objects.filter(plan_item=other_item).exists())
        other_plan.delete()


@override_settings(MEDIA_ROOT=_TEST_MEDIA_ROOT)
class RegeneratePosterTests(TestCase):

    def setUp(self):
        self.brand = Brand.objects.create(name='Test Brand 4')
        self.other_brand = Brand.objects.create(name='Other Brand 4')
        self.plan = ContentPlan.objects.create(
            brand=self.brand, period_start='2026-09-01', period_end='2026-09-30')
        self.item = PlanItem.objects.create(
            plan=self.plan, content_type=ContentSeries.ContentType.POSTER_LEARN,
            planned_date='2026-09-15', working_title='Test item')

    def tearDown(self):
        self.brand.delete()
        self.other_brand.delete()

    def test_raises_without_approved_script(self):
        with self.assertRaises(RuntimeError):
            designer.regenerate_poster(self.item)

    @mock.patch('content.agents.designer.select_poster_inspiration', return_value=None)
    @mock.patch('content.agents.designer.generate_poster_image', side_effect=_fake_image_ok)
    @mock.patch('content.agents.designer.generate_poster_brief', side_effect=_fake_brief)
    def test_creates_version_1_when_none_exists(self, mock_brief, mock_image, _select):
        Script.objects.create(
            plan_item=self.item, skill_used='x', raw_output='approved text',
            status=Script.Status.APPROVED, version=1)
        poster = designer.regenerate_poster(self.item)
        self.assertEqual(poster.version, 1)
        self.assertEqual(poster.status, PosterAsset.Status.NEEDS_REVIEW)

    @mock.patch('content.agents.designer.select_poster_inspiration', return_value=None)
    @mock.patch('content.agents.designer.generate_poster_image', side_effect=_fake_image_ok)
    @mock.patch('content.agents.designer.generate_poster_brief', side_effect=_fake_brief)
    def test_creates_new_version_without_mutating_existing(self, mock_brief, mock_image, _select):
        Script.objects.create(
            plan_item=self.item, skill_used='x', raw_output='approved text',
            status=Script.Status.APPROVED, version=1)
        v1 = PosterAsset.objects.create(
            plan_item=self.item, format=PosterFormat.SQUARE, version=1,
            status=PosterAsset.Status.APPROVED, image='content/posters/v1.jpg')
        v2 = designer.regenerate_poster(self.item, instruction='make it warmer')
        self.assertEqual(v2.version, 2)
        v1.refresh_from_db()
        self.assertEqual(v1.status, PosterAsset.Status.APPROVED)  # untouched
        self.assertEqual(mock_image.call_args.kwargs.get('extra_instruction'), 'make it warmer')

    @mock.patch('content.agents.designer.generate_poster_image', side_effect=_fake_image_ok)
    @mock.patch('content.agents.designer.generate_poster_brief', side_effect=_fake_brief)
    def test_inspiration_id_must_belong_to_same_brand(self, mock_brief, mock_image):
        Script.objects.create(
            plan_item=self.item, skill_used='x', raw_output='approved text',
            status=Script.Status.APPROVED, version=1)
        foreign_insp = PosterInspiration.objects.create(
            brand=self.other_brand, source_label='foreign', description='d',
            design_language='dl', topic_category='general', format=PosterFormat.SQUARE)
        with self.assertRaises(ValueError):
            designer.regenerate_poster(self.item, inspiration_id=foreign_insp.id)
        foreign_insp.delete()

    @mock.patch('content.agents.designer.generate_poster_image', side_effect=_fake_image_ok)
    @mock.patch('content.agents.designer.generate_poster_brief', side_effect=_fake_brief)
    def test_inspiration_id_used_when_valid(self, mock_brief, mock_image):
        Script.objects.create(
            plan_item=self.item, skill_used='x', raw_output='approved text',
            status=Script.Status.APPROVED, version=1)
        insp = PosterInspiration.objects.create(
            brand=self.brand, source_label='mine', description='d',
            design_language='dl', topic_category='general', format=PosterFormat.SQUARE)
        poster = designer.regenerate_poster(self.item, inspiration_id=insp.id)
        self.assertEqual(poster.inspiration_id, insp.id)
        insp.delete()

    @mock.patch('content.agents.designer.select_poster_inspiration', return_value=None)
    @mock.patch('content.agents.designer.generate_poster_image', side_effect=_fake_image_fail)
    @mock.patch('content.agents.designer.generate_poster_brief', side_effect=_fake_brief)
    def test_image_failure_does_not_raise_but_is_recorded(self, mock_brief, mock_image, _select):
        Script.objects.create(
            plan_item=self.item, skill_used='x', raw_output='approved text',
            status=Script.Status.APPROVED, version=1)
        poster = designer.regenerate_poster(self.item)
        self.assertFalse(poster.image)
        self.assertEqual(poster.status, PosterAsset.Status.NEEDS_REVIEW)


@override_settings(ROOT_URLCONF='content._test_urlconf_posters')
class PostersAPITests(TestCase):

    def setUp(self):
        self.brand = Brand.objects.create(name='Test Brand 5')
        self.other_brand = Brand.objects.create(name='Other Brand 5')
        self.plan = ContentPlan.objects.create(
            brand=self.brand, period_start='2026-09-01', period_end='2026-09-30')
        self.item = PlanItem.objects.create(
            plan=self.plan, content_type=ContentSeries.ContentType.POSTER_LEARN,
            planned_date='2026-09-15', working_title='Test item')
        Script.objects.create(
            plan_item=self.item, skill_used='x', raw_output='approved text',
            status=Script.Status.APPROVED, version=1)

        self.superuser = AdminUser.objects.create_user(
            'poster_boss', 'pw', role=AdminUser.Role.SUPER_ADMIN)
        self.staff_no_perm = AdminUser.objects.create_user(
            'poster_peon', 'pw', role=AdminUser.Role.STAFF,
            brand_permissions={str(self.brand.id): ['orders']})
        self.staff_with_perm = AdminUser.objects.create_user(
            'poster_staff', 'pw', role=AdminUser.Role.STAFF,
            brand_permissions={str(self.brand.id): ['content_posters']})

    def tearDown(self):
        self.brand.delete()
        self.other_brand.delete()

    def _login_super(self):
        self.client.force_login(self.superuser)
        session = self.client.session
        session[BRAND_SESSION_KEY] = self.brand.id
        session.save()

    def test_page_requires_permission(self):
        self.client.force_login(self.staff_no_perm)
        session = self.client.session
        session[BRAND_SESSION_KEY] = self.brand.id
        session.save()
        res = self.client.get(POSTERS_PAGE_URL)
        self.assertEqual(res.status_code, 403)

    def test_page_ok_with_permission(self):
        self.client.force_login(self.staff_with_perm)
        session = self.client.session
        session[BRAND_SESSION_KEY] = self.brand.id
        session.save()
        res = self.client.get(POSTERS_PAGE_URL)
        self.assertEqual(res.status_code, 200)

    def test_api_forbidden_without_permission(self):
        self.client.force_login(self.staff_no_perm)
        session = self.client.session
        session[BRAND_SESSION_KEY] = self.brand.id
        session.save()
        res = self.client.get(POSTERS_API_URL)
        self.assertEqual(res.status_code, 403)

    def test_list_and_detail(self):
        poster = PosterAsset.objects.create(
            plan_item=self.item, format=PosterFormat.SQUARE, version=1,
            status=PosterAsset.Status.NEEDS_REVIEW, brief={'text': 'a brief'},
            generation_metadata={'model': 'fake'})
        self._login_super()
        res = self.client.get(POSTERS_API_URL)
        body = res.json()
        self.assertTrue(body['success'])
        self.assertEqual(len(body['data']['items']), 1)
        self.assertEqual(body['data']['items'][0]['id'], poster.id)

        res = self.client.get(POSTERS_API_URL, {'id': poster.id})
        body = res.json()
        self.assertTrue(body['success'])
        self.assertEqual(body['data']['brief_text'], 'a brief')
        self.assertIn('eligible_inspirations', body['data'])

    def test_list_is_brand_scoped(self):
        other_plan = ContentPlan.objects.create(
            brand=self.other_brand, period_start='2026-09-01', period_end='2026-09-30')
        other_item = PlanItem.objects.create(
            plan=other_plan, content_type=ContentSeries.ContentType.POSTER_LEARN,
            planned_date='2026-09-15', working_title='Other item')
        PosterAsset.objects.create(
            plan_item=other_item, format=PosterFormat.SQUARE, version=1,
            status=PosterAsset.Status.NEEDS_REVIEW)
        self._login_super()
        res = self.client.get(POSTERS_API_URL)
        self.assertEqual(res.json()['data']['items'], [])
        other_plan.delete()

    def test_approve_rejects_stale_version(self):
        v1 = PosterAsset.objects.create(
            plan_item=self.item, format=PosterFormat.SQUARE, version=1,
            status=PosterAsset.Status.NEEDS_REVIEW, image='content/posters/v1.jpg')
        PosterAsset.objects.create(
            plan_item=self.item, format=PosterFormat.SQUARE, version=2,
            status=PosterAsset.Status.NEEDS_REVIEW, image='content/posters/v2.jpg')
        self._login_super()
        res = self.client.post(
            POSTERS_API_URL, {'action': 'approve', 'id': v1.id}, content_type='application/json')
        body = res.json()
        self.assertFalse(body['success'])
        v1.refresh_from_db()
        self.assertEqual(v1.status, PosterAsset.Status.NEEDS_REVIEW)

    def test_approve_latest_version_succeeds_and_resolves_action_item(self):
        poster = PosterAsset.objects.create(
            plan_item=self.item, format=PosterFormat.SQUARE, version=1,
            status=PosterAsset.Status.NEEDS_REVIEW, image='content/posters/v1.jpg')
        ActionItem.objects.create(
            plan_item=self.item, kind=ActionItem.Kind.POSTER_REVIEW,
            status=ActionItem.Status.OPEN, title='Review poster')
        self._login_super()
        res = self.client.post(
            POSTERS_API_URL, {'action': 'approve', 'id': poster.id}, content_type='application/json')
        body = res.json()
        self.assertTrue(body['success'])
        poster.refresh_from_db()
        self.assertEqual(poster.status, PosterAsset.Status.APPROVED)
        self.assertEqual(
            ActionItem.objects.get(plan_item=self.item).status, ActionItem.Status.DONE)

    def test_approve_rejects_failed_attempt(self):
        poster = PosterAsset.objects.create(
            plan_item=self.item, format=PosterFormat.SQUARE, version=1,
            status=PosterAsset.Status.NEEDS_REVIEW, image=None)
        self._login_super()
        res = self.client.post(
            POSTERS_API_URL, {'action': 'approve', 'id': poster.id}, content_type='application/json')
        self.assertFalse(res.json()['success'])

    def test_request_changes_stores_notes_and_sets_status(self):
        poster = PosterAsset.objects.create(
            plan_item=self.item, format=PosterFormat.SQUARE, version=1,
            status=PosterAsset.Status.NEEDS_REVIEW, image='content/posters/v1.jpg')
        self._login_super()
        res = self.client.post(
            POSTERS_API_URL,
            {'action': 'request_changes', 'id': poster.id, 'notes': 'logo too small'},
            content_type='application/json')
        self.assertTrue(res.json()['success'])
        poster.refresh_from_db()
        self.assertEqual(poster.status, PosterAsset.Status.CHANGES_REQUESTED)
        self.assertEqual(poster.review_notes, 'logo too small')
        self.assertIsNotNone(poster.reviewed_at)

    @mock.patch('content.views_posters.designer.regenerate_poster')
    def test_regenerate_calls_designer_and_handles_errors(self, mock_regen):
        poster = PosterAsset.objects.create(
            plan_item=self.item, format=PosterFormat.SQUARE, version=1,
            status=PosterAsset.Status.NEEDS_REVIEW, image='content/posters/v1.jpg')
        new_poster = PosterAsset(plan_item=self.item, format=PosterFormat.SQUARE, version=2,
                                  status=PosterAsset.Status.NEEDS_REVIEW)
        new_poster.id = 99999
        mock_regen.return_value = new_poster
        self._login_super()

        res = self.client.post(
            POSTERS_API_URL,
            {'action': 'regenerate', 'id': poster.id, 'instruction': 'warmer', 'inspiration_id': ''},
            content_type='application/json')
        body = res.json()
        self.assertTrue(body['success'])
        self.assertEqual(body['data']['version'], 2)
        mock_regen.assert_called_once_with(self.item, instruction='warmer', inspiration_id=None)

        mock_regen.side_effect = RuntimeError('no approved script')
        res = self.client.post(
            POSTERS_API_URL,
            {'action': 'regenerate', 'id': poster.id, 'instruction': ''},
            content_type='application/json')
        self.assertNotEqual(res.status_code, 500)  # never a 500 — caught and turned into err() (400)
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json()['success'])

    @mock.patch('content.views_posters.designer.regenerate_poster')
    def test_retry_only_allowed_on_failed_attempt(self, mock_regen):
        ok_poster = PosterAsset.objects.create(
            plan_item=self.item, format=PosterFormat.SQUARE, version=1,
            status=PosterAsset.Status.NEEDS_REVIEW, image='content/posters/v1.jpg')
        self._login_super()
        res = self.client.post(
            POSTERS_API_URL, {'action': 'retry', 'id': ok_poster.id}, content_type='application/json')
        self.assertFalse(res.json()['success'])
        mock_regen.assert_not_called()

        failed_poster = PosterAsset.objects.create(
            plan_item=self.item, format=PosterFormat.SQUARE, version=2,
            status=PosterAsset.Status.NEEDS_REVIEW, image=None)
        new_poster = PosterAsset(plan_item=self.item, format=PosterFormat.SQUARE, version=3,
                                  status=PosterAsset.Status.NEEDS_REVIEW)
        new_poster.id = 88888
        mock_regen.return_value = new_poster
        res = self.client.post(
            POSTERS_API_URL, {'action': 'retry', 'id': failed_poster.id}, content_type='application/json')
        self.assertTrue(res.json()['success'])
        mock_regen.assert_called_once_with(self.item, instruction='', inspiration_id=None)
