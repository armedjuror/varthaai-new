"""
Storefront customer account APIs (ported from PHP api/get_dashboard_data.php,
get_user_data.php, update_profile.php, track_order.php, cancel_order.php).

All read/write endpoints here operate on the logged-in storefront customer
(storefront session), except track_order which is a public mobile+order lookup.

NOTE: the dashboard order table's price column is labelled "Price/100g", so the
displayed `sale_price_per_kg`/`price_per_kg` fields are converted from per-kg to
per-100g (÷10), matching get_dashboard_data.php. `item_total` is still computed
from the full per-kg price (quantity * sale_price_per_kg / 1000), as in PHP.
"""
from django.utils.dateformat import format as dj_format
from rest_framework.response import Response

from accounts.models import PointsTransaction, User
from marketing.models import Review
from orders.models import Order

from storefront import services
from storefront.api_base import StorefrontAPIView

_DATE_FMT = 'd M Y, h:i A'

_STATUS_MESSAGES = {
    'pending': 'Order Received',
    'confirmed': 'Order Confirmed, Processing',
    'processing': 'Order is being prepared',
    'shipped': 'Order has been shipped',
    'delivered': 'Order Delivered',
    'cancelled': 'Order Cancelled',
}


def _order_summary(order):
    """Order dict with items + totals, mirroring get_dashboard_data.php."""
    items = []
    subtotal = 0.0
    for it in order.items.all():
        item_total = it.quantity * it.sale_price_per_kg / 1000
        subtotal += item_total
        items.append({
            'name': it.flavor_name,
            'description': it.flavor.description if it.flavor else '',
            'quantity': it.quantity,
            # Displayed under a "Price/100g" column, so convert per-kg -> per-100g.
            'sale_price_per_kg': it.sale_price_per_kg / 10,
            'price_per_kg': it.price_per_kg / 10,
            'item_total': item_total,
        })
    discount = order.coupon_discount or 0.0
    return {
        'id': order.id, 'status': order.status, 'payment_status': order.payment_status,
        'name': order.name, 'mobile': order.mobile, 'address': order.address,
        'pincode': order.pincode, 'delivery_charge': order.delivery_charge,
        'order_date': dj_format(order.order_date, _DATE_FMT),
        'coupon_code': order.coupon_code, 'coupon_discount': discount,
        'subtotal': subtotal, 'discount_amount': discount,
        'total_price': subtotal + order.delivery_charge - discount,
        'items': items, 'item_count': len(items),
    }


class DashboardDataAPI(StorefrontAPIView):
    """GET -> { success, user, orders, points_transactions, reviews } for the session user."""

    def get(self, request):
        user = services.current_user(request)
        if not user:
            return Response({'success': False, 'message': 'User not authenticated'})

        orders = (
            Order.objects.filter(user=user).exclude(status='deleted')
            .prefetch_related('items', 'items__flavor').order_by('-order_date')
        )
        order_ids = set()
        order_list = []
        for o in orders:
            order_ids.add(o.id)
            order_list.append(_order_summary(o))

        loyalty_points = 0
        points = []
        for pt in PointsTransaction.objects.filter(user=user).order_by('-transaction_date'):
            points.append({
                'points': pt.points, 'status': pt.status, 'type': pt.type,
                'reference': pt.reference,
                'transaction_date': dj_format(pt.transaction_date, _DATE_FMT),
                'order_id': pt.reference if pt.reference in order_ids else None,
            })
            if pt.status == 'credited':
                loyalty_points += pt.points

        reviews = [{
            'id': r.id, 'rating': r.rating, 'review': r.review,
            'is_approved': r.is_approved, 'created_at': r.created_at.isoformat(),
        } for r in Review.objects.filter(user=user).order_by('-created_at')]

        return Response({
            'success': True,
            'user': {
                'id': user.id, 'name': user.name, 'mobile': user.mobile,
                'address': user.address, 'designation': user.designation,
                'pincode': user.pincode, 'loyalty_points': loyalty_points,
            },
            'orders': order_list,
            'points_transactions': points,
            'reviews': reviews,
        })


