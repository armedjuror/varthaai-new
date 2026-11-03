import json
from datetime import date
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import requests

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import AdminUser
from core.api import BRAND_SESSION_KEY
from core.models import Brand
from crm import geocoding
from crm import services as crm_services
from crm.models import B2BActivity, B2BCompany
from products.models import Flavor


class VisitLoggingTests(TestCase):
    """log_visit() must mirror a field visit as a B2BActivity with
    structured purpose/outcome, and nudge a still-untouched lead forward a
    stage — but never regress or skip past where an admin/order already put
    it, and never bump the very company the visit itself created."""

    def setUp(self):
        self.brand = Brand.objects.create(name='Varthaai Test')
        self.user = AdminUser.objects.create_user(
            username='saad', password='pw123456', name='Saad', role=AdminUser.Role.STAFF,
        )
        self.company = B2BCompany.objects.create(
            brand=self.brand, company_name='ABC Mart', stage=B2BCompany.Stage.LEAD,
        )

    def test_logs_structured_activity(self):
        activity = crm_services.log_visit(
            self.user, self.company, 'retarget', 'follow_up', notes='left pamphlet',
        )
        self.assertEqual(activity.type, B2BActivity.Type.VISIT)
        self.assertEqual(activity.visit_purpose, 'retarget')
        self.assertEqual(activity.visit_outcome, 'follow_up')
        self.assertEqual(activity.admin_user_id, self.user.id)
        self.assertIn('Retarget', activity.subject)
        self.assertEqual(activity.description, 'left pamphlet')

    def test_retarget_visit_bumps_lead_to_contacted(self):
        crm_services.log_visit(self.user, self.company, 'retarget', 'none')

        self.company.refresh_from_db()
        self.assertEqual(self.company.stage, B2BCompany.Stage.CONTACTED)
        stage_activity = B2BActivity.objects.filter(
            company=self.company, type=B2BActivity.Type.STAGE_CHANGE,
        ).first()
        self.assertIsNotNone(stage_activity)
        self.assertEqual(stage_activity.old_stage, 'lead')
        self.assertEqual(stage_activity.new_stage, 'contacted')

    def test_new_lead_visit_does_not_bump_its_own_company(self):
        crm_services.log_visit(self.user, self.company, 'new_lead', 'none')

        self.company.refresh_from_db()
        self.assertEqual(self.company.stage, B2BCompany.Stage.LEAD)
        self.assertFalse(
            B2BActivity.objects.filter(company=self.company, type=B2BActivity.Type.STAGE_CHANGE).exists(),
        )

    def test_visit_does_not_regress_a_later_stage(self):
        self.company.stage = B2BCompany.Stage.NEGOTIATION
        self.company.save(update_fields=['stage'])

        crm_services.log_visit(self.user, self.company, 'retarget', 'none')

        self.company.refresh_from_db()
        self.assertEqual(self.company.stage, B2BCompany.Stage.NEGOTIATION)


class VisitPackCountTests(TestCase):
    """Audit, collection and delivery visits record packs on the shelf per
    flavour as {"<flavour_id>": count}; other purposes never do."""

    def setUp(self):
        self.brand = Brand.objects.create(name='Varthaai Test')
        self.user = AdminUser.objects.create_user(
            username='saad', password='pw123456', name='Saad', role=AdminUser.Role.STAFF,
        )
        self.company = B2BCompany.objects.create(brand=self.brand, company_name='ABC Mart')
        self.classic = Flavor.objects.create(name='Classic')
        self.peri = Flavor.objects.create(name='Peri Peri')

    def test_reorder_push_is_no_longer_a_purpose(self):
        self.assertNotIn('reorder_push', B2BActivity.VisitPurpose.values)

    def test_counts_stored_per_flavour(self):
        for purpose in ('audit', 'collection', 'delivery'):
            activity = crm_services.log_visit(
                self.user, self.company, purpose, pack_counts={self.classic.id: '4', str(self.peri.id): 0},
            )
            activity.refresh_from_db()
            self.assertEqual(activity.pack_counts, {str(self.classic.id): 4, str(self.peri.id): 0})

    def test_counts_required_for_count_purposes(self):
        with self.assertRaisesMessage(ValidationError, 'packs on the shelf'):
            crm_services.log_visit(self.user, self.company, 'delivery')
        with self.assertRaisesMessage(ValidationError, 'whole number'):
            crm_services.log_visit(self.user, self.company, 'audit', pack_counts={self.classic.id: 'x'})
        self.assertFalse(B2BActivity.objects.filter(type=B2BActivity.Type.VISIT).exists())

    def test_counts_dropped_for_other_purposes(self):
        activity = crm_services.log_visit(
            self.user, self.company, 'retarget', pack_counts={self.classic.id: 4},
        )
        self.assertIsNone(activity.pack_counts)


class PaymentTermsTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(name='Varthaai Test')
        self.company = B2BCompany.objects.create(brand=self.brand, company_name='ABC Mart')

    def _terms(self, **kw):
        for k, v in kw.items():
            setattr(self.company, k, v)
        return self.company

    def test_defaults(self):
        c = self.company
        self.assertEqual((c.upfront_percent, c.upfront_due, c.balance_rule, c.payment_terms_days),
                         (0, 'delivery', 'bill_to_bill', 30))

    def test_due_dates(self):
        d = date(2026, 10, 15)
        due = crm_services.payment_due_date
        self.assertEqual(due(self._terms(balance_rule='bill_to_bill', payment_terms_days=30), d), date(2026, 11, 14))
        self.assertEqual(due(self._terms(balance_rule='after_days', payment_terms_days=10), d), date(2026, 10, 25))
        self.assertEqual(due(self._terms(balance_rule='weekly_as_sold'), d), date(2026, 10, 22))
        fixed = self._terms(balance_rule='fixed_dates', payment_fixed_dates=[1, 15])
        self.assertEqual(due(fixed, d), date(2026, 11, 1))
        self.assertEqual(due(fixed, date(2026, 10, 14)), date(2026, 10, 15))
        self.assertEqual(due(self._terms(payment_fixed_dates=[31]), date(2026, 11, 2)), date(2026, 11, 30))
        self.assertEqual(due(self._terms(payment_fixed_dates=[15]), date(2026, 12, 20)), date(2027, 1, 15))
        self.assertEqual(due(self._terms(upfront_percent=Decimal('100')), d), d)

    def test_labels(self):
        label = crm_services.payment_terms_label
        self.assertEqual(label(self._terms(upfront_percent=Decimal('50.00'), upfront_due='advance',
                                           balance_rule='after_days', payment_terms_days=30)),
                         '50% with the order, balance after 30 days')
        self.assertEqual(label(self._terms(upfront_percent=Decimal('0'), balance_rule='fixed_dates',
                                           payment_fixed_dates=[1, 15])),
                         'Balance on the 1, 15 of the month')
        self.assertEqual(label(self._terms(upfront_percent=Decimal('100'), upfront_due='delivery')), '100% on delivery')

    def test_clean_rejects_bad_input(self):
        clean = crm_services.clean_payment_terms
        with self.assertRaises(ValidationError):
            clean({'upfront_percent': '120'})
        with self.assertRaises(ValidationError):
            clean({'balance_rule': 'monthly'})
        with self.assertRaisesMessage(ValidationError, 'fixed dates'):
            clean({'balance_rule': 'fixed_dates', 'payment_fixed_dates': ''})
        with self.assertRaisesMessage(ValidationError, 'between 1 and 31'):
            clean({'balance_rule': 'fixed_dates', 'payment_fixed_dates': '0, 15'})
        self.assertEqual(
            clean({'upfront_percent': '25', 'upfront_due': 'advance', 'balance_rule': 'fixed_dates',
                   'payment_fixed_dates': '15, 1, 15', 'payment_terms_days': ''}),
            {'upfront_percent': Decimal('25'), 'upfront_due': 'advance', 'balance_rule': 'fixed_dates',
             'payment_terms_days': 30, 'payment_fixed_dates': [1, 15]},
        )


