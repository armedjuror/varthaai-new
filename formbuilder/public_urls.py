from django.urls import path

from formbuilder import views_public as v

app_name = 'form_public'

# Mounted at /f/
urlpatterns = [
    path('<str:public_id>/', v.public_form, name='public'),
    path('<str:public_id>/thanks/', v.thanks, name='thanks'),
]
