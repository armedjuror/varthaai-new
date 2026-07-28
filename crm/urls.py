from django.urls import path

from crm import views

app_name = 'crm'

urlpatterns = [
    path('b2b/', views.pipeline_page, name='pipeline'),
    path('b2b/<int:pk>/', views.view_b2b_page, name='view_b2b'),
    path('api/b2b/', views.B2BAPI.as_view(), name='b2b_api'),
]
