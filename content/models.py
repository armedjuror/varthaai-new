"""
Content Studio data model (see content-generator-plan.md at the repo root
for the full design doc — this module implements Phase 0 of that plan).

Six tables:
  - ContentSeries : seeded, fixed rows describing each series' cadence and
                    which skill (if any) it dispatches to (§1/§7 of the plan).
  - ContentPlan   : one per planning period (a calendar month, or an
                    on-demand date range — §8), reviewed/approved as a whole.
  - PlanItem      : one per planned post within a ContentPlan.
  - TrendFlag     : admin-submitted "this is trending" notes for the Planner
                    to consider (§16 Track A note — no scraping in v1).
  - Script        : one skill-generated (or Blog-path) draft per PlanItem.
  - PosterAsset   : one generated poster image per PlanItem (posters only).
  - ActionItem    : the unified "needs your input" / "needs your review"
                    queue that backs the Pending Tasks page (§5).

Agent logic lives in content/agents/ + content/tasks.py (Phase 1+). The
Planner and Designer agents are non-agentic (no filesystem/DB tool access
needed) but still go through core.claude_cli.run_claude_cli with an empty
tool allowlist, not a direct `anthropic` SDK call — no service in this
codebase authenticates with ANTHROPIC_API_KEY, see core/claude_cli.py.
"""
from django.conf import settings
from django.db import models


