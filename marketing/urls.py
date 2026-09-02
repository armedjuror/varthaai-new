from django.urls import path

from marketing import views

app_name = 'marketing'

urlpatterns = [
    path('reviews/', views.reviews_page, name='reviews'),
    path('api/reviews/', views.ReviewsAPI.as_view(), name='reviews_api'),
    path('blogs/', views.blogs_page, name='blogs'),
    path('blogs/new/', views.blog_editor_page, name='blog_editor_new'),
    path('blogs/<int:pk>/edit/', views.blog_editor_page, name='blog_editor_edit'),
    path('api/blogs/', views.BlogsAPI.as_view(), name='blogs_api'),
    path('api/blogs/ai/sessions/', views.BlogAISessionAPI.as_view(), name='blog_ai_sessions'),
    path('api/blogs/ai/stream/', views.BlogAIStreamView.as_view(), name='blog_ai_stream'),
]
