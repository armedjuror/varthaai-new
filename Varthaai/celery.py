"""
Celery application for Varthaai.

Only the Debugger Agent (the `debugger` app) uses background tasks today — every
request/reply/close enqueues a task here so the RCA agent runs off the request
thread. Broker + result backend are Redis (see settings CELERY_*).

Run the worker:  celery -A Varthaai worker -l info
"""
import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Varthaai.settings')

app = Celery('varthaai')

# Pull all CELERY_* settings from Django settings.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks.py in every installed app (debugger.tasks).
app.autodiscover_tasks()
