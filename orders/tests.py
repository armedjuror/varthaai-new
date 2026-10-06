from django.test import RequestFactory, TestCase

from accounts.models import AdminUser
from core.models import Brand
from crm.models import B2BCompany
from orders.models import B2BOrder, B2BOrderItem
from orders.views_b2b import B2BOrdersAPI
from products.models import Flavor, Stock


class B2BOrderStockDeductionTestCase(TestCase):
    """
    B2B order lines pin a specific stock batch (`B2BOrderItem.stock`), chosen
    by the admin in the order-create form's batch selector. Confirming the
    order must deduct from THAT batch — the FIFO `is_active_batch` concept is
    for B2C orders only.
    """

    def setUp(self):
        self.brand = Brand.objects.create(name='Varthaai', order_prefix='VO')
        self.admin = AdminUser.objects.create_user(username='owner', password='x')
        self.flavor = Flavor.objects.create(name='Classic', reorder_level_grams=500)

        self.active_batch = Stock.objects.create(
            flavor=self.flavor, is_active_batch=True, quantity_grams=10000,
        )
        self.other_batch = Stock.objects.create(
            flavor=self.flavor, is_active_batch=False, quantity_grams=5000,
        )

        self.company = B2BCompany.objects.create(
            brand=self.brand, company_name='Acme Foods', stage='converted',
        )
        self.order = B2BOrder.objects.create(
            id='VOB_test1', brand=self.brand, company=self.company,
            status=B2BOrder.Status.DRAFT, total_amount=1000, balance_amount=1000,
        )
        B2BOrderItem.objects.create(
            b2b_order=self.order, flavor=self.flavor, stock=self.other_batch,
            quantity=1, weight_grams=1000, total_weight_grams=1000,
            selling_price=1000, flavor_name=self.flavor.name,
        )

        self.rf = RequestFactory()

    def _confirm(self):
        request = self.rf.post('/admin/api/b2b-orders/')
        request.user = self.admin
        view = B2BOrdersAPI()
        return view._confirm_order(request, self.brand.id, {'order_id': self.order.id})

    def test_confirm_deducts_from_the_batch_selected_on_the_order_line(self):
        response = self._confirm()
        self.assertTrue(response.data['success'], response.data)

        self.other_batch.refresh_from_db()
        self.active_batch.refresh_from_db()

        self.assertEqual(
            self.other_batch.quantity_grams, 4000,
            'Stock should be deducted from the batch pinned on the order line.',
        )
        self.assertEqual(
            self.active_batch.quantity_grams, 10000,
            'The FIFO active batch must be untouched by a B2B order pinned to another batch.',
        )

    def test_cancelling_a_confirmed_order_restores_the_pinned_batch(self):
        self._confirm()
        request = self.rf.post('/admin/api/b2b-orders/')
        request.user = self.admin
        view = B2BOrdersAPI()
        response = view._update_status(request, self.brand.id, {
            'order_id': self.order.id, 'status': 'cancelled',
        })
        self.assertTrue(response.data['success'], response.data)

        self.other_batch.refresh_from_db()
        self.active_batch.refresh_from_db()
        self.assertEqual(self.other_batch.quantity_grams, 5000)
        self.assertEqual(self.active_batch.quantity_grams, 10000)

    def test_rejects_mixed_pinned_and_unpinned_lines_when_flavor_stock_is_insufficient(self):
        # active_batch (pinned, 1000g needed) + an unpinned line needing 1500g
        # on the same flavor — total 2500g against 15000g on hand, but the
        # pinned batch's 1000g must not double-count as available to the
        # unpinned line once it's earmarked.
        self.active_batch.quantity_grams = 1000
        self.active_batch.save(update_fields=['quantity_grams'])
        self.other_batch.quantity_grams = 500
        self.other_batch.save(update_fields=['quantity_grams'])

        self.other_item = B2BOrderItem.objects.get()
        self.other_item.stock = self.active_batch
        self.other_item.total_weight_grams = 1000
        self.other_item.weight_grams = 1000
        self.other_item.quantity = 1
        self.other_item.save(update_fields=['stock', 'total_weight_grams', 'weight_grams', 'quantity'])

        B2BOrderItem.objects.create(
            b2b_order=self.order, flavor=self.flavor, stock=None,
            quantity=1, weight_grams=1500, total_weight_grams=1500,
            selling_price=1500, flavor_name=self.flavor.name,
        )

        response = self._confirm()
        self.assertFalse(response.data['success'])

        self.active_batch.refresh_from_db()
        self.other_batch.refresh_from_db()
        self.assertEqual(self.active_batch.quantity_grams, 1000, 'Rejected order must not touch stock.')
        self.assertEqual(self.other_batch.quantity_grams, 500, 'Rejected order must not touch stock.')
