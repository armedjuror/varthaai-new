from django.urls import path

from formbuilder import views_admin as v

app_name = 'formbuilder'

# Mounted at /admin/
urlpatterns = [
    path('forms/', v.forms_page, name='forms'),
    path('forms/<int:pk>/', v.form_detail_page, name='form_detail'),
    path('forms/<int:pk>/export.csv', v.export_csv, name='export_csv'),
    path('forms/<int:pk>/qr.pdf', v.qr_pdf, name='qr_pdf'),
    path('api/forms/', v.FormsAPI.as_view(), name='forms_api'),
    path('api/forms/<int:pk>/', v.FormDetailAPI.as_view(), name='form_api'),
    path('api/forms/<int:pk>/links/', v.FormLinksAPI.as_view(), name='links_api'),
    path('api/forms/<int:pk>/results/', v.FormResultsAPI.as_view(), name='results_api'),
]
