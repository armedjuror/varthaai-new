"""
Base for public storefront APIs.

The storefront is public (like the PHP endpoints): no DRF authentication and
no CSRF enforcement. The customer "session" is tracked directly on
`request.session` (see storefront.services), independent of DRF auth. Each
endpoint mirrors its PHP JSON shape verbatim, NOT the admin {success,message,data}
envelope, because the ported storefront JS reads those exact keys.
"""
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView


class StorefrontAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
