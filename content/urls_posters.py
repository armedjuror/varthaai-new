"""
URLs for the poster review admin page (content-generator-plan.md §13 Phase
3). Deliberately a separate file from content/urls.py (owned by a sibling
agent) — plain `urlpatterns`, no `app_name`, wired into Varthaai/urls.py's
`include()` list alongside `content.urls`.
"""
from django.urls import path

from content import views_posters

urlpatterns = [
    path('content/posters/', views_posters.content_posters_page, name='content_posters'),
    path('api/content/posters/', views_posters.PostersAPI.as_view(), name='content_posters_api'),
]
