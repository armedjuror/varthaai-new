import json
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import AdminUser
from core.api import BRAND_SESSION_KEY
from core.models import Brand
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

    def test_pasted_link_is_fallback(self):
        c = self._add(location_url='https://maps.app.goo.gl/abc')
        self.assertIsNone(c.latitude)
        self.assertEqual(c.maps_url, 'https://maps.app.goo.gl/abc')

    def test_rejects_half_or_out_of_range_coords(self):
        self.assertFalse(self._post({'action': 'add_company', 'company_name': 'S', 'latitude': '9.9'})['success'])
        self.assertFalse(self._post({'action': 'add_company', 'company_name': 'S',
                                     'latitude': '99', 'longitude': '76'})['success'])

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
