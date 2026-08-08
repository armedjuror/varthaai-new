"""
Debugger Agent data model.

Three tables, all written by the *app* on the `default` connection (this is the
app persisting its own feature data — NOT the RCA agent, which only ever reads,
and only via the `readonly` DB alias / read-only tools):

  - DebugRequest  : one bug / feature / query ticket + its lifecycle + result.
  - DebugMessage  : the threaded chat (admin / agent / system turns).
  - DebugLearning : finalized lessons stored on thread close; injected into the
                    system prompt of future runs (the agent's memory).
"""
from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models


class DebugRequest(models.Model):
    class Kind(models.TextChoices):
        BUG = 'bug', 'Bug'
        FEATURE = 'feature', 'Feature'
        QUERY = 'query', 'Query'

    class Status(models.TextChoices):
        NEW = 'new', 'New'                       # created, queued for the agent
        ANALYZING = 'analyzing', 'Analyzing'     # agent task running
        AWAITING_INPUT = 'awaiting_input', 'Awaiting input'  # agent asked a question
        READY = 'ready', 'Ready'                 # RCA/plan done; fix may be proposed
        PR_REQUESTED = 'pr_requested', 'PR requested'        # admin asked for a PR (Phase 2)
        PR_OPEN = 'pr_open', 'PR open'           # PR created (Phase 2)
        CHANGES_REQUESTED = 'changes_requested', 'Changes requested'  # PR review came in (Phase 2)
        REVISING = 'revising', 'Revising'        # agent addressing PR review (Phase 2)
        PR_UPDATED = 'pr_updated', 'PR updated'  # revision pushed (Phase 2)
        FINALIZING = 'finalizing', 'Finalizing'  # agent drafting the learning (Phase 3)
        LEARNING_REVIEW = 'learning_review', 'Learning review'  # draft awaiting admin (Phase 3)
        CLOSED = 'closed', 'Closed'
        FAILED = 'failed', 'Failed'

    kind = models.CharField(max_length=10, choices=Kind.choices)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='debug_requests',
    )

    # Result of the analysis.
    rca = models.TextField(blank=True)               # root-cause summary (bug) / plan (feature)
    proposed_diff = models.TextField(blank=True)     # unified diff for a fix (Phase 2 uses it)
    pr_title = models.CharField(max_length=200, blank=True)
    pr_body = models.TextField(blank=True)
    pr_url = models.URLField(blank=True)
    pr_branch = models.CharField(max_length=200, blank=True)
    # Commit the code checkout was at when the agent produced proposed_diff. The
    # PR builds the fix on THIS commit and cherry-picks it onto the latest
    # default branch, so a diff still applies after main has moved on.
    base_sha = models.CharField(max_length=40, blank=True)

    # PR-review ingestion bookkeeping (Phase 2) — highest GitHub ids already seen.
    last_seen_review_id = models.BigIntegerField(null=True, blank=True)
    last_seen_comment_id = models.BigIntegerField(null=True, blank=True)

    # Learning draft the agent proposes on close (Phase 3); admin edits then saves.
    learning_title = models.CharField(max_length=200, blank=True)
    learning_draft = models.TextField(blank=True)

    error = models.TextField(blank=True)             # last failure detail, if any
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'debug_requests'
        ordering = ['-created_at']

    def __str__(self):
        return f'[{self.kind}] {self.title}'


class DebugMessage(models.Model):
    class Role(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        AGENT = 'agent', 'Agent'
        SYSTEM = 'system', 'System'   # tool traces, PR-review ingest, status notes

    request = models.ForeignKey(
        DebugRequest, on_delete=models.CASCADE, related_name='messages',
    )
    role = models.CharField(max_length=10, choices=Role.choices)
    content = models.TextField()
    # Structured extras: tool calls made, sources touched (code/db/logs), usage.
    meta = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'debug_messages'
        ordering = ['created_at', 'id']

    def __str__(self):
        return f'{self.role} @ {self.request_id}'


class DebugLearning(models.Model):
    """A finalized lesson — the agent's persistent memory across threads."""
    kind = models.CharField(max_length=10, choices=DebugRequest.Kind.choices)
    title = models.CharField(max_length=200)
    content = models.TextField()
    tags = models.JSONField(null=True, blank=True)  # list of keyword strings
    source_request = models.ForeignKey(
        DebugRequest, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='learnings',
    )
    # Populated on every save() from title+content — backs the Postgres
    # full-text retrieval in debugger.agent._relevant_learnings (only
    # relevant learnings get injected into the prompt, not a recency dump).
    search_vector = SearchVectorField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'debug_learnings'
        ordering = ['-created_at']
        indexes = [GinIndex(fields=['search_vector'], name='debug_learning_search_gin')]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        type(self).objects.filter(pk=self.pk).update(
            search_vector=SearchVector('title', weight='A') + SearchVector('content', weight='B')
        )
