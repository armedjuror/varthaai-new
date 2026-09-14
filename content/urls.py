from django.urls import path

from content import views, urls_posters, urls_scripts

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

# Phase 2 (Script review) and Phase 3 (Poster review) each ship as a
# separate urls_*.py module (content-generator-plan.md §13) so two parallel
# agents never edited this shared file directly — folded into this single
# app_name='content' urlpatterns list here so `{% url 'content:...' %}`
# reverses correctly (a second include() under the same namespace doesn't
# reverse — Django's namespace_dict keeps only the first-registered one).
urlpatterns += urls_scripts.urlpatterns
urlpatterns += urls_posters.urlpatterns
