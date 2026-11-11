import csv
import io
import json

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AdminUser, User
from core.api import BRAND_SESSION_KEY
from core.models import Brand
from formbuilder import services, tokens
from formbuilder.models import Form, FormField, Submission
from formbuilder.services import FormError


def make_form(brand, **kw):
    kw.setdefault('title', 'Feedback')
    kw.setdefault('status', Form.LIVE)
    return Form.objects.create(brand=brand, **kw)


def add(form, label, ftype=FormField.TEXT, **kw):
    f = FormField(form=form, label=label, field_type=ftype, **kw)
    f.slug = services.make_slug(form, label)
    f.position = form.fields.count()
    f.save()
    return f


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.brand = Brand.objects.create(name='Test Brand')
        self.other_brand = Brand.objects.create(name='Other')
        self.admin = AdminUser.objects.create_superuser('root', 'pw')

    def login(self, user=None):
        self.client.force_login(user or self.admin)
        s = self.client.session
        s[BRAND_SESSION_KEY] = self.brand.id
        s.save()

    def post_json(self, url, data):
        return self.client.post(url, json.dumps(data), content_type='application/json')


class TokenTests(Base):
    def test_roundtrip_and_determinism(self):
        t1 = tokens.make_token('abc', {'store': 'Indiranagar'})
        assert t1 == tokens.make_token('abc', {'store': 'Indiranagar'})
        assert tokens.read_token('abc', t1) == {'store': 'Indiranagar'}

    def test_nothing_readable_in_token(self):
        t = tokens.make_token('abc', {'store': 'Indiranagar'})
        assert 'store' not in t and 'Indiranagar' not in t

    def test_wrong_form_tampered_and_garbage_are_rejected(self):
        t = tokens.make_token('abc', {'store': 'A'})
        assert tokens.read_token('other', t) is None
        flipped = t[:-2] + ('AA' if t[-2:] != 'AA' else 'BB')
        assert tokens.read_token('abc', flipped) is None
        for bad in ('', None, 'x', '!!!', 'A' * 700):
            assert tokens.read_token('abc', bad) is None


class FieldRuleTests(Base):
    def setUp(self):
        super().setUp()
        self.form = make_form(self.brand)

    def test_one_field_per_role_and_role_fixes_type(self):
        add(self.form, 'Phone', FormField.PHONE, role=FormField.ROLE_PHONE)
        dup = FormField(form=self.form, label='Mobile', field_type=FormField.PHONE, role=FormField.ROLE_PHONE)
        with self.assertRaises(FormError):
            services.validate_field_definition(self.form, dup)
        wrong = FormField(form=self.form, label='Who', field_type=FormField.NUMBER, role=FormField.ROLE_NAME)
        with self.assertRaises(FormError):
            services.validate_field_definition(self.form, wrong)

    def test_key_field_must_be_dropdown(self):
        text = add(self.form, 'Notes')
        with self.assertRaises(FormError):
            services.set_key_field(self.form, text)
        dd = add(self.form, 'Store', FormField.DROPDOWN, options=['A', 'B'])
        services.set_key_field(self.form, dd)
        self.form.refresh_from_db()
        assert self.form.key_field_id == dd.id

    def test_dropdown_needs_options_and_dedupes(self):
        assert services.clean_options(' A \nb\nB\n\n') == ['A', 'b']
        f = FormField(form=self.form, label='X', field_type=FormField.DROPDOWN, options=[])
        with self.assertRaises(FormError):
            services.validate_field_definition(self.form, f)

    def test_field_with_responses_is_archived_not_deleted(self):
        f = add(self.form, 'Comment')
        Submission.objects.create(form=self.form, answers={f.slug: 'hi'})
        assert services.remove_field(f) == 'archived'
        f.refresh_from_db()
        assert f.is_archived
        with self.assertRaises(FormError):
            services.check_type_change(f, FormField.NUMBER)
        g = add(self.form, 'Other')
        Submission.objects.all().delete()
        assert services.remove_field(g) == 'deleted'

    def test_removing_key_field_clears_key(self):
        dd = add(self.form, 'Store', FormField.DROPDOWN, options=['A'])
        services.set_key_field(self.form, dd)
        services.remove_field(dd)
        self.form.refresh_from_db()
        assert self.form.key_field_id is None


