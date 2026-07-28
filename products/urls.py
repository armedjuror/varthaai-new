from django.urls import path

from products import views

app_name = 'products'

urlpatterns = [
    path('flavors/', views.flavors_page, name='flavors'),
    path('api/flavors/', views.FlavorsAPI.as_view(), name='flavors_api'),
    path('stocks/', views.stocks_page, name='stocks'),
    path('api/stocks/', views.StocksAPI.as_view(), name='stocks_api'),
]
