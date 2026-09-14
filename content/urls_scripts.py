"""
URL routes for the Script review page/API (content-generator-plan.md §13
Phase 2). Deliberately a separate module from content/urls.py, not wired
into Varthaai/urls.py here — plain `urlpatterns`, no `app_name`, included by
hand elsewhere once both parallel Phase-2/3 agents' work is merged.
"""
from django.urls import path

from content import views_scripts

urlpatterns = [
    path('content/scripts/', views_scripts.content_scripts_page, name='content_scripts'),
    path('api/content/scripts/', views_scripts.ScriptsAPI.as_view(), name='content_scripts_api'),
]
