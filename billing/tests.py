"""
Service / view tests for GST billing: invoice issue on dispatch, numbering,
freeze rules, credit notes, cancel & reissue, and the GST records login.
"""
import hashlib
import io
import shutil
import tempfile
import threading
import zipfile
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal as D
from unittest import mock

from django.core.management import call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings

from accounts.models import AdminUser
from billing import services as billing
from billing.gstin import gstin_checksum
from billing.models import CreditNote, HSNCode, HSNRate, ImmutableDocumentError, Invoice, LegalEntity
from core.api import BRAND_SESSION_KEY
from core.models import Brand, Setting
from crm.models import B2BCompany, B2BContact
from orders.models import B2BOrder, B2BOrderItem, B2BReturn, Order, OrderItem
from products.models import Flavor, FlavorPack, Stock

MEDIA = tempfile.mkdtemp(prefix='gst-test-media-')


def make_gstin(state, pan='ABCDE1234F'):
    body = f'{state}{pan}1Z'
    return body + gstin_checksum(body)


SELLER_GSTIN = make_gstin('32', 'AAKFV1234Q')


def seed_gst(test, b2b_mode='exclusive'):
    test.entity = LegalEntity.objects.create(
        legal_name='Varthaai Foods LLP', gstin=SELLER_GSTIN, state_code='32',
        address='Kozhikode, Kerala', fssai_no='11223344556677',
    )
    test.brand = Brand.objects.create(name='Varthaai', order_prefix='VO', legal_entity=test.entity)
    for key, value in (('gst_effective_date', '2026-01-01'), ('gst_price_mode_b2b', b2b_mode),
                       ('gst_price_mode_b2c', 'inclusive')):
        Setting.objects.update_or_create(setting_key=key, defaults={'setting_value': value})
    test.hsn = HSNCode.objects.create(code='20089999', uqc='KGS')
    HSNRate.objects.create(hsn=test.hsn, effective_from=date(2026, 1, 1), gst_rate=D('5'))
    test.flavor = Flavor.objects.create(name='Classic', hsn=test.hsn, sale_price_per_kg=1000, price_per_kg=1200)
    test.pack = FlavorPack.objects.create(
        flavor=test.flavor, brand=test.brand, weight_grams=250, label='250g',
        mrp=D('300'), selling_price=D('262.50'), cost_price=D('100'),
    )
    Stock.objects.create(flavor=test.flavor, is_active_batch=True, quantity_grams=100000)
    test.admin = AdminUser.objects.create_user(username='owner', password='x', name='Owner',
                                               role=AdminUser.Role.SUPER_ADMIN)


def login(test, user=None):
    test.client.force_login(user or test.admin)
    session = test.client.session
    session[BRAND_SESSION_KEY] = test.brand.id
    session.save()


