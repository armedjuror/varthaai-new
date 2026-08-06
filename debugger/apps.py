import logging

from django.apps import AppConfig


class DebuggerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'debugger'
    verbose_name = 'Debugger Agent'

    def ready(self):
        # Recover investigations orphaned by a Celery restart/crash: when a
        # worker boots, re-queue anything stuck in a transient status. The
        # worker_ready signal only fires inside a running Celery worker, so web
        # and management processes are unaffected (the import is guarded so they
        # still load even if Celery is absent).
        try:
            from celery.signals import worker_ready
        except Exception:
            return

        @worker_ready.connect(weak=False)
        def _requeue_on_boot(**_kwargs):
            try:
                from debugger.tasks import requeue_orphaned_requests
                requeue_orphaned_requests()
            except Exception:  # never block worker startup
                logging.getLogger(__name__).exception(
                    'debugger: orphaned-request requeue on worker boot failed')
