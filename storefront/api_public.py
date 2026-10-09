"""
Public storefront read APIs + coupon validation + source tracking + reviews.

Ported from PHP api/get_flavors.php, get_reviews.php, get_blogs.php,
validate_coupon.php, track_source.php, submit_review.php.
"""
import json

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.dateformat import format as dj_format
from rest_framework.response import Response

from accounts.models import User
from core.models import Setting
from marketing.models import Blog, Review
from orders.models import Coupon
from orders.views_b2c import _validate_coupon
from products.models import Flavor

from storefront import services
from storefront.api_base import StorefrontAPIView


def _brand_id():
    return services.storefront_brand_id()


LEAD_POPUP_SETTING_KEYS = (
    'lead_popup_enabled', 'lead_popup_headline', 'lead_popup_body',
    'lead_popup_button_text', 'lead_popup_success_message', 'lead_popup_coupon_code',
    'lead_popup_delay_seconds', 'lead_popup_collect_name', 'lead_popup_collect_email',
    'lead_popup_collect_phone', 'lead_popup_require_email', 'lead_popup_require_phone',
    'lead_popup_extra_fields',
)


class FlavorsAPI(StorefrontAPIView):
    """GET active flavors — { success, flavors }."""

    def get(self, request):
        flavors = []
        for f in Flavor.objects.filter(is_active=True).order_by('id'):
            flavors.append({
                'id': f.id,
                'name': f.name,
                'description': f.description,
                'ingredients': f.ingredients,
                'nutrition_fact': f.nutrition_fact,
                'image': f.image.url if f.image else '',
                'price_per_kg': f.price_per_kg,
                'sale_price_per_kg': f.sale_price_per_kg,
            })
        return Response({'success': True, 'flavors': flavors})


class ReviewsAPI(StorefrontAPIView):
    """GET approved reviews — { success, reviews }."""

    def get(self, request):
        rows = (
            Review.objects.filter(brand_id=_brand_id(), is_approved=True)
            .select_related('user')
            .order_by('-created_at')
        )
        reviews = [{
            'id': r.id,
            'name': r.user.name if r.user else '',
            'designation': r.user.designation if r.user else '',
            'rating': r.rating,
            'review': r.review,
            'date': dj_format(r.created_at, 'd M Y'),
        } for r in rows]
        return Response({'success': True, 'reviews': reviews})


class BlogsAPI(StorefrontAPIView):
    """
    GET published blogs — list, or a single blog via ?slug=.
    Shape matches PHP get_blogs.php: { success, data, total } / { success, data }.
    """

    def get(self, request):
        slug = request.query_params.get('slug')
        # Match PHP: published AND not scheduled for the future.
        published = (
            Blog.objects.filter(is_published=True)
            .filter(Q(published_at__isnull=True) | Q(published_at__lte=timezone.now()))
            .select_related('created_by')
        )
        if slug:
            blog = published.filter(slug=slug).first()
            if not blog:
                return Response({'success': False, 'message': 'Blog not found'}, status=404)
            return Response({'success': True, 'data': self._full(blog)})

        try:
            limit = int(request.query_params.get('limit', 0))
            offset = int(request.query_params.get('offset', 0))
        except (TypeError, ValueError):
            limit = offset = 0
        qs = published.order_by('-published_at', '-created_at')
        total = qs.count()
        if limit > 0:
            qs = qs[offset:offset + limit]
        return Response({
            'success': True,
            'data': [self._card(b) for b in qs],
            'total': total,
        })

    @staticmethod
    def _card(b):
        return {
            'id': b.id, 'title': b.title, 'slug': b.slug, 'excerpt': b.excerpt,
            'content': b.content, 'featured_image': b.featured_image.url if b.featured_image else '',
            'published_at': b.published_at.isoformat() if b.published_at else None,
            'created_at': b.created_at.isoformat() if b.created_at else None,
            'author_name': b.created_by.name if b.created_by else None,
        }

    @classmethod
    def _full(cls, b):
        return cls._card(b)


