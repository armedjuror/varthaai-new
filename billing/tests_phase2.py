"""
Phase 2 tests: share links, paying from the link, WhatsApp buttons, B2C GST
on top + MRP guard, refunds, GSTR-1 / registers, month close, accounting
net of GST, input tax credit, and the database immutability trigger.
"""
import hashlib
import hmac
import io
import json
import zipfile
from datetime import timedelta
from decimal import Decimal as D
from unittest import mock

from django.db import connection, transaction
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AdminUser
from billing import links, refunds, reports
from billing import services as billing
from billing.models import CreditNote, Invoice, MessageLog, PaymentAttempt, Refund, ShareLink
from billing.tests import MEDIA, login, make_gstin, seed_gst
from core.models import Setting
from crm.models import B2BActivity, B2BCompany, B2BContact
from finance.models import Expense, ExpenseCategory, Income
from orders.models import B2BOrder, B2BOrderItem, B2BPayment, Order, OrderItem

RZP = dict(RAZORPAY_KEY_ID='rzp_test_key', RAZORPAY_KEY_SECRET='secret', RAZORPAY_WEBHOOK_SECRET='whsec')


class FakeRazorpay:
    """Stand-in for razorpay.Client: orders, refunds, signature check."""
    counter = 0

    def __init__(self, refund_status='processed', signature_ok=True):
        test = self

        class _Order:
            def create(self, data):
                FakeRazorpay.counter += 1
                test.last_order = data
                return {'id': f'order_fake{FakeRazorpay.counter}'}

        class _Payment:
            def refund(self, payment_id, data):
                FakeRazorpay.counter += 1
                test.last_refund = (payment_id, data)
                return {'id': f'rfnd_fake{FakeRazorpay.counter}', 'status': refund_status}

        class _Util:
            def verify_payment_signature(self, data):
                if not signature_ok:
                    import razorpay
                    raise razorpay.errors.SignatureVerificationError('bad')
                return True

        self.order = _Order()
        self.payment = _Payment()
        self.utility = _Util()


def patch_rzp(fake):
    return mock.patch('razorpay.Client', return_value=fake)


@override_settings(MEDIA_ROOT=MEDIA, PRIVATE_MEDIA_ROOT=MEDIA, **RZP)
class Phase2Base(TestCase):
    def setUp(self):
        seed_gst(self)
        login(self)
        self.company = B2BCompany.objects.create(
            brand=self.brand, company_name='Fresh Mart', stage='converted',
            gst_number=make_gstin('29'), state_code='29', address='Bengaluru', city='Bengaluru',
        )
        self.contact = B2BContact.objects.create(company=self.company, name='Ravi', phone='98765 00000',
                                                 is_primary=True)

    def b2c(self, oid='VO_p1', state='32', paid=False, mode='inclusive', grams=1000, delivery=0):
        o = Order.objects.create(
            id=oid, brand=self.brand, name='Asha', mobile='9876543210', address='MG Road', pincode='673001',
            shipping_state_code=state, delivery_charge=delivery, price_mode=mode,
            payment_status='paid' if paid else 'pending', payment_date=timezone.now() if paid else None,
            razorpay_payment_id='pay_orig' if paid else '',
        )
        OrderItem.objects.create(order=o, flavor=self.flavor, quantity=grams, price_per_kg=1200,
                                 sale_price_per_kg=1000, flavor_name='Classic')
        billing.refresh_b2c_gst(o)
        o.refresh_from_db()
        return o

    def b2b(self, oid='VOB_p1', total='1050.00'):
        o = B2BOrder.objects.create(
            id=oid, brand=self.brand, company=self.company, contact=self.contact, status='confirmed',
            subtotal=D('1000'), total_amount=D(total), gst_amount=D('50'), price_mode='exclusive',
            created_by=self.admin, due_date=timezone.localdate() - timedelta(days=3),
        )
        B2BOrderItem.objects.create(b2b_order=o, flavor=self.flavor, quantity=10, weight_grams=250,
                                    total_weight_grams=2500, selling_price=D('100'), flavor_name='Classic')
        return o


