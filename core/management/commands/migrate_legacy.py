"""
One-off importer: copy the live PHP MySQL data into the new Postgres DB.

Reads from a MySQL database (by default a local copy of the phpMyAdmin dump
loaded into `varthaai_legacy`) and writes through the Django ORM so foreign
keys, JSON fields, varchar order PKs and booleans all convert correctly.

Only tables that actually hold data in the source are copied:
    brands, admin_users, users, flavors, stock, coupons, orders, order_items,
    b2b_categories, expense_categories, reviews, blogs, points_transactions,
    sources_tracking
All other legacy tables (vendors, flavor_packs, the B2B order/company tables,
expenses, otps, stock movements/alerts, feedback, marketing_sources) are empty
in the export and are skipped.

Admin passwords are NOT migrated (PHP bcrypt is ignored) — every admin user is
given the same common password (see --admin-password), to be changed after login.

Usage:
    python manage.py migrate_legacy --flush                 # wipe target first
    python manage.py migrate_legacy --mysql-db varthaai_legacy --dry-run
"""
import json
from datetime import date, datetime
from decimal import Decimal

from django.apps import apps
from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models, transaction
from django.utils import timezone

# (label, "app_label.ModelName", mysql_table) in FK-dependency order.
PLAN = [
    ("brands",              "core.Brand",                "brands"),
    ("admin_users",         "accounts.AdminUser",        "admin_users"),
    ("customers",           "accounts.User",             "users"),
    ("flavors",             "products.Flavor",           "flavors"),
    ("stock",               "products.Stock",            "stock"),
    ("coupons",             "orders.Coupon",             "coupons"),
    ("orders",              "orders.Order",              "orders"),
    ("order_items",         "orders.OrderItem",          "order_items"),
    ("b2b_categories",      "crm.B2BCategory",           "b2b_categories"),
    ("expense_categories",  "finance.ExpenseCategory",   "expense_categories"),
    ("reviews",             "marketing.Review",          "reviews"),
    ("blogs",               "marketing.Blog",            "blogs"),
    ("points_transactions", "accounts.PointsTransaction","points_transactions"),
    ("sources_tracking",    "marketing.SourceTracking",  "sources_tracking"),
]

_STRING_TYPES = {"CharField", "TextField", "SlugField", "EmailField"}


