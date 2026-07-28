"""
Storefront (public site) URLs. API paths live under /api/ and return each PHP
endpoint's JSON shape verbatim. Pages are added in the marketing-HTML slice.
"""
from django.urls import path

from storefront import api_account, api_auth, api_checkout, api_public, views

app_name = 'storefront'

urlpatterns = [
    # Pages
    path('', views.home, name='home'),
    path('shop/', views.shop, name='shop'),
    path('blog/', views.blog, name='blog'),
    path('blog-detail/', views.blog_detail, name='blog_detail'),
    path('policy/', views.policy, name='policy'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('print-invoice/', views.print_invoice, name='print_invoice'),
    path('logout/', views.logout, name='logout'),

    # Public read APIs
    path('api/flavors/', api_public.FlavorsAPI.as_view(), name='flavors'),
    path('api/reviews/', api_public.ReviewsAPI.as_view(), name='reviews'),
    path('api/blogs/', api_public.BlogsAPI.as_view(), name='blogs'),
    path('api/validate-coupon/', api_public.ValidateCouponAPI.as_view(), name='validate_coupon'),
    path('api/track-source/', api_public.TrackSourceAPI.as_view(), name='track_source'),
    path('api/submit-review/', api_public.SubmitReviewAPI.as_view(), name='submit_review'),

    # OTP auth
    path('api/send-otp/', api_auth.SendOTPAPI.as_view(), name='send_otp'),
    path('api/verify-otp/', api_auth.VerifyOTPAPI.as_view(), name='verify_otp'),
    path('api/get-user-by-mobile/', api_auth.GetUserByMobileAPI.as_view(), name='get_user_by_mobile'),
    path('api/logout/', api_auth.LogoutAPI.as_view(), name='logout'),

    # Checkout
    path('api/place-order/', api_checkout.PlaceOrderAPI.as_view(), name='place_order'),
    path('api/verify-payment/', api_checkout.VerifyPaymentAPI.as_view(), name='verify_payment'),
    path('api/razorpay-webhook/', api_checkout.RazorpayWebhookAPI.as_view(), name='razorpay_webhook'),

    # Customer account
    path('api/dashboard-data/', api_account.DashboardDataAPI.as_view(), name='dashboard_data'),
    path('api/user-data/', api_account.GetUserDataAPI.as_view(), name='user_data'),
    path('api/update-profile/', api_account.UpdateProfileAPI.as_view(), name='update_profile'),
    path('api/track-order/', api_account.TrackOrderAPI.as_view(), name='track_order'),
    path('api/cancel-order/', api_account.CancelOrderAPI.as_view(), name='cancel_order'),
]
