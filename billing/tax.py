"""
GST tax engine — pure functions, no database access.

compute_invoice() turns priced order lines into invoice lines with taxable
value and CGST/SGST or IGST per line. Rules (see the GST invoicing plan, 4.2):

  1. Start from each line's amount as charged (`gross`).
  2. Spread the order-level discount (coupon / B2B discount) over the paid
     lines pro rata, largest remainder in paise, so the pieces add up exactly.
  3. Delivery charge becomes one SHIPPING line per GST rate present, split in
     proportion to the (discounted) goods value at that rate.
  4. Inclusive mode carves the tax out of the line amount; exclusive mode adds
     it on top.
  5. Intra-state: CGST = SGST = round(taxable * rate / 200). In inclusive mode
     any odd paisa stays in the taxable value, so the line total is exactly
     the amount charged. Inter-state: IGST = round(taxable * rate / 100).
  6. No round-off line.
  7. Free items: zero taxable value and tax, HSN still shown.

credit_line_amounts() pro-rates one invoice line for a credit note so that
crediting a line in several steps always adds up to exactly the line.
"""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

ZERO = Decimal('0.00')
CENT = Decimal('0.01')
INCLUSIVE = 'inclusive'
EXCLUSIVE = 'exclusive'
PRICE_MODES = (INCLUSIVE, EXCLUSIVE)


def q2(value):
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def q3(value):
    return Decimal(str(value)).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)


