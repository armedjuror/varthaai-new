"""
URLs for the Brand Assets admin page (reached from the Brands table, same
placement as Brand Kit's own page). Plain `urlpatterns`, no `app_name` —
folded into content/urls.py's own list, same pattern as urls_scripts.py/
urls_posters.py/urls_verdict.py.
"""
from django.urls import path

from content import views_brand_assets

urlpatterns = [
    path('content/brand-assets/<int:pk>/',
         views_brand_assets.brand_assets_page, name='brand_assets'),
    path('api/content/brand-assets/<int:pk>/',
         views_brand_assets.BrandAssetsAPI.as_view(), name='brand_assets_api'),
]
