from django.urls import path

from finance import views

app_name = 'finance'

urlpatterns = [
    path('expenses/', views.expenses_page, name='expenses'),
    path('api/expenses/', views.ExpensesAPI.as_view(), name='expenses_api'),
]