class ShareLinkTests(Phase2Base):
    def test_page_404s_and_headers(self):
        o = self.b2c()
        link = links.get_or_create_link(o, self.admin)
        self.client.logout()
        res = self.client.get(f'/o/{link.token}/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, '••••••3210')
        self.assertNotContains(res, '9876543210')
        self.assertNotContains(res, 'MG Road')
        self.assertEqual(res['X-Robots-Tag'], 'noindex, nofollow')
        self.assertEqual(res['Referrer-Policy'], 'no-referrer')
        self.assertIn('no-store', res['Cache-Control'])
        self.assertContains(res, 'Pay ₹1,000.00')
        self.assertEqual(ShareLink.objects.get().view_count, 1)
        self.assertEqual(self.client.get('/o/not-a-real-token/').status_code, 404)
        new = links.regenerate_link(o, self.admin)
        self.assertEqual(self.client.get(f'/o/{link.token}/').status_code, 404)
        self.assertEqual(self.client.get(f'/o/{new.token}/').status_code, 200)
        self.assertEqual(links.get_or_create_link(o).pk, new.pk)

    def test_expires_60_days_after_paid(self):
        o = self.b2c(paid=True)
        link = links.get_or_create_link(o)
        self.assertIsNotNone(links.resolve(link.token))
        Order.objects.filter(pk=o.pk).update(payment_date=timezone.now() - timedelta(days=61))
        self.assertIsNone(links.resolve(link.token))

    def test_paid_order_shows_seal_and_invoice_download(self):
        o = self.b2c(paid=True)
        billing.ensure_invoice(o)
        link = links.get_or_create_link(o)
        res = self.client.get(f'/o/{link.token}/')
        self.assertContains(res, 'PAID')
        self.assertNotContains(res, 'id="payBtn"')
        self.assertContains(res, f'/o/{link.token}/invoice.pdf')
        pdf = self.client.get(f'/o/{link.token}/invoice.pdf')
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(self.client.get(f'/o/{link.token}/summary.pdf').status_code, 200)

    def test_credit_note_lowers_amount_asked(self):
        o = self.b2c()
        inv = billing.ensure_invoice(o)
        goods = inv.lines.get(kind='GOODS')
        billing.b2c_credit_note(o, [(goods, 400, False)], 'sales_return', self.admin)
        self.assertEqual(links.amount_due(o), D('600.00'))


class LinkPaymentTests(Phase2Base):
    def _start(self, link, fake):
        with patch_rzp(fake):
            res = self.client.post(f'/o/{link.token}/pay/')
        return res.json()

    def _verify(self, link, rzp_order, payment_id='pay_123', fake=None):
        with patch_rzp(fake or FakeRazorpay()):
            return self.client.post(
                f'/o/{link.token}/pay/verify/',
                data=json.dumps({'razorpay_order_id': rzp_order, 'razorpay_payment_id': payment_id,
                                 'razorpay_signature': 'sig'}), content_type='application/json',
            ).json()

    def _webhook(self, rzp_order, payment_id='pay_123'):
        body = json.dumps({'event': 'payment.captured', 'payload': {
            'payment': {'entity': {'id': payment_id, 'order_id': rzp_order}}}}).encode()
        sig = hmac.new(b'whsec', body, hashlib.sha256).hexdigest()
        return self.client.post('/api/razorpay-webhook/', data=body, content_type='application/json',
                                HTTP_X_RAZORPAY_SIGNATURE=sig)

    def test_b2b_pay_from_link_is_recorded_once(self):
        o = self.b2b()
        link = links.get_or_create_link(o)
        fake = FakeRazorpay()
        res = self._start(link, fake)
        self.assertTrue(res['success'], res)
        self.assertEqual(res['amount'], 105000)
        self.assertEqual(fake.last_order['amount'], 105000)
        rzp_order = res['razorpay_order_id']
        self.assertTrue(self._verify(link, rzp_order)['success'])
        self.assertTrue(self._verify(link, rzp_order)['success'])
        self.assertEqual(self._webhook(rzp_order).status_code, 200)
        payment = B2BPayment.objects.get()
        self.assertEqual((payment.payment_method, payment.amount, payment.reference_number),
                         ('online', D('1050.00'), 'pay_123'))
        o.refresh_from_db()
        self.assertEqual(o.payment_status, 'paid')
        self.assertEqual(PaymentAttempt.objects.get().status, 'paid')
        # Nothing left to pay.
        res = self._start(link, FakeRazorpay())
        self.assertFalse(res['success'])

    def test_webhook_before_return_and_b2c(self):
        o = self.b2c()
        link = links.get_or_create_link(o)
        rzp_order = self._start(link, FakeRazorpay())['razorpay_order_id']
        self._webhook(rzp_order, 'pay_w')
        self._verify(link, rzp_order, 'pay_w')
        o.refresh_from_db()
        self.assertEqual((o.payment_status, o.status, o.razorpay_payment_id), ('paid', 'confirmed', 'pay_w'))
        self.assertEqual(Income.objects.filter(order=o).count(), 1)

    def test_bad_signature_and_foreign_order_rejected(self):
        o = self.b2c()
        link = links.get_or_create_link(o)
        rzp_order = self._start(link, FakeRazorpay())['razorpay_order_id']
        self.assertFalse(self._verify(link, rzp_order, fake=FakeRazorpay(signature_ok=False))['success'])
        other = links.get_or_create_link(self.b2b())
        self.assertFalse(self._verify(other, rzp_order)['success'])
        o.refresh_from_db()
        self.assertEqual(o.payment_status, 'pending')

    def test_partial_manual_payment_lowers_link_amount(self):
        o = self.b2b()
        self.client.post('/admin/api/b2b-orders/', {'action': 'add_payment', 'company_id': self.company.id,
                                                    'amount': 500, 'payment_method': 'cash', 'order_id': o.id},
                         content_type='application/json')
        link = links.get_or_create_link(o)
        self.assertEqual(self._start(link, FakeRazorpay())['amount'], 55000)


class WhatsAppTests(Phase2Base):
    def test_b2b_reminder_builds_link_and_logs(self):
        o = self.b2b()
        res = self.client.post('/admin/api/b2b-orders/', {'action': 'whatsapp', 'order_id': o.id,
                                                          'kind': 'reminder'}, content_type='application/json')
        data = res.json()['data']
        self.assertTrue(data['url'].startswith('https://wa.me/919876500000?text='))
        self.assertIn('%E2%82%B91%2C050.00', data['url'])  # ₹1,050.00
        self.assertIn('/o/', data['message'])
        self.assertEqual(ShareLink.objects.count(), 1)
        self.client.post('/admin/api/b2b-orders/', {'action': 'whatsapp', 'order_id': o.id, 'kind': 'delivery'},
                         content_type='application/json')
        self.assertEqual(ShareLink.objects.count(), 1)
        self.assertEqual(MessageLog.objects.count(), 2)
        self.assertEqual(B2BActivity.objects.filter(type='whatsapp').count(), 2)
        dash = self.client.get('/admin/api/b2b-dashboard/').json()['data']
        self.assertIsNotNone(dash['overdue_payments'][0]['last_reminded'])

    def test_no_valid_number_is_refused(self):
        self.contact.phone = '123'
        self.contact.save()
        o = self.b2b()
        res = self.client.post('/admin/api/b2b-orders/', {'action': 'whatsapp', 'order_id': o.id,
                                                          'kind': 'delivery'}, content_type='application/json')
        self.assertFalse(res.json()['success'])
        payload = links.share_payload(o)
        self.assertIsNone(payload['whatsapp_number'])
        self.assertTrue(payload['whatsapp_disabled_reason'])

    def test_custom_message_text(self):
        Setting.objects.create(setting_key='wa_message_delivery', setting_value='Hello {name}: {link} ({unknown})')
        o = self.b2c()
        data = links.prepare_whatsapp(o, 'delivery', self.admin)
        self.assertTrue(data['message'].startswith('Hello Asha: http'))
        self.assertIn('{unknown}', data['message'])


class B2CExclusiveTests(Phase2Base):
    def setUp(self):
        super().setUp()
        override = self.settings(STOREFRONT_BRAND_ID=self.brand.id)
        override.enable()
        self.addCleanup(override.disable)
        Setting.objects.filter(setting_key='gst_price_mode_b2c').update(setting_value='exclusive')

    def test_checkout_charges_gst_on_top_and_invoice_matches(self):
        fake = FakeRazorpay()
        with patch_rzp(fake):
            res = self.client.post('/api/place-order/', data=json.dumps({
                'name': 'Asha', 'mobile': '9876543210', 'address': 'MG Road', 'pincode': '560038',
                'state': '29', 'items': [{'id': self.flavor.id, 'quantity': 1000}],
            }), content_type='application/json').json()
        self.assertTrue(res['success'], res)
        self.assertEqual(res['amount'], 1050.0)
        self.assertEqual(fake.last_order['amount'], 105000)
        o = Order.objects.get()
        self.assertEqual((o.price_mode, o.gst_amount), ('exclusive', D('50.00')))
        o.payment_status = 'paid'
        o.save()
        inv = billing.ensure_invoice(o)
        self.assertEqual(inv.grand_total, D('1050.00'))
        self.assertTrue(inv.is_interstate)

    def test_mrp_guard(self):
        self.flavor.price_per_kg = 1020   # 1000 + 5% = 1050 > 1020
        self.flavor.save()
        self.assertTrue(billing.mrp_guard_errors())
        res = self.client.post('/admin/api/gst/settings/', {'action': 'save_settings', 'settings': {
            'gst_price_mode_b2b': 'exclusive', 'gst_price_mode_b2c': 'exclusive',
            'gst_effective_date': '2026-01-01', 'gst_invoice_copies': ['original'],
        }}, content_type='application/json').json()
        self.assertFalse(res['success'])
        self.assertIn('above', res['message'])
        with patch_rzp(FakeRazorpay()):
            res = self.client.post('/api/place-order/', data=json.dumps({
                'name': 'A', 'mobile': '9876543210', 'address': 'x', 'pincode': '673001', 'state': '32',
                'items': [{'id': self.flavor.id, 'quantity': 500}],
            }), content_type='application/json').json()
        self.assertFalse(res['success'])
        res = self.client.post('/admin/api/packs/', {'action': 'update', 'id': self.pack.id, 'flavor_id': self.flavor.id,
                                                     'weight_grams': 250, 'label': '250g', 'mrp': 270,
                                                     'selling_price': 262.5, 'cost_price': 100},
                               content_type='application/json')
        self.assertFalse(res.json()['success'])

    def test_optional_gstin_at_checkout_gives_b2b_invoice(self):
        Setting.objects.filter(setting_key='gst_price_mode_b2c').update(setting_value='inclusive')
        gstin = make_gstin('33', 'LMNOP4321Z')
        with patch_rzp(FakeRazorpay()):
            res = self.client.post('/api/place-order/', data=json.dumps({
                'name': 'Asha', 'mobile': '9876543210', 'address': 'Office', 'pincode': '673001', 'state': '32',
                'gstin': gstin.lower(), 'gst_name': 'Asha Traders', 'items': [{'id': self.flavor.id, 'quantity': 500}],
            }), content_type='application/json').json()
        self.assertTrue(res['success'], res)
        inv = billing.ensure_invoice(Order.objects.get())
        self.assertEqual((inv.supply_type, inv.bill_to_gstin, inv.bill_to_name), ('B2B', gstin, 'Asha Traders'))
        self.assertEqual(inv.place_of_supply_state, '33')
        self.assertTrue(inv.is_interstate)


class RefundTests(Phase2Base):
    def test_manual_refund_after_full_credit(self):
        o = self.b2c(paid=True)
        inv = billing.ensure_invoice(o)
        sel = [{'line_id': ln.id, 'quantity': ln.source_quantity} for ln in inv.lines.all()]
        res = self.client.post('/admin/api/gst/order/', {
            'action': 'credit_note', 'order_id': o.id, 'lines': sel, 'reason': 'sales_return',
            'refund_method': 'manual', 'refund_reference': 'UPI-1'}, content_type='application/json').json()
        self.assertTrue(res['success'], res)
        o.refresh_from_db()
        self.assertEqual((o.status, o.payment_status), ('cancelled', 'refunded'))
        self.assertEqual(Refund.objects.get().amount, D('1000.00'))
        with self.assertRaises(billing.BillingError):
            refunds.issue_refund(o, 1, 'manual', self.admin)

    def test_razorpay_refund_then_webhook(self):
        o = self.b2c(paid=True)
        inv = billing.ensure_invoice(o)
        goods = inv.lines.get(kind='GOODS')
        cn = billing.b2c_credit_note(o, [(goods, 300, True)], 'sales_return', self.admin)
        fake = FakeRazorpay(refund_status='pending')
        with patch_rzp(fake):
            refund = refunds.issue_refund(o, cn.grand_total, 'razorpay', self.admin, credit_note=cn)
        self.assertEqual(fake.last_refund, ('pay_orig', {'amount': 30000, 'notes': {'order_id': o.id,
                                                                                    'credit_note': cn.number}}))
        o.refresh_from_db()
        self.assertEqual(o.payment_status, 'refund_initiated')
        self.assertEqual(Income.objects.get(order=o).amount, D('1000.00'))
        body = json.dumps({'event': 'refund.processed', 'payload': {'refund': {'entity': {'id': refund.razorpay_refund_id}}}}).encode()
        sig = hmac.new(b'whsec', body, hashlib.sha256).hexdigest()
        self.client.post('/api/razorpay-webhook/', data=body, content_type='application/json',
                         HTTP_X_RAZORPAY_SIGNATURE=sig)
        o.refresh_from_db()
        self.assertEqual(o.payment_status, 'paid')  # partial refund done
        self.assertEqual(Income.objects.get(order=o).amount, D('700.00'))


class ReportsTests(Phase2Base):
    def _issue_set(self):
        b2c = self.b2c('VO_r1', state='32')
        billing.ensure_invoice(b2c)
        b2b = self.b2b('VOB_r1')
        b2b_inv = billing.ensure_invoice(b2b)
        big = self.b2c('VO_big', state='29', grams=110000)
        billing.ensure_invoice(big)
        line = b2b_inv.lines.first()
        billing.issue_credit_note(b2b_inv, [(line, 2, False)], 'sales_return', self.admin)
        return b2b_inv

    def test_gstr1_sections(self):
        self._issue_set()
        today = billing.ist_today()
        sections = reports.gstr1_sections(today.replace(day=1), today)
        self.assertEqual(len(sections['b2b'][1]), 1)
        self.assertEqual(sections['b2b'][1][0][5], '29-Karnataka')
        self.assertEqual(len(sections['b2cl'][1]), 1)          # inter-state, value > 1 lakh
        self.assertEqual(len(sections['b2cs'][1]), 1)          # Kerala small sale
        self.assertEqual(len(sections['cdnr'][1]), 1)
        self.assertEqual(sections['hsn_b2b'][1][0][6], D('800.00'))   # 1000 taxable less 200 credited
        self.assertEqual([r[3] for r in sections['docs'][1]], [3, 1])
        res = self.client.get(f'/admin/gst/records/gstr1/?month={today:%Y-%m}')
        names = zipfile.ZipFile(io.BytesIO(res.content)).namelist()
        self.assertIn('b2cs.csv', names)
        for kind in ('sales-register', 'credit-note-register', 'hsn-summary'):
            res = self.client.get(f'/admin/gst/records/report/{kind}.csv?month={today:%Y-%m}')
            self.assertEqual(res.status_code, 200, kind)

    def test_month_close_blocks_documents(self):
        self._issue_set()
        today = billing.ist_today()
        last = (today.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
        res = self.client.post('/admin/api/gst/records/', {'action': 'close_period', 'period': last},
                               content_type='application/json').json()
        self.assertTrue(res['success'], res)
        o = self.b2c('VO_late', state='32')
        with self.assertRaises(billing.BillingError):
            billing.ensure_invoice(o, on_date=today.replace(day=1) - timedelta(days=1))
        self.assertTrue(billing.ensure_invoice(o))  # this month is still open
        current = today.strftime('%Y-%m')
        res = self.client.post('/admin/api/gst/records/', {'action': 'close_period', 'period': current},
                               content_type='application/json').json()
        self.assertFalse(res['success'])  # not ended yet
        self.client.post('/admin/api/gst/records/', {'action': 'reopen_period', 'period': last},
                         content_type='application/json')
        self.assertFalse(reports.is_closed(self.entity, today.replace(day=1) - timedelta(days=1)))

    def test_ca_cannot_close(self):
        ca = AdminUser.objects.create_user(username='ca', password='x', brand_permissions={
            str(self.brand.id): ['gst_records']})
        login(self, ca)
        res = self.client.post('/admin/api/gst/records/', {'action': 'close_period', 'period': '2026-09'},
                               content_type='application/json')
        self.assertEqual(res.status_code, 403)
        data = self.client.get('/admin/api/gst/records/?month=2026-10').json()['data']
        self.assertFalse(data['can_close'])
        self.assertIn('turnover', data)


class AccountingTests(Phase2Base):
    def test_income_net_of_gst_and_itc(self):
        o = self.b2c(paid=True)
        billing.ensure_invoice(o)
        from finance.services import sync_b2c_order_income

        sync_b2c_order_income(o)
        income = Income.objects.get(order=o)
        self.assertEqual((income.amount, income.gst_amount), (D('1000.00'), D('47.62')))
        cat = ExpenseCategory.objects.create(name='Raw material')
        res = self.client.post('/admin/api/expenses/', {
            'action': 'create', 'category_id': cat.id, 'title': 'Bananas', 'amount': 1000,
            'expense_date': str(timezone.localdate()), 'supplier_gstin': make_gstin('32', 'QWERT1234Y'),
            'cgst_amount': 25, 'sgst_amount': 25, 'itc_eligible': 1,
        }, content_type='application/json').json()
        self.assertTrue(res['success'], res)
        exp = Expense.objects.get()
        self.assertEqual((exp.tax_amount, exp.taxable_value), (D('50.00'), D('1000.00')))
        overview = self.client.get('/admin/api/expenses/?action=overview').json()['data']
        self.assertAlmostEqual(overview['total_income'], 952.38)
        self.assertAlmostEqual(overview['total_expenses'], 1000.0)
        position = billing_position = self.client.get(
            f'/admin/api/gst/records/?month={timezone.localdate():%Y-%m}').json()['data']['gst_position']
        self.assertAlmostEqual(position['input_tax'], 50.0)
        self.assertAlmostEqual(billing_position['output_tax'], 47.62)
        res = self.client.post('/admin/api/expenses/', {
            'action': 'create', 'category_id': cat.id, 'title': 'Bad', 'amount': 10,
            'expense_date': str(timezone.localdate()), 'supplier_gstin': '12BAD',
        }, content_type='application/json').json()
        self.assertFalse(res['success'])


class TriggerTests(Phase2Base):
    def test_database_refuses_edits_and_deletes(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL trigger')
        inv = billing.ensure_invoice(self.b2c())
        with self.assertRaises(DatabaseError), transaction.atomic():
            Invoice.objects.filter(pk=inv.pk).update(grand_total=1)
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cur:
                cur.execute('DELETE FROM invoice_lines WHERE invoice_id = %s', [inv.pk])
        billing.set_eway_bill(inv, 'EWB1', None)
        self.assertEqual(Invoice.objects.get(pk=inv.pk).eway_bill_no, 'EWB1')
        cn = billing.issue_credit_note(inv, [(inv.lines.first(), 100, False)], 'other', self.admin)
        with self.assertRaises(DatabaseError), transaction.atomic():
            CreditNote.objects.filter(pk=cn.pk).update(grand_total=0)
