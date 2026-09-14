"""
Tests for Brand Assets: content.agents.designer.select_brand_asset, the
has_brand_logo gating on generate_poster_brief's logo_position field, and
the new admin API (content/views_brand_assets.py). New file — content/
tests.py (empty) and tests_designer.py are untouched.

No live claude/Gemini calls — generate_poster_brief/generate_poster_image
are mocked (matches tests_designer.py's own convention: those are the
permanent deterministic suite; live verification is separate).
"""
import shutil
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from accounts.models import AdminUser
from core.api import BRAND_SESSION_KEY
from core.models import Brand

from content.agents import designer
from content.models import BrandAsset, ContentPlan, ContentSeries, PlanItem, PosterAsset, PosterFormat, PosterInspiration, Script

_TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix='varthaai_test_media_brand_assets_')


def tearDownModule():
    shutil.rmtree(_TEST_MEDIA_ROOT, ignore_errors=True)


def _mk_brand(name='Brand Asset Test Brand'):
    return Brand.objects.create(name=name, order_prefix='BAT', is_active=True)


def _mk_asset(brand, tag=BrandAsset.Tag.PRODUCT, is_active=True, filename='a.jpg'):
    with override_settings(MEDIA_ROOT=_TEST_MEDIA_ROOT):
        return BrandAsset.objects.create(
            brand=brand, tag=tag, is_active=is_active,
            image=SimpleUploadedFile(filename, b'fake-bytes', content_type='image/jpeg'),
        )


class SelectBrandAssetTests(TestCase):
    def setUp(self):
        self.brand = _mk_brand()

    def tearDown(self):
        self.brand.delete()

    def test_no_assets_returns_none(self):
        self.assertIsNone(designer.select_brand_asset(self.brand))
        self.assertIsNone(designer.select_brand_asset(self.brand, tag=BrandAsset.Tag.LOGO))

    def test_returns_matching_tag(self):
        _mk_asset(self.brand, tag=BrandAsset.Tag.LOGO)
        _mk_asset(self.brand, tag=BrandAsset.Tag.PRODUCT)
        result = designer.select_brand_asset(self.brand, tag=BrandAsset.Tag.LOGO)
        self.assertEqual(result.tag, BrandAsset.Tag.LOGO)

    def test_no_tag_matches_any(self):
        _mk_asset(self.brand, tag=BrandAsset.Tag.TEAM)
        result = designer.select_brand_asset(self.brand)
        self.assertIsNotNone(result)

    def test_inactive_excluded(self):
        _mk_asset(self.brand, tag=BrandAsset.Tag.LOGO, is_active=False)
        self.assertIsNone(designer.select_brand_asset(self.brand, tag=BrandAsset.Tag.LOGO))

    def test_no_fallback_across_tags(self):
        """Unlike select_poster_inspiration, there's no 3-layer fallback —
        asking for LOGO when only PRODUCT exists must return None, not a
        mismatched asset."""
        _mk_asset(self.brand, tag=BrandAsset.Tag.PRODUCT)
        self.assertIsNone(designer.select_brand_asset(self.brand, tag=BrandAsset.Tag.LOGO))

    def test_other_brand_excluded(self):
        other = _mk_brand(name='Other Asset Brand')
        try:
            _mk_asset(other, tag=BrandAsset.Tag.LOGO)
            self.assertIsNone(designer.select_brand_asset(self.brand, tag=BrandAsset.Tag.LOGO))
        finally:
            other.delete()


