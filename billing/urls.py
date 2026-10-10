from django.urls import path

from billing import views

app_name = 'billing'

urlpatterns = [
    path('gst/invoice/<int:pk>/', views.invoice_print, name='invoice_print'),
    path('gst/invoice/<int:pk>/pdf/', views.invoice_pdf, name='invoice_pdf'),
    path('gst/credit-note/<int:pk>/', views.credit_note_print, name='credit_note_print'),
    path('gst/credit-note/<int:pk>/pdf/', views.credit_note_pdf, name='credit_note_pdf'),
    path('gst/settings/', views.gst_settings_page, name='gst_settings'),
    path('gst/records/', views.gst_records_page, name='gst_records'),
    path('gst/records/export/', views.gst_records_export, name='gst_records_export'),
    path('api/gst/settings/', views.GSTSettingsAPI.as_view(), name='gst_settings_api'),
    path('api/gst/records/', views.GSTRecordsAPI.as_view(), name='gst_records_api'),
    path('api/gst/order/', views.B2COrderBillingAPI.as_view(), name='b2c_order_billing_api'),
]