@override_settings(MEDIA_ROOT=MEDIA, PRIVATE_MEDIA_ROOT=MEDIA)
class B2CInvoiceTests(TestCase):
    def setUp(self):
        seed_gst(self)
        login(self)

    def _order(self, state='32', grams=1000, delivery=50, coupon=0):
        order = Order.objects.create(
            id='VO_' + str(Order.objects.count() + 1), brand=self.brand, name='Asha', mobile='9876543210',
            address='MG Road', pincode='673001', shipping_state_code=state, delivery_charge=delivery,
            coupon_discount=coupon, price_mode='inclusive',
        )
        OrderItem.objects.create(order=order, flavor=self.flavor, quantity=grams, price_per_kg=1200,
                                 sale_price_per_kg=1000, flavor_name='Classic')
        return order

    def _status(self, order, status):
        return self.client.post('/admin/api/orders/', {'action': 'update_status', 'order_id': order.id,
                                                       'status': status}, content_type='application/json')

    def test_ship_issues_intra_state_invoice_that_ties_to_amount_charged(self):
        order = self._order(coupon=100)
        res = self._status(order, 'shipped')
        self.assertTrue(res.json()['success'], res.json())
        inv = Invoice.objects.get(b2c_order=order)
        self.assertFalse(inv.is_interstate)
        self.assertEqual(inv.cgst_total, inv.sgst_total)
        self.assertEqual(inv.igst_total, D('0'))
        self.assertEqual(inv.grand_total, D('1000') - D('100') + D('50'))
        self.assertEqual(inv.number, f'{billing.fiscal_year(billing.ist_today())[2:]}/0000001')
        self.assertTrue(inv.lines.filter(kind='SHIPPING').exists())

    def test_other_state_gets_igst(self):
        order = self._order(state='29')
        self._status(order, 'shipped')
        inv = Invoice.objects.get(b2c_order=order)
        self.assertTrue(inv.is_interstate)
        self.assertEqual(inv.cgst_total, D('0'))
        self.assertGreater(inv.igst_total, 0)

    def test_double_dispatch_and_ensure_are_idempotent(self):
        order = self._order()
        self._status(order, 'shipped')
        self._status(order, 'delivered')
        billing.ensure_invoice(order)
        self.assertEqual(Invoice.objects.filter(b2c_order=order).count(), 1)

    def test_missing_state_blocks_dispatch_and_rolls_back(self):
        order = self._order(state='')
        res = self._status(order, 'shipped')
        self.assertFalse(res.json()['success'])
        self.assertIn('state', res.json()['message'])
        order.refresh_from_db()
        self.assertEqual(order.status, 'pending')
        self.assertFalse(order.stock_deducted)
        self.assertEqual(Stock.objects.get().quantity_grams, 100000)

    def test_missing_hsn_blocks_dispatch(self):
        self.flavor.hsn = None
        self.flavor.save()
        order = self._order()
        res = self._status(order, 'shipped')
        self.assertFalse(res.json()['success'])
        self.assertIn('HSN', res.json()['message'])

    def test_before_effective_date_no_invoice(self):
        Setting.objects.filter(setting_key='gst_effective_date').update(setting_value='2099-01-01')
        order = self._order()
        self.assertTrue(self._status(order, 'shipped').json()['success'])
        self.assertFalse(Invoice.objects.exists())

    def test_freeze_after_invoice(self):
        order = self._order()
        self._status(order, 'shipped')
        res = self.client.post('/admin/api/orders/', {'action': 'add_item', 'order_id': order.id,
                                                      'flavor_id': self.flavor.id, 'quantity': 100},
                               content_type='application/json')
        self.assertFalse(res.json()['success'])
        for target in ('pending', 'cancelled', 'deleted'):
            self.assertFalse(self._status(order, target).json()['success'])
        self.assertTrue(self._status(order, 'delivered').json()['success'])
        res = self.client.post('/admin/api/orders/', {
            'action': 'update', 'order_id': order.id, 'name': 'Asha', 'mobile': '9876543210',
            'address': 'Changed', 'pincode': '673001', 'delivery_charge': 50,
        }, content_type='application/json')
        self.assertFalse(res.json()['success'])

    def test_consecutive_numbers(self):
        a, b = self._order(), self._order()
        self._status(a, 'shipped')
        self._status(b, 'shipped')
        nums = list(Invoice.objects.order_by('id').values_list('number', flat=True))
        self.assertEqual([n[-7:] for n in nums], ['0000001', '0000002'])

    def test_failed_issue_leaves_no_gap(self):
        bad = self._order(state='')
        self._status(bad, 'shipped')
        good = self._order()
        self._status(good, 'shipped')
        self.assertTrue(Invoice.objects.get(b2c_order=good).number.endswith('0000001'))

    def test_issued_invoice_is_immutable(self):
        order = self._order()
        self._status(order, 'shipped')
        inv = Invoice.objects.get()
        inv.bill_to_name = 'x'
        with self.assertRaises(ImmutableDocumentError):
            inv.save()
        with self.assertRaises(ImmutableDocumentError):
            inv.delete()

    def test_full_credit_note_cancels_order(self):
        order = self._order()
        self._status(order, 'shipped')
        inv = Invoice.objects.get()
        lines = [{'line_id': ln.id, 'quantity': ln.source_quantity, 'restock': True} for ln in inv.lines.all()]
        res = self.client.post('/admin/api/gst/order/', {'action': 'credit_note', 'order_id': order.id,
                                                         'lines': lines, 'reason': 'sales_return'},
                               content_type='application/json')
        self.assertTrue(res.json()['success'], res.json())
        cn = CreditNote.objects.get()
        self.assertEqual(cn.grand_total, inv.grand_total)
        self.assertTrue(cn.number.startswith('CN/'))
        order.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')
        self.assertEqual(Stock.objects.get().quantity_grams, 100000)

    def test_partial_credit_cannot_exceed_invoice(self):
        order = self._order()
        self._status(order, 'shipped')
        goods = Invoice.objects.get().lines.get(kind='GOODS')
        billing.b2c_credit_note(order, [(goods, 600, False)], 'sales_return', self.admin)
        with self.assertRaises(billing.BillingError):
            billing.b2c_credit_note(order, [(goods, 600, False)], 'sales_return', self.admin)

    def test_cancel_and_reissue(self):
        order = self._order(state='32')
        self._status(order, 'shipped')
        res = self.client.post('/admin/api/gst/order/', {
            'action': 'cancel_reissue', 'order_id': order.id, 'name': 'Asha K', 'address': 'Indiranagar',
            'pincode': '560038', 'shipping_state_code': '29',
        }, content_type='application/json')
        self.assertTrue(res.json()['success'], res.json())
        old, new = Invoice.objects.order_by('id')
        self.assertEqual(old.status, Invoice.Status.CANCELLED)
        self.assertEqual(new.status, Invoice.Status.ACTIVE)
        self.assertTrue(new.is_interstate)
        self.assertEqual(new.reissue_of, old)
        self.assertEqual(CreditNote.objects.get().grand_total, old.grand_total)

    def test_order_summary_matches_invoice(self):
        order = self._order(coupon=37)
        before = billing.order_gst_summary(order)
        self._status(order, 'shipped')
        after = billing.order_gst_summary(order)
        self.assertEqual(before['tax_total'], after['tax_total'])
        self.assertEqual(after['tax_total'], Invoice.objects.get().tax_total)

    def test_pdf_stored_once_with_hash(self):
        order = self._order()
        with self.captureOnCommitCallbacks(execute=True):
            self._status(order, 'shipped')
        inv = Invoice.objects.get()
        self.assertTrue(inv.pdf_file)
        with inv.pdf_file.open('rb') as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), inv.pdf_sha256)
        res = self.client.get(f'/admin/gst/invoice/{inv.id}/pdf/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(hashlib.sha256(b''.join(res.streaming_content)).hexdigest(), inv.pdf_sha256)
        self.assertEqual(billing.store_pdf(inv.id, 'invoice').pdf_sha256, inv.pdf_sha256)

    def test_print_page_renders(self):
        order = self._order()
        self._status(order, 'shipped')
        inv = Invoice.objects.get()
        res = self.client.get(f'/admin/gst/invoice/{inv.id}/?copies=original&copies=duplicate')
        self.assertContains(res, 'TAX INVOICE')
        self.assertContains(res, 'Duplicate for Transporter')
        self.assertContains(res, 'Kerala (32)')

    def test_ist_date_used(self):
        # 22:30 UTC on 31 Mar is already 1 Apr in India — a new financial year.
        with mock.patch('django.utils.timezone.now') as now:
            from datetime import datetime, timezone as tz
            now.return_value = datetime(2027, 3, 31, 22, 30, tzinfo=tz.utc)
            self.assertEqual(billing.ist_today(), date(2027, 4, 1))
            self.assertEqual(billing.fiscal_year(billing.ist_today()), '2027-28')


@override_settings(MEDIA_ROOT=MEDIA, PRIVATE_MEDIA_ROOT=MEDIA)
class B2BInvoiceTests(TestCase):
    def setUp(self):
        seed_gst(self)
        login(self)
        self.company = B2BCompany.objects.create(
            brand=self.brand, company_name='Fresh Mart', stage='converted',
            gst_number=make_gstin('29'), state_code='29', address='Bengaluru',
        )
        self.contact = B2BContact.objects.create(company=self.company, name='Ravi', phone='9876500000')

    def _post(self, data):
        return self.client.post('/admin/api/b2b-orders/', data, content_type='application/json').json()

    def _create(self, qty=10, **extra):
        res = self._post({
            'action': 'create_order', 'company_id': self.company.id, 'contact_id': self.contact.id,
            'items': [{'flavor_id': self.flavor.id, 'flavor_pack_id': self.pack.id, 'weight_grams': 250,
                       'quantity': qty, 'selling_price': 100, 'flavor_name': 'Classic', 'pack_label': '250g'},
                      {'flavor_id': self.flavor.id, 'flavor_pack_id': self.pack.id, 'weight_grams': 250,
                       'quantity': 1, 'selling_price': 100, 'flavor_name': 'Classic', 'pack_label': '250g',
                       'is_free_item': 1}],
            **extra,
        })
        self.assertTrue(res['success'], res)
        return B2BOrder.objects.get(id=res['data']['order_id'])

    def test_exclusive_mode_adds_gst_to_total(self):
        order = self._create()
        self.assertEqual(order.price_mode, 'exclusive')
        self.assertEqual(order.gst_amount, D('50.00'))
        self.assertEqual(order.total_amount, D('1050.00'))
        self.assertEqual(order.place_of_supply_state, '29')

    def test_dispatch_issues_igst_invoice_with_free_line(self):
        order = self._create(customer_po_no='PO-77')
        self._post({'action': 'update_status', 'order_id': order.id, 'status': 'confirmed'})
        res = self._post({'action': 'update_status', 'order_id': order.id, 'status': 'dispatched'})
        self.assertTrue(res['success'], res)
        inv = Invoice.objects.get(b2b_order=order)
        self.assertEqual(inv.supply_type, 'B2B')
        self.assertTrue(inv.is_interstate)
        self.assertEqual(inv.igst_total, D('50.00'))
        self.assertEqual(inv.grand_total, D('1050.00'))
        self.assertEqual(inv.customer_po_no, 'PO-77')
        free = inv.lines.get(is_free=True)
        self.assertEqual(free.total_amount, D('0'))
        self.assertEqual(free.hsn_code, '20089999')
        order.refresh_from_db()
        self.assertEqual(order.invoice_number, inv.number)
        # Frozen afterwards.
        self.assertFalse(self._post({'action': 'update_status', 'order_id': order.id, 'status': 'draft'})['success'])
        self.assertFalse(self._post({'action': 'update_status', 'order_id': order.id, 'status': 'cancelled'})['success'])
        self.assertFalse(self._post({'action': 'delete_order', 'order_id': order.id})['success'])

    def test_unregistered_company_gets_b2c_type_invoice_by_delivery_state(self):
        self.company.gst_number = ''
        self.company.state_code = '32'
        self.company.save()
        order = self._create(ship_to_same_as_bill_to=0, ship_to_name='Godown', ship_to_address='Hosur Rd',
                             ship_to_state_code='29', ship_to_pincode='560068')
        self._post({'action': 'update_status', 'order_id': order.id, 'status': 'dispatched'})
        inv = Invoice.objects.get()
        self.assertEqual(inv.supply_type, 'B2C')
        self.assertEqual(inv.place_of_supply_state, '29')
        self.assertFalse(inv.ship_to_same)

    def test_partial_return_needs_credit_note_then_balances(self):
        order = self._create()
        self._post({'action': 'update_status', 'order_id': order.id, 'status': 'dispatched'})
        self._post({'action': 'add_payment', 'company_id': self.company.id, 'amount': 1050,
                    'payment_method': 'cash', 'order_id': order.id})
        item = order.items.get(is_free_item=False)
        res = self._post({'action': 'return_items', 'order_id': order.id,
                          'items': [{'item_id': item.id, 'quantity': 2, 'restock': False}]})
        self.assertTrue(res['success'], res)
        ret = B2BReturn.objects.get()
        self.assertTrue(ret.credit_note_pending)
        order.refresh_from_db()
        self.assertEqual(order.total_amount, D('1050.00'))  # money waits for the credit note
        res = self._post({'action': 'generate_credit_note', 'return_id': ret.id, 'refund_method': 'upi'})
        self.assertTrue(res['success'], res)
        cn = CreditNote.objects.get()
        self.assertEqual(cn.grand_total, D('210.00'))
        order.refresh_from_db()
        self.assertEqual(order.total_amount, D('840.00'))
        self.assertEqual(order.paid_amount, D('840.00'))
        self.assertEqual(order.payments.filter(payment_type='refund').get().amount, D('210.00'))
        ret.refresh_from_db()
        self.assertFalse(ret.credit_note_pending)

    def test_cancel_reissue_corrects_gstin(self):
        order = self._create()
        self._post({'action': 'update_status', 'order_id': order.id, 'status': 'dispatched'})
        new_gstin = make_gstin('32', 'PQRST6789K')
        res = self._post({'action': 'cancel_reissue', 'order_id': order.id, 'gst_number': new_gstin,
                          'gst_legal_name': 'Fresh Mart LLP', 'address': 'Calicut', 'city': 'Kozhikode',
                          'pincode': '673001'})
        self.assertTrue(res['success'], res)
        new = Invoice.objects.get(status='active')
        self.assertFalse(new.is_interstate)
        self.assertEqual(new.bill_to_gstin, new_gstin)
        self.assertEqual(new.bill_to_name, 'Fresh Mart LLP')
        self.assertEqual(new.grand_total, D('1050.00'))

    def test_gst_preview(self):
        res = self._post({'action': 'gst_preview', 'company_id': self.company.id,
                          'items': [{'flavor_id': self.flavor.id, 'weight_grams': 250, 'quantity': 4,
                                     'selling_price': 100, 'flavor_name': 'Classic'}]})
        self.assertTrue(res['data']['enabled'])
        self.assertEqual(res['data']['igst'], 20.0)
        self.assertEqual(res['data']['grand_total'], 420.0)


@override_settings(MEDIA_ROOT=MEDIA, PRIVATE_MEDIA_ROOT=MEDIA)
class GSTRecordsAccessTests(TestCase):
    def setUp(self):
        seed_gst(self)
        self.ca = AdminUser.objects.create_user(
            username='ca', password='pw-123456', name='CA', role=AdminUser.Role.STAFF,
            brand_permissions={str(self.brand.id): ['gst_records']},
        )

    def test_ca_lands_on_records_and_nothing_else(self):
        res = self.client.post('/admin/', {'username': 'ca', 'password': 'pw-123456'})
        self.assertRedirects(res, '/admin/gst/records/', fetch_redirect_response=False)
        self.assertEqual(self.client.get('/admin/gst/records/').status_code, 200)
        self.assertEqual(self.client.get('/admin/api/gst/records/').status_code, 200)
        for url in ('/admin/dashboard/', '/admin/orders/', '/admin/b2b-orders/', '/admin/settings/'):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        for url in ('/admin/api/orders/', '/admin/api/b2b-orders/', '/admin/api/gst/settings/'):
            self.assertEqual(self.client.get(url).status_code, 403, url)


@override_settings(MEDIA_ROOT=MEDIA, PRIVATE_MEDIA_ROOT=MEDIA)
class BackfillAndPagesTests(TestCase):
    def setUp(self):
        seed_gst(self)
        login(self)
        self.company = B2BCompany.objects.create(brand=self.brand, company_name='Bakery', stage='converted',
                                                 state_code='32', address='Calicut')

    def _b2c(self, oid, pincode='673001', state=''):
        o = Order.objects.create(id=oid, brand=self.brand, name='Old', mobile='9876543210', address='x',
                                 pincode=pincode, shipping_state_code=state, status='shipped')
        OrderItem.objects.create(order=o, flavor=self.flavor, quantity=1000, price_per_kg=1000,
                                 sale_price_per_kg=1000, flavor_name='Classic')
        return o

    def _b2b(self, oid, paid=0):
        o = B2BOrder.objects.create(id=oid, brand=self.brand, company=self.company, status='delivered',
                                    subtotal=D('500'), total_amount=D('500'), paid_amount=D(paid),
                                    delivered_at=datetime(2026, 10, 5, 6, 0, tzinfo=dt_timezone.utc))
        B2BOrderItem.objects.create(b2b_order=o, flavor=self.flavor, quantity=5, weight_grams=100,
                                    total_weight_grams=500, selling_price=D('100'), flavor_name='Classic')
        return o

    def _run(self, *args):
        out = io.StringIO()
        call_command('backfill_invoices', '--from', '2026-01-01', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        self._b2c('VO_old1')
        out = self._run('--dry-run')
        self.assertIn('would issue', out)
        self.assertIn('state from pincode', out)
        self.assertFalse(Invoice.objects.exists())
        self.assertEqual(Order.objects.get().shipping_state_code, '')

    def test_backfill_in_date_order_inclusive(self):
        self._b2c('VO_late')
        self._b2b('VOB_early')
        self._run('--date', 'VO_late=2026-10-08')
        early = Invoice.objects.get(b2b_order_id='VOB_early')
        late = Invoice.objects.get(b2c_order_id='VO_late')
        self.assertEqual(early.invoice_date, date(2026, 10, 5))
        self.assertEqual(late.invoice_date, date(2026, 10, 8))
        self.assertLess(early.number, late.number)
        self.assertEqual(early.price_mode, 'inclusive')
        self.assertEqual(early.grand_total, D('500.00'))
        self.assertEqual(late.place_of_supply_state, '32')
        self.assertIn('Nothing to backfill', self._run())

    def test_exclusive_switch_for_unpaid_b2b(self):
        self._b2b('VOB_x')
        self._run('--exclusive', 'VOB_x')
        order = B2BOrder.objects.get()
        self.assertEqual(order.total_amount, D('525.00'))
        self.assertEqual(Invoice.objects.get().grand_total, D('525.00'))

    def test_pages_render(self):
        o = self._b2c('VO_page', state='32')
        b = self._b2b('VOB_page')
        billing.ensure_invoice(o)
        billing.ensure_invoice(b)
        urls = [
            '/admin/orders/', f'/admin/orders/{o.id}/', f'/admin/orders/{o.id}/edit/',
            f'/admin/orders/{o.id}/invoice/', '/admin/b2b-orders/', '/admin/b2b-orders/create/',
            f'/admin/b2b-orders/{b.id}/invoice/', '/admin/gst/settings/', '/admin/gst/records/',
            f'/admin/gst/invoice/{Invoice.objects.get(b2b_order=b).id}/', '/admin/b2b/',
            '/admin/api/gst/settings/', '/admin/api/gst/records/?tab=missing',
            f'/admin/api/b2b-orders/?view=order&id={b.id}', f'/admin/api/gst/order/?order_id={o.id}',
        ]
        for url in urls:
            res = self.client.get(url)
            self.assertEqual(res.status_code, 200, url)
        summary = self.client.get(f'/admin/orders/{o.id}/invoice/').content.decode()
        self.assertIn('ORDER SUMMARY', summary)
        self.assertIn('This is not a tax invoice', summary)
        self.assertIn('Total GST', summary)
        self.assertIn(Invoice.objects.get(b2c_order=o).number, summary)
        res = self.client.get(f'/admin/gst/records/export/?fy=2026-27')
        self.assertEqual(res['Content-Type'], 'application/zip')
        names = zipfile.ZipFile(io.BytesIO(res.content)).namelist()
        self.assertIn('invoices.csv', names)
        self.assertEqual(len([n for n in names if n.endswith('.pdf')]), 2)

    def test_storefront_downloads(self):
        from accounts.models import User
        from storefront import services as sf

        customer = User.objects.create(brand=self.brand, mobile='9876543210', name='Asha')
        o = self._b2c('VO_sf', state='32')
        o.user = customer
        o.save()
        session = self.client.session
        session[sf.SESSION_USER_ID] = customer.id
        session.save()
        self.assertEqual(self.client.get(f'/tax-invoice/{o.id}.pdf').status_code, 404)  # not issued yet
        billing.ensure_invoice(o)
        res = self.client.get(f'/tax-invoice/{o.id}.pdf')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Cache-Control'], 'private, no-store')
        res = self.client.get(f'/order-summary/{o.id}.pdf')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.content.startswith(b'%PDF'))
        data = self.client.get('/api/dashboard-data/').json()
        self.assertEqual(data['orders'][0]['tax_invoice']['number'], Invoice.objects.get().number)
        other = self._b2c('VO_other', state='32')
        billing.ensure_invoice(other)
        self.assertEqual(self.client.get(f'/tax-invoice/{other.id}.pdf').status_code, 404)


class ConcurrentNumberingTests(TransactionTestCase):
    def setUp(self):
        seed_gst(self)

    def test_parallel_issue_gives_unique_consecutive_numbers(self):
        if connection.vendor != 'postgresql':
            self.skipTest('needs row locks')
        orders = []
        for i in range(6):
            o = Order.objects.create(id=f'VO_c{i}', brand=self.brand, name='C', mobile='9876543210',
                                     address='x', pincode='673001', shipping_state_code='32',
                                     price_mode='inclusive')
            OrderItem.objects.create(order=o, flavor=self.flavor, quantity=500, price_per_kg=1000,
                                     sale_price_per_kg=1000, flavor_name='Classic')
            orders.append(o)
        errors = []

        def work(order):
            from django.db import connection as conn
            try:
                with mock.patch('billing.services._safe_store_pdf'):
                    billing.ensure_invoice(order)
                    billing.ensure_invoice(order)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                conn.close()

        threads = [threading.Thread(target=work, args=(o,)) for o in orders]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        numbers = sorted(int(n[-7:]) for n in Invoice.objects.values_list('number', flat=True))
        self.assertEqual(numbers, list(range(1, 7)))


def tearDownModule():
    shutil.rmtree(MEDIA, ignore_errors=True)
