"""
Cashfree checkout: gateway switch + fallback, order placement, server-side
verification, signed webhooks (payments + refunds) and Cashfree refunds.
The Cashfree HTTP API is mocked at storefront.payments._cashfree.
"""
import base64
import hashlib
import hmac
import json
from decimal import Decimal as D
from unittest import mock

from django.test import TestCase, override_settings

from accounts.models import PointsTransaction
from billing import refunds
from billing.models import Refund
from billing.tests import MEDIA, login, seed_gst
from core.models import Setting
from orders.models import Order, OrderItem
from storefront import payments

KEYS = dict(
    RAZORPAY_KEY_ID='rzp_test_key', RAZORPAY_KEY_SECRET='secret', RAZORPAY_WEBHOOK_SECRET='whsec',
    CASHFREE_APP_ID='cf_app', CASHFREE_SECRET_KEY='cf_secret', CASHFREE_ENV='sandbox',
    PAYMENT_GATEWAY='razorpay', SITE_URL='https://shop.example',
)


class FakeCashfree:
    def __init__(self, order_status='PAID', refund_status='PENDING'):
        self.order_status = order_status
        self.refund_status = refund_status
        self.calls = []

    def __call__(self, method, path, payload=None, idempotency_key=None):
        self.calls.append((method, path, payload))
        if method == 'POST' and path == '/orders':
            return {'cf_order_id': '2149460581', 'order_id': payload['order_id'],
                    'payment_session_id': 'session_abc', 'order_status': 'ACTIVE'}
        if method == 'GET' and path.endswith('/payments'):
            return [{'cf_payment_id': '111', 'payment_status': 'FAILED'},
                    {'cf_payment_id': '4128924251', 'payment_status': 'SUCCESS'}]
        if method == 'GET':
            return {'order_status': self.order_status}
        if method == 'POST' and path.endswith('/refunds'):
            return {'cf_refund_id': '1553338', 'refund_id': payload['refund_id'],
                    'refund_status': self.refund_status}
        raise AssertionError(path)


def sign(body, secret='cf_secret', ts='1617695238078'):
    sig = base64.b64encode(hmac.new(secret.encode(), ts.encode() + body, hashlib.sha256).digest()).decode()
    return {'HTTP_X_WEBHOOK_TIMESTAMP': ts, 'HTTP_X_WEBHOOK_SIGNATURE': sig}


@override_settings(MEDIA_ROOT=MEDIA, PRIVATE_MEDIA_ROOT=MEDIA, **KEYS)
class CashfreeBase(TestCase):
    def setUp(self):
        seed_gst(self)
        override = self.settings(STOREFRONT_BRAND_ID=self.brand.id)
        override.enable()
        self.addCleanup(override.disable)

    def use(self, gateway):
        Setting.objects.update_or_create(setting_key='payment_gateway', defaults={'setting_value': gateway})

    def place(self, fake):
        with mock.patch('storefront.payments._cashfree', fake):
            return self.client.post('/api/place-order/', data=json.dumps({
                'name': 'Asha', 'mobile': '9876543210', 'address': 'MG Road', 'pincode': '673001',
                'state': '32', 'items': [{'id': self.flavor.id, 'quantity': 1000}],
            }), content_type='application/json').json()

    def webhook(self, payload, headers=None):
        body = json.dumps(payload).encode()
        return self.client.post('/api/cashfree-webhook/', data=body, content_type='application/json',
                                **(headers if headers is not None else sign(body)))


class GatewaySwitchTests(CashfreeBase):
    def test_setting_wins_over_env_default(self):
        self.assertEqual(payments.active_gateway(), 'razorpay')
        self.use('cashfree')
        self.assertEqual(payments.active_gateway(), 'cashfree')

    def test_falls_back_when_selected_gateway_has_no_keys(self):
        self.use('cashfree')
        with self.settings(CASHFREE_APP_ID=''):
            self.assertEqual(payments.selected_gateway(), 'cashfree')
            self.assertEqual(payments.active_gateway(), 'razorpay')

    def test_admin_can_switch_and_unknown_value_is_refused(self):
        login(self)
        res = self.client.post('/admin/api/settings/', {'action': 'update_settings',
                               'data': {'payment_gateway': 'paypal'}}, content_type='application/json').json()
        self.assertFalse(res['success'])
        res = self.client.post('/admin/api/settings/', {'action': 'update_settings',
                               'data': {'payment_gateway': 'cashfree'}}, content_type='application/json').json()
        self.assertTrue(res['success'])
        res = self.client.get('/admin/api/settings/').json()
        self.assertEqual(res['payment_gateways']['active'], 'cashfree')
        self.assertEqual(res['payment_gateways']['webhooks']['cashfree'],
                         'https://shop.example/api/cashfree-webhook/')


