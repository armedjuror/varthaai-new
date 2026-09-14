"""
Throwaway settings module used ONLY to get an isolated test-database name for
verifying this phase's work in a shared dev environment where a concurrent
sibling agent (Phase 3 / Designer) may be running its own `manage.py test`
against the same remote Postgres instance at the same time — two processes
racing `CREATE DATABASE test_varthaai_db` / holding connections open against
it causes exactly the "test database is being accessed by other users" /
silent-hang failures seen during this phase's verification.

Not a real settings file and not wired into anything — a one-off testing aid,
deleted or ignored once this phase's verification is done. Does NOT touch
Varthaai/settings.py (off-limits for this phase).
"""
from Varthaai.settings import *  # noqa: F401,F403

DATABASES['default']['TEST'] = {'NAME': 'test_varthaai_cw_isolated'}  # noqa: F405
