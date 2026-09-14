"""
Tests for Phase 5 (Varthaai Verdict): the VerdictIntake model/API, the
Copywriter's dual-skill reel_verdict dispatch (content/agents/copywriter.py),
the Planner's placeholder reel_verdict slots, and the Verdict History API.

New file — content/tests.py (empty), tests_copywriter.py, tests_designer.py,
tests_planner.py are untouched. No live `claude -p` calls here — the dual-
skill CLI round trip is mocked; content/tests_copywriter.py already proves
the general single-skill dispatch works live, this file focuses on what
Phase 5 actually added (gating, prompt construction, JSON extraction,
multipart intake, history aggregation). All data disposable.
"""
import json
from datetime import timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import AdminUser
from core.api import BRAND_SESSION_KEY
from core.models import Brand

from content.agents import copywriter
from content.models import ActionItem, ContentPlan, ContentSeries, PlanItem, Script, VerdictIntake

_BRAND_KIT = "Identity: Varthaai is a premium Kerala banana chips brand."


def _mk_brand(name='Verdict Test Brand'):
    return Brand.objects.create(name=name, order_prefix='VDT', is_active=True, brand_kit=_BRAND_KIT)


def _mk_plan(brand, status=ContentPlan.Status.APPROVED):
    today = timezone.localdate()
    return ContentPlan.objects.create(
        brand=brand, period_start=today - timedelta(days=5), period_end=today + timedelta(days=25), status=status)


def _mk_item(plan, planned_date=None, status=PlanItem.Status.APPROVED):
    return PlanItem.objects.create(
        plan=plan, content_type='reel_verdict', planned_date=planned_date or timezone.localdate() + timedelta(days=1),
        working_title=f'Varthaai Verdict — {(planned_date or timezone.localdate()).isoformat()}',
        context_notes='Awaiting product selection and intake from Ajwad (photos, scores) — do not draft until submitted.',
        status=status,
    )


def _complete_intake_kwargs(**overrides):
    kwargs = dict(
        product_name='Competitor X — Classic 150g', product_category='banana chips', market='India',
        price_point='₹50/100g',
        design_notes='Packaging feels premium, clear window.', design_score=7,
        pricing_notes='Slightly overpriced for the quantity.', pricing_score=5,
        taste_notes='Crunchy but over-salted.', taste_score=6,
        ingredients_text='Banana, palm oil, salt.', nutrition_text='Energy 550kcal/100g',
    )
    kwargs.update(overrides)
    return kwargs


class VerdictIntakeMissingFieldsTests(TestCase):
    def setUp(self):
        self.brand = _mk_brand()
        self.plan = _mk_plan(self.brand)
        self.item = _mk_item(self.plan)

    def tearDown(self):
        self.brand.delete()

    def test_complete_intake_has_no_missing_fields(self):
        intake = VerdictIntake.objects.create(plan_item=self.item, **_complete_intake_kwargs())
        self.assertEqual(copywriter._verdict_intake_missing_fields(intake), [])

    def test_missing_product_name(self):
        intake = VerdictIntake.objects.create(plan_item=self.item, **_complete_intake_kwargs(product_name=''))
        self.assertIn('product name', copywriter._verdict_intake_missing_fields(intake))

    def test_missing_score_but_has_notes_still_flagged(self):
        intake = VerdictIntake.objects.create(plan_item=self.item, **_complete_intake_kwargs(design_score=None))
        self.assertIn('Design score + notes', copywriter._verdict_intake_missing_fields(intake))

    def test_ingredients_photo_satisfies_without_text(self):
        photo = SimpleUploadedFile('ingredients.jpg', b'fake-bytes', content_type='image/jpeg')
        intake = VerdictIntake.objects.create(
            plan_item=self.item, **_complete_intake_kwargs(ingredients_text='', ingredients_label_photo=photo))
        self.assertNotIn('ingredients list (photo or typed text)', copywriter._verdict_intake_missing_fields(intake))

    def test_neither_ingredients_photo_nor_text_flagged(self):
        intake = VerdictIntake.objects.create(plan_item=self.item, **_complete_intake_kwargs(ingredients_text=''))
        self.assertIn('ingredients list (photo or typed text)', copywriter._verdict_intake_missing_fields(intake))


