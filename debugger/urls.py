from django.urls import path

from debugger import views

app_name = 'debugger'

urlpatterns = [
    path('debugger/', views.debugger_page, name='page'),
    path('api/debugger/', views.DebuggerAPI.as_view(), name='api'),
]
