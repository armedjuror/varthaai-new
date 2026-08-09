from django.conf import settings
from django.db import models


class ExpenseCategory(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=7, default='#007bff')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'expense_categories'
        verbose_name_plural = 'Expense categories'

    def __str__(self):
        return self.name


class Expense(models.Model):
    class PaymentMethod(models.TextChoices):
        CASH = 'cash', 'Cash'
        BANK_TRANSFER = 'bank_transfer', 'Bank Transfer'
        UPI = 'upi', 'UPI'
        CHEQUE = 'cheque', 'Cheque'
        CARD = 'card', 'Card'

    class PaymentStatus(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PAID = 'paid', 'Paid'
        OVERDUE = 'overdue', 'Overdue'

    class RecurringPeriod(models.TextChoices):
        WEEKLY = 'weekly', 'Weekly'
        MONTHLY = 'monthly', 'Monthly'
        QUARTERLY = 'quarterly', 'Quarterly'
        YEARLY = 'yearly', 'Yearly'

    brand = models.ForeignKey('core.Brand', on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses')
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name='expenses')
    stock = models.ForeignKey('products.Stock', on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    expense_date = models.DateField()
    vendor_name = models.CharField(max_length=255, blank=True)
    vendor_contact = models.CharField(max_length=255, blank=True)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices)
    invoice_number = models.CharField(max_length=100, blank=True)
    receipt_image = models.ImageField(upload_to='receipts/', null=True, blank=True)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_recurring = models.BooleanField(default=False)
    recurring_period = models.CharField(max_length=20, choices=RecurringPeriod.choices, null=True, blank=True)
    tags = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'expenses'

    def __str__(self):
        return f'{self.title} ({self.amount})'


class ExpenseAttachment(models.Model):
    expense = models.ForeignKey(Expense, on_delete=models.CASCADE, related_name='attachments')
    filename = models.CharField(max_length=255)
    original_filename = models.CharField(max_length=255)
    file_path = models.CharField(max_length=500)
    file_size = models.IntegerField()
    mime_type = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'expense_attachments'

    def __str__(self):
        return self.original_filename


class Income(models.Model):
    """Ledger row booked when a B2C or B2B order becomes fully paid.

    Kept in sync by `finance.services.sync_b2c_order_income` /
    `sync_b2b_order_income`, called from every code path that changes an
    order's payment_status (admin actions, Razorpay webhook/verify, B2B
    payment recording). One row per paid order — removed again if the order
    is later cancelled/deleted or its payment status regresses.
    """
    class SourceType(models.TextChoices):
        B2C_ORDER = 'b2c_order', 'B2C Order'
        B2B_ORDER = 'b2b_order', 'B2B Order'

    brand = models.ForeignKey('core.Brand', on_delete=models.CASCADE, related_name='incomes')
    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    order = models.ForeignKey(
        'orders.Order', on_delete=models.CASCADE,
        null=True, blank=True, related_name='income_entries',
    )
    b2b_order = models.ForeignKey(
        'orders.B2BOrder', on_delete=models.CASCADE,
        null=True, blank=True, related_name='income_entries',
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    income_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'incomes'
        constraints = [
            models.UniqueConstraint(
                fields=['order'], condition=models.Q(order__isnull=False),
                name='uniq_income_per_b2c_order',
            ),
            models.UniqueConstraint(
                fields=['b2b_order'], condition=models.Q(b2b_order__isnull=False),
                name='uniq_income_per_b2b_order',
            ),
        ]

    def __str__(self):
        return f'{self.source_type} #{self.order_id or self.b2b_order_id} ({self.amount})'


class Investment(models.Model):
    partner_name = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    investment_date = models.DateField()
    description = models.TextField(blank=True)
    payment_method = models.CharField(max_length=50, blank=True)
    reference_number = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'investments'

    def __str__(self):
        return f'{self.partner_name} ({self.amount})'
