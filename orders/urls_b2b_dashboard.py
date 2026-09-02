from django.urls import path

from orders import views_b2b_dashboard as views

# Phase 11: B2B dashboard (overview page + stats API). Namespaced under `orders`.
urlpatterns = [
    path('b2b-dashboard/', views.b2b_dashboard_page, name='b2b_dashboard'),
    path('b2b-dashboard/report/', views.b2b_report_page, name='b2b_dashboard_report'),
    path(
        'api/b2b-dashboard/',
        views.B2BDashboardStatsAPI.as_view(),
        name='b2b_dashboard_api',
    ),
]
