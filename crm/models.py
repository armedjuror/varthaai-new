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

    class UpfrontDue(models.TextChoices):
        ADVANCE = 'advance', 'With the order'
        DELIVERY = 'delivery', 'On delivery'

    class BalanceRule(models.TextChoices):
        BILL_TO_BILL = 'bill_to_bill', 'Bill to bill'
        WEEKLY_AS_SOLD = 'weekly_as_sold', 'Weekly as sold'
        AFTER_DAYS = 'after_days', 'After N days'
        FIXED_DATES = 'fixed_dates', 'Fixed dates'

    brand = models.ForeignKey('core.Brand', on_delete=models.CASCADE, related_name='b2b_companies')
    category = models.ForeignKey(B2BCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='companies')
    company_name = models.CharField(max_length=255)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    pincode = models.CharField(max_length=10, blank=True)
    gst_number = models.CharField(max_length=20, blank=True)
    # GST state code of the billing address (derived from the GSTIN when one
    # is set) and the legal name registered against the GSTIN.
    state_code = models.CharField(max_length=2, blank=True)
    gst_legal_name = models.CharField(max_length=255, blank=True)
    # Where the store is. Captured as coordinates from the field phone
    # ("Use current location"); `location_url` is the fallback for leads
    # added away from the store (a pasted Google Maps link). Read the link
    # through `maps_url`, which prefers the coordinates.
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    location_url = models.URLField(max_length=500, blank=True)
    photo = models.ImageField(upload_to='leads/', null=True, blank=True)
    stage = models.CharField(max_length=20, choices=Stage.choices, default=Stage.LEAD)
    source = models.CharField(max_length=255, blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    discount_type = models.CharField(max_length=20, choices=DiscountType.choices, blank=True)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Payment terms, in two parts: an upfront share of each order, then a
    # rule for when the balance is due (see crm.services.payment_due_date).
    upfront_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    upfront_due = models.CharField(max_length=10, choices=UpfrontDue.choices, default=UpfrontDue.DELIVERY)
    balance_rule = models.CharField(max_length=20, choices=BalanceRule.choices, default=BalanceRule.BILL_TO_BILL)
    # N for "after N days", and the backstop for bill to bill.
    payment_terms_days = models.IntegerField(default=30)
    # Days of the month the balance falls due on, e.g. [1, 15].
    payment_fixed_dates = models.JSONField(default=list, blank=True)
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

    @property
    def maps_url(self):
        if self.latitude is not None and self.longitude is not None:
            return f'https://www.google.com/maps/search/?api=1&query={self.latitude},{self.longitude}'
        return self.location_url


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
        VISIT = 'visit', 'Field Visit'
        STAGE_CHANGE = 'stage_change', 'Stage Change'
        ORDER = 'order', 'Order'
        PAYMENT = 'payment', 'Payment'
        RETURN = 'return', 'Return'

    class VisitPurpose(models.TextChoices):
        NEW_LEAD = 'new_lead', 'New Lead'
        RETARGET = 'retarget', 'Retarget'
        DELIVERY = 'delivery', 'Delivery'
        COLLECTION = 'collection', 'Collection'
        AUDIT = 'audit', 'Audit'

    # Purposes where the visit records packs on the shelf per flavour — on a
    # delivery, counted before restocking, so sell-through can be measured.
    PACK_COUNT_PURPOSES = ('audit', 'collection', 'delivery')

    class VisitOutcome(models.TextChoices):
        ORDER = 'order', 'Order'
        FOLLOW_UP = 'follow_up', 'Follow Up'
        REJECTED = 'rejected', 'Rejected'
        NONE = 'none', 'None'

    company = models.ForeignKey(B2BCompany, on_delete=models.CASCADE, related_name='activities')
    contact = models.ForeignKey(B2BContact, on_delete=models.SET_NULL, null=True, blank=True, related_name='activities')
    order = models.ForeignKey(
        'orders.B2BOrder', on_delete=models.SET_NULL, null=True, blank=True, related_name='activities',
    )
    admin_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+',
    )
    type = models.CharField(max_length=20, choices=Type.choices)
    subject = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    old_stage = models.CharField(max_length=20, blank=True)
    new_stage = models.CharField(max_length=20, blank=True)
    # Only set (and only valid) when type == VISIT.
    visit_purpose = models.CharField(max_length=20, choices=VisitPurpose.choices, blank=True)
    visit_outcome = models.CharField(max_length=20, choices=VisitOutcome.choices, blank=True)
    # Packs on the shelf per flavour, {"<flavour_id>": count}. Only set for
    # PACK_COUNT_PURPOSES visits; null otherwise.
    pack_counts = models.JSONField(null=True, blank=True)
    follow_up_date = models.DateField(null=True, blank=True)
    is_follow_up_done = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'b2b_activities'
        verbose_name_plural = 'B2B activities'

    def __str__(self):
        return f'{self.type} — {self.company.company_name}'