def allocate(total, weights):
    """Split Decimal `total` across `weights` pro rata (largest remainder in
    paise). Returns a list of Decimals summing exactly to `total`."""
    total_paise = int(q2(total) * 100)
    weights = [Decimal(str(w)) for w in weights]
    weight_sum = sum(weights)
    if not weights:
        return []
    if weight_sum <= 0:
        shares = [0] * len(weights)
        shares[0] = total_paise
        return [Decimal(p) / 100 for p in shares]
    raw = [Fraction(total_paise) * Fraction(w) / Fraction(weight_sum) for w in weights]
    floors = [int(r // 1) for r in raw]
    leftover = total_paise - sum(floors)
    order = sorted(range(len(raw)), key=lambda i: (-(raw[i] - floors[i]), i))
    for i in order[:leftover]:
        floors[i] += 1
    return [(Decimal(p) / 100).quantize(CENT) for p in floors]


@dataclass
class LineInput:
    description: str
    gross: Decimal
    rate: Decimal
    hsn_code: str = ''
    uqc: str = ''
    quantity: Decimal = Decimal('1')
    source_quantity: int = 1
    is_free: bool = False
    cess_rate: Decimal = Decimal('0')
    ref: object = None


@dataclass
class LineResult:
    description: str
    kind: str
    hsn_code: str
    uqc: str
    quantity: Decimal
    source_quantity: int
    unit_price: Decimal
    gross_amount: Decimal
    discount_amount: Decimal
    taxable_value: Decimal
    gst_rate: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal
    cess_amount: Decimal
    total_amount: Decimal
    is_free: bool
    ref: object = None

    @property
    def tax_amount(self):
        return self.cgst_amount + self.sgst_amount + self.igst_amount + self.cess_amount


@dataclass
class TaxResult:
    lines: list = field(default_factory=list)
    price_mode: str = INCLUSIVE
    is_interstate: bool = False

    def _sum(self, attr):
        return sum((getattr(line, attr) for line in self.lines), ZERO)

    @property
    def gross_total(self):
        return self._sum('gross_amount')

    @property
    def discount_total(self):
        return self._sum('discount_amount')

    @property
    def taxable_total(self):
        return self._sum('taxable_value')

    @property
    def cgst_total(self):
        return self._sum('cgst_amount')

    @property
    def sgst_total(self):
        return self._sum('sgst_amount')

    @property
    def igst_total(self):
        return self._sum('igst_amount')

    @property
    def cess_total(self):
        return self._sum('cess_amount')

    @property
    def tax_total(self):
        return self.cgst_total + self.sgst_total + self.igst_total + self.cess_total

    @property
    def grand_total(self):
        return self._sum('total_amount')


def _tax_line(net, rate, cess_rate, price_mode, interstate):
    """Return (taxable, cgst, sgst, igst, cess) for one line amount."""
    rate = Decimal(str(rate))
    cess_rate = Decimal(str(cess_rate))
    if net <= 0:
        return ZERO, ZERO, ZERO, ZERO, ZERO
    if price_mode == INCLUSIVE:
        base = q2(net * 100 / (100 + rate + cess_rate))
    else:
        base = net
    cess = q2(base * cess_rate / 100)
    if interstate:
        igst = q2(base * rate / 100)
        cgst = sgst = ZERO
    else:
        cgst = sgst = q2(base * rate / 200)
        igst = ZERO
    if price_mode == INCLUSIVE:
        taxable = net - cgst - sgst - igst - cess
    else:
        taxable = base
    return taxable, cgst, sgst, igst, cess


def compute_invoice(lines, *, price_mode, interstate, discount=ZERO, shipping=ZERO,
                    shipping_description='Delivery charges'):
    if price_mode not in PRICE_MODES:
        raise ValueError(f'Unknown price mode {price_mode!r}')
    lines = list(lines)
    discount = q2(discount or 0)
    shipping = q2(shipping or 0)
    if discount < 0 or shipping < 0:
        raise ValueError('Discount and shipping must not be negative.')

    paid_idx = [i for i, ln in enumerate(lines) if not ln.is_free and q2(ln.gross) > 0]
    paid_gross = sum((q2(lines[i].gross) for i in paid_idx), ZERO)
    if discount > paid_gross:
        raise ValueError(f'Discount {discount} exceeds the goods value {paid_gross}.')

    shares = dict(zip(paid_idx, allocate(discount, [q2(lines[i].gross) for i in paid_idx])))

    result = TaxResult(price_mode=price_mode, is_interstate=interstate)
    net_by_rate = {}
    hsn_by_rate = {}
    for i, ln in enumerate(lines):
        gross = ZERO if ln.is_free else q2(ln.gross)
        disc = shares.get(i, ZERO)
        net = gross - disc
        rate = Decimal(str(ln.rate)).quantize(CENT)
        taxable, cgst, sgst, igst, cess = _tax_line(net, rate, ln.cess_rate, price_mode, interstate)
        qty = q3(ln.quantity)
        result.lines.append(LineResult(
            description=ln.description, kind='GOODS', hsn_code=ln.hsn_code, uqc=ln.uqc,
            quantity=qty, source_quantity=ln.source_quantity,
            unit_price=q2(gross / qty) if qty else gross,
            gross_amount=gross, discount_amount=disc, taxable_value=taxable, gst_rate=rate,
            cgst_amount=cgst, sgst_amount=sgst, igst_amount=igst, cess_amount=cess,
            total_amount=taxable + cgst + sgst + igst + cess, is_free=ln.is_free, ref=ln.ref,
        ))
        key = (rate, Decimal(str(ln.cess_rate)))
        if not ln.is_free:
            net_by_rate[key] = net_by_rate.get(key, ZERO) + net
            hsn_by_rate.setdefault(key, {})
            hsn_by_rate[key][ln.hsn_code] = hsn_by_rate[key].get(ln.hsn_code, ZERO) + net

    if shipping > 0:
        keys = [k for k, v in sorted(net_by_rate.items()) if v > 0]
        if not keys:
            rates = [(Decimal(str(ln.rate)).quantize(CENT), Decimal(str(ln.cess_rate))) for ln in lines]
            if not rates:
                raise ValueError('Cannot invoice a delivery charge without any goods.')
            keys = [max(rates)]
            hsn_by_rate.setdefault(keys[0], {next(
                (ln.hsn_code for ln in lines
                 if (Decimal(str(ln.rate)).quantize(CENT), Decimal(str(ln.cess_rate))) == keys[0]), ''): ZERO})
        parts = allocate(shipping, [net_by_rate.get(k, ZERO) for k in keys])
        for (rate, cess_rate), amount in zip(keys, parts):
            if amount <= 0:
                continue
            hsns = hsn_by_rate.get((rate, cess_rate)) or {'': ZERO}
            hsn = max(hsns.items(), key=lambda kv: kv[1])[0]
            taxable, cgst, sgst, igst, cess = _tax_line(amount, rate, cess_rate, price_mode, interstate)
            desc = shipping_description if len(keys) == 1 else f'{shipping_description} ({rate.normalize()}% goods)'
            result.lines.append(LineResult(
                description=desc, kind='SHIPPING', hsn_code=hsn, uqc='',
                quantity=Decimal('1.000'), source_quantity=1, unit_price=amount,
                gross_amount=amount, discount_amount=ZERO, taxable_value=taxable, gst_rate=rate,
                cgst_amount=cgst, sgst_amount=sgst, igst_amount=igst, cess_amount=cess,
                total_amount=taxable + cgst + sgst + igst + cess, is_free=False,
            ))
    return result


def hsn_summary(lines):
    """Group lines (anything with hsn_code, gst_rate, taxable_value and tax
    amounts) by (HSN, rate) for the invoice tax table and the CA export."""
    groups = {}
    for ln in lines:
        key = (ln.hsn_code, Decimal(str(ln.gst_rate)).quantize(CENT))
        g = groups.setdefault(key, {
            'hsn_code': key[0], 'gst_rate': key[1], 'uqc': getattr(ln, 'uqc', ''),
            'quantity': Decimal('0.000'), 'taxable_value': ZERO, 'cgst_amount': ZERO,
            'sgst_amount': ZERO, 'igst_amount': ZERO, 'cess_amount': ZERO, 'total_amount': ZERO,
        })
        if getattr(ln, 'kind', 'GOODS') == 'GOODS':
            g['quantity'] += Decimal(str(ln.quantity))
            if not g['uqc']:
                g['uqc'] = getattr(ln, 'uqc', '')
        for attr in ('taxable_value', 'cgst_amount', 'sgst_amount', 'igst_amount', 'cess_amount', 'total_amount'):
            g[attr] += getattr(ln, attr)
    for g in groups.values():
        g['tax_amount'] = g['cgst_amount'] + g['sgst_amount'] + g['igst_amount'] + g['cess_amount']
    return [groups[k] for k in sorted(groups)]


CREDIT_FIELDS = ('taxable_value', 'cgst_amount', 'sgst_amount', 'igst_amount', 'cess_amount')


def credit_line_amounts(line, earlier_source_qty, earlier, source_qty):
    """
    Amounts to credit for `source_qty` more units of invoice `line`.

    `line` has source_quantity, quantity and the CREDIT_FIELDS amounts;
    `earlier_source_qty` / `earlier` (dict of CREDIT_FIELDS sums) describe
    credit notes already issued against it. The cumulative credit is
    round(line_amount * cumulative_fraction), minus what was credited before,
    so a line credited in pieces always totals exactly the invoice line and
    CGST always equals SGST.
    """
    if source_qty <= 0:
        raise ValueError('Credited quantity must be positive.')
    if earlier_source_qty + source_qty > line.source_quantity:
        raise ValueError(
            f'Cannot credit {source_qty} — only {line.source_quantity - earlier_source_qty} left on the invoice line.'
        )
    cumulative = Fraction(earlier_source_qty + source_qty, line.source_quantity)
    out = {}
    for attr in CREDIT_FIELDS:
        full = getattr(line, attr)
        target = q2(Decimal(full) * Decimal(cumulative.numerator) / Decimal(cumulative.denominator))
        out[attr] = target - earlier.get(attr, ZERO)
    out['quantity'] = q3(Decimal(line.quantity) * Decimal(source_qty) / Decimal(line.source_quantity))
    out['total_amount'] = sum((out[a] for a in CREDIT_FIELDS), ZERO)
    return out
