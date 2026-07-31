"""Expose the Celery app so `@shared_task` autodiscovery works on startup."""
from .celery import app as celery_app

__all__ = ('celery_app',)