class HasBrandLogoGatingTests(TestCase):
    """generate_poster_brief only asks for logo_position when BOTH the
    reference inspiration has a logo slot AND a real brand logo asset is
    actually available to composite in."""

    def setUp(self):
        self.brand = _mk_brand()
        self.inspiration = PosterInspiration.objects.create(
            brand=self.brand, source_label='ref', description='d', design_language='dl',
            topic_category='general', format='1:1', has_logo=True,
        )

    def tearDown(self):
        self.brand.delete()

    def _run(self, has_brand_logo):
        captured = {}

        def fake_run_claude_cli(prompt, **kwargs):
            captured['prompt'] = prompt
            return {'is_error': False, 'text': 'headline: x'}

        with mock.patch.object(designer, 'run_claude_cli', side_effect=fake_run_claude_cli), \
             mock.patch('shutil.which', return_value='/usr/bin/claude'):
            designer.generate_poster_brief(
                self.brand, 'topic', self.inspiration, has_brand_logo=has_brand_logo)
        return captured['prompt']

    @staticmethod
    def _fields_line(prompt):
        """The actual field LIST line — distinct from the boilerplate Rules
        text, which always mentions "logo_position" by name as a worked
        example regardless of whether it's actually requested this call
        (`assertIn('logo_position', prompt)` alone would false-pass)."""
        for line in prompt.splitlines():
            if line.startswith('scene, mood'):
                return line
        return ''

    def test_logo_position_requested_when_logo_asset_available(self):
        prompt = self._run(has_brand_logo=True)
        self.assertIn('logo_position', self._fields_line(prompt))

    def test_logo_position_not_requested_without_a_real_logo_asset(self):
        prompt = self._run(has_brand_logo=False)
        self.assertNotIn('logo_position', self._fields_line(prompt))

    def test_default_is_false_backward_compatible(self):
        """Existing callers that never pass has_brand_logo (e.g. any code
        written before Brand Assets existed) keep the old behavior — never
        asks for logo_position, since there was never a real asset to
        composite anyway."""
        captured = {}

        def fake_run_claude_cli(prompt, **kwargs):
            captured['prompt'] = prompt
            return {'is_error': False, 'text': 'headline: x'}

        with mock.patch.object(designer, 'run_claude_cli', side_effect=fake_run_claude_cli), \
             mock.patch('shutil.which', return_value='/usr/bin/claude'):
            designer.generate_poster_brief(self.brand, 'topic', self.inspiration)
        self.assertNotIn('logo_position', self._fields_line(captured['prompt']))


class BrandAssetsAPITests(TestCase):
    def setUp(self):
        self.brand = _mk_brand()
        self.super_admin = AdminUser.objects.create_user(
            username='ba_test_super', password='x',
            role=AdminUser.Role.SUPER_ADMIN, is_staff=True, is_superuser=True,
        )
        self.staff_admin = AdminUser.objects.create_user(
            username='ba_test_staff', password='x', role=AdminUser.Role.STAFF,
            brand_permissions={str(self.brand.id): ['all']},
        )
        self.client = Client()

    def tearDown(self):
        self.super_admin.delete()
        self.staff_admin.delete()
        self.brand.delete()

    def _login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session[BRAND_SESSION_KEY] = self.brand.id
        session.save()

    def _url(self):
        return f'/admin/api/content/brand-assets/{self.brand.id}/'

    @override_settings(MEDIA_ROOT=_TEST_MEDIA_ROOT)
    def test_super_admin_can_add_asset(self):
        self._login(self.super_admin)
        image = SimpleUploadedFile('logo.png', b'fake-bytes', content_type='image/png')
        res = self.client.post(self._url(), {'action': 'add', 'tag': 'logo', 'label': 'Primary logo', 'image': image})
        self.assertTrue(res.json()['success'])
        asset = BrandAsset.objects.get(brand=self.brand)
        self.assertEqual(asset.tag, 'logo')
        self.assertEqual(asset.label, 'Primary logo')
        asset.image.delete(save=False)

    def test_add_without_image_rejected(self):
        self._login(self.super_admin)
        res = self.client.post(self._url(), {'action': 'add', 'tag': 'logo'})
        self.assertFalse(res.json()['success'])

    def test_add_invalid_tag_rejected(self):
        self._login(self.super_admin)
        image = SimpleUploadedFile('x.png', b'fake-bytes', content_type='image/png')
        res = self.client.post(self._url(), {'action': 'add', 'tag': 'not_a_real_tag', 'image': image})
        self.assertFalse(res.json()['success'])

    def test_non_super_admin_cannot_add(self):
        self._login(self.staff_admin)
        image = SimpleUploadedFile('x.png', b'fake-bytes', content_type='image/png')
        res = self.client.post(self._url(), {'action': 'add', 'tag': 'logo', 'image': image})
        self.assertFalse(res.json()['success'])
        self.assertFalse(BrandAsset.objects.filter(brand=self.brand).exists())

    def test_any_logged_in_admin_can_list(self):
        asset = _mk_asset(self.brand)
        self._login(self.staff_admin)
        res = self.client.get(self._url())
        self.assertTrue(res.json()['success'])
        self.assertEqual(len(res.json()['data']['items']), 1)
        asset.image.delete(save=False)

    @override_settings(MEDIA_ROOT=_TEST_MEDIA_ROOT)
    def test_toggle_and_delete(self):
        asset = _mk_asset(self.brand)
        self._login(self.super_admin)
        res = self.client.post(self._url(), {'action': 'toggle', 'id': asset.id})
        self.assertTrue(res.json()['success'])
        asset.refresh_from_db()
        self.assertFalse(asset.is_active)

        res = self.client.post(self._url(), {'action': 'delete', 'id': asset.id})
        self.assertTrue(res.json()['success'])
        self.assertFalse(BrandAsset.objects.filter(id=asset.id).exists())

    def test_cross_brand_asset_not_toggleable(self):
        other = _mk_brand(name='Other Brand Asset Test')
        try:
            other_asset = _mk_asset(other)
            self._login(self.super_admin)
            res = self.client.post(self._url(), {'action': 'toggle', 'id': other_asset.id})
            self.assertFalse(res.json()['success'])
            other_asset.image.delete(save=False)
        finally:
            other.delete()


