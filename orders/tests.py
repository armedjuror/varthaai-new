from django.test import RequestFactory, TestCase

from accounts.models import AdminUser
from core.api import BRAND_SESSION_KEY
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


class B2COrderCouponTestCase(TestCase):
    """The admin edit screen previews the coupon discount client-side; the
    server must persist it (and keep it in step with the items), or the
    order detail, invoice and tax invoice all show the order undiscounted."""

    def setUp(self):
        from orders.models import Coupon, Order, OrderItem
        from orders.views_b2c import OrdersAPI

        self.Coupon, self.Order, self.OrderItem = Coupon, Order, OrderItem
        self.brand = Brand.objects.create(name='Varthaai', order_prefix='VO')
        self.admin = AdminUser.objects.create_user(username='owner', password='x', role='super_admin')
        self.flavor = Flavor.objects.create(name='Classic', sale_price_per_kg=1000, price_per_kg=1200)
        self.order = Order.objects.create(
            id='VO_test1', brand=self.brand, name='Asha', mobile='9999999999',
            address='x', pincode='682001',
        )
        self.item = OrderItem.objects.create(
            order=self.order, flavor=self.flavor, flavor_name='Classic',
            price_per_kg=1200, sale_price_per_kg=1000, quantity=1000,
        )
        self.pct = Coupon.objects.create(brand=self.brand, code='TEN', discount_percentage=10)
        self.flat = Coupon.objects.create(brand=self.brand, code='FLAT50', discount_amount=50)
        self.api = OrdersAPI()
        self.rf = RequestFactory()

    def _update(self, **extra):
        data = {
            'order_id': self.order.id, 'name': 'Asha', 'mobile': '9999999999',
            'delivery_charge': 0, 'item_quantities': {str(self.item.id): 1000},
            **extra,
        }
        request = self.rf.post('/admin/api/orders/')
        request.user = self.admin
        request.session = {BRAND_SESSION_KEY: self.brand.id}
        request.data = data
        return self.api._update(request)

    def test_choosing_a_coupon_in_edit_persists_discount_and_code(self):
        response = self._update(coupon_id=self.flat.id)
        self.assertTrue(response.data['success'], response.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.coupon_discount, 50)
        self.assertEqual(self.order.coupon_code, 'FLAT50')

    def test_percentage_discount_follows_item_quantity_changes(self):
        self._update(coupon_id=self.pct.id)
        self._update(coupon_id=self.pct.id, item_quantities={str(self.item.id): 2000})
        self.order.refresh_from_db()
        self.assertEqual(self.order.coupon_discount, 200)

    def test_removing_the_coupon_clears_discount_and_code(self):
        self._update(coupon_id=self.flat.id)
        self._update(coupon_id=None)
        self.order.refresh_from_db()
        self.assertEqual(self.order.coupon_discount, 0)
        self.assertEqual(self.order.coupon_code, '')

    def test_discount_never_exceeds_the_goods_value(self):
        big = self.Coupon.objects.create(brand=self.brand, code='BIG', discount_amount=5000)
        self._update(coupon_id=big.id)
        self.order.refresh_from_db()
        self.assertEqual(self.order.coupon_discount, 1000)