class CompanyApiTests(TestCase):
    """Location (coords preferred over a pasted link), payment terms and
    visit pack counts through the B2B API."""

    def setUp(self):
        self.brand = Brand.objects.create(name='Varthaai Test')
        AdminUser.objects.create_user(
            username='root', password='pw123456', name='Root', role=AdminUser.Role.SUPER_ADMIN,
        )
        self.client.login(username='root', password='pw123456')
        session = self.client.session
        session[BRAND_SESSION_KEY] = self.brand.id
        session.save()
        self.url = reverse('crm:b2b_api')
        self.flavor = Flavor.objects.create(name='Classic')

    def _post(self, data):
        return self.client.post(self.url, data=json.dumps(data), content_type='application/json').json()

    def _add(self, **extra):
        res = self._post({'action': 'add_company', 'company_name': 'Store', **extra})
        self.assertTrue(res['success'], res)
        return B2BCompany.objects.get(id=res['data']['id'])

    def test_coords_build_maps_link(self):
        c = self._add(latitude='9.9312328', longitude='76.2673041')
        self.assertEqual(c.latitude, Decimal('9.931233'))
        self.assertEqual(c.maps_url, 'https://www.google.com/maps/search/?api=1&query=9.931233,76.267304')
        detail = self.client.get(self.url, {'view': 'company', 'id': c.id}).json()['data']['company']
        self.assertEqual(detail['maps_url'], c.maps_url)

    def test_pasted_link_kept_without_coords(self):
        c = self._add(location_url='https://maps.app.goo.gl/abc')
        self.assertIsNone(c.latitude)
        self.assertEqual(c.location_url, 'https://maps.app.goo.gl/abc')
        self.assertEqual(c.maps_url, '')
        self.assertEqual(c.geo_status, B2BCompany.GeoStatus.PENDING)

    def test_browser_coords_are_manual_and_listed(self):
        c = self._add(latitude='12.9716', longitude='77.5946',
                      location_url='https://www.google.com/maps/search/?api=1&query=12.9716,77.5946')
        self.assertEqual(c.geo_status, B2BCompany.GeoStatus.MANUAL)
        rows = self.client.get(self.url, {'view': 'companies'}).json()['data']['companies']
        row = next(r for r in rows if r['id'] == c.id)
        self.assertEqual((row['latitude'], row['longitude']), (12.9716, 77.5946))
        self.assertEqual(row['maps_url'], 'https://www.google.com/maps/search/?api=1&query=12.971600,77.594600')

    def _edit(self, c, **extra):
        res = self._post({'action': 'edit_company', 'id': c.id, 'company_name': c.company_name, **extra})
        self.assertTrue(res['success'], res)
        c.refresh_from_db()
        return c

    def test_edit_geo_status(self):
        c = self._add(location_url='https://maps.app.goo.gl/abc')
        B2BCompany.objects.filter(id=c.id).update(
            latitude=Decimal('12.9716'), longitude=Decimal('77.5946'), geo_status='resolved',
        )
        c.refresh_from_db()
        # The form re-sends the saved coordinates -> status kept.
        c = self._edit(c, latitude='12.9716', longitude='77.5946', location_url=c.location_url)
        self.assertEqual(c.geo_status, 'resolved')
        # New coordinates from the browser -> manual.
        c = self._edit(c, latitude='12.98', longitude='77.6', location_url=c.location_url)
        self.assertEqual((c.geo_status, c.latitude), ('manual', Decimal('12.98')))
        # Cleared -> pending again, for the backfill.
        c = self._edit(c, latitude='', longitude='', location_url=c.location_url)
        self.assertEqual((c.geo_status, c.latitude), ('pending', None))

    def test_rejects_half_or_out_of_range_coords(self):
        self.assertFalse(self._post({'action': 'add_company', 'company_name': 'S', 'latitude': '9.9'})['success'])
        self.assertFalse(self._post({'action': 'add_company', 'company_name': 'S',
                                     'latitude': '99', 'longitude': '76'})['success'])

    def test_add_saves_payment_terms(self):
        c = self._add(upfront_percent='25', upfront_due='advance', balance_rule='fixed_dates',
                      payment_fixed_dates='1, 15', payment_terms_days='30')
        self.assertEqual((c.upfront_percent, c.upfront_due, c.balance_rule, c.payment_fixed_dates),
                         (Decimal('25'), 'advance', 'fixed_dates', [1, 15]))
        # Without terms in the request, a lead gets the defaults.
        d = self._add()
        self.assertEqual((d.upfront_percent, d.upfront_due, d.balance_rule, d.payment_terms_days),
                         (0, 'delivery', 'bill_to_bill', 30))
        res = self._post({'action': 'add_company', 'company_name': 'S', 'balance_rule': 'fixed_dates'})
        self.assertFalse(res['success'])

    def test_edit_saves_payment_terms(self):
        c = self._add()
        res = self._post({
            'action': 'edit_company', 'id': c.id, 'company_name': 'Store',
            'upfront_percent': '50', 'upfront_due': 'advance', 'balance_rule': 'after_days',
            'payment_terms_days': '15',
        })
        self.assertTrue(res['success'], res)
        c.refresh_from_db()
        self.assertEqual((c.upfront_percent, c.upfront_due, c.balance_rule, c.payment_terms_days),
                         (Decimal('50'), 'advance', 'after_days', 15))
        detail = self.client.get(self.url, {'view': 'company', 'id': c.id}).json()['data']['company']
        self.assertEqual(detail['payment_terms_label'], '50% with the order, balance after 15 days')

    def test_visit_pack_counts_via_api(self):
        c = self._add()
        res = self._post({'action': 'add_activity', 'company_id': c.id, 'type': 'visit',
                          'visit_purpose': 'delivery'})
        self.assertFalse(res['success'])
        res = self._post({'action': 'add_activity', 'company_id': c.id, 'type': 'visit',
                          'visit_purpose': 'delivery', 'pack_counts': {str(self.flavor.id): 2}})
        self.assertTrue(res['success'], res)
        acts = self.client.get(self.url, {'view': 'activities', 'id': c.id}).json()['data']
        visit = next(a for a in acts if a['type'] == 'visit')
        self.assertEqual(visit['pack_counts'], {str(self.flavor.id): 2})
        res = self._post({'action': 'add_activity', 'company_id': c.id, 'type': 'visit',
                          'visit_purpose': 'reorder_push'})
        self.assertFalse(res['success'])


