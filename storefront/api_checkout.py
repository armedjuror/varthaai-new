"""
Storefront checkout (ported from PHP api/place_order.php, verify_payment.php,
razorpay_webhook.php).

Storefront orders do NOT deduct FIFO stock — deduction happens later at
ship/deliver via the admin, matching the PHP behaviour and the spec.

Loyalty: a pending 'purchase' transaction is created on order placement; once
payment is confirmed it is credited via orders.views_b2c._sync_loyalty, keeping
storefront-paid orders consistent with the admin order machinery.
"""
import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response

from accounts.models import User
from core.models import Brand
from orders.models import Coupon, Order, OrderItem
from orders.views_b2c import _sync_loyalty, _validate_coupon
from products.models import Flavor

from storefront import services
from storefront.api_base import StorefrontAPIView

log = logging.getLogger(__name__)


def _brand():
    return Brand.objects.filter(id=services.storefront_brand_id()).first()


def _razorpay_client():
    # Lazy import: the payment SDK is optional at load time so a missing/uninstalled
    # `razorpay` package can't break the whole URLconf — only checkout needs it.
    import razorpay

    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


def _order_details(order):
    """Single-order shape mirroring PHP get_orders()[$orderId]."""
    items = []
    total = sale_total = 0.0
    for it in order.items.all():
        price = it.price_per_kg * it.quantity / 1000
        sale = it.sale_price_per_kg * it.quantity / 1000
        total += price
        sale_total += sale
        items.append({
            'flavor_id': it.flavor_id, 'flavor_name': it.flavor_name,
            'price_per_kg': it.price_per_kg, 'sale_price_per_kg': it.sale_price_per_kg,
            'quantity': it.quantity, 'price': price, 'salePrice': sale,
        })
    return {
        'id': order.id, 'user_id': order.user_id, 'status': order.status,
        'payment_status': order.payment_status, 'name': order.name, 'mobile': order.mobile,
        'address': order.address, 'pincode': order.pincode,
        'delivery_charge': order.delivery_charge, 'referral_id': order.referral_id,
        'coupon_id': order.coupon_id, 'order_date': order.order_date.isoformat(),
        'items': items, 'total': total, 'saleTotal': sale_total,
    }


class PlaceOrderAPI(StorefrontAPIView):
    """POST checkout -> creates the order + Razorpay order."""

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        if not services.verify_recaptcha(body.get('recaptcha_token')):
            return Response({'success': False, 'message': 'reCAPTCHA verification failed. Please try again.'})

        name = (body.get('name') or '').strip()
        mobile = (body.get('mobile') or '').strip()
        address = (body.get('address') or '').strip()
        pincode = (body.get('pincode') or '').strip()
        referral_code = (body.get('referral') or '').strip()
        coupon_code = (body.get('coupon') or '').strip()
        items = body.get('items') or []

        if not (name and mobile and address and pincode):
            return Response({'success': False, 'message': 'All fields are required'})
        if not items:
            return Response({'success': False, 'message': 'Please add an item!'})

        clean = services.clean_mobile(mobile)
        if not clean:
            return Response({'success': False, 'message': 'Please enter a valid 10-digit mobile number'})

        brand = _brand()
        user, _ = User.objects.get_or_create(
            brand_id=brand.id, mobile=clean,
            defaults={'name': name, 'address': address, 'pincode': pincode},
        )

        # Authoritative prices from the flavors table; quantity (grams) from input.
        flavor_ids = [it.get('id') for it in items]
        flavors = {f.id: f for f in Flavor.objects.filter(brand_id=brand.id, id__in=flavor_ids)}
        line_items = []
        total = sale_total = 0.0
        for it in items:
            f = flavors.get(int(it.get('id'))) if str(it.get('id')).isdigit() else None
            if not f:
                return Response({'success': False, 'message': f"Invalid flavor: {it.get('id')}"})
            qty = int(it.get('quantity') or 0)
            total += f.price_per_kg * qty / 1000
            sale_total += f.sale_price_per_kg * qty / 1000
            line_items.append((f, qty))

        coupon = None
        coupon_discount = 0.0
        if coupon_code:
            candidate = Coupon.objects.filter(
                brand_id=brand.id, code=coupon_code, is_active=True,
            ).first()
            if candidate:
                disc, error = _validate_coupon(candidate, sale_total, len(items), flavor_ids)
                if not error:
                    coupon = candidate
                    coupon_discount = disc

        delivery_charge = 50 if sale_total < 300 else 0
        final_amount = sale_total - coupon_discount + delivery_charge

        referral_user = None
        if referral_code:
            referral_user = User.objects.filter(brand_id=brand.id, referral_code=referral_code).first()

        order_id = services.generate_order_id(brand)
        try:
            with transaction.atomic():
                order = Order.objects.create(
                    id=order_id, brand_id=brand.id, user=user,
                    name=name, mobile=clean, address=address, pincode=pincode,
                    referral=referral_user, coupon=coupon, coupon_code=coupon_code,
                    coupon_discount=coupon_discount, delivery_charge=delivery_charge,
                    status='pending', payment_status='pending',
                )
                OrderItem.objects.bulk_create([
                    OrderItem(
                        order=order, flavor=f, flavor_name=f.name,
                        price_per_kg=f.price_per_kg, sale_price_per_kg=f.sale_price_per_kg,
                        quantity=qty,
                    ) for f, qty in line_items
                ])
                rzp = _razorpay_client().order.create({
                    'receipt': order_id,
                    'amount': int(round(final_amount * 100)),
                    'currency': 'INR',
                    'notes': {'order_id': order_id, 'customer_name': name, 'customer_mobile': clean},
                })
                order.razorpay_order_id = rzp['id']
                order.save(update_fields=['razorpay_order_id'])
        except Exception as e:  # noqa: BLE001 — mirror PHP: any gateway error rolls back
            log.warning('place_order failed for %s: %s', order_id, e)
            return Response({'success': False, 'message': f'Payment gateway error: {e}'})

        order.refresh_from_db()
        return Response({
            'success': True,
            'order_id': order_id,
            'razorpay_order_id': order.razorpay_order_id,
            'amount': final_amount,
            'key_id': settings.RAZORPAY_KEY_ID,
            'customer_name': name,
            'customer_email': '',
            'customer_mobile': clean,
            'delivery_charge': delivery_charge,
            'order_details': _order_details(order),
        })


