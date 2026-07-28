from django.conf import settings
from django.db import models


class Coupon(models.Model):
    brand = models.ForeignKey('core.Brand', on_delete=models.CASCADE, related_name='coupons')
    code = models.CharField(max_length=50)
    discount_amount = models.FloatField(null=True, blank=True)
    discount_percentage = models.IntegerField(null=True, blank=True)
    min_order_value = models.FloatField(default=0)
    min_quantity = models.FloatField(default=0)
    valid_flavors = models.JSONField(null=True, blank=True)
    max_discount_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    max_uses = models.IntegerField(null=True, blank=True)
    used_count = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    start_date = models.DateTimeField(null=True, blank=True)
    expiry_date = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'coupons'
        unique_together = ('brand', 'code')

    def __str__(self):
        return self.code


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        CONFIRMED = 'confirmed', 'Confirmed'
        PROCESSING = 'processing', 'Processing'
        SHIPPED = 'shipped', 'Shipped'
        DELIVERED = 'delivered', 'Delivered'
        CANCELLED = 'cancelled', 'Cancelled'
        FAILED = 'failed', 'Failed'
        DELETED = 'deleted', 'Deleted'

    class PaymentStatus(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PAID = 'paid', 'Paid'
        FAILED = 'failed', 'Failed'
        REFUND_INITIATED = 'refund_initiated', 'Refund Initiated'
        REFUNDED = 'refunded', 'Refunded'
        CANCELLED = 'cancelled', 'Cancelled'

    # PREFIX_uniqid, e.g. "VO_669f1ff5" (prefix from brand.order_prefix).
    id = models.CharField(max_length=20, primary_key=True)
    brand = models.ForeignKey('core.Brand', on_delete=models.CASCADE, related_name='orders')
    user = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='orders',
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    name = models.CharField(max_length=255)
    mobile = models.CharField(max_length=15)
    address = models.TextField(blank=True)
    pincode = models.CharField(max_length=10, blank=True)
    delivery_charge = models.FloatField(default=0)
    referral = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='referred_orders',
    )
    coupon = models.ForeignKey(Coupon, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    coupon_code = models.CharField(max_length=50, blank=True)
    coupon_discount = models.FloatField(default=0)
    stock_deducted = models.BooleanField(default=False)
    order_date = models.DateTimeField(auto_now_add=True)
    razorpay_order_id = models.CharField(max_length=255, blank=True)
    razorpay_payment_id = models.CharField(max_length=255, blank=True)
    payment_date = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'orders'

    def __str__(self):
        return self.id


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    flavor = models.ForeignKey('products.Flavor', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    flavor_pack = models.ForeignKey('products.FlavorPack', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    quantity = models.IntegerField(help_text='In grams')
    price_per_kg = models.FloatField()
    sale_price_per_kg = models.FloatField()
    flavor_name = models.CharField(max_length=255)
    pack_label = models.CharField(max_length=50, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'order_items'

    def __str__(self):
        return f'{self.flavor_name} x{self.quantity}g'


class B2BOffer(models.Model):
    class Type(models.TextChoices):
        BUY_X_GET_Y = 'buy_x_get_y', 'Buy X Get Y'
        DISCOUNT_PERCENT = 'discount_percent', 'Discount Percent'
        FLAT_DISCOUNT = 'flat_discount', 'Flat Discount'

    brand = models.ForeignKey('core.Brand', on_delete=models.CASCADE, related_name='b2b_offers')
    # NULL company = global offer.
    company = models.ForeignKey('crm.B2BCompany', on_delete=models.CASCADE, null=True, blank=True, related_name='offers')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    type = models.CharField(max_length=20, choices=Type.choices)
    min_quantity = models.IntegerField(null=True, blank=True)
    free_quantity = models.IntegerField(null=True, blank=True)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    is_stackable = models.BooleanField(default=False)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'b2b_offers'

    def __str__(self):
        return self.name


class B2BOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        CONFIRMED = 'confirmed', 'Confirmed'
        DISPATCHED = 'dispatched', 'Dispatched'
        DELIVERED = 'delivered', 'Delivered'
        CANCELLED = 'cancelled', 'Cancelled'

    class PaymentStatus(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PARTIAL = 'partial', 'Partial'
        PAID = 'paid', 'Paid'

    id = models.CharField(max_length=50, primary_key=True)
    brand = models.ForeignKey('core.Brand', on_delete=models.CASCADE, related_name='b2b_orders')
    company = models.ForeignKey('crm.B2BCompany', on_delete=models.CASCADE, related_name='orders')
    contact = models.ForeignKey('crm.B2BContact', on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_type = models.CharField(max_length=20, blank=True)
    discount_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    offer_discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    balance_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    due_date = models.DateField(null=True, blank=True)
    invoice_number = models.CharField(max_length=100, blank=True)
    source_order = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='repeats')
    notes = models.TextField(blank=True)
    stock_deducted = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    order_date = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'b2b_orders'

    def __str__(self):
        return self.id


class B2BOrderItem(models.Model):
    b2b_order = models.ForeignKey(B2BOrder, on_delete=models.CASCADE, related_name='items')
    flavor = models.ForeignKey('products.Flavor', on_delete=models.PROTECT, related_name='+')
    flavor_pack = models.ForeignKey('products.FlavorPack', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    stock = models.ForeignKey('products.Stock', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    quantity = models.IntegerField()
    weight_grams = models.IntegerField()
    total_weight_grams = models.IntegerField()
    mrp = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    is_free_item = models.BooleanField(default=False)
    offer = models.ForeignKey(B2BOffer, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    flavor_name = models.CharField(max_length=255)
    pack_label = models.CharField(max_length=50, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'b2b_order_items'

    def __str__(self):
        return f'{self.flavor_name} x{self.quantity}'


class B2BPayment(models.Model):
    class PaymentType(models.TextChoices):
        PAYMENT = 'payment', 'Payment'
        REFUND = 'refund', 'Refund'

    class PaymentMethod(models.TextChoices):
        CASH = 'cash', 'Cash'
        UPI = 'upi', 'UPI'
        BANK_TRANSFER = 'bank_transfer', 'Bank Transfer'
        CHEQUE = 'cheque', 'Cheque'
        CREDIT_ADJUSTMENT = 'credit_adjustment', 'Credit Adjustment'

    company = models.ForeignKey('crm.B2BCompany', on_delete=models.CASCADE, related_name='payments')
    b2b_order = models.ForeignKey(B2BOrder, on_delete=models.SET_NULL, null=True, blank=True, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_type = models.CharField(max_length=20, choices=PaymentType.choices, default=PaymentType.PAYMENT)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    reference_number = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    payment_date = models.DateField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'b2b_payments'

    def __str__(self):
        return f'{self.payment_type} {self.amount}'