class ValueCleaningTests(Base):
    def f(self, ftype, **kw):
        return FormField(label='x', slug='x', field_type=ftype, **kw)

    def test_types(self):
        c = services.clean_value
        assert c(self.f('number'), '12.50') == (12.5, None)
        assert c(self.f('number'), '3') == (3, None)
        assert c(self.f('number'), 'nan')[1]
        assert c(self.f('email'), 'A@B.com') == ('a@b.com', None)
        assert c(self.f('email'), 'nope')[1]
        assert c(self.f('phone'), '+91 98765 43210') == ('9876543210', None)
        assert c(self.f('phone'), '12345')[1]
        assert c(self.f('dropdown', options=['A']), 'A') == ('A', None)
        assert c(self.f('dropdown', options=['A']), 'Z')[1]
        assert c(self.f('rating'), '5') == (5, None)
        assert c(self.f('rating'), '6')[1]
        assert c(self.f('date'), '2026-10-25') == ('2026-10-25', None)
        assert c(self.f('date'), '25/10/2026')[1]
        assert c(self.f('checkbox'), 'on') == (True, None)
        assert c(self.f('checkbox'), None) == (False, None)
        assert c(self.f('checkbox', required=True), None)[1]

    def test_required_and_optional_empty(self):
        assert services.clean_value(self.f('text', required=True), ' ')[1]
        assert services.clean_value(self.f('text'), ' ') == (None, None)


