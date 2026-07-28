from django.conf import settings
from django.db import models


class B2BCategory(models.Model):
    brand = models.ForeignKey('core.Brand', on_delete=models.CASCADE, related_name='b2b_categories')
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'b2b_categories'
        verbose_name_plural = 'B2B categories'

    def __str__(self):
        return self.name


class B2BCompany(models.Model):
    class Stage(models.TextChoices):
        LEAD = 'lead', 'Lead'
        CONTACTED = 'contacted', 'Contacted'
        NEGOTIATION = 'negotiation', 'Negotiation'
        CONVERTED = 'converted', 'Converted'
        LOST = 'lost', 'Lost'

    class DiscountType(models.TextChoices):
        PERCENTAGE = 'percentage', 'Percentage'
        AMOUNT = 'amount', 'Amount'

    brand = models.ForeignKey('core.Brand', on_delete=models.CASCADE, related_name='b2b_companies')
    category = models.ForeignKey(B2BCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='companies')
    company_name = models.CharField(max_length=255)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    pincode = models.CharField(max_length=10, blank=True)
    gst_number = models.CharField(max_length=20, blank=True)
    stage = models.CharField(max_length=20, choices=Stage.choices, default=Stage.LEAD)
    source = models.CharField(max_length=255, blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    discount_type = models.CharField(max_length=20, choices=DiscountType.choices, blank=True)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_terms_days = models.IntegerField(default=0)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    converted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'b2b_companies'
        verbose_name_plural = 'B2B companies'

    def __str__(self):
        return self.company_name


class B2BContact(models.Model):
    company = models.ForeignKey(B2BCompany, on_delete=models.CASCADE, related_name='contacts')
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, blank=True)
    email = models.CharField(max_length=255, blank=True)
    designation = models.CharField(max_length=255, blank=True)
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'b2b_contacts'

    def __str__(self):
        return f'{self.name} @ {self.company.company_name}'


class B2BActivity(models.Model):
    class Type(models.TextChoices):
        CALL = 'call', 'Call'
        MEETING = 'meeting', 'Meeting'
        EMAIL = 'email', 'Email'
        WHATSAPP = 'whatsapp', 'WhatsApp'
        NOTE = 'note', 'Note'
        STAGE_CHANGE = 'stage_change', 'Stage Change'
        ORDER = 'order', 'Order'
        PAYMENT = 'payment', 'Payment'
        RETURN = 'return', 'Return'

    company = models.ForeignKey(B2BCompany, on_delete=models.CASCADE, related_name='activities')
    contact = models.ForeignKey(B2BContact, on_delete=models.SET_NULL, null=True, blank=True, related_name='activities')
    admin_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+',
    )
    type = models.CharField(max_length=20, choices=Type.choices)
    subject = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    old_stage = models.CharField(max_length=20, blank=True)
    new_stage = models.CharField(max_length=20, blank=True)
    follow_up_date = models.DateField(null=True, blank=True)
    is_follow_up_done = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'b2b_activities'
        verbose_name_plural = 'B2B activities'

    def __str__(self):
        return f'{self.type} — {self.company.company_name}'