class IsReadyVerdictBranchTests(TestCase):
    def setUp(self):
        self.brand = _mk_brand()
        self.plan = _mk_plan(self.brand)
        self.item = _mk_item(self.plan)

    def tearDown(self):
        self.brand.delete()

    def test_no_intake_at_all_not_ready(self):
        ready, reason = copywriter._is_ready(self.item)
        self.assertFalse(ready)
        self.assertIn('No product intake submitted', reason)

    def test_incomplete_intake_not_ready(self):
        VerdictIntake.objects.create(plan_item=self.item, **_complete_intake_kwargs(taste_notes=''))
        ready, reason = copywriter._is_ready(self.item)
        self.assertFalse(ready)
        self.assertIn('Taste score + notes', reason)

    def test_complete_intake_ready(self):
        VerdictIntake.objects.create(plan_item=self.item, **_complete_intake_kwargs())
        ready, reason = copywriter._is_ready(self.item)
        self.assertTrue(ready)
        self.assertEqual(reason, '')

    def test_reel_verdict_now_included_in_due_plan_items(self):
        """The Phase-1-era hard exclusion is gone — reel_verdict is due like
        any other content_type once its prep_lead_days deadline passes."""
        due_ids = list(copywriter.due_plan_items(brand=self.brand).values_list('id', flat=True))
        self.assertIn(self.item.id, due_ids)


class ExtractLastJsonBlockTests(TestCase):
    def test_single_json_block(self):
        text = 'Some prose.\n\n```json\n{"final_score": 6.5}\n```\n'
        self.assertEqual(copywriter._extract_last_json_block(text), {'final_score': 6.5})

    def test_takes_the_last_of_multiple_blocks(self):
        text = (
            '```json\n{"category_score": 7.0}\n```\n\nmore prose\n\n'
            '```json\n{"final_score": 6.5}\n```'
        )
        self.assertEqual(copywriter._extract_last_json_block(text), {'final_score': 6.5})

    def test_no_json_block_returns_empty_dict(self):
        self.assertEqual(copywriter._extract_last_json_block('just prose, no fences'), {})

    def test_malformed_last_block_falls_back_to_earlier_valid_one(self):
        text = '```json\n{"final_score": 6.5}\n```\n\n```json\n{not valid json\n```'
        self.assertEqual(copywriter._extract_last_json_block(text), {'final_score': 6.5})


