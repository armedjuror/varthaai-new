"""
Storefront OTP auth (ported from PHP api/send_otp.php, verify_otp.php,
get_user_by_mobile.php). Login/logout use the storefront session in
storefront.services (separate from the admin session).
"""
import re

from rest_framework.response import Response

from accounts.models import User

from storefront import services
from storefront.api_base import StorefrontAPIView


def _brand_id():
    return services.storefront_brand_id()


class SendOTPAPI(StorefrontAPIView):
    """POST { mobile, recaptcha_token } -> 2Factor send result."""

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        if not services.verify_recaptcha(body.get('recaptcha_token')):
            return Response({'success': False, 'message': 'reCAPTCHA verification failed. Please try again.'})
        mobile = (body.get('mobile') or '').strip()
        if not mobile:
            return Response({'success': False, 'message': 'Mobile number is required'})
        return Response(services.send_otp(mobile))


class VerifyOTPAPI(StorefrontAPIView):
    """
    POST { mobile, otp } -> verify, then log the customer in (creating the
    account on first login). Shape matches PHP verify_otp.php.
    """

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        mobile = (body.get('mobile') or '').strip()
        otp = (body.get('otp') or '').strip()
        if not mobile:
            return Response({'success': False, 'message': 'Mobile number is required'})
        if not otp:
            return Response({'success': False, 'message': 'OTP is required'})

        result = services.verify_otp(mobile, otp)
        if not result['success']:
            return Response(result)

        clean = services.clean_mobile(mobile)
        user = User.objects.filter(brand_id=_brand_id(), mobile=clean).first()
        if user:
            services.login_user(request, user)
            return Response({
                'success': True, 'message': 'Login successful',
                'user_id': user.id, 'is_new_user': False, 'user_name': user.name,
            })
        user = User.objects.create(brand_id=_brand_id(), mobile=clean)
        services.login_user(request, user)
        return Response({
            'success': True, 'message': 'Account created successfully',
            'user_id': user.id, 'is_new_user': True, 'user_name': None,
        })


class GetUserByMobileAPI(StorefrontAPIView):
    """POST { mobile } -> { success, user } (port of get_user_by_mobile.php)."""

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        mobile = (body.get('mobile') or '').strip()
        if not mobile:
            return Response({'success': False, 'message': 'Mobile number is required'})
        if not re.match(r'^\d{10}$', mobile):
            return Response({'success': False, 'message': 'Invalid mobile number format'})
        user = User.objects.filter(brand_id=_brand_id(), mobile=mobile).first()
        if not user:
            return Response({'success': False, 'message': 'User not found'})
        return Response({
            'success': True,
            'user': {
                'id': user.id, 'name': user.name, 'mobile': user.mobile,
                'address': user.address, 'pincode': user.pincode,
            },
        })


class LogoutAPI(StorefrontAPIView):
    """POST -> clear the storefront session."""

    def post(self, request):
        services.logout_user(request)
        return Response({'success': True, 'message': 'Logged out'})
