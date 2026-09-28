import asyncio
from datetime import timedelta

from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from bizmcp import sql_tool, tools
from bizmcp.auth import BearerTokenMiddleware, StripMcpTrailingSlashMiddleware
from accounts.models import User
from core.models import Brand
from crm.models import B2BCompany
from orders.models import B2BOrder, Order, OrderItem
from products.models import Flavor, Stock


class ToolsTestCase(TestCase):
    databases = {'default', 'readonly'}

    def setUp(self):
        self.brand = Brand.objects.create(name='Varthaai', order_prefix='VO')
        self.flavor = Flavor.objects.create(
            name='Classic Peanut Butter', reorder_level_grams=5000,
        )
        Stock.objects.create(
            flavor=self.flavor, is_active_batch=True, quantity_grams=2000,
        )

        self.order = Order.objects.create(
            id='VO_test1', brand=self.brand, status=Order.Status.DELIVERED,
            payment_status=Order.PaymentStatus.PAID, name='Test Buyer', mobile='9000000000',
        )
        OrderItem.objects.create(
            order=self.order, flavor=self.flavor, quantity=1000,
            price_per_kg=1490, sale_price_per_kg=990, flavor_name=self.flavor.name,
        )

        self.company = B2BCompany.objects.create(
            brand=self.brand, company_name='Acme Foods', stage='converted',
        )
        self.b2b_order = B2BOrder.objects.create(
            id='VO_B2B_test1', brand=self.brand, company=self.company,
            status='confirmed', total_amount=10000, paid_amount=4000,
            due_date=timezone.localdate() - timedelta(days=5),
        )

    def test_dashboard_overview_combines_b2c_and_b2b(self):
        data = tools.dashboard_overview(brand_id=self.brand.id, days=30)
        self.assertEqual(data['b2c']['orders'], 1)
        self.assertAlmostEqual(data['b2c']['revenue'], 990.0)
        self.assertEqual(data['b2b']['orders'], 1)
        self.assertEqual(data['b2b']['revenue'], 10000.0)
        self.assertEqual(data['combined']['orders'], 2)

    def test_b2b_outstanding_uses_total_minus_paid_never_stored_balance(self):
        data = tools.b2b_outstanding(brand_id=self.brand.id)
        self.assertEqual(data['total_outstanding'], 6000.0)
        self.assertEqual(data['total_overdue'], 6000.0)
        self.assertEqual(data['overdue_order_count'], 1)
        self.assertEqual(data['total_advance_credit'], 0.0)

    def test_b2b_outstanding_advance_credit_for_overpayment(self):
        self.b2b_order.paid_amount = 12000
        self.b2b_order.save(update_fields=['paid_amount'])
        data = tools.b2b_outstanding(brand_id=self.brand.id)
        self.assertEqual(data['total_outstanding'], 0.0)
        self.assertEqual(data['total_advance_credit'], 2000.0)

    def test_stock_status_flags_low_stock(self):
        data = tools.stock_status()
        row = next(r for r in data['flavors'] if r['flavor_id'] == self.flavor.id)
        self.assertEqual(row['available_grams'], 2000)
        self.assertEqual(row['status'], 'low_stock')  # 2000 <= reorder_level_grams (5000)

    def test_stock_status_out_of_stock(self):
        Stock.objects.filter(flavor=self.flavor).update(quantity_grams=0)
        data = tools.stock_status()
        row = next(r for r in data['flavors'] if r['flavor_id'] == self.flavor.id)
        self.assertEqual(row['status'], 'out_of_stock')

    def test_b2b_pipeline_summary_counts_stage(self):
        data = tools.b2b_pipeline_summary(brand_id=self.brand.id)
        self.assertEqual(data['total_companies'], 1)
        self.assertEqual(data['by_stage']['converted'], 1)
        self.assertEqual(data['conversion_rate_pct'], 100.0)

    def test_describe_schema_lists_core_tables(self):
        data = tools.describe_schema()
        self.assertIn('orders', data['tables'])
        self.assertIn('b2b_orders', data['tables'])

    def test_customer_insights_top_spender_revenue(self):
        customer = User.objects.create(brand=self.brand, mobile='9111111111', name='Big Spender')
        self.order.user = customer
        self.order.save(update_fields=['user'])
        data = tools.customer_insights(brand_id=self.brand.id, days=30)
        self.assertEqual(data['total_customers'], 1)
        spenders = {s['id']: s['lifetime_spend'] for s in data['top_spenders']}
        self.assertEqual(spenders.get(customer.id), 990.0)


class RunReadonlySqlTestCase(TransactionTestCase):
    """
    TransactionTestCase (not TestCase): the `readonly` alias is a genuinely
    separate physical connection (a different Postgres role), even though it
    targets the same mirrored test database. TestCase wraps each test in an
    outer transaction on 'default' that never commits, which the `readonly`
    connection — a separate session — can never see. TransactionTestCase
    commits real rows so run_readonly_sql can actually read them back,
    matching how the two connections behave in production.
    """
    databases = {'default', 'readonly'}

    def setUp(self):
        Brand.objects.create(name='Varthaai', order_prefix='VO')

    def test_rejects_mutating_sql(self):
        result = sql_tool.run_readonly_sql("UPDATE brands SET name = 'x'")
        self.assertIn('error', result)

    def test_rejects_chained_statements(self):
        result = sql_tool.run_readonly_sql('SELECT 1; DROP TABLE brands')
        self.assertIn('error', result)

    def test_allows_plain_select(self):
        result = sql_tool.run_readonly_sql('SELECT count(*) AS n FROM brands')
        self.assertNotIn('error', result)
        self.assertEqual(result['rows'][0]['n'], 1)


def _call_asgi(app, path, headers=()):
    """Drive an ASGI app with one HTTP request; return (status, path_seen_by_inner_app)."""
    seen = {}

    async def inner(scope, receive, send):
        seen['path'] = scope['path']
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b''})

    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {'type': 'http.request', 'body': b''}

    scope = {'type': 'http', 'path': path, 'headers': list(headers)}
    asyncio.run(app(inner)(scope, receive, send))
    return sent[0]['status'], seen.get('path')


@override_settings(MCP_API_KEY='test-token')
class AsgiMiddlewareTests(SimpleTestCase):
    """Regression cover for a production bug: a client configured with the
    trailing-slash URL hit FastMCP's own /mcp/ -> /mcp 307, dropped the
    Authorization header following it, and got 401 despite a correct token."""

    def test_trailing_slash_rewritten_without_redirect(self):
        status, path = _call_asgi(StripMcpTrailingSlashMiddleware, '/mcp/')
        self.assertEqual(status, 200)
        self.assertEqual(path, '/mcp')

    def test_other_paths_untouched(self):
        for p in ('/mcp', '/mcp/foo', '/'):
            _, path = _call_asgi(StripMcpTrailingSlashMiddleware, p)
            self.assertEqual(path, p)

    def test_auth_still_enforced_on_trailing_slash(self):
        wrap = lambda inner: BearerTokenMiddleware(StripMcpTrailingSlashMiddleware(inner))
        status, path = _call_asgi(wrap, '/mcp/')
        self.assertEqual(status, 401)
        self.assertIsNone(path)

    def test_valid_token_on_trailing_slash_reaches_app(self):
        wrap = lambda inner: BearerTokenMiddleware(StripMcpTrailingSlashMiddleware(inner))
        status, path = _call_asgi(wrap, '/mcp/', [(b'authorization', b'Bearer test-token')])
        self.assertEqual(status, 200)
        self.assertEqual(path, '/mcp')