class VerifyPaymentAPI(StorefrontAPIView):
    """POST razorpay_{payment_id,order_id,signature} -> verify + confirm order."""

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        payment_id = body.get('razorpay_payment_id')
        rzp_order_id = body.get('razorpay_order_id')
        signature = body.get('razorpay_signature')
        if not (payment_id and rzp_order_id and signature):
            return Response({'success': False, 'message': 'Missing payment data'})

        import razorpay

        try:
            _razorpay_client().utility.verify_payment_signature({
                'razorpay_order_id': rzp_order_id,
                'razorpay_payment_id': payment_id,
                'razorpay_signature': signature,
            })
        except razorpay.errors.SignatureVerificationError:
            return Response({'success': False, 'message': 'Payment verification failed'})
        except Exception as e:  # noqa: BLE001
            return Response({'success': False, 'message': f'Payment verification error: {e}'})

        order = Order.objects.filter(razorpay_order_id=rzp_order_id).first()
        if not order:
            return Response({'success': False, 'message': 'Order not found'})

        order.payment_status = 'paid'
        order.status = 'confirmed'
        order.razorpay_payment_id = payment_id
        order.payment_date = timezone.now()
        order.save(update_fields=['payment_status', 'status', 'razorpay_payment_id', 'payment_date'])

        details = _order_details(order)
        points = services.calculate_purchase_points(details['saleTotal'])
        services.add_points(order.user_id, points, 'purchase', order.id)
        _sync_loyalty(order.id, 'paid')
        if order.referral_id:
            services.add_points(order.referral_id, settings.REFERRAL_POINTS, 'referral', order.id)
        services.notify_new_order(order, order.items.all())

        return Response({
            'success': True, 'message': 'Payment verified successfully',
            'order_id': order.id, 'payment_id': payment_id, 'points_earned': points,
        })


class RazorpayWebhookAPI(StorefrontAPIView):
    """POST Razorpay webhook -> update order state (HMAC-verified)."""

    def post(self, request):
        raw = request.body
        received = request.META.get('HTTP_X_RAZORPAY_SIGNATURE', '')
        secret = settings.RAZORPAY_WEBHOOK_SECRET or ''
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received):
            return Response({'error': 'Invalid signature'}, status=400)

        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return Response({'error': 'Invalid JSON'}, status=400)

        event = data.get('event')
        payment = (data.get('payload', {}).get('payment', {}) or {}).get('entity')
        order_entity = (data.get('payload', {}).get('order', {}) or {}).get('entity')

        if event in ('payment.captured', 'order.paid'):
            rzp_order_id = (payment or {}).get('order_id') or (order_entity or {}).get('id')
            self._mark_paid(rzp_order_id)
        elif event == 'payment.failed':
            self._mark_failed((payment or {}).get('order_id'))
        else:
            log.info('Unhandled webhook event: %s', event)

        return Response({'status': 'success'})

    def _mark_paid(self, rzp_order_id):
        if not rzp_order_id:
            return
        order = Order.objects.filter(razorpay_order_id=rzp_order_id).first()
        if not order:
            return
        already_paid = order.payment_status == 'paid'
        order.payment_status = 'paid'
        order.status = 'confirmed'
        if not order.payment_date:
            order.payment_date = timezone.now()
        order.save(update_fields=['payment_status', 'status', 'payment_date'])
        if not already_paid:
            details = _order_details(order)
            points = services.calculate_purchase_points(details['saleTotal'] + order.delivery_charge)
            services.add_points(order.user_id, points, 'purchase', order.id)
            _sync_loyalty(order.id, 'paid')

    def _mark_failed(self, rzp_order_id):
        if not rzp_order_id:
            return
        Order.objects.filter(razorpay_order_id=rzp_order_id).update(
            payment_status='failed', status='cancelled',
        )
