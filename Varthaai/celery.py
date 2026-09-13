"""
Celery application for Varthaai.

The Debugger Agent (`debugger` app) and the Content Studio's Planner Agent
(`content` app) use background tasks — every long-running Claude call is
enqueued here so it runs off the request thread. Broker + result backend
are Redis (see settings CELERY_*).

Run the worker:  celery -A Varthaai worker -l info
"""
import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Varthaai.settings')

app = Celery('varthaai')

# Pull all CELERY_* settings from Django settings.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks.py in every installed app (debugger.tasks, content.tasks).
app.autodiscover_tasks()