class ParseCoordsTests(TestCase):
    def test_sample_urls(self):
        parse = geocoding.parse_coords
        D = Decimal
        place = ('https://www.google.com/maps/place/Fresh+Mart/@12.9600000,77.6400000,17z/'
                 'data=!3m1!4b1!4m6!3m5!1s0x0:0x0!8m2!3d12.9716123!4d77.5946456!16s')
        # !3d/!4d (the pin) wins over @ (the viewport centre).
        self.assertEqual(parse(place), (D('12.971612'), D('77.594646')))
        self.assertEqual(parse('https://www.google.com/maps/@12.935,77.624,15z'), (D('12.935'), D('77.624')))
        self.assertEqual(parse('https://maps.google.com/?q=12.9352,77.6245'), (D('12.9352'), D('77.6245')))
        self.assertEqual(parse('https://maps.google.com/maps?ll=12.9352,77.6245&z=16'), (D('12.9352'), D('77.6245')))
        self.assertEqual(
            parse('https://www.google.com/maps/search/?api=1&query=12.9352%2C77.6245'), (D('12.9352'), D('77.6245')),
        )
        consent = ('https://consent.google.com/m?continue=https://www.google.com/maps/place/X/'
                   'data%3D!3d13.0100000!4d77.5500000&gl=IN')
        self.assertEqual(parse(consent), (D('13.01'), D('77.55')))

    def test_rejects_outside_bengaluru_and_junk(self):
        parse = geocoding.parse_coords
        self.assertIsNone(parse('https://www.google.com/maps/@9.9312,76.2673,15z'))  # Kochi
        self.assertIsNone(parse('https://maps.app.goo.gl/AbCdEf123'))
        self.assertIsNone(parse(''))
        self.assertIsNone(parse(None))


def _resp(url='', status=200, history=(), payload=None):
    return SimpleNamespace(
        url=url, status_code=status, history=list(history), headers={},
        json=lambda: payload, text='',
    )