class ValidateCouponAPI(StorefrontAPIView):
    """
    POST { coupon_code, order:{ items:[{id, quantity, price_per_kg, sale_price_per_kg}] } }
    -> { success, message, data:{ discount_amount } }  (port of validate_coupon.php).
    """

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        code = (body.get('coupon_code') or '').strip()
        order = body.get('order') or {}
        items = order.get('items') if isinstance(order, dict) else None
        if not code or not order:
            return Response({'success': False, 'message': 'Missing required parameters'})
        if not items:
            return Response({'success': False, 'message': 'Please add an item!'})

        sale_total = 0.0
        flavor_ids = []
        for it in items:
            qty = float(it.get('quantity') or 0)
            sale_total += float(it.get('sale_price_per_kg') or 0) * qty / 1000
            flavor_ids.append(it.get('id'))

        coupon = Coupon.objects.filter(
            brand_id=_brand_id(), code=code, is_active=True,
        ).first()
        if not coupon:
            return Response({'success': False, 'message': 'This coupon is not valid'})

        discount, error = _validate_coupon(coupon, sale_total, len(items), flavor_ids)
        if error:
            return Response({'success': False, 'message': error})
        return Response({
            'success': True,
            'message': 'Woohoo, Coupon Applied!',
            'data': {'discount_amount': discount},
        })


class TrackSourceAPI(StorefrontAPIView):
    """POST { source, referral } -> { success, message } (port of track_source.php)."""

    def post(self, request):
        from marketing.models import SourceTracking

        body = request.data if isinstance(request.data, dict) else {}
        SourceTracking.objects.create(
            source=(body.get('source') or '')[:50],
            referral=(body.get('referral') or '')[:255],
        )
        return Response({'success': True, 'message': 'Source tracked successfully'})


class SubmitReviewAPI(StorefrontAPIView):
    """
    POST { name, mobile, designation, rating, review, recaptcha_token }
    -> { success, message, points_earned }  (port of submit_review.php).
    Authenticated storefront users skip reCAPTCHA.
    """

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        name = (body.get('name') or '').strip()
        mobile = (body.get('mobile') or '').strip()
        designation = (body.get('designation') or '').strip()
        review_text = (body.get('review') or '').strip()
        try:
            rating = int(body.get('rating') or 0)
        except (TypeError, ValueError):
            rating = 0

        if not services.current_user(request):
            if not services.verify_recaptcha(body.get('recaptcha_token')):
                return Response({'success': False, 'message': 'reCAPTCHA verification failed. Please try again.'})

        if not (name and mobile and designation and rating and review_text):
            return Response({'success': False, 'message': 'All fields are required'})
        if rating < 1 or rating > 5:
            return Response({'success': False, 'message': 'Rating must be between 1 and 5'})

        clean = services.clean_mobile(mobile)
        if not clean:
            return Response({'success': False, 'message': 'Please enter a valid 10-digit mobile number'})

        brand_id = _brand_id()
        user, created = User.objects.get_or_create(
            brand_id=brand_id, mobile=clean, defaults={'name': name},
        )
        if not user.designation:
            user.designation = designation
            user.save(update_fields=['designation'])

        review = Review.objects.create(
            brand_id=brand_id, user=user, rating=rating,
            review=review_text, is_approved=False,
        )
        services.add_points(user.id, settings.REVIEW_POINTS, 'review', review.id)
        return Response({
            'success': True,
            'message': 'Review submitted successfully and will be visible after approval',
            'points_earned': settings.REVIEW_POINTS,
        })


