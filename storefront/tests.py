from django.test import TestCase

from accounts.models import User
from core.models import Brand
from orders.models import Order
from storefront import services


class CancelOrderOwnershipTestCase(TestCase):
    """Only the order's owner (logged-in customer or placing session) may cancel."""

    URL = '/api/cancel-order/'

    def setUp(self):
        self.brand = Brand.objects.create(name='Varthaai', order_prefix='VO')
        self.owner = User.objects.create(brand=self.brand, mobile='9000000001', name='Owner')
        self.other = User.objects.create(brand=self.brand, mobile='9000000002', name='Other')
        self.order = Order.objects.create(
            id='VO_cancel1', brand=self.brand, user=self.owner, name='Owner',
            mobile='9000000001', address='x', pincode='670001',
        )

    def _login(self, user):
        session = self.client.session
        session[services.SESSION_USER_ID] = user.id
        session.save()

    def _cancel(self):
        return self.client.post(self.URL, {'order_id': self.order.id}).json()

    def assertNotCancelled(self):
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'pending')
        self.assertEqual(self.order.payment_status, 'pending')

    def assertCancelled(self):
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'cancelled')
        self.assertEqual(self.order.payment_status, 'cancelled')

    def test_anonymous_cannot_cancel(self):
        self.assertFalse(self._cancel()['success'])
        self.assertNotCancelled()

    def test_other_customer_cannot_cancel(self):
        self._login(self.other)
        self.assertFalse(self._cancel()['success'])
        self.assertNotCancelled()

    def test_owner_can_cancel(self):
        self._login(self.owner)
        self.assertTrue(self._cancel()['success'])
        self.assertCancelled()

    def test_guest_session_that_placed_order_can_cancel(self):
        session = self.client.session
        session[services.SESSION_PLACED_ORDERS] = [self.order.id]
        session.save()
        self.assertTrue(self._cancel()['success'])
        self.assertCancelled()

    def test_paid_order_cannot_be_cancelled_by_owner(self):
        Order.objects.filter(id=self.order.id).update(payment_status='paid')
        self._login(self.owner)
        self.assertFalse(self._cancel()['success'])
