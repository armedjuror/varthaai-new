"""
Global switch for which Claude auth the `claude` CLI subprocess uses — the
metered ANTHROPIC_API_KEY, or a Pro/Max subscription OAuth token (budget
option). This is deliberately a single global toggle, not a per-request
choice: persisted in core.Setting, read fresh by the agent each time a run
actually starts, so flipping it takes effect on the next request/reply/retry
the worker picks up (in-flight runs keep whatever they already started with).
"""
from django.conf import settings

SETTING_KEY = 'debugger_auth_mode'


def get_auth_mode():
    from core.models import Setting
    from debugger.models import DebugRequest

    val = (Setting.objects.filter(setting_key=SETTING_KEY)
           .values_list('setting_value', flat=True).first())
    if val in DebugRequest.AuthMode.values:
        return val
    return DebugRequest.AuthMode.API


def set_auth_mode(mode):
    from core.models import Setting
    from debugger.models import DebugRequest

    if mode not in DebugRequest.AuthMode.values:
        raise ValueError(f'Invalid auth mode: {mode}')
    if mode == DebugRequest.AuthMode.SUBSCRIPTION and not settings.DEBUGGER_CLAUDE_OAUTH_TOKEN:
        raise ValueError('Subscription auth is not configured on the server.')
    Setting.objects.update_or_create(
        setting_key=SETTING_KEY,
        defaults={
            'setting_value': mode,
            'setting_type': Setting.Type.STRING,
            'category': 'debugger',
            'description': 'Debugger Agent: which Claude auth the CLI uses (api | subscription).',
        },
    )