class ContentSeries(models.Model):
    """Fixed catalogue of what the Copywriter Agent can produce. Seeded by a
    data migration (0002_seed_series.py) with the six rows from the plan
    doc's §1 table — not admin-editable in v1, this is configuration, not
    content."""

    class Format(models.TextChoices):
        REEL = 'reel', 'Reel'
        POSTER = 'poster', 'Poster'
        BLOG = 'blog', 'Blog'

    class ContentType(models.TextChoices):
        REEL_VARTHAANM = 'reel_varthaanm', 'Reel — Varthaai Varthaanm'
        REEL_VERDICT = 'reel_verdict', 'Reel — Varthaai Verdict'
        REEL_INSIDE = 'reel_inside', 'Reel — Varthaai Inside'
        POSTER_LEARN = 'poster_learn', 'Poster — Learn With Varthaai'
        POSTER_OCCASION = 'poster_occasion', 'Poster — Occasion'
        POSTER_EVENT = 'poster_event', 'Poster — Event'
        BLOG = 'blog', 'Blog'

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=50, unique=True)
    format = models.CharField(max_length=10, choices=Format.choices)
    # Default content_type for PlanItems in this series. Occasion has no
    # fixed series-level default — poster_occasion vs poster_event is
    # decided per PlanItem (§1) — so this is nullable only for that row.
    content_type = models.CharField(max_length=20, choices=ContentType.choices, blank=True)

    # Cadence (§1 table). 0=Monday..6=Sunday, null for flexible/ad-hoc series.
    weekday = models.PositiveSmallIntegerField(null=True, blank=True)
    is_alternate_week = models.BooleanField(default=False)
    prep_lead_days = models.PositiveSmallIntegerField(null=True, blank=True)
    intake_lead_days = models.PositiveSmallIntegerField(null=True, blank=True)

    # Blank for series that dispatch through varthaai-generic-content.md
    # instead of their own skill (Blog, Occasion) — see §1.
    skill_slug = models.CharField(max_length=100, blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'content_series'
        verbose_name_plural = 'content series'

    def __str__(self):
        return self.name


class ContentPlan(models.Model):
    """One per planning period. §8: a period is either the Planner's monthly
    cron seed or an admin-triggered on-demand range — same model either
    way, `period_start`/`period_end` generalize over both instead of a
    fixed month/year pair."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        GENERATING = 'generating', 'Generating'
        NEEDS_REVIEW = 'needs_review', 'Needs review'
        APPROVED = 'approved', 'Approved'

    brand = models.ForeignKey('core.Brand', on_delete=models.CASCADE, related_name='content_plans')
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    # Set by the Planner task on failure, cleared on the next successful run —
    # surfaced on the calendar page so a manual trigger's failure isn't silent.
    generation_error = models.TextField(blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'content_plans'
        ordering = ['-period_start']

    def __str__(self):
        return f'{self.period_start} – {self.period_end}'


class PlanItem(models.Model):
    """One planned post. No `brand` field here on purpose — brand scoping
    lives on `ContentPlan` (every PlanItem belongs to exactly one plan,
    which belongs to exactly one brand), so filter/join through `plan__brand`
    rather than duplicating the FK. `series` is null for genuinely ad-hoc
    items even though Blog/Occasion now have their own ContentSeries rows
    (kept nullable for flexibility, per the original §7 design)."""

    class Status(models.TextChoices):
        PLANNED = 'planned', 'Planned'
        NEEDS_INPUT = 'needs_input', 'Needs input'
        READY_FOR_SCRIPT = 'ready_for_script', 'Ready for script'
        SCRIPTED = 'scripted', 'Scripted'
        NEEDS_APPROVAL = 'needs_approval', 'Needs approval'
        APPROVED = 'approved', 'Approved'
        POSTED = 'posted', 'Posted'
        SKIPPED = 'skipped', 'Skipped'

    plan = models.ForeignKey(ContentPlan, on_delete=models.CASCADE, related_name='items')
    series = models.ForeignKey(
        ContentSeries, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='plan_items',
    )
    content_type = models.CharField(max_length=20, choices=ContentSeries.ContentType.choices)
    planned_date = models.DateField()
    working_title = models.CharField(max_length=255)
    # Free-form context — for poster_event this is where date/time/venue/CTA
    # specifics live (§1: "never inferred", supplied here explicitly).
    context_notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    # Phase 4 (§4's "improvised later" rule): the Planner's daily nudge run
    # proposes changes to an ALREADY-APPROVED item as a diff sitting here,
    # never overwriting working_title/context_notes/planned_date/content_type
    # directly. Empty dict = no pending proposal. Keys are a subset of
    # {working_title, context_notes, planned_date, content_type}; applied
    # atomically onto the real fields (and cleared) only on admin accept,
    # discarded (cleared, untouched fields) on reject.
    proposed_changes = models.JSONField(default=dict, blank=True)

    # Derived 5-stage lifecycle (Planned -> Scripted -> Generated -> Approved
    # -> Published — see content/lifecycle.py) reuses existing signals
    # (Script/PosterAsset rows + their approval) wherever one already
    # exists, rather than a new status enum. Two gaps needed real fields:
    # reels have no in-system artifact "Approved" can key off (a reel's
    # Script approval already means something else — the written caption is
    # reviewed independently of whether the reel has actually been
    # filmed/edited), so both Generated and Approved are manual admin
    # milestones for reels only. Published reuses the pre-existing (until
    # now unused) PlanItem.Status.POSTED value instead of a new field.
    manually_generated_at = models.DateTimeField(null=True, blank=True)
    manually_generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    manually_approved_at = models.DateTimeField(null=True, blank=True)
    manually_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'plan_items'
        ordering = ['planned_date']

    def __str__(self):
        return f'{self.working_title} ({self.planned_date})'


class TrendFlag(models.Model):
    """Manual trend input (§16 Track A / §11 out-of-scope note — no
    automated IG/X scraping in v1)."""

    source_text = models.TextField()
    flagged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    plan_item = models.ForeignKey(
        PlanItem, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='trend_flags',
    )
    considered = models.BooleanField(default=False)
    flagged_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'trend_flags'
        ordering = ['-flagged_at']

    def __str__(self):
        return self.source_text[:60]


class Script(models.Model):
    """One skill (or generic-content) output per PlanItem. Not a
    OneToOneField — §7's versioning intent (regenerate/edit) means a
    PlanItem can accumulate multiple Script rows over time; the latest by
    `version` is the current one."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        NEEDS_REVIEW = 'needs_review', 'Needs review'
        APPROVED = 'approved', 'Approved'
        CHANGES_REQUESTED = 'changes_requested', 'Changes requested'

    plan_item = models.ForeignKey(PlanItem, on_delete=models.CASCADE, related_name='scripts')
    skill_used = models.CharField(max_length=100)
    raw_output = models.TextField(blank=True)
    structured_json = models.JSONField(default=dict, blank=True)
    # Recap continuity for Varthaanm (§6) — replaces the claude.ai memory
    # file the skill was originally written against.
    recap_summary = models.TextField(blank=True)
    version = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'scripts'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.plan_item.working_title} v{self.version}'


