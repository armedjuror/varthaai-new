from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.db import models


class AdminUserManager(BaseUserManager):
    """Manager for the custom admin/staff identity."""

    use_in_migrations = True

    def create_user(self, username, password=None, **extra_fields):
        if not username:
            raise ValueError('AdminUser requires a username')
        user = self.model(username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, password=None, **extra_fields):
        extra_fields.setdefault('role', AdminUser.Role.SUPER_ADMIN)
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(username, password, **extra_fields)


class AdminUser(AbstractBaseUser, PermissionsMixin):
    """
    Admin panel identity (ported from the PHP `admin_users` table).

    Auth is session based. `brand_permissions` is a JSON object keyed by
    brand id whose values are permission arrays, e.g.
    {"1": ["all", "orders"], "2": ["orders"]}. "all" is a wildcard;
    super_admin bypasses every permission check; a non-super_admin with
    NULL brand_permissions is denied access at login.
    """

    class Role(models.TextChoices):
        SUPER_ADMIN = 'super_admin', 'Super Admin'
        ADMIN = 'admin', 'Admin'
        STAFF = 'staff', 'Staff'

    username = models.CharField(max_length=150, unique=True)
    name = models.CharField(max_length=255, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STAFF)
    brand_permissions = models.JSONField(null=True, blank=True)
    telegram_user_id = models.BigIntegerField(unique=True, null=True, blank=True)

    is_staff = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = AdminUserManager()

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = []

    class Meta:
        db_table = 'admin_users'

    def __str__(self):
        return self.username

    @property
    def is_super_admin(self):
        return self.role == self.Role.SUPER_ADMIN


class User(models.Model):
    """Storefront customer (ported from the PHP `users` table)."""

    brand = models.ForeignKey('core.Brand', on_delete=models.CASCADE, related_name='customers')
    mobile = models.CharField(max_length=15)
    name = models.CharField(max_length=255, blank=True)
    address = models.TextField(blank=True)
    designation = models.CharField(max_length=255, blank=True)
    pincode = models.CharField(max_length=10, blank=True)
    referral_code = models.CharField(max_length=50, unique=True, null=True, blank=True)
    loyalty_points = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'users'
        unique_together = ('brand', 'mobile')

    def __str__(self):
        return f'{self.name or self.mobile}'


class OTP(models.Model):
    mobile = models.CharField(max_length=15)
    otp = models.CharField(max_length=6)
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'otps'

    def __str__(self):
        return f'{self.mobile}: {self.otp}'


class PointsTransaction(models.Model):
    class Type(models.TextChoices):
        PURCHASE = 'purchase', 'Purchase'
        REVIEW = 'review', 'Review'
        FEEDBACK = 'feedback', 'Feedback'
        REFERRAL = 'referral', 'Referral'
        REDEMPTION = 'redemption', 'Redemption'
        ADJUSTMENT = 'adjustment', 'Adjustment'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        CREDITED = 'credited', 'Credited'
        CANCELLED = 'cancelled', 'Cancelled'

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='points_transactions')
    points = models.IntegerField()
    type = models.CharField(max_length=20, choices=Type.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    # VARCHAR: stores both order IDs ("VO_xxx") and integer review IDs — always a string.
    # Uniqueness is per-(user, reference) so a purchase row (buyer) and a referral
    # row (referrer) can share the same order id, while a given user is still credited
    # only once per reference. PHP's global UNIQUE(reference) silently dropped the
    # referral row; the composite constraint lets referral points persist.
    reference = models.CharField(max_length=50)
    transaction_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'points_transactions'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'reference'],
                name='uniq_points_user_reference',
            ),
        ]

    def __str__(self):
        return f'{self.user_id}: {self.points} ({self.type})'
