"""
Generic form builder.

A Form has typed Fields. At most one dropdown field can be the *key field*:
admins generate one link (and QR code) per option of that field, and the
option is carried in an encrypted token (see formbuilder.tokens) so it is
prefilled and hidden on the public page. The module knows nothing about what
the key means (a store, a campaign, an event, ...).

Three optional field roles - user_name / user_phone / user_email - make a
submission create (or update) a customer record, see formbuilder.services.
"""
import re

from django.db import models
from django.utils.crypto import get_random_string

PUBLIC_ID_LENGTH = 10
SLUG_RE = re.compile(r'^[a-z][a-z0-9_]{0,39}$')


def new_public_id():
    return get_random_string(PUBLIC_ID_LENGTH, 'abcdefghjkmnpqrstuvwxyz23456789')


class Form(models.Model):
    DRAFT, LIVE, CLOSED = 'draft', 'live', 'closed'
    STATUS_CHOICES = [(DRAFT, 'Draft'), (LIVE, 'Live'), (CLOSED, 'Closed')]

    brand = models.ForeignKey('core.Brand', on_delete=models.CASCADE, related_name='forms')
    # Random, non-guessable id used in the public URL (/f/<public_id>/).
    public_id = models.CharField(max_length=20, unique=True, default=new_public_id, editable=False)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)

    # Optional dropdown field whose value links prefill (and hide).
    key_field = models.ForeignKey(
        'FormField', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    # Customer records
    create_users = models.BooleanField(default=True)
    one_response_per_phone = models.BooleanField(default=False)
    consent_text = models.CharField(max_length=500, blank=True)

    # Thank-you page
    success_title = models.CharField(max_length=200, default='Thank you!')
    success_message = models.TextField(blank=True, default='Your response has been recorded.')
    success_redirect_url = models.URLField(blank=True)
    show_code = models.BooleanField(default=False)
    code_length = models.PositiveSmallIntegerField(default=6)
    code_prefix = models.CharField(max_length=10, blank=True)
    code_label = models.CharField(max_length=100, default='Your code')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'forms'
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class FormField(models.Model):
    TEXT, LONG_TEXT, NUMBER, EMAIL, PHONE = 'text', 'long_text', 'number', 'email', 'phone'
    DROPDOWN, CHECKBOX, DATE, RATING = 'dropdown', 'checkbox', 'date', 'rating'
    TYPE_CHOICES = [
        (TEXT, 'Short text'), (LONG_TEXT, 'Long text'), (NUMBER, 'Number'),
        (EMAIL, 'Email'), (PHONE, 'Phone'), (DROPDOWN, 'Dropdown'),
        (CHECKBOX, 'Checkbox (yes/no)'), (DATE, 'Date'), (RATING, 'Rating (1-5)'),
    ]

    ROLE_NONE, ROLE_NAME, ROLE_PHONE, ROLE_EMAIL = '', 'user_name', 'user_phone', 'user_email'
    ROLE_CHOICES = [
        (ROLE_NONE, 'None'), (ROLE_NAME, 'User name'),
        (ROLE_PHONE, 'User phone'), (ROLE_EMAIL, 'User email'),
    ]
    # The only field type each role may sit on.
    ROLE_TYPES = {ROLE_NAME: TEXT, ROLE_PHONE: PHONE, ROLE_EMAIL: EMAIL}

    form = models.ForeignKey(Form, on_delete=models.CASCADE, related_name='fields')
    label = models.CharField(max_length=200)
    slug = models.CharField(max_length=40)
    field_type = models.CharField(max_length=12, choices=TYPE_CHOICES, default=TEXT)
    required = models.BooleanField(default=False)
    help_text = models.CharField(max_length=300, blank=True)
    placeholder = models.CharField(max_length=100, blank=True)
    options = models.JSONField(default=list, blank=True)   # dropdown choices, in order
    role = models.CharField(max_length=12, choices=ROLE_CHOICES, blank=True, default=ROLE_NONE)
    position = models.PositiveIntegerField(default=0)
    # Fields that already hold responses are archived, never deleted.
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'form_fields'
        ordering = ['position', 'id']
        unique_together = ('form', 'slug')

    def __str__(self):
        return f'{self.form_id}:{self.slug}'


class Submission(models.Model):
    form = models.ForeignKey(Form, on_delete=models.CASCADE, related_name='submissions')
    # {field slug: value}. Includes the key field's value, whether it came
    # from a link (via_link=True) or from the person picking it.
    answers = models.JSONField(default=dict)
    via_link = models.BooleanField(default=False)
    user = models.ForeignKey(
        'accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='form_submissions')
    code = models.CharField(max_length=30, blank=True)
    phone = models.CharField(max_length=15, blank=True, db_index=True)  # normalised, for the one-per-phone rule
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'form_submissions'
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['form', '-created_at'])]
        constraints = [
            models.UniqueConstraint(
                fields=['form', 'code'], condition=~models.Q(code=''), name='uniq_form_submission_code'),
        ]
