from django.urls import path

from orders import views_b2b

# Orders-B2B agent (Phase 10): B2B orders + offers routes.
urlpatterns = [
    path('b2b-orders/', views_b2b.b2b_orders_page, name='b2b_orders'),
    path('b2b-orders/create/', views_b2b.b2b_order_create_page, name='b2b_order_create'),
    path('b2b-orders/<str:pk>/invoice/', views_b2b.print_b2b_invoice_page, name='print_b2b_invoice'),
    path('b2b-offers/', views_b2b.b2b_offers_page, name='b2b_offers'),
    path('api/b2b-orders/', views_b2b.B2BOrdersAPI.as_view(), name='b2b_orders_api'),
    path('api/b2b-offers/', views_b2b.B2BOffersAPI.as_view(), name='b2b_offers_api'),
]
