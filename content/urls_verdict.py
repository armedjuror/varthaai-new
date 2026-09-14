"""
URLs for the Varthaai Verdict intake form + History page (content-generator-
plan.md §13 Phase 5). Plain `urlpatterns`, no `app_name` — folded into
content/urls.py's own list (same pattern urls_scripts.py/urls_posters.py
established for Phase 2/3), not included separately from Varthaai/urls.py.
"""
from django.urls import path

from content import views_verdict

urlpatterns = [
    path('content/verdict/intake/<int:plan_item_id>/',
         views_verdict.verdict_intake_page, name='verdict_intake'),
    path('api/content/verdict/intake/<int:plan_item_id>/',
         views_verdict.VerdictIntakeAPI.as_view(), name='verdict_intake_api'),

    path('content/verdict/history/', views_verdict.verdict_history_page, name='verdict_history'),
    path('api/content/verdict/history/', views_verdict.VerdictHistoryAPI.as_view(), name='verdict_history_api'),
]
