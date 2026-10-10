"""
GST billing: the legal entity, HSN codes and rates, numbered tax invoices and
credit notes.

Issued documents (Invoice, InvoiceLine, CreditNote, CreditNoteLine) are
immutable: `save()` on an existing row and `delete()` both raise. The few
fields that may legitimately change after issue (status, PDF, e-way bill)
are written with `QuerySet.update()` from billing.services, never `save()`.
"""
from django.conf import settings
from django.db import models
from django.db.models import Q

from billing.gst_states import STATES


class ImmutableDocumentError(Exception):
    pass


class ImmutableModel(models.Model):
    """Rows can be created, never edited or deleted through the ORM."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableDocumentError(f'{type(self).__name__} rows cannot be edited once issued.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableDocumentError(f'{type(self).__name__} rows cannot be deleted.')


class LegalEntity(models.Model):
    """The GST-registered business. One row today (Varthaai Foods LLP); every
    brand bills under it via Brand.legal_entity."""
    legal_name = models.CharField(max_length=255)
    trade_name = models.CharField(max_length=255, blank=True)
    gstin = models.CharField(max_length=15, blank=True)
    pan = models.CharField(max_length=10, blank=True)
    state_code = models.CharField(max_length=2, choices=STATES, default='32')
    address = models.TextField(blank=True)
    fssai_no = models.CharField(max_length=50, blank=True)
    email = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    bank_name = models.CharField(max_length=255, blank=True)
    bank_account_no = models.CharField(max_length=50, blank=True)
    bank_ifsc = models.CharField(max_length=20, blank=True)
    bank_branch = models.CharField(max_length=255, blank=True)
    upi_id = models.CharField(max_length=100, blank=True)
    jurisdiction = models.CharField(max_length=100, blank=True, default='Kozhikode')
    signatory = models.CharField(max_length=255, blank=True)
    b2b_terms = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'legal_entities'
        verbose_name_plural = 'legal entities'

    def __str__(self):
        return self.legal_name


class HSNCode(models.Model):
    class UQC(models.TextChoices):
        KGS = 'KGS', 'KGS — Kilograms'
        PCS = 'PCS', 'PCS — Pieces'
        NOS = 'NOS', 'NOS — Numbers'
        PAC = 'PAC', 'PAC — Packs'

    code = models.CharField(max_length=8, unique=True)
    description = models.CharField(max_length=255, blank=True)
    uqc = models.CharField(max_length=3, choices=UQC.choices, default=UQC.KGS)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'hsn_codes'

    def __str__(self):
        return self.code


class HSNRate(models.Model):
    """GST rate for an HSN, valid from `effective_from` until `effective_to`
    (inclusive, open-ended when NULL). Looked up by invoice date."""
    hsn = models.ForeignKey(HSNCode, on_delete=models.CASCADE, related_name='rates')
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, help_text='Total rate, e.g. 5.00')
    cess_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'hsn_rates'
        ordering = ['hsn_id', '-effective_from']

    def __str__(self):
        return f'{self.hsn.code} @ {self.gst_rate}% from {self.effective_from}'


class InvoiceSeries(models.Model):
    class DocType(models.TextChoices):
        INVOICE = 'INV', 'Tax Invoice'
        CREDIT_NOTE = 'CN', 'Credit Note'

    entity = models.ForeignKey(LegalEntity, on_delete=models.PROTECT, related_name='series')
    fiscal_year = models.CharField(max_length=7, help_text="e.g. '2026-27'")
    doc_type = models.CharField(max_length=3, choices=DocType.choices)
    prefix = models.CharField(max_length=10, blank=True)
    pad_width = models.PositiveSmallIntegerField(default=7)
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'invoice_series'
        constraints = [
            models.UniqueConstraint(fields=['entity', 'fiscal_year', 'doc_type'], name='uniq_series_entity_fy_type'),
        ]

    def __str__(self):
        return f'{self.doc_type} {self.fiscal_year} (last {self.last_number})'


class Invoice(ImmutableModel):
    class SupplyType(models.TextChoices):
        B2B = 'B2B', 'B2B'
        B2C = 'B2C', 'B2C'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        CANCELLED = 'cancelled', 'Cancelled (credit note issued, reissued)'

    entity = models.ForeignKey(LegalEntity, on_delete=models.PROTECT, related_name='invoices')
    brand = models.ForeignKey('core.Brand', on_delete=models.PROTECT, related_name='+')
    series = models.ForeignKey(InvoiceSeries, on_delete=models.PROTECT, related_name='invoices')
    number = models.CharField(max_length=16)
    fiscal_year = models.CharField(max_length=7)
    invoice_date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    b2c_order = models.ForeignKey(
        'orders.Order', on_delete=models.PROTECT, null=True, blank=True, related_name='invoices',
    )
    b2b_order = models.ForeignKey(
        'orders.B2BOrder', on_delete=models.PROTECT, null=True, blank=True, related_name='invoices',
    )
    reissue_of = models.ForeignKey(
        'self', on_delete=models.PROTECT, null=True, blank=True, related_name='reissues',
    )
    supply_type = models.CharField(max_length=3, choices=SupplyType.choices)
    price_mode = models.CharField(max_length=10)
    # Seller snapshot
    seller_legal_name = models.CharField(max_length=255)
    seller_trade_name = models.CharField(max_length=255, blank=True)
    seller_gstin = models.CharField(max_length=15)
    seller_address = models.TextField(blank=True)
    seller_state_code = models.CharField(max_length=2)
    seller_fssai = models.CharField(max_length=50, blank=True)
    seller_phone = models.CharField(max_length=50, blank=True)
    seller_email = models.CharField(max_length=255, blank=True)
    seller_extra = models.JSONField(default=dict, blank=True, help_text='Bank, jurisdiction, signatory, terms')
    brand_name = models.CharField(max_length=255, blank=True)
    # Bill-to snapshot
    bill_to_name = models.CharField(max_length=255)
    bill_to_gstin = models.CharField(max_length=15, blank=True)
    bill_to_address = models.TextField(blank=True)
    bill_to_state_code = models.CharField(max_length=2, blank=True)
    bill_to_pincode = models.CharField(max_length=10, blank=True)
    # Ship-to snapshot
    ship_to_same = models.BooleanField(default=True)
    ship_to_name = models.CharField(max_length=255, blank=True)
    ship_to_address = models.TextField(blank=True)
    ship_to_state_code = models.CharField(max_length=2, blank=True)
    ship_to_pincode = models.CharField(max_length=10, blank=True)
    ship_to_gstin = models.CharField(max_length=15, blank=True)
    place_of_supply_state = models.CharField(max_length=2)
    is_interstate = models.BooleanField(default=False)
    customer_po_no = models.CharField(max_length=100, blank=True)
    customer_po_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    # Totals
    gross_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    taxable_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cgst_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sgst_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    igst_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cess_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    round_off = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Reserved for e-invoicing / e-way bill
    irn = models.CharField(max_length=64, blank=True)
    ack_no = models.CharField(max_length=30, blank=True)
    ack_date = models.DateTimeField(null=True, blank=True)
    signed_qr = models.TextField(blank=True)
    eway_bill_no = models.CharField(max_length=20, blank=True)
    eway_bill_date = models.DateField(null=True, blank=True)
    # Stored PDF (generated once at issue)
    pdf_file = models.FileField(upload_to='gst/invoices/', blank=True)
    pdf_sha256 = models.CharField(max_length=64, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'invoices'
        ordering = ['-invoice_date', '-id']
        constraints = [
            models.UniqueConstraint(fields=['series', 'number'], name='uniq_invoice_series_number'),
            models.CheckConstraint(
                condition=(
                    Q(b2c_order__isnull=False, b2b_order__isnull=True)
                    | Q(b2c_order__isnull=True, b2b_order__isnull=False)
                ),
                name='invoice_exactly_one_order',
            ),
            models.UniqueConstraint(
                fields=['b2c_order'], condition=Q(status='active', b2c_order__isnull=False),
                name='uniq_active_invoice_b2c_order',
            ),
            models.UniqueConstraint(
                fields=['b2b_order'], condition=Q(status='active', b2b_order__isnull=False),
                name='uniq_active_invoice_b2b_order',
            ),
        ]

    def __str__(self):
        return self.number

    @property
    def order(self):
        return self.b2c_order if self.b2c_order_id else self.b2b_order

    @property
    def order_id(self):
        return self.b2c_order_id or self.b2b_order_id

    @property
    def tax_total(self):
        return self.cgst_total + self.sgst_total + self.igst_total + self.cess_total


class InvoiceLine(ImmutableModel):
    class Kind(models.TextChoices):
        GOODS = 'GOODS', 'Goods'
        SHIPPING = 'SHIPPING', 'Shipping'

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='lines')
    line_no = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.GOODS)
    b2c_item = models.ForeignKey('orders.OrderItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    b2b_item = models.ForeignKey('orders.B2BOrderItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    description = models.CharField(max_length=255)
    hsn_code = models.CharField(max_length=8, blank=True)
    uqc = models.CharField(max_length=3, blank=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    # Quantity in the order's own unit (grams for B2C, packs for B2B, 1 for
    # shipping) — credit notes pro-rate against this exactly.
    source_quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    gross_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    taxable_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    cgst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sgst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    igst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cess_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_free = models.BooleanField(default=False)

    class Meta:
        db_table = 'invoice_lines'
        ordering = ['invoice_id', 'line_no']

    @property
    def tax_amount(self):
        return self.cgst_amount + self.sgst_amount + self.igst_amount + self.cess_amount


class CreditNote(ImmutableModel):
    class Reason(models.TextChoices):
        SALES_RETURN = 'sales_return', 'Sales return'
        POST_SALE_DISCOUNT = 'post_sale_discount', 'Post-sale discount'
        DEFICIENCY = 'deficiency', 'Deficiency in goods'
        CORRECTION = 'correction', 'Correction in invoice'
        OTHER = 'other', 'Other'

    entity = models.ForeignKey(LegalEntity, on_delete=models.PROTECT, related_name='credit_notes')
    brand = models.ForeignKey('core.Brand', on_delete=models.PROTECT, related_name='+')
    series = models.ForeignKey(InvoiceSeries, on_delete=models.PROTECT, related_name='credit_notes')
    number = models.CharField(max_length=16)
    fiscal_year = models.CharField(max_length=7)
    credit_note_date = models.DateField()
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='credit_notes')
    reason = models.CharField(max_length=20, choices=Reason.choices)
    notes = models.TextField(blank=True)
    taxable_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cgst_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sgst_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    igst_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cess_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    irn = models.CharField(max_length=64, blank=True)
    ack_no = models.CharField(max_length=30, blank=True)
    ack_date = models.DateTimeField(null=True, blank=True)
    pdf_file = models.FileField(upload_to='gst/credit_notes/', blank=True)
    pdf_sha256 = models.CharField(max_length=64, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'credit_notes'
        ordering = ['-credit_note_date', '-id']
        constraints = [
            models.UniqueConstraint(fields=['series', 'number'], name='uniq_credit_note_series_number'),
        ]

    def __str__(self):
        return self.number

    @property
    def tax_total(self):
        return self.cgst_total + self.sgst_total + self.igst_total + self.cess_total


class CreditNoteLine(ImmutableModel):
    credit_note = models.ForeignKey(CreditNote, on_delete=models.PROTECT, related_name='lines')
    invoice_line = models.ForeignKey(InvoiceLine, on_delete=models.PROTECT, related_name='credit_lines')
    line_no = models.PositiveSmallIntegerField()
    source_quantity = models.PositiveIntegerField()
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    taxable_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cgst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sgst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    igst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cess_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    restock = models.BooleanField(default=False)

    class Meta:
        db_table = 'credit_note_lines'
        ordering = ['credit_note_id', 'line_no']

    @property
    def tax_amount(self):
        return self.cgst_amount + self.sgst_amount + self.igst_amount + self.cess_amount