class RunVerdictAndSaveTests(TestCase):
    """The dual-skill dispatch, CLI mocked. Asserts on the ACTUAL call
    arguments (system_prompt/tools), not just the persisted result, so a
    regression that silently drops one skill's text or the Read tool grant
    is actually caught."""

    def setUp(self):
        self.brand = _mk_brand()
        self.plan = _mk_plan(self.brand)
        self.item = _mk_item(self.plan)
        self.intake = VerdictIntake.objects.create(plan_item=self.item, **_complete_intake_kwargs())

    def tearDown(self):
        self.brand.delete()

    def _mock_verdict_response(self):
        payload = {
            'product': 'Competitor X — Classic 150g', 'market': 'India',
            'design': {'verdict': 'Feels premium.', 'score': 7},
            'pricing': {'verdict': 'A bit much.', 'score': 5},
            'taste': {'verdict': 'Over-salted.', 'score': 6},
            'ingredients_nutrition': {
                'condensed_verdict': 'Standard palm-oil fried snack, nothing alarming.',
                'score': 6, 'legitimacy_status': 'Consistent', 'controversial': False,
                'full_analysis': {},
            },
            'final_score': 6.0, 'scoring_method': 'equal-weight average of 4 category scores',
        }
        text = (
            'DESIGN — Score: 7/10\nFeels premium.\n\n'
            'FINAL SCORE: 6.0/10\n\n'
            '```json\n' + json.dumps(payload) + '\n```'
        )
        return {'is_error': False, 'text': text}

    def test_draft_calls_cli_with_both_skills_and_read_tool(self):
        with mock.patch.object(copywriter, 'run_claude_cli') as mock_cli:
            mock_cli.return_value = self._mock_verdict_response()
            script = copywriter.draft_script_for_item(self.item)

        mock_cli.assert_called_once()
        _, kwargs = mock_cli.call_args
        self.assertIn('Varthaai Verdict', kwargs['system_prompt'])
        self.assertIn('Food Quality Analyst', kwargs['system_prompt'])
        self.assertIn('regulatory-sources.md', kwargs['system_prompt'])
        self.assertIn('scoring-rubric.md', kwargs['system_prompt'])
        self.assertEqual(kwargs['allowed_tools'], ['Read', 'WebSearch', 'WebFetch'])
        self.assertNotIn('Read', kwargs['disallowed_tools'])
        self.assertNotIn('WebSearch', kwargs['disallowed_tools'])

        self.assertEqual(script.skill_used, 'varthaai-verdict-script+food-quality-analyst')
        self.assertEqual(script.structured_json['final_score'], 6.0)
        self.assertEqual(script.structured_json['product'], 'Competitor X — Classic 150g')
        self.assertEqual(script.status, Script.Status.NEEDS_REVIEW)

    def test_draft_includes_product_identity_and_intake_data_in_prompt(self):
        with mock.patch.object(copywriter, 'run_claude_cli') as mock_cli:
            mock_cli.return_value = self._mock_verdict_response()
            copywriter.draft_script_for_item(self.item)
        args, kwargs = mock_cli.call_args
        prompt = args[0]
        self.assertIn('Competitor X — Classic 150g', prompt)
        self.assertIn(self.intake.taste_notes, prompt)
        self.assertIn('Design score (Ajwad, out of 10): 7', prompt)

    def test_draft_reads_photo_path_instead_of_typed_text_when_photo_present(self):
        self.intake.ingredients_label_photo = SimpleUploadedFile(
            'ingredients.jpg', b'fake-bytes', content_type='image/jpeg')
        self.intake.save(update_fields=['ingredients_label_photo'])
        with mock.patch.object(copywriter, 'run_claude_cli') as mock_cli:
            mock_cli.return_value = self._mock_verdict_response()
            copywriter.draft_script_for_item(self.item)
        prompt = mock_cli.call_args[0][0]
        self.assertIn('use the Read tool', prompt)
        self.assertIn('ingredients.jpg'.split('.')[0], prompt)  # filename stem appears in the resolved path
        self.assertNotIn('Banana, palm oil, salt.', prompt)  # typed fallback not used when a photo exists

    def test_plan_item_and_action_items_transition_correctly(self):
        ActionItem.objects.create(
            plan_item=self.item, kind=ActionItem.Kind.INPUT_NEEDED, status=ActionItem.Status.OPEN,
            title='Input needed: placeholder')
        with mock.patch.object(copywriter, 'run_claude_cli') as mock_cli:
            mock_cli.return_value = self._mock_verdict_response()
            copywriter.draft_script_for_item(self.item)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, PlanItem.Status.NEEDS_APPROVAL)
        self.assertFalse(ActionItem.objects.filter(
            plan_item=self.item, kind=ActionItem.Kind.INPUT_NEEDED, status=ActionItem.Status.OPEN).exists())
        self.assertTrue(ActionItem.objects.filter(
            plan_item=self.item, kind=ActionItem.Kind.SCRIPT_REVIEW, status=ActionItem.Status.OPEN).exists())

    def test_raises_when_intake_incomplete(self):
        self.intake.taste_notes = ''
        self.intake.save(update_fields=['taste_notes'])
        with mock.patch.object(copywriter, 'run_claude_cli') as mock_cli:
            with self.assertRaises(RuntimeError):
                copywriter.draft_script_for_item(self.item)
        mock_cli.assert_not_called()

    def test_malformed_json_response_still_saves_script_with_empty_structured_json(self):
        with mock.patch.object(copywriter, 'run_claude_cli') as mock_cli:
            mock_cli.return_value = {'is_error': False, 'text': 'talking points but no fenced json at all'}
            script = copywriter.draft_script_for_item(self.item)
        self.assertEqual(script.structured_json, {})
        self.assertIn('talking points', script.raw_output)

    def test_run_daily_processes_verdict_item(self):
        with mock.patch.object(copywriter, 'run_claude_cli') as mock_cli:
            mock_cli.return_value = self._mock_verdict_response()
            summary = copywriter.run_daily(brand=self.brand)
        self.assertEqual(summary['drafted'], 1)
        self.assertEqual(summary['skipped'], 0)


