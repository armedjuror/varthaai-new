from django.urls import path

from marketing import views

app_name = 'marketing'

urlpatterns = [
    path('reviews/', views.reviews_page, name='reviews'),
    path('api/reviews/', views.ReviewsAPI.as_view(), name='reviews_api'),
    path('blogs/', views.blogs_page, name='blogs'),
    path('api/blogs/', views.BlogsAPI.as_view(), name='blogs_api'),
]
