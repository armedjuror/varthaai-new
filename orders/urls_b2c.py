from django.urls import path

from orders import views_b2c as views

# B2C orders + coupons routes (mounted under /admin/ by Varthaai/urls.py).
urlpatterns = [
    path('orders/', views.orders_page, name='orders'),
    path('orders/<str:pk>/', views.view_order, name='view_order'),
    path('orders/<str:pk>/edit/', views.edit_order, name='edit_order'),
    path('orders/<str:pk>/invoice/', views.print_invoice, name='print_invoice'),
    path('api/orders/', views.OrdersAPI.as_view(), name='orders_api'),
    path('api/orders/create/', views.CreateOrderAPI.as_view(), name='create_order'),
    path('coupons/', views.coupons_page, name='coupons'),
    path('api/coupons/', views.CouponsAPI.as_view(), name='coupons_api'),
]
