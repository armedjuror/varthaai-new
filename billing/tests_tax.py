"""Unit tests for the pure GST tax engine (billing.tax)."""
import random
from decimal import Decimal as D
from types import SimpleNamespace

from django.test import SimpleTestCase

from billing.gstin import gstin_checksum, validate_gstin
from billing.gst_states import normalise_state_code, state_from_pincode
from billing.tax import (
    EXCLUSIVE, INCLUSIVE, LineInput, allocate, compute_invoice, credit_line_amounts, hsn_summary,
)


def L(gross, rate='5', free=False, hsn='2008', qty='1', src=1):
    return LineInput(description='x', gross=D(gross), rate=D(rate), hsn_code=hsn,
                     quantity=D(qty), source_quantity=src, is_free=free)


class AllocateTests(SimpleTestCase):
    def test_sums_exactly(self):
        parts = allocate(D('10.00'), [1, 1, 1])
        self.assertEqual(sum(parts), D('10.00'))
        self.assertEqual(sorted(parts), [D('3.33'), D('3.33'), D('3.34')])

    def test_zero_weights_put_all_on_first(self):
        self.assertEqual(allocate(D('5'), [0, 0]), [D('5.00'), D('0.00')])


class ComputeInvoiceTests(SimpleTestCase):
    def test_inclusive_intra_state(self):
        r = compute_invoice([L('105.00')], price_mode=INCLUSIVE, interstate=False)
        ln = r.lines[0]
        self.assertEqual(ln.taxable_value, D('100.00'))
        self.assertEqual(ln.cgst_amount, D('2.50'))
        self.assertEqual(ln.sgst_amount, D('2.50'))
        self.assertEqual(r.grand_total, D('105.00'))

    def test_inclusive_odd_paisa_goes_to_taxable(self):
        r = compute_invoice([L('99.99')], price_mode=INCLUSIVE, interstate=False)
        ln = r.lines[0]
        self.assertEqual(ln.cgst_amount, ln.sgst_amount)
        self.assertEqual(ln.total_amount, D('99.99'))

    def test_inclusive_inter_state(self):
        r = compute_invoice([L('105.00')], price_mode=INCLUSIVE, interstate=True)
        ln = r.lines[0]
        self.assertEqual((ln.taxable_value, ln.igst_amount, ln.cgst_amount), (D('100.00'), D('5.00'), D('0.00')))

    def test_exclusive_adds_tax_on_top(self):
        r = compute_invoice([L('1000.00', '12')], price_mode=EXCLUSIVE, interstate=False)
        self.assertEqual(r.taxable_total, D('1000.00'))
        self.assertEqual(r.cgst_total, D('60.00'))
        self.assertEqual(r.grand_total, D('1120.00'))

    def test_discount_spread_and_shipping_per_rate(self):
        r = compute_invoice(
            [L('300', '5'), L('100', '12'), L('50', '5', free=True)],
            price_mode=INCLUSIVE, interstate=False, discount=D('40'), shipping=D('50'),
        )
        goods = [ln for ln in r.lines if ln.kind == 'GOODS']
        ship = [ln for ln in r.lines if ln.kind == 'SHIPPING']
        self.assertEqual(sum(ln.discount_amount for ln in goods), D('40.00'))
        self.assertEqual(goods[2].total_amount, D('0.00'))
        self.assertEqual(goods[2].hsn_code, '2008')
        self.assertEqual(len(ship), 2)
        self.assertEqual(sum(ln.gross_amount for ln in ship), D('50.00'))
        self.assertEqual({ln.gst_rate for ln in ship}, {D('5.00'), D('12.00')})
        self.assertEqual(r.grand_total, D('300') + D('100') - D('40') + D('50'))

    def test_discount_larger_than_goods_rejected(self):
        with self.assertRaises(ValueError):
            compute_invoice([L('10')], price_mode=INCLUSIVE, interstate=False, discount=D('11'))

    def test_random_orders_tie_out(self):
        rng = random.Random(42)
        for _ in range(2000):
            lines = [
                L(str(D(rng.randint(1, 500000)) / 100), rng.choice(['0', '5', '12', '18']),
                  free=rng.random() < 0.1)
                for _ in range(rng.randint(1, 6))
            ]
            paid = sum((ln.gross for ln in lines if not ln.is_free), D('0'))
            discount = (paid * D(rng.randint(0, 30)) / 100).quantize(D('0.01'))
            shipping = D(rng.choice([0, 0, 40, 50, 99.5]))
            if paid == 0:
                shipping = D('0')
            mode = rng.choice([INCLUSIVE, EXCLUSIVE])
            inter = rng.random() < 0.5
            r = compute_invoice(lines, price_mode=mode, interstate=inter, discount=discount, shipping=shipping)
            charged = paid - discount + shipping
            if mode == INCLUSIVE:
                self.assertEqual(r.grand_total, charged)
            else:
                self.assertEqual(r.taxable_total, charged)
                self.assertEqual(r.grand_total, charged + r.tax_total)
            for ln in r.lines:
                self.assertEqual(ln.cgst_amount, ln.sgst_amount)
                if inter:
                    self.assertEqual(ln.cgst_amount, D('0'))
                else:
                    self.assertEqual(ln.igst_amount, D('0'))
                self.assertEqual(ln.total_amount, ln.taxable_value + ln.tax_amount)

    def test_hsn_summary_groups_by_hsn_and_rate(self):
        r = compute_invoice([L('105', '5', qty='1'), L('210', '5', qty='2')], price_mode=INCLUSIVE, interstate=False)
        summary = hsn_summary(r.lines)
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]['quantity'], D('3.000'))
        self.assertEqual(summary[0]['total_amount'], D('315.00'))


class CreditLineTests(SimpleTestCase):
    def _line(self):
        return SimpleNamespace(source_quantity=3, quantity=D('1.500'), taxable_value=D('100.01'),
                               cgst_amount=D('2.51'), sgst_amount=D('2.51'), igst_amount=D('0'),
                               cess_amount=D('0'))

    def test_credit_in_pieces_sums_to_line(self):
        line = self._line()
        earlier = {}
        done = 0
        for qty in (1, 1, 1):
            amounts = credit_line_amounts(line, done, earlier, qty)
            self.assertEqual(amounts['cgst_amount'], amounts['sgst_amount'])
            for k in ('taxable_value', 'cgst_amount', 'sgst_amount', 'igst_amount', 'cess_amount'):
                earlier[k] = earlier.get(k, D('0')) + amounts[k]
            done += qty
        self.assertEqual(earlier['taxable_value'], D('100.01'))
        self.assertEqual(earlier['cgst_amount'], D('2.51'))

    def test_cannot_over_credit(self):
        with self.assertRaises(ValueError):
            credit_line_amounts(self._line(), 2, {}, 2)


class GSTINAndStateTests(SimpleTestCase):
    def test_valid_gstin(self):
        body = '29ABCDE1234F1Z'
        gstin = body + gstin_checksum(body)
        self.assertEqual(validate_gstin(gstin.lower()), (gstin, None))

    def test_bad_checksum(self):
        body = '29ABCDE1234F1Z'
        wrong = '0' if gstin_checksum(body) != '0' else '1'
        self.assertIsNotNone(validate_gstin(body + wrong)[1])

    def test_state_helpers(self):
        self.assertEqual(normalise_state_code('kerala'), '32')
        self.assertEqual(normalise_state_code('9'), '09')
        self.assertEqual(state_from_pincode('673001'), '32')
        self.assertEqual(state_from_pincode('560001'), '29')
        self.assertEqual(state_from_pincode('12'), '')
