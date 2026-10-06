import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from core.permissions import MODULE_KEYS

# Matches `permission_module = 'key'` (DRF views) and `require_module('key')`
# / `@require_module('key')` (plain Django views) — the two ways a view
# declares which brand-permission module guards it.
_MODULE_KEY_RE = re.compile(
    r"""(?:permission_module\s*=\s*|require_module\(\s*)['"]([A-Za-z0-9_]+)['"]"""
)

_SKIP_DIR_NAMES = {
    '.git', '.claude', 'migrations', '__pycache__', 'node_modules',
    'venv', '.venv', 'env', 'static', 'staticfiles', 'media',
}


class ModulePermissionRegistryTests(SimpleTestCase):
    """
    Guards against the Settings > Admin Users permission list silently
    drifting out of sync with what's actually enforced in code: every
    `permission_module` / `require_module` key used anywhere must be
    registered in core.permissions.MODULE_PERMISSIONS, or it can never be
    granted to a non-super_admin through the UI.
    """

    def test_every_declared_permission_module_is_registered(self):
        base_dir = Path(settings.BASE_DIR)
        self_path = Path(__file__).resolve()
        unregistered = {}
        for path in base_dir.rglob('*.py'):
            if any(part in _SKIP_DIR_NAMES for part in path.parts):
                continue
            if path.resolve() == self_path:
                continue
            text = path.read_text(encoding='utf-8', errors='ignore')
            for match in _MODULE_KEY_RE.finditer(text):
                key = match.group(1)
                if key not in MODULE_KEYS:
                    unregistered.setdefault(key, []).append(
                        str(path.relative_to(base_dir))
                    )
        self.assertFalse(
            unregistered,
            'Permission module key(s) used in code but not registered in '
            'core/permissions.py (add them there so they appear as '
            'checkboxes in Settings > Admin Users): ' + repr(unregistered),
        )
