"""
Public storefront read APIs + coupon validation + source tracking + reviews.

Ported from PHP api/get_flavors.php, get_reviews.php, get_blogs.php,
validate_coupon.php, track_source.php, submit_review.php.
"""
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.dateformat import format as dj_format
from rest_framework.response import Response

from accounts.models import User
from marketing.models import Blog, Review
from orders.models import Coupon
from orders.views_b2c import _validate_coupon
from products.models import Flavor

from storefront import services
from storefront.api_base import StorefrontAPIView


def _brand_id():
    return services.storefront_brand_id()


class FlavorsAPI(StorefrontAPIView):
    """GET active flavors — { success, flavors }."""

    def get(self, request):
        flavors = []
        for f in Flavor.objects.filter(brand_id=_brand_id(), is_active=True).order_by('id'):
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