class CashfreeCheckoutTests(CashfreeBase):
    def setUp(self):
        super().setUp()
        self.use('cashfree')

    def test_place_order_creates_cashfree_order(self):
        fake = FakeCashfree()
        res = self.place(fake)
        self.assertTrue(res['success'], res)
        self.assertEqual(res['gateway'], 'cashfree')
        self.assertEqual(res['payment_session_id'], 'session_abc')
        self.assertEqual(res['cashfree_mode'], 'sandbox')
        self.assertEqual(res['key_id'], '')
        method, path, payload = fake.calls[0]
        self.assertEqual((method, path), ('POST', '/orders'))
        self.assertEqual(payload['order_id'], res['order_id'])
        self.assertEqual(payload['order_amount'], 1000.0)
        self.assertEqual(payload['customer_details']['customer_phone'], '9876543210')
        self.assertTrue(payload['customer_details']['customer_id'].isalnum())
        self.assertEqual(payload['order_meta']['notify_url'], 'https://shop.example/api/cashfree-webhook/')
        self.assertIn(f"cf_order_id={res['order_id']}", payload['order_meta']['return_url'])
        order = Order.objects.get()
        self.assertEqual((order.payment_gateway, order.razorpay_order_id), ('cashfree', ''))

    def test_gateway_error_rolls_back_order(self):
        def boom(*a, **k):
            raise payments.CashfreeError('bad credentials')
        res = self.place(boom)
        self.assertFalse(res['success'])
        self.assertIn('bad credentials', res['message'])
        self.assertFalse(Order.objects.exists())

    def test_verify_marks_paid_once_from_cashfree_status(self):
        res = self.place(FakeCashfree())
        oid = res['order_id']
        with mock.patch('storefront.payments._cashfree', FakeCashfree()), \
                mock.patch('storefront.services.notify_new_order') as notify:
            v = self.client.post('/api/verify-cashfree-payment/', {'order_id': oid}).json()
            again = self.client.post('/api/verify-cashfree-payment/', {'order_id': oid}).json()
        self.assertTrue(v['success'], v)
        self.assertTrue(again['success'])
        self.assertEqual(v['payment_id'], '4128924251')
        order = Order.objects.get()
        self.assertEqual((order.payment_status, order.status, order.cashfree_payment_id),
                         ('paid', 'confirmed', '4128924251'))
        self.assertEqual(notify.call_count, 1)
        self.assertEqual(PointsTransaction.objects.filter(type='purchase').count(), 1)

    def test_verify_unpaid_order_is_not_confirmed(self):
        oid = self.place(FakeCashfree())['order_id']
        with mock.patch('storefront.payments._cashfree', FakeCashfree(order_status='ACTIVE')):
            v = self.client.post('/api/verify-cashfree-payment/', {'order_id': oid}).json()
        self.assertFalse(v['success'])
        self.assertEqual(v['status'], 'ACTIVE')
        self.assertEqual(Order.objects.get().payment_status, 'pending')

    def test_verify_ignores_razorpay_orders(self):
        self.use('razorpay')
        with mock.patch('razorpay.Client') as client:
            client.return_value.order.create.return_value = {'id': 'order_rzp1'}
            oid = self.place(FakeCashfree())['order_id']
        v = self.client.post('/api/verify-cashfree-payment/', {'order_id': oid}).json()
        self.assertFalse(v['success'])
        self.assertEqual(Order.objects.get().razorpay_order_id, 'order_rzp1')


class CashfreeWebhookTests(CashfreeBase):
    def setUp(self):
        super().setUp()
        self.use('cashfree')
        self.oid = self.place(FakeCashfree())['order_id']

    def success(self):
        return {'type': 'PAYMENT_SUCCESS_WEBHOOK', 'data': {
            'order': {'order_id': self.oid, 'order_amount': 1000},
            'payment': {'cf_payment_id': 1453995084705707520, 'payment_status': 'SUCCESS'},
        }}

    def test_bad_signature_rejected(self):
        res = self.webhook(self.success(), headers=sign(b'other body'))
        self.assertEqual(res.status_code, 400)
        res = self.webhook(self.success(), headers={})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Order.objects.get().payment_status, 'pending')

    def test_success_webhook_confirms_and_is_idempotent(self):
        with mock.patch('storefront.services.notify_new_order') as notify:
            self.assertEqual(self.webhook(self.success()).status_code, 200)
            self.assertEqual(self.webhook(self.success()).status_code, 200)
        order = Order.objects.get()
        self.assertEqual((order.payment_status, order.status), ('paid', 'confirmed'))
        self.assertEqual(order.cashfree_payment_id, '1453995084705707520')
        self.assertEqual(notify.call_count, 1)

    def test_failed_webhook_cancels_pending_but_never_a_paid_order(self):
        failed = {'type': 'PAYMENT_FAILED_WEBHOOK', 'data': {
            'order': {'order_id': self.oid}, 'payment': {'payment_status': 'FAILED'}}}
        self.webhook(failed)
        self.assertEqual(Order.objects.get().payment_status, 'failed')
        with mock.patch('storefront.services.notify_new_order'):
            self.webhook(self.success())   # customer retried and paid
        self.webhook(failed)
        order = Order.objects.get()
        self.assertEqual((order.payment_status, order.status), ('paid', 'confirmed'))