class PosterFormat(models.TextChoices):
    """Aspect ratios actually published (poster-generation-plan.md §2's
    layout-conventions section) — shared by PosterInspiration and
    PosterAsset so selection can match on it."""
    SQUARE = '1:1', 'Square (1:1) — feed'
    STORY = '9:16', 'Story/Reel (9:16)'
    PORTRAIT = '4:5', 'Portrait (4:5) — feed'


class BrandAsset(models.Model):
    """The brand asset library (poster-generation-plan.md §3) — logo and
    product/lifestyle/team photos a poster can composite in. Local Django
    media storage, not S3 — this codebase doesn't use S3 anywhere else
    (CLAUDE.md: "File uploads | Django media files"), so the poster plan's
    S3 assumption (written against a different codebase) doesn't apply
    here; ImageField is consistent with how `Brand.logo` already works."""

    class Tag(models.TextChoices):
        LOGO = 'logo', 'Logo'
        PRODUCT = 'product', 'Product'
        LIFESTYLE = 'lifestyle', 'Lifestyle'
        TEAM = 'team', 'Team'
        EVENT = 'event', 'Event'
        ICON = 'icon', 'Icon'
        OTHER = 'other', 'Other'

    brand = models.ForeignKey('core.Brand', on_delete=models.CASCADE, related_name='content_assets')
    image = models.ImageField(upload_to='content/brand_assets/')
    tag = models.CharField(max_length=20, choices=Tag.choices, default=Tag.OTHER)
    label = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'content_brand_assets'
        indexes = [models.Index(fields=['brand', 'tag'])]

    def __str__(self):
        return self.label or f'{self.get_tag_display()} asset #{self.id}'


class PosterInspiration(models.Model):
    """Past posters, self-described by a vision LLM on ingestion
    (poster-generation-plan.md §4) — the reference library that keeps
    generated posters looking like *this brand's* posters instead of
    generic AI output. `image` is local media (see BrandAsset's note on
    why not S3)."""

    brand = models.ForeignKey('core.Brand', on_delete=models.CASCADE, related_name='poster_inspirations')
    source_label = models.CharField(max_length=100)
    image = models.ImageField(upload_to='content/poster_inspirations/')
    description = models.TextField()
    design_language = models.TextField()
    topic_category = models.CharField(max_length=80, db_index=True)
    format = models.CharField(max_length=10, choices=PosterFormat.choices, db_index=True)
    tags = models.JSONField(default=list, blank=True)
    has_logo = models.BooleanField(null=True, blank=True, default=None)
    score = models.PositiveSmallIntegerField(default=80)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'poster_inspirations'
        indexes = [models.Index(fields=['topic_category', 'format'])]

    def __str__(self):
        return self.source_label