class LeadPopupConfigAPI(StorefrontAPIView):
    """GET -> the admin-configured homepage lead-capture popup, driven by
    `core.Setting` rows the same way every other storefront setting is."""

    def get(self, request):
        s = dict(
            Setting.objects.filter(setting_key__in=LEAD_POPUP_SETTING_KEYS)
            .values_list('setting_key', 'setting_value')
        )
        if s.get('lead_popup_enabled') != 'true':
            return Response({'success': True, 'enabled': False})

        extra_fields = [
            line.strip() for line in (s.get('lead_popup_extra_fields') or '').splitlines()
            if line.strip()
        ]
        try:
            delay_seconds = int(s.get('lead_popup_delay_seconds') or 8)
        except ValueError:
            delay_seconds = 8

        return Response({
            'success': True,
            'enabled': True,
            'headline': s.get('lead_popup_headline') or 'Get a discount on your first order',
            'body': s.get('lead_popup_body') or '',
            'button_text': s.get('lead_popup_button_text') or 'Get My Code',
            'success_message': s.get('lead_popup_success_message') or "Thanks! Here's your code:",
            'coupon_code': s.get('lead_popup_coupon_code') or '',
            'delay_seconds': delay_seconds,
            'collect_name': s.get('lead_popup_collect_name', 'true') != 'false',
            'collect_email': s.get('lead_popup_collect_email', 'true') != 'false',
            'collect_phone': s.get('lead_popup_collect_phone', 'true') != 'false',
            'require_email': s.get('lead_popup_require_email') == 'true',
            'require_phone': s.get('lead_popup_require_phone') == 'true',
            'extra_fields': extra_fields,
        })


class SubmitLeadAPI(StorefrontAPIView):
    """
    POST { name?, email?, phone?, extra?: {label: value}, recaptcha_token }
    -> { success, message, coupon_code }

    Writes straight into `accounts.User` (never a separate leads table) so a
    lead who later signs up or orders is the same row, just flipped off
    `is_lead`. At least one of email/phone is required to identify the row.
    """

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        if not services.current_user(request):
            if not services.verify_recaptcha(body.get('recaptcha_token')):
                return Response({'success': False, 'message': 'reCAPTCHA verification failed. Please try again.'})

        name = (body.get('name') or '').strip()
        email = (body.get('email') or '').strip()
        phone = (body.get('phone') or '').strip()
        raw_extra = body.get('extra')
        if isinstance(raw_extra, str):
            try:
                raw_extra = json.loads(raw_extra)
            except ValueError:
                raw_extra = {}
        extra = raw_extra if isinstance(raw_extra, dict) else {}
        extra = {str(k)[:100]: str(v)[:500] for k, v in extra.items() if str(v).strip()}

        mobile = services.clean_mobile(phone) if phone else None
        if phone and not mobile:
            return Response({'success': False, 'message': 'Please enter a valid 10-digit mobile number'})
        if not mobile and not email:
            return Response({'success': False, 'message': 'Please enter your email or mobile number'})

        brand_id = _brand_id()
        user = User.objects.filter(brand_id=brand_id, mobile=mobile).first() if mobile else None
        if not user and email:
            user = User.objects.filter(brand_id=brand_id, email__iexact=email).first()

        if user:
            update_fields = []
            if name and not user.name:
                user.name, update_fields = name, update_fields + ['name']
            if email and not user.email:
                user.email, update_fields = email, update_fields + ['email']
            if mobile and not user.mobile:
                user.mobile, update_fields = mobile, update_fields + ['mobile']
            if extra:
                merged = dict(user.extra_user_data or {})
                merged.update(extra)
                user.extra_user_data, update_fields = merged, update_fields + ['extra_user_data']
            if update_fields:
                user.save(update_fields=update_fields)
        else:
            user = User.objects.create(
                brand_id=brand_id, name=name, email=email, mobile=mobile,
                extra_user_data=extra, is_lead=True,
            )

        coupon_code = (
            Setting.objects.filter(setting_key='lead_popup_coupon_code')
            .values_list('setting_value', flat=True).first()
        )
        return Response({
            'success': True,
            'message': "Thanks! Here's your code.",
            'coupon_code': coupon_code or '',
        })