class SubmissionTests(Base):
    def setUp(self):
        super().setUp()
        self.form = make_form(self.brand)
        self.store = add(self.form, 'Store', FormField.DROPDOWN, options=['Indiranagar', 'Koramangala'], required=True)
        self.name = add(self.form, 'Name', FormField.TEXT, role=FormField.ROLE_NAME)
        self.phone = add(self.form, 'Phone', FormField.PHONE, role=FormField.ROLE_PHONE)
        self.email = add(self.form, 'Email', FormField.EMAIL, role=FormField.ROLE_EMAIL)
        self.liked = add(self.form, 'Liked it', FormField.RATING)
        services.set_key_field(self.form, self.store)
        self.url = reverse('form_public:public', args=[self.form.public_id])

    def link(self, value='Indiranagar'):
        return self.url + '?p=' + tokens.make_token(self.form.public_id, {self.store.slug: value})

    def data(self, **extra):
        d = {'name': 'Asha', 'phone': '98765 43210', 'email': 'asha@example.com', 'liked_it': '4'}
        d.update(extra)
        return d

    def test_prefilled_link_hides_key_and_overrides_posted_value(self):
        page = self.client.get(self.link('Koramangala')).content.decode()
        assert 'name="store"' not in page and 'Koramangala' not in page
        self.client.post(self.link('Koramangala'), self.data(store='Indiranagar'))
        sub = Submission.objects.get()
        assert sub.answers['store'] == 'Koramangala' and sub.via_link

    def test_general_url_shows_key_as_normal_dropdown(self):
        page = self.client.get(self.url).content.decode()
        assert 'name="store"' in page
        r = self.client.post(self.url, self.data())
        assert r.status_code == 400                       # store is required, nothing prefilled
        r = self.client.post(self.url, self.data(store='Indiranagar'))
        assert r.status_code == 302
        assert Submission.objects.get().via_link is False

    def test_bad_tokens_fall_back_to_general_form(self):
        for p in ('garbage', tokens.make_token('someotherform', {'store': 'Indiranagar'}),
                  tokens.make_token(self.form.public_id, {'store': 'Removed'}),
                  tokens.make_token(self.form.public_id, {'liked_it': '5'})):
            page = self.client.get(self.url + '?p=' + p).content.decode()
            assert 'name="store"' in page

    def test_token_for_removed_option_is_ignored_after_edit(self):
        token_url = self.link('Koramangala')
        self.store.options = ['Indiranagar']
        self.store.save()
        assert 'name="store"' in self.client.get(token_url).content.decode()

    def test_user_created_then_reused_by_phone(self):
        self.client.post(self.link(), self.data())
        self.client.post(self.link('Koramangala'), self.data(name='Someone else', email='new@example.com'))
        assert User.objects.count() == 1
        u = User.objects.get()
        assert (u.brand_id, u.mobile, u.name, u.email, u.is_lead) == (self.brand.id, '9876543210', 'Asha', 'asha@example.com', True)
        assert Submission.objects.filter(user=u).count() == 2

    def test_existing_customer_is_not_overwritten(self):
        User.objects.create(brand=self.brand, mobile='9876543210', name='Real Name')
        self.client.post(self.link(), self.data(name='Typo'))
        u = User.objects.get()
        assert u.name == 'Real Name' and u.email == 'asha@example.com'
        assert u.is_lead is False

    def test_match_by_email_when_no_phone(self):
        User.objects.create(brand=self.brand, email='asha@example.com', name='Asha')
        self.client.post(self.link(), self.data(phone=''))
        assert User.objects.count() == 1

    def test_same_phone_in_other_brand_is_a_different_user(self):
        User.objects.create(brand=self.other_brand, mobile='9876543210', name='Elsewhere')
        self.client.post(self.link(), self.data())
        assert User.objects.count() == 2

    def test_create_users_off(self):
        self.form.create_users = False
        self.form.save()
        self.client.post(self.link(), self.data())
        assert User.objects.count() == 0 and Submission.objects.count() == 1

    def test_one_response_per_phone(self):
        self.form.one_response_per_phone = True
        self.form.save()
        assert self.client.post(self.link(), self.data()).status_code == 302
        r = self.client.post(self.link(), self.data(name='Again'))
        assert r.status_code == 400 and 'already responded' in r.content.decode()
        assert Submission.objects.count() == 1

    def test_validation_errors_keep_input_and_store_nothing(self):
        r = self.client.post(self.link(), self.data(email='bad', phone='1'))
        body = r.content.decode()
        assert r.status_code == 400 and 'valid email' in body and 'Asha' in body
        assert Submission.objects.count() == 0 and User.objects.count() == 0

    def test_honeypot_stores_nothing(self):
        r = self.client.post(self.link(), self.data(website='http://spam'))
        assert r.status_code == 302 and Submission.objects.count() == 0

    def test_rate_limit(self):
        from formbuilder.views_public import POST_LIMIT
        for _ in range(POST_LIMIT):
            self.client.post(self.link(), self.data(phone='1'))
        assert self.client.post(self.link(), self.data()).status_code == 429

    def test_draft_404_closed_410(self):
        self.form.status = Form.DRAFT
        self.form.save()
        assert self.client.get(self.url).status_code == 404
        self.form.status = Form.CLOSED
        self.form.save()
        assert self.client.get(self.url).status_code == 410
        assert self.client.post(self.url, self.data()).status_code == 410
        assert self.client.get('/f/doesnotexist/').status_code == 404


class ThankYouTests(Base):
    def setUp(self):
        super().setUp()
        self.form = make_form(self.brand, success_title='Done!', success_message='See you <b>soon</b>\nbye')
        add(self.form, 'Comment')
        self.url = reverse('form_public:public', args=[self.form.public_id])

    def test_configurable_message_is_escaped_and_no_code_by_default(self):
        r = self.client.post(self.url, {}, follow=True)
        body = r.content.decode()
        assert 'Done!' in body and '&lt;b&gt;soon' in body and '<b>soon' not in body
        assert Submission.objects.get().code == ''
        assert 'Your code' not in body

    def test_unique_code(self):
        self.form.show_code, self.form.code_length, self.form.code_prefix = True, 8, 'VT-'
        self.form.code_label = 'Redeem code'
        self.form.save()
        seen = set()
        for _ in range(20):
            c = type(self.client)()
            r = c.post(self.url, {}, follow=True)
            sub = Submission.objects.latest('id')
            assert sub.code.startswith('VT-') and len(sub.code) == 11
            assert sub.code in r.content.decode() and 'Redeem code' in r.content.decode()
            seen.add(sub.code)
        assert len(seen) == 20

    def test_thanks_without_submitting_redirects_to_form(self):
        r = self.client.get(reverse('form_public:thanks', args=[self.form.public_id]))
        assert r.status_code == 302 and r.url == self.url

    def test_redirect_url_used_when_no_code(self):
        self.form.success_redirect_url = 'https://example.com/next'
        self.form.save()
        r = self.client.post(self.url, {})
        assert r.status_code == 302 and r.url == 'https://example.com/next'
        self.form.show_code = True
        self.form.save()
        r = self.client.post(self.url, {})
        assert r.url.endswith('/thanks/')

    def test_code_collision_raises_after_retries(self):
        self.form.show_code, self.form.code_length = True, 4
        self.form.save()
        from unittest import mock
        Submission.objects.create(form=self.form, code='AAAA')
        with mock.patch('formbuilder.services.secrets.choice', return_value='A'):
            with self.assertRaises(FormError):
                services.generate_code(self.form)