class PosterAsset(models.Model):
    """One generated poster per attempt (poster_learn/poster_occasion/
    poster_event PlanItems only — §12)."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        NEEDS_REVIEW = 'needs_review', 'Needs review'
        APPROVED = 'approved', 'Approved'
        CHANGES_REQUESTED = 'changes_requested', 'Changes requested'

    plan_item = models.ForeignKey(PlanItem, on_delete=models.CASCADE, related_name='posters')
    format = models.CharField(max_length=10, choices=PosterFormat.choices, default=PosterFormat.SQUARE)
    image = models.ImageField(upload_to='content/posters/', null=True, blank=True)
    brief = models.JSONField(default=dict, blank=True)
    inspiration = models.ForeignKey(
        PosterInspiration, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='poster_assets',
    )
    generation_metadata = models.JSONField(default=dict, blank=True)
    version = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'poster_assets'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.plan_item.working_title} poster v{self.version}'


class VerdictIntake(models.Model):
    """The rich input a Varthaai Verdict episode needs before the Copywriter
    can draft it (content-generator-plan.md §5: "the richest ActionItem
    case", §1: "a physically bought, tasted, photographed competitor
    product; ingredients + nutrition label; Ajwad's Design/Pricing/Taste
    notes + scores"). One row per PlanItem — filled in once via the intake
    form, then read by copywriter.py's reel_verdict dispatch.

    `product_name` is internal-only and must never reach a public-facing
    field or template — the reel films with the brand hidden (varthaai-
    verdict-script skill's own confidentiality rule, restated in the Brand
    Kit per content-generator-plan.md §2)."""

    plan_item = models.OneToOneField(PlanItem, on_delete=models.CASCADE, related_name='verdict_intake')

    product_name = models.CharField(max_length=255, blank=True)
    product_category = models.CharField(max_length=100, blank=True)
    market = models.CharField(max_length=100, blank=True, default='India')
    price_point = models.CharField(max_length=100, blank=True)

    # Reference only — not fed to the food-quality-analyst call (that skill
    # analyzes the ingredients/nutrition labels specifically, not a general
    # product photo). Kept for Ajwad's own record and the History page.
    product_photo = models.ImageField(upload_to='content/verdict/', blank=True)

    # Either the photo OR the typed text is enough for each of these two —
    # the readiness check (copywriter._verdict_intake_missing_fields)
    # accepts whichever is present, never both.
    ingredients_label_photo = models.ImageField(upload_to='content/verdict/', blank=True)
    ingredients_text = models.TextField(blank=True)
    nutrition_label_photo = models.ImageField(upload_to='content/verdict/', blank=True)
    nutrition_text = models.TextField(blank=True)

    design_score = models.PositiveSmallIntegerField(null=True, blank=True)
    design_notes = models.TextField(blank=True)
    pricing_score = models.PositiveSmallIntegerField(null=True, blank=True)
    pricing_notes = models.TextField(blank=True)
    taste_score = models.PositiveSmallIntegerField(null=True, blank=True)
    taste_notes = models.TextField(blank=True)

    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'verdict_intakes'

    def __str__(self):
        return f'Verdict intake — {self.plan_item.working_title}'


class ActionItem(models.Model):
    """The Pending Tasks queue (§5) — every "input needed" and "needs your
    review" event resolves to one row here, so the dashboard is one query."""

    class Kind(models.TextChoices):
        INPUT_NEEDED = 'input_needed', 'Input needed'
        PLAN_REVIEW = 'plan_review', 'Plan review'
        ITEM_CHANGE_PROPOSED = 'item_change_proposed', 'Plan item change proposed'
        SCRIPT_REVIEW = 'script_review', 'Script review'
        POSTER_REVIEW = 'poster_review', 'Poster review'

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        DONE = 'done', 'Done'
        DISMISSED = 'dismissed', 'Dismissed'

    plan_item = models.ForeignKey(
        PlanItem, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='action_items',
    )
    plan = models.ForeignKey(
        ContentPlan, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='action_items',
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    # Structured intake once fulfilled — product name/scores/topic text,
    # plus uploaded-file references for Verdict's richer intake (§5).
    payload = models.JSONField(default=dict, blank=True)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'action_items'
        ordering = ['due_date', '-created_at']

    def __str__(self):
        return self.title