@override_settings(MEDIA_ROOT=MEDIA, PRIVATE_MEDIA_ROOT=MEDIA, **KEYS)
class CashfreeRefundTests(TestCase):
    def setUp(self):
        seed_gst(self)
        login(self)
        self.order = Order.objects.create(
            id='VO_cf1', brand=self.brand, name='Asha', mobile='9876543210', address='x', pincode='673001',
            shipping_state_code='32', price_mode='inclusive', payment_status='paid',
            payment_gateway='cashfree', cashfree_payment_id='4128924251',
        )
        OrderItem.objects.create(order=self.order, flavor=self.flavor, quantity=1000, price_per_kg=1200,
                                 sale_price_per_kg=1000, flavor_name='Classic')

    def test_online_method_follows_the_paying_gateway(self):
        self.assertEqual(refunds.online_refund_method(self.order), 'cashfree')
        self.order.cashfree_payment_id = ''
        self.order.razorpay_payment_id = 'pay_x'
        self.assertEqual(refunds.online_refund_method(self.order), 'razorpay')

    def test_refund_then_webhook_processes_it(self):
        fake = FakeCashfree(refund_status='PENDING')
        with mock.patch('storefront.payments._cashfree', fake):
            refund = refunds.issue_refund(self.order, '400', Refund.Method.CASHFREE, self.admin)
        method, path, payload = fake.calls[-1]
        self.assertEqual((method, path), ('POST', '/orders/VO_cf1/refunds'))
        self.assertEqual(payload['refund_amount'], 400.0)
        self.assertEqual(refund.status, Refund.Status.INITIATED)
        self.assertEqual(refund.reference, '1553338')
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, 'refund_initiated')

        body = json.dumps({'type': 'REFUND_STATUS_WEBHOOK', 'data': {'refund': {
            'refund_id': refund.cashfree_refund_id, 'order_id': 'VO_cf1', 'refund_status': 'SUCCESS'}}}).encode()
        res = self.client.post('/api/cashfree-webhook/', data=body, content_type='application/json', **sign(body))
        self.assertEqual(res.status_code, 200)
        refund.refresh_from_db()
        self.assertEqual(refund.status, Refund.Status.PROCESSED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, 'paid')  # partial refund leaves it paid
        self.assertEqual(refunds.refundable(self.order), D('600.00'))

    def test_refund_api_error_surfaces(self):
        def boom(*a, **k):
            raise payments.CashfreeError('insufficient balance')
        with mock.patch('storefront.payments._cashfree', boom):
            with self.assertRaisesMessage(Exception, 'insufficient balance'):
                refunds.issue_refund(self.order, '100', Refund.Method.CASHFREE, self.admin)
        self.assertFalse(Refund.objects.exists())


class SignatureTests(TestCase):
    @override_settings(CASHFREE_SECRET_KEY='cf_secret')
    def test_signature_matches_documented_algorithm(self):
        body = b'{"data":{}}'
        headers = sign(body)
        self.assertTrue(payments.cashfree_verify_webhook(
            body, headers['HTTP_X_WEBHOOK_TIMESTAMP'], headers['HTTP_X_WEBHOOK_SIGNATURE']))
        self.assertFalse(payments.cashfree_verify_webhook(
            body + b' ', headers['HTTP_X_WEBHOOK_TIMESTAMP'], headers['HTTP_X_WEBHOOK_SIGNATURE']))


@override_settings(MEDIA_ROOT=MEDIA, PRIVATE_MEDIA_ROOT=MEDIA, **KEYS)
class CashfreePayPageRefundTests(TestCase):
    """A B2C order paid from the pay page is refunded on that Cashfree order."""

    def setUp(self):
        seed_gst(self)
        self.order = Order.objects.create(
            id='VO_pp1', brand=self.brand, name='Asha', mobile='9876543210', address='x', pincode='673001',
            shipping_state_code='32', price_mode='inclusive',
        )
        OrderItem.objects.create(order=self.order, flavor=self.flavor, quantity=1000, price_per_kg=1200,
                                 sale_price_per_kg=1000, flavor_name='Classic')
        from billing import links
        from billing.models import PaymentAttempt

        link = links.get_or_create_link(self.order)
        PaymentAttempt.objects.create(share_link=link, gateway='cashfree', cashfree_order_id='VPabc123',
                                      amount=D('1000'), b2c_order=self.order)
        links.complete_cashfree_payment('VPabc123', '555')
        self.order.refresh_from_db()

    def test_refund_goes_to_the_pay_page_cashfree_order(self):
        self.assertEqual((self.order.payment_gateway, self.order.cashfree_payment_id), ('cashfree', '555'))
        fake = FakeCashfree()
        with mock.patch('storefront.payments._cashfree', fake):
            refunds.issue_refund(self.order, '200', Refund.Method.CASHFREE, self.admin)
        self.assertEqual(fake.calls[-1][1], '/orders/VPabc123/refunds')