class GetUserDataAPI(StorefrontAPIView):
    """POST { user_id } -> { success, user, orders, points_transactions }."""

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        try:
            user_id = int(body.get('user_id') or 0)
        except (TypeError, ValueError):
            user_id = 0
        if not user_id:
            return Response({'success': False, 'message': 'User ID is required'})
        user = User.objects.filter(id=user_id).first()
        if not user:
            return Response({'success': False, 'message': 'User not found'})

        orders = (
            Order.objects.filter(user=user).exclude(status='deleted')
            .prefetch_related('items', 'items__flavor').order_by('-order_date')
        )
        points = [{
            'points': pt.points, 'type': pt.type, 'reference': pt.reference,
            'transaction_date': dj_format(pt.transaction_date, _DATE_FMT),
        } for pt in PointsTransaction.objects.filter(user=user).order_by('-transaction_date')]

        return Response({
            'success': True,
            'user': {
                'id': user.id, 'name': user.name, 'mobile': user.mobile,
                'address': user.address, 'pincode': user.pincode,
                'loyalty_points': user.loyalty_points,
            },
            'orders': [_order_summary(o) for o in orders],
            'points_transactions': points,
        })


class UpdateProfileAPI(StorefrontAPIView):
    """POST { user_id, name, address, pincode } — session user updates own profile."""

    def post(self, request):
        session_user = services.current_user(request)
        if not session_user:
            return Response({'success': False, 'message': 'User not authenticated'})
        body = request.data if isinstance(request.data, dict) else {}
        try:
            user_id = int(body.get('user_id') or 0)
        except (TypeError, ValueError):
            user_id = 0
        if user_id != session_user.id:
            return Response({'success': False, 'message': 'Unauthorized: You can only update your own profile'})
        name = (body.get('name') or '').strip()
        if not user_id or not name:
            return Response({'success': False, 'message': 'User ID and Name are required'})
        session_user.name = name
        session_user.address = (body.get('address') or '').strip()
        session_user.pincode = (body.get('pincode') or '').strip()
        session_user.save(update_fields=['name', 'address', 'pincode'])
        return Response({'success': True, 'message': 'Profile updated successfully'})


class TrackOrderAPI(StorefrontAPIView):
    """POST { mobile, order_id, recaptcha_token } -> public order tracking."""

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        if not services.verify_recaptcha(body.get('recaptcha_token')):
            return Response({'success': False, 'message': 'reCAPTCHA verification failed. Please try again.'})
        mobile = (body.get('mobile') or '').strip()
        order_id = (body.get('order_id') or '').strip()
        if not mobile or not order_id:
            return Response({'success': False, 'message': 'Mobile number and Order ID are required'})

        order = (
            Order.objects.filter(id=order_id, mobile=mobile)
            .prefetch_related('items').first()
        )
        if not order:
            return Response({
                'success': False,
                'message': 'No order found with the provided details. '
                           'Please check your Order ID and mobile number.',
            })

        items = [{
            'flavor_name': it.flavor_name, 'quantity': it.quantity,
            'price_per_kg': it.price_per_kg, 'sale_price_per_kg': it.sale_price_per_kg,
        } for it in order.items.all()]
        sale_total = sum(it.sale_price_per_kg * it.quantity / 1000 for it in order.items.all())
        return Response({
            'success': True,
            'order': {
                'order_id': order.id,
                'status': _STATUS_MESSAGES.get(order.status, order.status),
                'order_date': dj_format(order.order_date, _DATE_FMT),
                'total': sale_total + order.delivery_charge,
                'delivery_charge': order.delivery_charge,
                'payment_status': (order.payment_status or '').capitalize(),
                'items': items,
            },
        })


class CancelOrderAPI(StorefrontAPIView):
    """POST { order_id } -> cancel a still-pending order."""

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        order_id = (body.get('order_id') or '').strip()
        if not order_id:
            return Response({'success': False, 'message': 'Order ID is required'})
        updated = Order.objects.filter(id=order_id, payment_status='pending').update(
            status='cancelled', payment_status='cancelled',
        )
        if updated:
            return Response({'success': True, 'message': 'Order cancelled successfully'})
        return Response({'success': False, 'message': 'Order not found or cannot be cancelled'})
