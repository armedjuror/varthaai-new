from django.urls import path

from sessions_tracking import views_admin, views_employee

app_name = 'sessions_tracking'

urlpatterns = [
    # Employee — mobile "My Day"
    path('sessions/', views_employee.my_day_page, name='my_day'),
    path('api/sessions/state/', views_employee.SessionStateAPI.as_view(), name='session_state_api'),
    path('api/sessions/start/', views_employee.SessionStartAPI.as_view(), name='session_start_api'),
    path('api/sessions/break/', views_employee.SessionBreakAPI.as_view(), name='session_break_api'),
    path('api/sessions/resume/', views_employee.SessionResumeAPI.as_view(), name='session_resume_api'),
    path('api/sessions/end/', views_employee.SessionEndAPI.as_view(), name='session_end_api'),
    path(
        'api/sessions/edit-end-time/', views_employee.SessionEditEndTimeAPI.as_view(),
        name='session_edit_end_time_api',
    ),
    path('api/sessions/report/', views_employee.DailyReportAPI.as_view(), name='session_report_api'),
    path('api/sessions/companies/', views_employee.CompanyPickerAPI.as_view(), name='company_picker_api'),
    path('api/sessions/flavors/', views_employee.FlavorPickerAPI.as_view(), name='flavor_picker_api'),
    path('api/sessions/visits/', views_employee.VisitCreateAPI.as_view(), name='visit_create_api'),
    path('api/sessions/weekly-off/', views_employee.WeeklyOffAPI.as_view(), name='weekly_off_api'),
    path('api/sessions/leave/', views_employee.LeaveAPI.as_view(), name='leave_api'),
    path('api/sessions/orders/', views_employee.MyOrdersAPI.as_view(), name='my_orders_api'),
    path(
        'api/sessions/orders/status/', views_employee.MyOrderUpdateStatusAPI.as_view(),
        name='my_order_status_api',
    ),
    path(
        'api/sessions/orders/payment/', views_employee.MyOrderPaymentAPI.as_view(),
        name='my_order_payment_api',
    ),

    # Admin — Employee Performance dashboard
    path('performance/', views_admin.performance_daily_page, name='performance'),
    path('performance/period/', views_admin.performance_period_page, name='performance_period'),
    path('performance/overall/', views_admin.performance_overall_page, name='performance_overall'),
    path('api/performance/employees/', views_admin.EmployeeListAPI.as_view(), name='performance_employees_api'),
    path('api/performance/daily/', views_admin.DailyReportAdminAPI.as_view(), name='performance_daily_api'),
    path(
        'api/performance/edit-end-time/', views_admin.EditSessionEndTimeAdminAPI.as_view(),
        name='performance_edit_end_time_api',
    ),
    path('api/performance/period/', views_admin.PeriodReportAdminAPI.as_view(), name='performance_period_api'),
    path(
        'api/performance/period/export/', views_admin.PeriodReportCSVAPI.as_view(),
        name='performance_period_csv_api',
    ),
    path('api/performance/overall/', views_admin.OverallPerformanceAdminAPI.as_view(), name='performance_overall_api'),
]