class AdminAPITests(Base):
    def setUp(self):
        super().setUp()
        self.login()

    def test_create_configure_and_links_flow(self):
        r = self.post_json('/admin/api/forms/', {'action': 'create', 'title': 'Sampling'}).json()
        assert r['success']
        api = f"/admin/api/forms/{r['data']['id']}/"
        for role in ('user_name', 'user_phone', 'user_email'):
            assert self.post_json(api, {'action': 'add_user_field', 'role': role}).json()['success']
        assert not self.post_json(api, {'action': 'add_user_field', 'role': 'user_phone'}).json()['success']
        f = self.post_json(api, {'action': 'add_field', 'label': 'Store', 'field_type': 'dropdown',
                                 'options': ['A b', 'C'], 'required': True}).json()
        assert f['success'] and f['data']['slug'] == 'store'
        assert self.post_json(api, {'action': 'set_key_field', 'field_id': f['data']['id']}).json()['success']
        links = self.client.get(api + 'links/').json()['data']
        assert links['key_label'] == 'Store' and [l['value'] for l in links['links']] == ['A b', 'C']
        assert all('store' not in l['url'] and 'A%20b' not in l['url'] for l in links['links'])
        form = Form.objects.get()
        assert services.resolve_prefill(form, links['links'][0]['url'].split('p=')[1]) == {'store': 'A b'}

    def test_bad_input_returns_400_envelope(self):
        form = make_form(self.brand)
        api = f'/admin/api/forms/{form.id}/'
        r = self.post_json(api, {'action': 'add_field', 'label': 'X', 'field_type': 'dropdown', 'options': []})
        assert r.status_code == 400 and not r.json()['success']
        r = self.post_json(api, {'action': 'update_settings', 'success_redirect_url': 'javascript:alert(1)'})
        assert not r.json()['success']
        r = self.post_json(api, {'action': 'update_settings', 'code_length': 99})
        assert not r.json()['success']

    def test_brand_isolation_and_permission(self):
        mine = make_form(self.brand)
        theirs = make_form(self.other_brand)
        assert self.client.get(f'/admin/api/forms/{theirs.id}/').status_code == 404
        assert [f['id'] for f in self.client.get('/admin/api/forms/').json()['data']['forms']] == [mine.id]
        staff = AdminUser.objects.create_user('staff', 'pw', brand_permissions={str(self.brand.id): ['orders']})
        self.login(staff)
        assert self.client.get('/admin/api/forms/').status_code == 403
        assert self.client.get('/admin/forms/').status_code == 403
        self.client.logout()
        assert self.client.get('/admin/api/forms/').status_code == 401

    def test_pages_render(self):
        form = make_form(self.brand)
        assert self.client.get('/admin/forms/').status_code == 200
        assert self.client.get(f'/admin/forms/{form.id}/').status_code == 200

    def test_field_type_locked_once_responses_exist(self):
        form = make_form(self.brand)
        f = add(form, 'Notes')
        Submission.objects.create(form=form, answers={})
        r = self.post_json(f'/admin/api/forms/{form.id}/', {'action': 'update_field', 'field_id': f.id, 'field_type': 'number'})
        assert not r.json()['success']
        r = self.post_json(f'/admin/api/forms/{form.id}/', {'action': 'update_field', 'field_id': f.id, 'label': 'Renamed'})
        assert r.json()['success']


