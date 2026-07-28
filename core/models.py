from django.conf import settings
from django.db import models


class Brand(models.Model):
    name = models.CharField(max_length=255)
    logo = models.ImageField(upload_to='brands/', null=True, blank=True)
    website = models.CharField(max_length=255, blank=True)
    instagram = models.CharField(max_length=255, blank=True)
    order_prefix = models.CharField(max_length=10, default='ORD')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'brands'

    def __str__(self):
        return self.name


class Setting(models.Model):
    class Type(models.TextChoices):
        STRING = 'string', 'String'
        NUMBER = 'number', 'Number'
        BOOLEAN = 'boolean', 'Boolean'
        JSON = 'json', 'JSON'

    setting_key = models.CharField(max_length=255, unique=True)
    setting_value = models.TextField(blank=True)
    setting_type = models.CharField(max_length=10, choices=Type.choices, default=Type.STRING)
    description = models.CharField(max_length=255, blank=True)
    category = models.CharField(max_length=100, default='general')
    is_public = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'settings'

    def __str__(self):
        return self.setting_key


class ActivityLog(models.Model):
    user = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='activity_logs',
    )
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    action = models.CharField(max_length=255)
    module = models.CharField(max_length=100, blank=True)
    details = models.TextField(blank=True)
    ip_address = models.CharField(max_length=45, blank=True)
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'activity_log'

    def __str__(self):
        return f'{self.action} ({self.module})'
