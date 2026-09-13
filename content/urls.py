from django.urls import path

from content import views

app_name = 'content'

urlpatterns = [
    path('content/', views.content_dashboard_page, name='content_dashboard'),
    path('api/content/dashboard/', views.ContentDashboardAPI.as_view(), name='dashboard_api'),
    path('api/content/designer/test/', views.DesignerTestAPI.as_view(), name='designer_test_api'),

    path('content/calendar/', views.content_calendar_page, name='calendar'),
    path('api/content/calendar/', views.ContentCalendarAPI.as_view(), name='calendar_api'),
    path('api/content/planner/trigger/', views.PlannerTriggerAPI.as_view(), name='planner_trigger_api'),

    path('content/tasks/', views.pending_tasks_page, name='tasks'),
    path('api/content/tasks/', views.PendingTasksAPI.as_view(), name='tasks_api'),
]