class ResultsTests(Base):
    def setUp(self):
        super().setUp()
        self.login()
        self.form = make_form(self.brand, show_code=True)
        self.store = add(self.form, 'Store', FormField.DROPDOWN, options=['A', 'B'])
        self.name = add(self.form, 'Name')
        self.age = add(self.form, 'Age', FormField.NUMBER)
        self.ok = add(self.form, 'Would buy', FormField.CHECKBOX)
        self.stars = add(self.form, 'Stars', FormField.RATING)
        for store, name, age, ok, stars in [
            ('A', 'Asha', 25, True, 5), ('A', 'Ravi', 40, False, 2), ('B', 'Meera', 33, True, 4)]:
            Submission.objects.create(form=self.form, code=f'C{age}', answers={
                'store': store, 'name': name, 'age': age, 'would_buy': ok, 'stars': stars})
        self.api = f'/admin/api/forms/{self.form.id}/results/'

    def names(self, **params):
        rows = self.client.get(self.api, params).json()['data']['rows']
        return sorted(r['answers']['name'] for r in rows)

    def test_filters(self):
        assert self.names() == ['Asha', 'Meera', 'Ravi']
        assert self.names(**{'f.store': 'A'}) == ['Asha', 'Ravi']
        assert self.names(**{'f.store': 'A,B'}) == ['Asha', 'Meera', 'Ravi']
        assert self.names(**{'f.name': 'sh'}) == ['Asha']
        assert self.names(**{'f.age.min': '30'}) == ['Meera', 'Ravi']
        assert self.names(**{'f.age.min': '26', 'f.age.max': '35'}) == ['Meera']
        assert self.names(**{'f.would_buy': 'yes'}) == ['Asha', 'Meera']
        assert self.names(**{'f.would_buy': 'no'}) == ['Ravi']
        assert self.names(**{'f.stars': '4'}) == ['Meera']
        assert self.names(**{'f.stars.min': '4'}) == ['Asha', 'Meera']
        assert self.names(**{'f.store': 'A', 'f.age.min': '30'}) == ['Ravi']
        assert self.names(**{'f.age.min': 'abc'}) == ['Asha', 'Meera', 'Ravi']   # ignored, not a crash

    def test_search_matches_text_dropdown_and_code(self):
        assert self.names(search='meer') == ['Meera']
        assert self.names(search='C40') == ['Ravi']
        assert self.names(search='B') == ['Meera']

    def test_hostile_param_names_are_harmless(self):
        assert len(self.names(**{'f.x__y': '1', 'f.name"; drop': '1', "f.'": '1'})) == 3

    def test_pagination(self):
        d = self.client.get(self.api, {'per_page': 10}).json()['data']
        assert d['total'] == 3 and d['pages'] == 1
        assert [f['slug'] for f in d['fields']] == ['store', 'name', 'age', 'would_buy', 'stars']

    def test_csv_export_respects_filters_and_escapes_formulas(self):
        Submission.objects.create(form=self.form, code='X1', answers={
            'store': 'B', 'name': '=HYPERLINK("http://evil")', 'age': 50, 'would_buy': True, 'stars': 1})
        r = self.client.get(f'/admin/forms/{self.form.id}/export.csv', {'f.store': 'B'})
        assert r.status_code == 200 and r['Content-Type'].startswith('text/csv')
        rows = list(csv.reader(io.StringIO(b''.join(r.streaming_content).decode())))
        assert rows[0] == ['Submitted at', 'Code', 'Via link', 'Store', 'Name', 'Age', 'Would buy', 'Stars']
        assert len(rows) == 3
        assert any(row[4] == '\'=HYPERLINK("http://evil")' for row in rows)
        assert any(row[6] == 'Yes' for row in rows[1:])

    def test_archived_field_data_kept_in_export(self):
        services.remove_field(self.age)
        r = self.client.get(f'/admin/forms/{self.form.id}/export.csv')
        header = next(csv.reader(io.StringIO(b''.join(r.streaming_content).decode())))
        assert 'Age (archived)' in header


