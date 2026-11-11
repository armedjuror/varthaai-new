"""
Cache-busting for static files.

nginx serves /static/ with a 30-day cache and file names never change, so
after a deploy browsers kept running old JS (e.g. the storefront still
opening Razorpay after the switch to Cashfree). This storage adds
`?v=<content hash>` to every {% static %} URL: a changed file gets a new
URL, an unchanged one stays cached.

Unlike ManifestStaticFilesStorage it never rewrites file contents, so a
vendor file referencing a missing source map can't break collectstatic.
"""
import hashlib

from django.conf import settings
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import StaticFilesStorage


class VersionedStaticFilesStorage(StaticFilesStorage):
    _versions = {}

    def url(self, name):
        url = super().url(name)
        if not name or '?' in name:
            return url
        version = self._version(name)
        return f'{url}?v={version}' if version else url

    def _version(self, name):
        if not settings.DEBUG and name in self._versions:
            return self._versions[name]
        path = None
        try:
            if self.exists(name):
                path = self.path(name)     # collected copy (production)
        except (NotImplementedError, ValueError):
            path = None
        path = path or finders.find(name)  # source copy (development)
        version = ''
        if path:
            try:
                with open(path, 'rb') as fh:
                    version = hashlib.md5(fh.read(), usedforsecurity=False).hexdigest()[:10]
            except OSError:
                version = ''
        self._versions[name] = version
        return version
