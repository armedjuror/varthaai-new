from django.urls import path

from core import brands_views, settings_views, views

app_name = 'core'

urlpatterns = [
    path('dashboard/', views.dashboard_page, name='dashboard'),
    path('api/dashboard/', views.DashboardStatsAPI.as_view(), name='dashboard_api'),

    # Settings slice
    path('settings/', settings_views.settings_page, name='settings'),
    path('api/settings/', settings_views.SettingsAPI.as_view(), name='settings_api'),
    path(
        'api/settings/upload-crunch-audio/',
        settings_views.UploadCrunchAudioAPI.as_view(),
        name='upload_crunch_audio',
    ),

    # Brands slice (super_admin only)
    path('brands/', brands_views.brands_page, name='brands'),
    path('api/brands/', brands_views.BrandsAPI.as_view(), name='brands_api'),
    path('brands/<int:pk>/kit/', brands_views.brand_kit_page, name='brand_kit'),
    path('api/brands/<int:pk>/kit/', brands_views.BrandKitAPI.as_view(), name='brand_kit_api'),
]
