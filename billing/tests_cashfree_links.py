"""
Our own pay page (/o/<token>/pay/) for payment reminders, with the active
gateway's popup: the reminder carries the pay page link, the page shows only
the amount due, Pay creates a Cashfree order for that amount, and the
payment settles once from the popup return, the webhook or a status check on
the next page view. Cashfree's HTTP API is mocked.
"""
import base64
import hashlib
import hmac
import json
from decimal import Decimal as D
from unittest import mock
from urllib.parse import unquote

from django.test import override_settings

from billing import links
from billing.models import PaymentAttempt
from billing.tests_phase2 import RZP, FakeRazorpay, Phase2Base, patch_rzp
from core.models import Setting
from orders.models import B2BPayment

CF = dict(CASHFREE_APP_ID='cf_app', CASHFREE_SECRET_KEY='cf_secret', CASHFREE_ENV='sandbox',
          SITE_URL='https://shop.example')


class FakeCashfree:
    def __init__(self):
        self.orders, self.status = [], 'ACTIVE'

    def __call__(self, method, path, payload=None, idempotency_key=None):
        if method == 'POST' and path == '/orders':
            self.orders.append(payload)
            return {'order_id': payload['order_id'], 'payment_session_id': 'sess_' + payload['order_id'],
                    'order_status': 'ACTIVE'}
        if method == 'GET' and path.endswith('/payments'):
            return [{'cf_payment_id': 9988, 'payment_status': 'SUCCESS'}]
        if method == 'GET' and path.startswith('/orders/'):
            return {'order_status': self.status}
        raise AssertionError((method, path))


def sign(body):
    ts = '1700000000000'
    sig = base64.b64encode(hmac.new(b'cf_secret', ts.encode() + body, hashlib.sha256).digest()).decode()
    return {'HTTP_X_WEBHOOK_TIMESTAMP': ts, 'HTTP_X_WEBHOOK_SIGNATURE': sig}


