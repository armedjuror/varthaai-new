"""
Test-only URLconf that wires content/urls_scripts.py's routes in, WITHOUT
touching Varthaai/urls.py (explicitly off-limits for this phase of work —
owned by a concurrently-running sibling agent's integration pass). Used via
`@override_settings(ROOT_URLCONF=...)` in content/tests_copywriter.py so
django.test.Client can hit the real view/URL path, same as production will
once Varthaai/urls.py's own `include('content.urls_scripts')` line lands.
"""
from django.urls import include, path

from Varthaai.urls import urlpatterns as _project_urlpatterns

# base.html (extended by admin/content-scripts.html) uses namespaced
# {% url 'core:...' %}/{% url 'accounts:...' %} tags for the sidebar/topbar
# (brand switcher, logout, etc.) — a urlconf with ONLY content.urls_scripts
# can't render it. Reuse the real project urlpatterns wholesale and just add
# the scripts routes, rather than re-declaring every other app's urls here.
urlpatterns = list(_project_urlpatterns) + [
    path('admin/', include('content.urls_scripts')),
]
