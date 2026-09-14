from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Review(models.Model):
    brand = models.ForeignKey('core.Brand', on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='reviews',
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    review = models.TextField(blank=True)
    is_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'reviews'

    def __str__(self):
        return f'{self.rating}★ — {self.brand_id}'


class Blog(models.Model):
    brand = models.ForeignKey('core.Brand', on_delete=models.CASCADE, related_name='blogs')
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    content = models.TextField(blank=True)
    featured_image = models.ImageField(upload_to='blogs/', null=True, blank=True)
    excerpt = models.TextField(blank=True)
    meta_title = models.CharField(max_length=255, blank=True)
    meta_description = models.CharField(max_length=320, blank=True)
    tags = models.JSONField(default=list, blank=True)
    is_published = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'blogs'

    def __str__(self):
        return self.title


class BlogDraftSession(models.Model):
    """
    A single AI-assisted blog drafting session — the inputs + chat history
    that produced (or are producing) a Blog. Persisted so an admin can leave
    and resume a half-finished session, and so token usage/cost is auditable.

    Deliberately has NO brand FK of its own: it takes its brand from the
    linked Blog (set once the draft is applied), and Flavor (referenced via
    flavor_refs below) is still global — not brand-scoped.
    """

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        APPLIED = 'applied', 'Applied to blog'
        ABANDONED = 'abandoned', 'Abandoned'

    blog = models.ForeignKey(Blog, on_delete=models.SET_NULL, null=True, blank=True, related_name='ai_sessions')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    topic = models.CharField(max_length=255, blank=True)
    target_audience = models.CharField(max_length=255, blank=True)
    tone = models.CharField(max_length=50, blank=True)
    word_count_target = models.PositiveIntegerField(null=True, blank=True)
    flavor_refs = models.JSONField(default=list, blank=True)  # list of Flavor ids to mention
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'blog_draft_sessions'
        ordering = ['-updated_at']

    def __str__(self):
        return f'Draft session #{self.pk} ({self.status})'


class BlogDraftMessage(models.Model):
    """One turn in a BlogDraftSession's chat — admin instruction or assistant
    output, with the provider/model/cost recorded for audit."""

    class Role(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        ASSISTANT = 'assistant', 'Assistant'
        SYSTEM = 'system', 'System'

    class Action(models.TextChoices):
        GENERATE = 'generate', 'Generate draft'
        REWRITE = 'rewrite', 'Rewrite/improve'
        SUGGEST_META = 'suggest_meta', 'Suggest title/excerpt/meta'
        OUTLINE = 'outline', 'Outline'
        CHAT = 'chat', 'Chat/feedback'

    session = models.ForeignKey(BlogDraftSession, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=10, choices=Role.choices)
    action = models.CharField(max_length=15, choices=Action.choices, default=Action.CHAT)
    content = models.TextField(blank=True)
    provider = models.CharField(max_length=30, blank=True)
    model = models.CharField(max_length=80, blank=True)
    prompt_tokens = models.PositiveIntegerField(null=True, blank=True)
    completion_tokens = models.PositiveIntegerField(null=True, blank=True)
    cost_usd = models.DecimalField(max_digits=8, decimal_places=5, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'blog_draft_messages'
        ordering = ['created_at', 'id']

    def __str__(self):
        return f'{self.role} @ session {self.session_id}'


class Feedback(models.Model):
    user = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='feedbacks',
    )
    feedback = models.TextField(blank=True)
    is_reviewed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'feedback'
        verbose_name_plural = 'Feedback'

    def __str__(self):
        return f'Feedback #{self.pk}'


class MarketingSource(models.Model):
    source_name = models.CharField(max_length=255)
    source_code = models.CharField(max_length=50, unique=True)
    qr_image = models.ImageField(upload_to='qr/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'marketing_sources'

    def __str__(self):
        return f'{self.source_name} ({self.source_code})'


class SourceTracking(models.Model):
    source = models.CharField(max_length=50)
    referral = models.CharField(max_length=255, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'sources_tracking'

    def __str__(self):
        return f'{self.source} @ {self.timestamp}'