def _fake_brief(*args, **kwargs):
    return 'scene: x\nheadline: Test Headline\nsubheadline: Test Subheadline'


def _fake_image_ok(*args, **kwargs):
    return b'FAKEJPEGBYTES', {'mime_type': 'image/jpeg', 'model': 'fake'}


class PosterGenerationAssetWiringTests(TestCase):
    """The actual wiring in run_daily/regenerate_poster — not just that
    select_brand_asset and the has_brand_logo gate work in isolation
    (covered above), but that the poster-generation call sites actually
    select brand assets and pass them through to generate_poster_brief/
    generate_poster_image. Mirrors tests_designer.py's own mocking
    convention (kept in this file, not appended there, since a concurrent
    session owns that file right now)."""

    def setUp(self):
        self.brand = _mk_brand(name='Asset Wiring Test Brand')
        self.plan = ContentPlan.objects.create(
            brand=self.brand, period_start='2026-09-01', period_end='2026-09-30')
        self.item = PlanItem.objects.create(
            plan=self.plan, content_type=ContentSeries.ContentType.POSTER_LEARN,
            planned_date='2026-09-15', working_title='Test item')
        Script.objects.create(
            plan_item=self.item, skill_used='learn-with-varthaai-script',
            raw_output='approved text', status=Script.Status.APPROVED, version=1)

    def tearDown(self):
        self.brand.delete()

    @override_settings(MEDIA_ROOT=_TEST_MEDIA_ROOT)
    @mock.patch('content.agents.designer.select_poster_inspiration', return_value=None)
    @mock.patch('content.agents.designer.generate_poster_image', side_effect=_fake_image_ok)
    @mock.patch('content.agents.designer.generate_poster_brief', side_effect=_fake_brief)
    def test_run_daily_passes_selected_assets_through(self, mock_brief, mock_image, _select_insp):
        logo = _mk_asset(self.brand, tag=BrandAsset.Tag.LOGO)
        product = _mk_asset(self.brand, tag=BrandAsset.Tag.PRODUCT)
        try:
            summary = designer.run_daily(brand=self.brand)
            self.assertEqual(summary['generated'], 1)

            self.assertTrue(mock_brief.call_args.kwargs.get('has_brand_logo'))

            image_kwargs = mock_image.call_args.kwargs
            self.assertEqual(image_kwargs.get('topic_asset'), product)
            self.assertEqual(image_kwargs.get('brand_logo'), logo)
        finally:
            logo.image.delete(save=False)
            product.image.delete(save=False)

    @override_settings(MEDIA_ROOT=_TEST_MEDIA_ROOT)
    @mock.patch('content.agents.designer.select_poster_inspiration', return_value=None)
    @mock.patch('content.agents.designer.generate_poster_image', side_effect=_fake_image_ok)
    @mock.patch('content.agents.designer.generate_poster_brief', side_effect=_fake_brief)
    def test_run_daily_with_no_assets_passes_none(self, mock_brief, mock_image, _select_insp):
        summary = designer.run_daily(brand=self.brand)
        self.assertEqual(summary['generated'], 1)
        self.assertFalse(mock_brief.call_args.kwargs.get('has_brand_logo'))
        image_kwargs = mock_image.call_args.kwargs
        self.assertIsNone(image_kwargs.get('topic_asset'))
        self.assertIsNone(image_kwargs.get('brand_logo'))

    @override_settings(MEDIA_ROOT=_TEST_MEDIA_ROOT)
    @mock.patch('content.agents.designer.select_poster_inspiration', return_value=None)
    @mock.patch('content.agents.designer.generate_poster_image', side_effect=_fake_image_ok)
    @mock.patch('content.agents.designer.generate_poster_brief', side_effect=_fake_brief)
    def test_regenerate_poster_passes_selected_assets_through(self, mock_brief, mock_image, _select_insp):
        logo = _mk_asset(self.brand, tag=BrandAsset.Tag.LOGO)
        try:
            designer.regenerate_poster(self.item)
            self.assertTrue(mock_brief.call_args.kwargs.get('has_brand_logo'))
            self.assertEqual(mock_image.call_args.kwargs.get('brand_logo'), logo)
        finally:
            logo.image.delete(save=False)
