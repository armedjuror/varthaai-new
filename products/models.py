from django.conf import settings
from django.db import models


class Flavor(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    price_per_kg = models.FloatField(default=1490)
    sale_price_per_kg = models.FloatField(default=990)
    reorder_level_grams = models.IntegerField(default=5000)
    is_active = models.BooleanField(default=True)
    ingredients = models.TextField(blank=True)
    nutrition_fact = models.JSONField(null=True, blank=True)
    image = models.ImageField(upload_to='flavors/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'flavors'

    def __str__(self):
        return self.name


class FlavorPack(models.Model):
    flavor = models.ForeignKey(Flavor, on_delete=models.CASCADE, related_name='packs')
    brand = models.ForeignKey('core.Brand', on_delete=models.CASCADE, related_name='flavor_packs')
    weight_grams = models.IntegerField()
    label = models.CharField(max_length=50)
    mrp = models.DecimalField(max_digits=10, decimal_places=2)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2)
    sku = models.CharField(max_length=100, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'flavor_packs'
        unique_together = ('flavor', 'brand', 'label')

    def __str__(self):
        return f'{self.flavor.name} — {self.label}'


class Vendor(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=3, unique=True)
    fssai_number = models.CharField(max_length=50, blank=True)
    contact_name = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.CharField(max_length=255, blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'vendors'

    def __str__(self):
        return f'{self.name} ({self.code})'


class Stock(models.Model):
    flavor = models.ForeignKey(Flavor, on_delete=models.CASCADE, related_name='stock_batches')
    is_active_batch = models.BooleanField(default=False)
    quantity_grams = models.IntegerField(default=0)
    reserved_quantity_grams = models.IntegerField(default=0)
    cost_price_per_kg = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    supplier_name = models.CharField(max_length=255, blank=True)
    supplier_contact = models.CharField(max_length=255, blank=True)
    vendor = models.ForeignKey(Vendor, on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_batches')
    last_restocked_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    batch_number = models.CharField(max_length=100, blank=True)
    storage_location = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'stock'

    def __str__(self):
        return f'{self.flavor.name} batch #{self.batch_number or self.pk}'


class StockMovement(models.Model):
    class MovementType(models.TextChoices):
        IN = 'in', 'In'
        OUT = 'out', 'Out'
        ADJUSTMENT = 'adjustment', 'Adjustment'
        RESERVED = 'reserved', 'Reserved'
        RELEASED = 'released', 'Released'

    class ReferenceType(models.TextChoices):
        PURCHASE = 'purchase', 'Purchase'
        SALE = 'sale', 'Sale'
        WASTAGE = 'wastage', 'Wastage'
        ADJUSTMENT = 'adjustment', 'Adjustment'
        ORDER_RESERVE = 'order_reserve', 'Order Reserve'
        ORDER_RELEASE = 'order_release', 'Order Release'
        B2B_SALE = 'b2b_sale', 'B2B Sale'
        B2B_REVERSAL = 'b2b_reversal', 'B2B Reversal'

    flavor = models.ForeignKey(Flavor, on_delete=models.CASCADE, related_name='movements')
    stock = models.ForeignKey(Stock, on_delete=models.SET_NULL, null=True, blank=True, related_name='movements')
    movement_type = models.CharField(max_length=20, choices=MovementType.choices)
    quantity_grams = models.IntegerField()
    previous_quantity_grams = models.IntegerField()
    new_quantity_grams = models.IntegerField()
    reference_type = models.CharField(max_length=20, choices=ReferenceType.choices)
    reference_id = models.CharField(max_length=50, blank=True)
    cost_price_per_kg = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'stock_movements'

    def __str__(self):
        return f'{self.movement_type} {self.quantity_grams}g'


class StockAlert(models.Model):
    class AlertType(models.TextChoices):
        LOW_STOCK = 'low_stock', 'Low Stock'
        OUT_OF_STOCK = 'out_of_stock', 'Out of Stock'
        EXPIRING_SOON = 'expiring_soon', 'Expiring Soon'
        EXPIRED = 'expired', 'Expired'

    flavor = models.ForeignKey(Flavor, on_delete=models.CASCADE, related_name='alerts')
    alert_type = models.CharField(max_length=20, choices=AlertType.choices)
    current_quantity_grams = models.IntegerField()
    reorder_level_grams = models.IntegerField()
    expiry_date = models.DateField(null=True, blank=True)
    is_acknowledged = models.BooleanField(default=False)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'stock_alerts'

    def __str__(self):
        return f'{self.alert_type} — {self.flavor.name}'