class PlannerVerdictPlaceholderTests(TestCase):
    """generate_plan's reel_verdict handling — placeholder forced regardless
    of what the model actually returned (defense in depth for the
    never-invent-a-product rule)."""

    def setUp(self):
        self.brand = _mk_brand()

    def tearDown(self):
        self.brand.delete()

    def test_model_inventing_a_product_is_overridden_with_placeholder(self):
        from content.agents import planner
        plan = _mk_plan(self.brand, status=ContentPlan.Status.DRAFT)
        target_date = timezone.localdate() + timedelta(days=3)
        proposed = [{
            'planned_date': target_date.isoformat(), 'series_slug': 'verdict', 'content_type': 'reel_verdict',
            # The model misbehaving and inventing a product anyway:
            'working_title': 'Reviewing BrandName Chips', 'context_notes': 'BrandName is overpriced and salty.',
        }]
        with mock.patch.object(planner, 'run_claude_cli') as mock_cli:
            mock_cli.return_value = {'is_error': False, 'text': json.dumps(proposed)}
            created = planner.generate_plan(plan)
        self.assertEqual(created, 1)
        item = PlanItem.objects.get(plan=plan, content_type='reel_verdict')
        self.assertEqual(item.working_title, f'Varthaai Verdict — {target_date.isoformat()}')
        self.assertNotIn('BrandName', item.context_notes)
        self.assertIn('Awaiting product selection', item.context_notes)


