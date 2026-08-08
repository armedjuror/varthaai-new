from django.urls import path

from products import packs_api, views

app_name = 'products'

urlpatterns = [
    path('flavors/', views.flavors_page, name='flavors'),
    path('api/flavors/', views.FlavorsAPI.as_view(), name='flavors_api'),
    path('packs/', packs_api.packs_page, name='packs'),
    path('api/packs/', packs_api.PacksAPI.as_view(), name='packs_api'),
    path('stocks/', views.stocks_page, name='stocks'),
    path('api/stocks/', views.StocksAPI.as_view(), name='stocks_api'),
]