class QRTests(Base):
    def setUp(self):
        super().setUp()
        self.login()
        self.form = make_form(self.brand)
        self.store = add(self.form, 'Store', FormField.DROPDOWN, options=['A', 'B', 'C'])

    def test_qr_pdf(self):
        services.set_key_field(self.form, self.store)
        r = self.client.get(f'/admin/forms/{self.form.id}/qr.pdf', {'options': json.dumps(['A', 'C']), 'per_page': 4})
        if r.status_code == 503:
            self.skipTest('PDF engine (Pango) not installed')
        assert r.status_code == 200 and r.content.startswith(b'%PDF')

    def test_qr_svg_is_inline_and_scannable_size(self):
        from formbuilder.pdf import qr_svg
        svg = qr_svg('https://varthaai.com/f/abc/?p=xyz')
        assert svg.startswith('<svg') and 'http' not in svg.split('>')[0]

    def test_qr_without_key_field_is_a_400(self):
        r = self.client.get(f'/admin/forms/{self.form.id}/qr.pdf')
        assert r.status_code in (400, 503)


# ── download on success + per-brand branding ────────────────────────────

import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from formbuilder.models import FormBranding

_PRIVATE = tempfile.mkdtemp(prefix='fb-private-')
_MEDIA = tempfile.mkdtemp(prefix='fb-media-')


