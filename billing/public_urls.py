from django.urls import path

from billing import public_views as views

app_name = 'order_link'

urlpatterns = [
    path('<str:token>/', views.order_page, name='page'),
    path('<str:token>/summary.pdf', views.summary_pdf, name='summary_pdf'),
    path('<str:token>/invoice.pdf', views.invoice_pdf, name='invoice_pdf'),
    path('<str:token>/credit-note/<int:pk>.pdf', views.credit_note_pdf, name='credit_note_pdf'),
    path('<str:token>/pay/', views.pay_start, name='pay'),
    path('<str:token>/pay/verify/', views.pay_verify, name='pay_verify'),
]
