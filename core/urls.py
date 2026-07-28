from django.urls import path

from core import brands_views, settings_views, views

app_name = 'core'

urlpatterns = [
    path('dashboard/', views.dashboard_page, name='dashboard'),
    path('api/dashboard/', views.DashboardStatsAPI.as_view(), name='dashboard_api'),

    # Settings slice
    path('settings/', settings_views.settings_page, name='settings'),
    path('api/settings/', settings_views.SettingsAPI.as_view(), name='settings_api'),

    # Brands slice (super_admin only)
    path('brands/', brands_views.brands_page, name='brands'),
    path('api/brands/', brands_views.BrandsAPI.as_view(), name='brands_api'),
]