@override_settings(PRIVATE_MEDIA_ROOT=_PRIVATE, MEDIA_ROOT=_MEDIA)
class DownloadTests(Base):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_PRIVATE, ignore_errors=True)
        shutil.rmtree(_MEDIA, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self.form = make_form(self.brand, title='Free guide')
        add(self.form, 'Your name', role=FormField.ROLE_NAME, required=True)
        add(self.form, 'Phone', FormField.PHONE, role=FormField.ROLE_PHONE, required=True)
        services.set_download(self.form, SimpleUploadedFile('guide.pdf', b'%PDF-1.4 hello'))

    def submit(self):
        return self.client.post(f'/f/{self.form.public_id}/', {'your_name': 'Asha', 'phone': '9876543210'})

    def thanks_download_url(self):
        r = self.client.get(f'/f/{self.form.public_id}/thanks/')
        assert r.status_code == 200
        url = r.context['download_url']
        assert url
        return url.split('testserver', 1)[1]

    def test_file_is_private_and_unlocks_after_submit(self):
        assert self.form.download_file.name.startswith('form_downloads/')
        assert self.form.download_file.path.startswith(_PRIVATE)
        r = self.submit()
        assert r.status_code == 302
        url = self.thanks_download_url()
        r = self.client.get(url)
        assert r.status_code == 200
        assert b''.join(r.streaming_content) == b'%PDF-1.4 hello'
        assert 'inline' in r['Content-Disposition'] and 'guide.pdf' in r['Content-Disposition']
        assert r['Cache-Control'] == 'private, no-store'
        sub = self.form.submissions.get()
        assert sub.download_count == 1 and sub.last_downloaded_at

    def test_no_link_without_submitting(self):
        r = self.client.get(f'/f/{self.form.public_id}/download/')
        assert r.status_code == 410
        r = self.client.get(f'/f/{self.form.public_id}/download/?t=garbage')
        assert r.status_code == 410

    def test_link_works_in_another_browser_until_it_expires(self):
        self.submit()
        url = self.thanks_download_url()
        self.client.logout()                 # fresh session, like opening it in Chrome
        assert self.client.get(url).status_code == 200
        later = timezone.now() + timedelta(hours=self.form.download_hours, minutes=1)
        with mock.patch('django.core.signing.time.time', return_value=later.timestamp()):
            assert self.client.get(url).status_code == 410

    def test_token_bound_to_form(self):
        self.submit()
        url = self.thanks_download_url()
        other = make_form(self.brand, title='Other')
        services.set_download(other, SimpleUploadedFile('x.pdf', b'x'))
        token = url.split('?t=')[1]
        assert self.client.get(f'/f/{other.public_id}/download/?t={token}').status_code == 410

    def test_honeypot_gets_no_download(self):
        self.client.post(f'/f/{self.form.public_id}/', {'your_name': 'Bot', 'phone': '9876543210', 'website': 'x'})
        r = self.client.get(f'/f/{self.form.public_id}/thanks/')
        assert r.status_code == 302              # no session entry, back to the form
        assert not self.form.submissions.exists()

    def test_redirect_url_does_not_skip_the_download(self):
        self.form.success_redirect_url = 'https://example.com/'
        self.form.save()
        r = self.submit()
        assert r['Location'].endswith('/thanks/')

    def test_non_pdf_is_an_attachment(self):
        services.set_download(self.form, SimpleUploadedFile('pack.zip', b'PK..'))
        self.submit()
        r = self.client.get(self.thanks_download_url())
        assert r['Content-Disposition'].startswith('attachment')

    def test_upload_rules_and_replace_removes_old_file(self):
        import os
        old_path = self.form.download_file.path
        with self.assertRaises(FormError):
            services.set_download(self.form, SimpleUploadedFile('evil.exe', b'MZ'))
        with self.assertRaises(FormError):
            services.set_download(self.form, SimpleUploadedFile('empty.pdf', b''))
        services.set_download(self.form, SimpleUploadedFile('v2.pdf', b'%PDF v2'))
        assert not os.path.exists(old_path)
        assert self.form.download_name == 'v2.pdf'

    def test_deleting_form_deletes_file(self):
        import os
        path = self.form.download_file.path
        self.form.delete()
        assert not os.path.exists(path)

    def test_admin_upload_settings_and_remove(self):
        self.login()
        api = reverse('formbuilder:form_api', args=[self.form.pk])
        r = self.client.post(api, {'action': 'upload_download',
                                   'file': SimpleUploadedFile('menu.pdf', b'%PDF menu')})
        assert r.json()['success'], r.json()
        assert r.json()['data']['download_name'] == 'menu.pdf'
        r = self.post_json(api, {'action': 'update_settings', 'download_label': 'Get the menu', 'download_hours': 5})
        assert r.json()['success']
        self.form.refresh_from_db()
        assert (self.form.download_label, self.form.download_hours) == ('Get the menu', 5)
        r = self.post_json(api, {'action': 'update_settings', 'download_hours': 0})
        assert not r.json()['success']
        r = self.post_json(api, {'action': 'remove_download'})
        assert r.json()['success'] and r.json()['data']['download_name'] == ''

    def test_admin_cannot_touch_other_brands_form(self):
        self.login()
        foreign = make_form(self.other_brand)
        r = self.client.post(reverse('formbuilder:form_api', args=[foreign.pk]),
                             {'action': 'upload_download', 'file': SimpleUploadedFile('a.pdf', b'x')})
        assert r.status_code == 404


@override_settings(MEDIA_ROOT=_MEDIA)
class BrandingTests(Base):
    def test_branding_is_per_brand_and_themes_public_pages(self):
        self.login()
        r = self.client.post(reverse('formbuilder:branding_api'), {
            'primary_color': '#1a2b3c', 'accent_color': '#FF6600', 'background_color': '',
            'footer_text': 'Byte After Eight', 'default_download_hours': '48'})
        assert r.json()['success'], r.json()
        b = FormBranding.objects.get(brand=self.brand)
        assert b.primary_color == '#1A2B3C' and b.default_download_hours == 48

        form = make_form(self.brand)
        html = self.client.get(f'/f/{form.public_id}/').content.decode()
        assert '--leaf:#1A2B3C' in html and '--chip:#FF6600' in html and 'Byte After Eight' in html

        other = make_form(self.other_brand)
        html = self.client.get(f'/f/{other.public_id}/').content.decode()
        assert '--leaf:#1A2B3C' not in html

    def test_bad_colour_rejected(self):
        self.login()
        r = self.client.post(reverse('formbuilder:branding_api'), {'primary_color': 'red;}</style><script>'})
        assert not r.json()['success']

    def test_new_form_takes_brand_default_hours(self):
        FormBranding.objects.create(brand=self.brand, default_download_hours=12)
        self.login()
        r = self.post_json(reverse('formbuilder:forms_api'), {'action': 'create', 'title': 'Guide'})
        assert Form.objects.get(pk=r.json()['data']['id']).download_hours == 12