@patch('crm.geocoding.time.sleep')
@patch('crm.geocoding.requests.get')
class BackfillCommandTests(TestCase):
    PLACE = 'https://www.google.com/maps/place/Store/data=!3d12.9716!4d77.5946'

    def setUp(self):
        self.brand = Brand.objects.create(name='Varthaai Test')

    def _co(self, name, **kw):
        return B2BCompany.objects.create(brand=self.brand, company_name=name, **kw)

    def _run(self, *args):
        out = StringIO()
        call_command('backfill_company_coordinates', *args, stdout=out)
        return out.getvalue()

    def test_resolves_short_link_and_falls_back_to_nominatim(self, mock_get, mock_sleep):
        short = self._co('Short', location_url='https://maps.app.goo.gl/short1')
        by_addr = self._co('Addr', location_url='https://maps.app.goo.gl/dead', address='12 CMH Road',
                           city='Bengaluru', pincode='560038')
        lost = self._co('Lost', location_url='https://maps.app.goo.gl/lost')  # no address -> no fallback
        none = self._co('No link')
        manual = self._co('Manual', location_url='https://maps.app.goo.gl/m', latitude=Decimal('12.9'),
                          longitude=Decimal('77.6'), geo_status='manual')

        def fake_get(url, params=None, headers=None, **kw):
            if url == 'https://maps.app.goo.gl/short1':
                self.assertIn('Mozilla', headers['User-Agent'])
                return _resp(self.PLACE, history=[_resp(url, status=302)])
            if url == geocoding.NOMINATIM_URL:
                self.assertIn('560038', params['q'])
                self.assertIn('Varthaai', headers['User-Agent'])
                return _resp(url, payload=[{'lat': '12.9784', 'lon': '77.6408'}])
            return _resp('https://www.google.com/maps')  # no coordinates in it
        mock_get.side_effect = fake_get

        out = self._run()
        for c in (short, by_addr, lost, none, manual):
            c.refresh_from_db()
        self.assertEqual((short.geo_status, short.latitude, short.longitude),
                         ('resolved', Decimal('12.9716'), Decimal('77.5946')))
        self.assertEqual((by_addr.geo_status, by_addr.latitude), ('resolved', Decimal('12.9784')))
        self.assertEqual((lost.geo_status, lost.latitude), ('failed', None))
        self.assertEqual(none.geo_status, 'pending')
        self.assertEqual(manual.latitude, Decimal('12.9'))
        self.assertIn('Resolved: 2 | Failed: 1 | Skipped (no link): 1', out)
        self.assertIn(f'Failed company IDs: {lost.id}', out)
        # Throttled: Google calls 2s apart, so sleep was used.
        self.assertTrue(mock_sleep.called)

    def test_full_link_needs_no_request(self, mock_get, mock_sleep):
        c = self._co('Full', location_url=self.PLACE)
        self._run()
        c.refresh_from_db()
        self.assertEqual(c.geo_status, 'resolved')
        mock_get.assert_not_called()

    def test_retries_with_backoff_then_resolves(self, mock_get, mock_sleep):
        c = self._co('Flaky', location_url='https://maps.app.goo.gl/flaky')
        mock_get.side_effect = [requests.ConnectionError(), _resp(status=503), _resp(self.PLACE)]
        self._run()
        c.refresh_from_db()
        self.assertEqual(c.geo_status, 'resolved')
        self.assertEqual(mock_get.call_count, 3)
        waits = [call.args[0] for call in mock_sleep.call_args_list]
        self.assertIn(2.0, waits)
        self.assertIn(4.0, waits)

    def test_rejects_points_outside_bengaluru(self, mock_get, mock_sleep):
        c = self._co('Kochi', location_url='https://maps.app.goo.gl/kochi', address='MG Road', pincode='682016')
        mock_get.side_effect = [
            _resp('https://www.google.com/maps/place/X/data=!3d9.9312!4d76.2673'),
            _resp(geocoding.NOMINATIM_URL, payload=[{'lat': '9.9312', 'lon': '76.2673'}]),
        ]
        self._run()
        c.refresh_from_db()
        self.assertEqual((c.geo_status, c.latitude), ('failed', None))

    def test_dry_run_and_limit(self, mock_get, mock_sleep):
        a = self._co('A', location_url=self.PLACE)
        b = self._co('B', location_url=self.PLACE)
        out = self._run('--dry-run', '--limit', '1')
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((a.geo_status, a.latitude), ('pending', None))
        self.assertEqual(b.geo_status, 'pending')
        self.assertIn('[dry run] Resolved: 1 | Failed: 0', out)
        self.assertIn(f'#{a.id}', out)
        self.assertNotIn(f'#{b.id}', out)
