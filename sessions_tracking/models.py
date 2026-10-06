"""
Work-session tracking for field-sales and packing employees.

Employees are AdminUser rows with role=EMPLOYEE (see accounts.models.AdminUser
and sessions_tracking.permissions) — a separate, restricted role from
admin/staff. Nothing here is brand-scoped: the spec's data model has no brand
FK on these tables, so reports aggregate across brands.

`SessionEvent` is the append-only source of truth for break/active time —
`Session.status`/`ended_at` are derived state that must always agree with the
latest event (see sessions_tracking.services.sessions). Never mutate or
delete events.

Weekday numbering throughout uses Python's `date.weekday()` convention:
Monday=0 .. Sunday=6.
"""
from django.conf import settings
from django.db import models
from django.db.models import Q


class Session(models.Model):
    class Type(models.TextChoices):
        SALES = 'sales', 'Sales'
        PACKING = 'packing', 'Packing'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        ON_BREAK = 'on_break', 'On Break'
        ENDED = 'ended', 'Ended'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='work_sessions',
    )
    type = models.CharField(max_length=10, choices=Type.choices)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    # True when auto-closed by the 19:30 IST job rather than by the user/admin.
    auto_closed = models.BooleanField(default=False)
    # True once `ended_at` has been manually corrected after the fact.
    end_edited = models.BooleanField(default=False)
    end_edit_note = models.TextField(blank=True)

    class Meta:
        db_table = 'work_sessions'
        indexes = [
            models.Index(fields=['user', 'started_at'], name='worksession_user_started_idx'),
        ]
        constraints = [
            # A user cannot have two sessions open (any type) at once.
            models.UniqueConstraint(
                fields=['user'], condition=Q(ended_at__isnull=True),
                name='one_open_work_session_per_user',
            ),
        ]

    def __str__(self):
        return f'{self.user_id}:{self.type}:{self.started_at:%Y-%m-%d}'


class SessionEvent(models.Model):
    class Event(models.TextChoices):
        START = 'start', 'Start'
        BREAK = 'break', 'Break'
        RESUME = 'resume', 'Resume'
        END = 'end', 'End'

    class Reason(models.TextChoices):
        LUNCH = 'lunch_break', 'Lunch Break'
        EVENING = 'evening_break', 'Evening Break'

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='events')
    event = models.CharField(max_length=10, choices=Event.choices)
    at = models.DateTimeField()
    # Only set (and only valid) for `event == BREAK`.
    reason = models.CharField(max_length=20, choices=Reason.choices, null=True, blank=True)

    class Meta:
        db_table = 'work_session_events'
        indexes = [
            models.Index(fields=['session', 'at'], name='worksession_event_sess_at_idx'),
        ]
        ordering = ['at', 'id']

    def __str__(self):
        return f'{self.session_id}:{self.event}@{self.at}'


class PackingItem(models.Model):
    """One row per flavour packed in a packing session (no generic 'pack type').

    FKs to Flavor, not FlavorPack: a flavour can have several pack-size
    variants (e.g. 100g/150g) but this count does not distinguish between
    them — see the migration guide note on catalog.FlavorPack. Flagged as an
    open question in the feature's summary.
    """
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='packing_items')
    flavor = models.ForeignKey('products.Flavor', on_delete=models.PROTECT, related_name='+')
    packs = models.PositiveIntegerField()

    class Meta:
        db_table = 'work_session_packing_items'
        unique_together = ('session', 'flavor')

    def __str__(self):
        return f'{self.session_id}:{self.flavor_id}x{self.packs}'


class Visit(models.Model):
    class Purpose(models.TextChoices):
        NEW_LEAD = 'new_lead', 'New Lead'
        RETARGET = 'retarget', 'Retarget'
        DELIVERY = 'delivery', 'Delivery'
        COLLECTION = 'collection', 'Collection'
        AUDIT = 'audit', 'Audit'
        REORDER_PUSH = 'reorder_push', 'Reorder Push'

    class Outcome(models.TextChoices):
        ORDER = 'order', 'Order'
        FOLLOW_UP = 'follow_up', 'Follow Up'
        REJECTED = 'rejected', 'Rejected'
        NONE = 'none', 'None'

    # Always a `sales` session — enforced in sessions_tracking.services.visits.
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='visits')
    company = models.ForeignKey('crm.B2BCompany', on_delete=models.PROTECT, related_name='+')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='visits',
    )
    visited_at = models.DateTimeField()
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    outcome = models.CharField(max_length=20, choices=Outcome.choices, default=Outcome.NONE)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = 'work_session_visits'
        indexes = [
            models.Index(fields=['user', 'visited_at'], name='worksession_visit_user_at_idx'),
        ]
        ordering = ['visited_at']

    def __str__(self):
        return f'{self.user_id}@{self.company_id}:{self.purpose}'


class UserSetting(models.Model):
    """Per-employee settings. `weekly_off_weekday` is the *current* default —
    always resolve the *effective* weekday for a given date via
    sessions_tracking.services.leave.effective_weekly_off_weekday(user, date),
    which layers WeeklyOffChange on top of this default."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='session_settings',
    )
    weekly_off_weekday = models.PositiveSmallIntegerField(default=6)  # default Sunday

    class Meta:
        db_table = 'work_session_user_settings'

    def __str__(self):
        return f'settings:{self.user_id}'


class WeeklyOffChange(models.Model):
    """A requested weekly-off change, effective from the given (future) Monday."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='weekly_off_changes',
    )
    new_weekday = models.PositiveSmallIntegerField()
    effective_from = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'work_session_weekly_off_changes'
        ordering = ['-effective_from']

    def __str__(self):
        return f'{self.user_id}->{self.new_weekday} from {self.effective_from}'


class Leave(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='leaves',
    )
    date = models.DateField()
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'work_session_leaves'
        unique_together = ('user', 'date')
        ordering = ['-date']

    def __str__(self):
        return f'{self.user_id}:{self.date}'


class TelegramReportLog(models.Model):
    """One row per report date — makes the 19:30 send idempotent on retry."""
    class Status(models.TextChoices):
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed'
        SKIPPED = 'skipped', 'Skipped'

    report_date = models.DateField(unique=True)
    status = models.CharField(max_length=10, choices=Status.choices)
    detail = models.TextField(blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'work_session_telegram_report_log'
        ordering = ['-report_date']

    def __str__(self):
        return f'{self.report_date}:{self.status}'