@override_settings(**CF)
class PayPageTests(Phase2Base):
    def setUp(self):
        super().setUp()
        Setting.objects.update_or_create(setting_key='payment_gateway', defaults={'setting_value': 'cashfree'})
        self.fake = FakeCashfree()
        patcher = mock.patch('storefront.payments._cashfree', self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.order = self.b2b()

    def remind(self):
        return self.client.post('/admin/api/b2b-orders/', {'action': 'whatsapp', 'order_id': self.order.id,
                                'kind': 'reminder'}, content_type='application/json').json()['data']

    def start(self, token):
        return self.client.post(f'/o/{token}/pay/', content_type='application/json').json()

    def test_reminder_links_to_our_pay_page(self):
        data = self.remind()
        token = links.live_link(self.order).token
        pay = f'http://testserver/o/{token}/pay/'
        self.assertEqual(data['pay_link'], pay)
        self.assertIn('Pay ₹1,050.00 online: ' + pay, data['message'])
        self.assertIn(pay, unquote(data['url']))
        self.assertEqual(self.fake.orders, [])   # nothing is created at Cashfree until Pay is pressed

    def test_template_can_place_the_pay_link(self):
        Setting.objects.update_or_create(setting_key='wa_message_reminder',
                                         defaults={'setting_value': 'Pay {amount} here {pay_link}'})
        message = self.remind()['message']
        token = links.live_link(self.order).token
        self.assertEqual(message, f'Pay 1,050.00 here http://testserver/o/{token}/pay/')

    def test_pay_page_shows_only_the_amount_and_pay_button(self):
        link = links.get_or_create_link(self.order, self.admin)
        self.client.logout()
        res = self.client.get(f'/o/{link.token}/pay/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, '₹1,050.00')
        self.assertContains(res, 'id="payBtn"')
        self.assertContains(res, 'sdk.cashfree.com/js/v3/cashfree.js')
        self.assertNotContains(res, '<input')            # nothing to edit
        self.assertNotContains(res, 'MG Road')
        self.assertEqual(res['X-Robots-Tag'], 'noindex, nofollow')

    def test_pay_creates_cashfree_order_for_amount_due_and_verifies(self):
        link = links.get_or_create_link(self.order, self.admin)
        self.client.logout()
        self.client.get(f'/o/{link.token}/pay/')
        res = self.start(link.token)
        self.assertTrue(res['success'], res)
        self.assertEqual(res['gateway'], 'cashfree')
        self.assertEqual(res['mode'], 'sandbox')
        created = self.fake.orders[0]
        self.assertEqual(created['order_amount'], 1050.0)
        self.assertEqual(created['order_id'], res['cashfree_order_id'])
        self.assertEqual(res['payment_session_id'], 'sess_' + created['order_id'])
        self.assertEqual(created['customer_details']['customer_phone'], '9876500000')
        self.assertTrue(created['order_meta']['return_url'].endswith(f'/o/{link.token}/pay/'))

        # Popup closed before paying: not recorded.
        v = self.client.post(f'/o/{link.token}/pay/verify/', {'cashfree_order_id': created['order_id']},
                             content_type='application/json').json()
        self.assertFalse(v['success'])
        self.fake.status = 'PAID'
        v = self.client.post(f'/o/{link.token}/pay/verify/', {'cashfree_order_id': created['order_id']},
                             content_type='application/json').json()
        self.assertTrue(v['success'])
        self.order.refresh_from_db()
        self.assertEqual((self.order.paid_amount, self.order.payment_status), (D('1050.00'), 'paid'))
        payment = B2BPayment.objects.get()
        self.assertEqual((payment.payment_method, payment.reference_number), ('online', '9988'))
        self.assertIn('Cashfree', payment.notes)
        # Page now says nothing is due; no Pay button.
        res = self.client.get(f'/o/{link.token}/pay/')
        self.assertContains(res, 'Nothing due')
        self.assertNotContains(res, 'id="payBtn"')

    def test_verify_refuses_other_orders_attempts(self):
        link = links.get_or_create_link(self.order, self.admin)
        other = links.get_or_create_link(self.b2b(oid='VOB_p2'), self.admin)
        cf_id = self.start(other.token)['cashfree_order_id']
        v = self.client.post(f'/o/{link.token}/pay/verify/', {'cashfree_order_id': cf_id},
                             content_type='application/json').json()
        self.assertFalse(v['success'])

    def test_webhook_records_payment_once(self):
        link = links.get_or_create_link(self.order, self.admin)
        cf_id = self.start(link.token)['cashfree_order_id']
        body = json.dumps({'type': 'PAYMENT_SUCCESS_WEBHOOK', 'data': {
            'order': {'order_id': cf_id, 'order_amount': 1050},
            'payment': {'cf_payment_id': 9988, 'payment_status': 'SUCCESS', 'payment_amount': 1050},
        }}).encode()
        for _ in range(2):
            res = self.client.post('/api/cashfree-webhook/', data=body, content_type='application/json', **sign(body))
            self.assertEqual(res.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.paid_amount, D('1050.00'))
        self.assertEqual(B2BPayment.objects.count(), 1)
        self.assertEqual(PaymentAttempt.objects.get().status, 'paid')

    def test_page_view_settles_a_paid_attempt_when_webhook_missed(self):
        link = links.get_or_create_link(self.order, self.admin)
        self.start(link.token)
        self.fake.status = 'PAID'
        self.client.logout()
        self.client.get(f'/o/{link.token}/pay/')   # back from an in-app browser redirect
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, 'paid')
        self.assertEqual(PaymentAttempt.objects.get().cashfree_payment_id, '9988')

    def test_part_paid_order_asks_only_for_the_balance(self):
        links.apply_b2b_payment(self.order, D('50'), method='cash', user=self.admin)
        link = links.get_or_create_link(self.order, self.admin)
        self.assertContains(self.client.get(f'/o/{link.token}/pay/'), '₹1,000.00')
        self.start(link.token)
        self.assertEqual(self.fake.orders[0]['order_amount'], 1000.0)

    def test_admin_share_payload_has_pay_link(self):
        res = self.client.post('/admin/api/b2b-orders/', {'action': 'share_link', 'order_id': self.order.id},
                               content_type='application/json').json()
        token = links.live_link(self.order).token
        self.assertEqual(res['data']['pay_link'], f'http://testserver/o/{token}/pay/')

    @override_settings(**RZP)
    def test_razorpay_active_uses_same_pay_page(self):
        Setting.objects.filter(setting_key='payment_gateway').update(setting_value='razorpay')
        self.assertIn('/pay/', self.remind()['pay_link'])
        link = links.live_link(self.order)
        self.assertContains(self.client.get(f'/o/{link.token}/pay/'), 'checkout.razorpay.com')
        with patch_rzp(FakeRazorpay()):
            res = self.start(link.token)
        self.assertEqual(res['gateway'], 'razorpay')
        self.assertEqual(self.fake.orders, [])
