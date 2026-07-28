from django.urls import path

from . import views, views_customers

app_name = 'accounts'

urlpatterns = [
    path('', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('brand/switch/', views.brand_switch, name='brand_switch'),

    # Customers slice
    path('customers/', views_customers.customers_page, name='customers'),
    path('customers/<int:pk>/', views_customers.view_customer_page, name='view_customer'),
    path('customers/<int:pk>/edit/', views_customers.edit_customer_page, name='edit_customer'),
    path('api/customers/', views_customers.CustomersAPI.as_view(), name='customers_api'),
]