class VerdictIntakeAPITests(TestCase):
    def setUp(self):
        self.brand = _mk_brand()
        self.admin = AdminUser.objects.create_user(
            username='vdt_test_admin', password='x',
            role=AdminUser.Role.SUPER_ADMIN, is_staff=True, is_superuser=True,
        )
        self.client = Client()
        self.client.force_login(self.admin)
        session = self.client.session
        session[BRAND_SESSION_KEY] = self.brand.id
        session.save()
        self.plan = _mk_plan(self.brand)
        self.item = _mk_item(self.plan)

    def tearDown(self):
        self.admin.delete()
        self.brand.delete()

    def _url(self):
        return f'/admin/api/content/verdict/intake/{self.item.id}/'

    def test_get_empty_intake_shape(self):
        res = self.client.get(self._url())
        self.assertEqual(res.status_code, 200)
        data = res.json()['data']
        self.assertIsNone(data['product_name'])
        self.assertEqual(data['working_title'], self.item.working_title)

    def test_post_creates_intake_with_text_fields_and_scores(self):
        res = self.client.post(self._url(), _complete_intake_kwargs())
        self.assertTrue(res.json()['success'])
        intake = VerdictIntake.objects.get(plan_item=self.item)
        self.assertEqual(intake.product_name, 'Competitor X — Classic 150g')
        self.assertEqual(intake.design_score, 7)
        self.assertEqual(intake.submitted_by, self.admin)

    def test_score_out_of_range_rejected(self):
        """get_or_create persists an (empty) intake row immediately — that's
        fine, a blank draft row is harmless — but the invalid score must not
        make it onto that row: validation runs before any field is saved."""
        res = self.client.post(self._url(), _complete_intake_kwargs(design_score=15))
        self.assertFalse(res.json()['success'])
        intake = VerdictIntake.objects.get(plan_item=self.item)
        self.assertIsNone(intake.design_score)
        self.assertEqual(intake.product_name, '')

    def test_partial_resave_does_not_wipe_existing_photo(self):
        photo = SimpleUploadedFile('ingredients.jpg', b'fake-bytes', content_type='image/jpeg')
        self.client.post(self._url(), dict(_complete_intake_kwargs(), ingredients_label_photo=photo))
        intake = VerdictIntake.objects.get(plan_item=self.item)
        self.assertTrue(intake.ingredients_label_photo)

        # Re-save WITHOUT the photo field at all — should keep the existing one.
        res = self.client.post(self._url(), _complete_intake_kwargs(product_name='Updated Name'))
        self.assertTrue(res.json()['success'])
        intake.refresh_from_db()
        self.assertTrue(intake.ingredients_label_photo)
        self.assertEqual(intake.product_name, 'Updated Name')
        intake.ingredients_label_photo.delete(save=True)

    def test_wrong_content_type_plan_item_404s(self):
        other_item = PlanItem.objects.create(
            plan=self.plan, content_type='blog', planned_date=timezone.localdate(),
            working_title='Not a verdict item', status=PlanItem.Status.APPROVED)
        res = self.client.get(f'/admin/api/content/verdict/intake/{other_item.id}/')
        self.assertEqual(res.status_code, 404)

    def test_cross_brand_404s(self):
        other_brand = _mk_brand(name='Other Verdict Brand')
        try:
            other_plan = _mk_plan(other_brand)
            other_item = _mk_item(other_plan)
            res = self.client.get(f'/admin/api/content/verdict/intake/{other_item.id}/')
            self.assertEqual(res.status_code, 404)
        finally:
            other_brand.delete()


class VerdictHistoryAPITests(TestCase):
    def setUp(self):
        self.brand = _mk_brand()
        self.admin = AdminUser.objects.create_user(
            username='vdt_hist_admin', password='x',
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

    def test_item_with_no_script_shows_not_yet_drafted(self):
        plan = _mk_plan(self.brand)
        _mk_item(plan)
        res = self.client.get('/admin/api/content/verdict/history/')
        data = res.json()['data']['items']
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['product_name'], '(not yet drafted)')
        self.assertIsNone(data[0]['final_score'])

    def test_latest_version_wins_not_first(self):
        plan = _mk_plan(self.brand)
        item = _mk_item(plan)
        Script.objects.create(
            plan_item=item, skill_used='x', raw_output='v1', version=1,
            structured_json={'product': 'Old draft', 'final_score': 4.0}, status=Script.Status.CHANGES_REQUESTED)
        Script.objects.create(
            plan_item=item, skill_used='x', raw_output='v2', version=2,
            structured_json={'product': 'Real Product', 'final_score': 6.5}, status=Script.Status.NEEDS_REVIEW)
        res = self.client.get('/admin/api/content/verdict/history/')
        row = res.json()['data']['items'][0]
        self.assertEqual(row['product_name'], 'Real Product')
        self.assertEqual(row['final_score'], 6.5)

    def test_brand_scoping(self):
        other_brand = _mk_brand(name='Other History Brand')
        try:
            other_plan = _mk_plan(other_brand)
            _mk_item(other_plan)
            plan = _mk_plan(self.brand)
            _mk_item(plan)
            res = self.client.get('/admin/api/content/verdict/history/')
            self.assertEqual(len(res.json()['data']['items']), 1)
        finally:
            other_brand.delete()