class Command(BaseCommand):
    help = "Import legacy PHP MySQL data into Postgres via the ORM."

    def add_arguments(self, parser):
        parser.add_argument("--mysql-host", default="127.0.0.1")
        parser.add_argument("--mysql-port", type=int, default=3306)
        parser.add_argument("--mysql-user", default="root")
        parser.add_argument("--mysql-password", default="")
        parser.add_argument("--mysql-db", default="varthaai_legacy")
        parser.add_argument(
            "--admin-password", default="Varthaai@2026",
            help="Common password assigned to every migrated admin user.",
        )
        parser.add_argument(
            "--flush", action="store_true",
            help="Delete the target tables (reverse FK order) before importing.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Roll back the transaction at the end instead of committing.",
        )

    def handle(self, *args, **opts):
        try:
            import pymysql
        except ImportError as exc:  # pragma: no cover
            raise CommandError("pymysql is required: pip install pymysql") from exc

        src = pymysql.connect(
            host=opts["mysql_host"], port=opts["mysql_port"],
            user=opts["mysql_user"], password=opts["mysql_password"],
            database=opts["mysql_db"], charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
        self.stdout.write(f"Connected to MySQL '{opts['mysql_db']}'.")
        common_pw_hash = make_password(opts["admin_password"])

        try:
            with transaction.atomic():
                if opts["flush"]:
                    self._delete_targets()
                for label, model_path, table in PLAN:
                    self._import_table(src, label, model_path, table, common_pw_hash)
                self._reset_sequences()
                if opts["dry_run"]:
                    self.stdout.write(self.style.WARNING("Dry run — rolling back."))
                    transaction.set_rollback(True)
        finally:
            src.close()

        if not opts["dry_run"]:
            self.stdout.write(self.style.SUCCESS("Legacy import complete."))
            self.stdout.write(self.style.WARNING(
                f"All admin users share password '{opts['admin_password']}' — "
                "change it after logging in."
            ))

    # ------------------------------------------------------------------ #

    def _delete_targets(self):
        for label, model_path, _table in reversed(PLAN):
            Model = apps.get_model(model_path)
            n, _ = Model.objects.all().delete()
            self.stdout.write(f"  flushed {label}: {n} rows removed")

    def _import_table(self, src, label, model_path, table, common_pw_hash):
        Model = apps.get_model(model_path)
        with src.cursor() as cur:
            cur.execute(f"SELECT * FROM `{table}`")
            rows = cur.fetchall()

        with _auto_dates_disabled(Model):
            instances = [
                self._build(Model, row, common_pw_hash) for row in rows
            ]
            Model.objects.bulk_create(instances, batch_size=500)
        self.stdout.write(self.style.SUCCESS(f"  {label}: {len(instances)} rows imported"))

    def _build(self, Model, row, common_pw_hash):
        kwargs = {}
        is_admin = Model._meta.db_table == "admin_users"
        for field in Model._meta.concrete_fields:
            # Passwords are never migrated from PHP bcrypt.
            if is_admin and field.name == "password":
                kwargs["password"] = common_pw_hash
                continue

            # Resolve the source column: FKs use e.g. 'brand_id'; some legacy
            # columns drop the _id suffix (blogs.created_by -> created_by_id).
            if field.column in row:
                value = row[field.column]
            elif field.name in row:
                value = row[field.name]
            else:
                continue  # absent in legacy -> keep the model default

            kwargs[field.attname] = self._coerce(field, value)

        # Some legacy tables (e.g. order_items) have no created_at column; since
        # auto_now/auto_now_add is disabled during import, backfill any missing
        # non-null date field with the current time.
        now = timezone.now()
        for field in Model._meta.concrete_fields:
            if isinstance(field, (models.DateTimeField, models.DateField)) \
                    and not field.null and kwargs.get(field.attname) is None:
                kwargs[field.attname] = now if isinstance(field, models.DateTimeField) else now.date()

        # PHP allowed NULL point references (MySQL treats NULLs as distinct);
        # the Django (user, reference) unique constraint rejects repeated ''.
        # Give reference-less legacy rows a synthetic unique value.
        if Model._meta.db_table == "points_transactions" and not kwargs.get("reference"):
            kwargs["reference"] = f"legacy-{kwargs['id']}"

        instance = Model(**kwargs)
        if is_admin:
            # super_admin role gets is_superuser so it bypasses permission checks.
            instance.is_superuser = (getattr(instance, "role", "") == "super_admin")
            instance.is_staff = True
            instance.is_active = True
        return instance

    def _coerce(self, field, value):
        internal = field.get_internal_type()

        if isinstance(field, models.JSONField):
            if isinstance(value, (bytes, bytearray)):
                value = value.decode()
            if isinstance(value, str):
                return json.loads(value) if value.strip() else None
            return value

        if isinstance(field, models.BooleanField):
            return None if value is None else bool(value)

        if value is None:
            # Non-nullable string columns must be '' not NULL.
            if not field.null and internal in _STRING_TYPES:
                return ""
            return None

        if internal in _STRING_TYPES and not isinstance(value, str):
            value = str(value)

        # Make naive datetimes timezone-aware (project is UTC / USE_TZ=True).
        if isinstance(value, datetime) and timezone.is_naive(value):
            value = timezone.make_aware(value, timezone.get_default_timezone())

        return value

    def _reset_sequences(self):
        """Advance Postgres id sequences past the imported explicit PKs."""
        with connection.cursor() as cur:
            for _label, model_path, _table in PLAN:
                Model = apps.get_model(model_path)
                pk = Model._meta.pk
                if not isinstance(pk, models.AutoField):
                    continue  # e.g. orders has a varchar PK — no sequence
                t = Model._meta.db_table
                cur.execute(
                    "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
                    "COALESCE((SELECT MAX(id) FROM \"%s\"), 1), true)"
                    % ("%s", t),
                    [t],
                )
        self.stdout.write("  sequences reset")


class _auto_dates_disabled:
    """Temporarily turn off auto_now / auto_now_add so legacy timestamps survive."""

    def __init__(self, Model):
        self.fields = [
            f for f in Model._meta.get_fields()
            if getattr(f, "auto_now", False) or getattr(f, "auto_now_add", False)
        ]

    def __enter__(self):
        self.saved = [(f, f.auto_now, f.auto_now_add) for f in self.fields]
        for f in self.fields:
            f.auto_now = False
            f.auto_now_add = False

    def __exit__(self, *exc):
        for f, an, ana in self.saved:
            f.auto_now = an
            f.auto_now_add = ana
