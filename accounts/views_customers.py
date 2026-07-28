"""
Customers slice: the admin HTML pages (list / detail / edit) and the
customers API, ported from the PHP `admin/customers.php`,
`admin/view-customer.php`, `admin/edit-customer.php` and
`admin/api/customers.php`.

Everything is scoped to the active brand. Loyalty balance is derived from
`points_transactions` (sum of credited points), matching the PHP, rather than
the stored `users.loyalty_points` column. Order revenue for a line is
`sale_price_per_kg * quantity / 1000` (quantity is grams), as in the PHP.
"""
import random
import string
import uuid

from django.db.models import (
    Count,
    ExpressionWrapper,
    F,
    FloatField,
    OuterRef,
    Q,
    Subquery,
    Sum,
)
from django.db.models.functions import Coalesce
from django.shortcuts import redirect, render
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView

from accounts.models import PointsTransaction, User
from core.api import HasModulePermission, current_brand_id, err, ok
from core.auth import admin_login_required, require_module
from marketing.models import Review
from orders.models import Order, OrderItem

# oi.quantity (grams) * oi.sale_price_per_kg / 1000 == rupees for the line.
REVENUE = ExpressionWrapper(
    F('quantity') * F('sale_price_per_kg') / 1000.0,
    output_field=FloatField(),
)
PER_PAGE_CHOICES = (10, 25, 50, 100)

# order status -> (text colour, background) for the profile order list.
STATUS_COLORS = {
    'pending': ('#f59e0b', '#fffbeb'),
    'confirmed': ('#3b82f6', '#eff6ff'),
    'processing': ('#8b5cf6', '#f5f3ff'),
    'shipped': ('#06b6d4', '#ecfeff'),
    'delivered': ('#16a34a', '#f0fdf4'),
    'cancelled': ('#dc2626', '#fef2f2'),
    'failed': ('#dc2626', '#fef2f2'),
}
POINTS_TYPE_LABELS = {
    'purchase': 'Purchase Reward',
    'review': 'Review Bonus',
    'feedback': 'Feedback Bonus',
    'referral': 'Referral Bonus',
    'redemption': 'Points Redemption',
    'adjustment': 'Manual Adjustment',
}


def _transaction_view(pt):
    """Shape a PointsTransaction for the profile's points-history list."""
    credited = pt.status == PointsTransaction.Status.CREDITED
    positive = credited and pt.points > 0
    negative = credited and pt.points < 0
    return {
        'type': pt.type,
        'type_label': POINTS_TYPE_LABELS.get(pt.type, pt.type.capitalize()),
        'transaction_date': pt.transaction_date,
        'points': pt.points,
        'reference': pt.reference,
        'is_purchase': pt.type == PointsTransaction.Type.PURCHASE,
        'border_color': '#16a34a' if positive else ('#dc2626' if negative else '#d1d5db'),
        'amt_color': '#16a34a' if positive else ('#dc2626' if negative else '#9ca3af'),
    }


def _credited_points(user_id):
    """Loyalty balance = sum of credited points transactions (matches PHP)."""
    return PointsTransaction.objects.filter(
        user_id=user_id, status=PointsTransaction.Status.CREDITED,
    ).aggregate(s=Coalesce(Sum('points'), 0))['s']


# ── HTML pages ──────────────────────────────────────────────────────────

@admin_login_required
@require_module('customers')
@ensure_csrf_cookie
def customers_page(request):
    return render(request, 'admin/customers.html')


@admin_login_required
@require_module('customers')
@ensure_csrf_cookie
def view_customer_page(request, pk):
    brand_id = current_brand_id(request)
    customer = User.objects.filter(id=pk, brand_id=brand_id).first()
    if not customer:
        return redirect('accounts:customers')

    active_orders = Order.objects.filter(user=customer).exclude(status='deleted')
    total_orders = active_orders.count()
    total_spent = OrderItem.objects.filter(order__in=active_orders).aggregate(
        s=Coalesce(Sum(REVENUE), 0.0),
    )['s']
    last_order_date = active_orders.order_by('-order_date').values_list(
        'order_date', flat=True,
    ).first()

    reviews = list(
        Review.objects.filter(user=customer).order_by('-created_at')[:10]
    )
    total_reviews = Review.objects.filter(user=customer).count()
    avg_rating = Review.objects.filter(user=customer).aggregate(
        a=Coalesce(Sum('rating') * 1.0 / Count('id'), 0.0),
    )['a'] if total_reviews else 0

    loyalty_points = _credited_points(customer.id)

    transactions = [
        _transaction_view(pt)
        for pt in PointsTransaction.objects.filter(user=customer)
        .order_by('-transaction_date')[:20]
    ]

    orders = []
    for o in active_orders.prefetch_related('items').order_by('-order_date')[:10]:
        items = list(o.items.all())
        color, bg = STATUS_COLORS.get(o.status, ('#6b7280', '#f3f4f6'))
        orders.append({
            'id': o.id,
            'status': o.status,
            'status_label': o.status.capitalize(),
            'status_color': color,
            'status_bg': bg,
            'order_date': o.order_date,
            'item_count': len(items),
            'total_amount': sum(i.quantity * i.sale_price_per_kg / 1000 for i in items),
        })

    if loyalty_points >= 1000:
        tier = {'label': 'Gold', 'color': '#b8860b', 'bg': '#fdf6dc'}
    elif loyalty_points >= 100:
        tier = {'label': 'Silver', 'color': '#666', 'bg': '#f0f0f0'}
    else:
        tier = {'label': 'Bronze', 'color': '#cd7f32', 'bg': '#f9ece3'}

    return render(request, 'admin/view-customer.html', {
        'customer': customer,
        'loyalty_points': loyalty_points,
        'tier': tier,
        'total_orders': total_orders,
        'total_spent': total_spent,
        'total_reviews': total_reviews,
        'avg_rating': avg_rating,
        'last_order_date': last_order_date,
        'orders': orders,
        'transactions': transactions,
        'reviews': reviews,
    })


@admin_login_required
@require_module('customers')
@ensure_csrf_cookie
def edit_customer_page(request, pk):
    brand_id = current_brand_id(request)
    customer = User.objects.filter(id=pk, brand_id=brand_id).first()
    if not customer:
        return redirect('accounts:customers')
    return render(request, 'admin/edit-customer.html', {
        'customer': customer,
        'loyalty_points': _credited_points(customer.id),
    })


# ── API ─────────────────────────────────────────────────────────────────

class CustomersAPI(APIView):
    """GET lists/searches customers; POST dispatches on `action`."""

    permission_classes = [HasModulePermission]
    permission_module = 'customers'

    def get(self, request):
        brand_id = current_brand_id(request)
        params = request.query_params

        search = (params.get('search') or '').strip()
        date_from = parse_date(params.get('date_from') or '')
        date_to = parse_date(params.get('date_to') or '')
        page = max(1, int(params.get('page') or 1))
        per_page = int(params.get('per_page') or 0)
        if per_page not in PER_PAGE_CHOICES:
            per_page = 25
        offset = (page - 1) * per_page

        qs = User.objects.filter(brand_id=brand_id)
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(mobile__icontains=search)
                | Q(referral_code__icontains=search)
                | Q(designation__icontains=search)
            )
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        total = qs.count()

        loyalty_sq = (
            PointsTransaction.objects
            .filter(user=OuterRef('pk'), status=PointsTransaction.Status.CREDITED)
            .values('user').annotate(s=Sum('points')).values('s')
        )
        orders_sq = (
            Order.objects.filter(user=OuterRef('pk')).exclude(status='deleted')
            .values('user').annotate(c=Count('id')).values('c')
        )
        spent_sq = (
            OrderItem.objects
            .filter(order__user=OuterRef('pk'), order__payment_status='paid')
            .exclude(order__status='deleted')
            .values('order__user').annotate(s=Sum(REVENUE)).values('s')
        )

        rows = (
            qs.annotate(
                credited_points=Coalesce(Subquery(loyalty_sq), 0),
                total_orders=Coalesce(Subquery(orders_sq), 0),
                total_spent=Coalesce(Subquery(spent_sq), 0.0),
            )
            .order_by('-created_at')[offset:offset + per_page]
        )

        data = [{
            'id': u.id,
            'mobile': u.mobile,
            'name': u.name,
            'address': u.address,
            'designation': u.designation,
            'pincode': u.pincode,
            'loyalty_points': u.credited_points,
            'referral_code': u.referral_code,
            'created_at': u.created_at.isoformat(),
            'total_orders': u.total_orders,
            'total_spent': float(u.total_spent or 0),
        } for u in rows]

        return ok({
            'items': data,
            'total': total,
            'page': page,
            'per_page': per_page,
        })

    def post(self, request):
        brand_id = current_brand_id(request)
        body = request.data if isinstance(request.data, dict) else {}
        action = body.get('action', '')

        if action == 'update':
            return self._update(body, brand_id)
        if action == 'create':
            return self._create(body, brand_id)
        if action == 'adjust_points':
            return self._adjust_points(body, brand_id)
        if action == 'bulk_update_points':
            return self._bulk_update_points(body, brand_id)
        return err('Unknown action.')

    # ── actions ──

    def _update(self, body, brand_id):
        uid = int(body.get('id') or 0)
        mobile = (body.get('mobile') or '').strip()
        if not uid or not mobile:
            return err('ID and mobile are required.')

        customer = User.objects.filter(id=uid, brand_id=brand_id).first()
        if not customer:
            return err('Customer not found.', status=404)

        clash = User.objects.filter(
            brand_id=brand_id, mobile=mobile,
        ).exclude(id=uid).exists()
        if clash:
            return err('Mobile already in use by another customer.')

        customer.name = (body.get('name') or '').strip()
        customer.mobile = mobile
        customer.address = (body.get('address') or '').strip()
        customer.designation = (body.get('designation') or '').strip()
        customer.pincode = (body.get('pincode') or '').strip()
        customer.save(update_fields=[
            'name', 'mobile', 'address', 'designation', 'pincode', 'updated_at',
        ])
        return ok(message='Customer updated successfully.')

    def _create(self, body, brand_id):
        mobile = (body.get('mobile') or '').strip()
        if not mobile:
            return err('Mobile number is required.')

        exists = User.objects.filter(brand_id=brand_id, mobile=mobile).exists()
        if exists:
            return err('A customer with this mobile already exists.')

        User.objects.create(
            brand_id=brand_id,
            mobile=mobile,
            name=(body.get('name') or '').strip(),
            address=(body.get('address') or '').strip(),
            designation=(body.get('designation') or '').strip(),
            pincode=(body.get('pincode') or '').strip(),
            referral_code=self._unique_referral_code(),
        )
        return ok(message='Customer created successfully.')

    def _adjust_points(self, body, brand_id):
        uid = int(body.get('user_id') or 0)
        points_action = body.get('points_action') or ''
        points_value = int(body.get('points_value') or 0)
        if not uid or points_action not in ('add', 'subtract') or points_value <= 0:
            return err('Invalid parameters.')

        customer = User.objects.filter(id=uid, brand_id=brand_id).first()
        if not customer:
            return err('Customer not found.', status=404)

        points = -points_value if points_action == 'subtract' else points_value
        PointsTransaction.objects.create(
            user=customer,
            points=points,
            type=PointsTransaction.Type.ADJUSTMENT,
            status=PointsTransaction.Status.CREDITED,
            reference=self._adjustment_reference(),
        )
        return ok(
            {'new_balance': _credited_points(customer.id)},
            message='Points adjusted successfully.',
        )

    def _bulk_update_points(self, body, brand_id):
        ids = body.get('user_ids') or []
        points_action = body.get('points_action') or ''
        points_value = int(body.get('points_value') or 0)
        if not ids or points_action not in ('add', 'subtract') or points_value <= 0:
            return err('Invalid parameters.')

        multiplier = 1 if points_action == 'add' else -1
        valid_ids = list(
            User.objects.filter(id__in=ids, brand_id=brand_id)
            .values_list('id', flat=True)
        )
        if not valid_ids:
            return err('No matching customers found.')

        PointsTransaction.objects.bulk_create([
            PointsTransaction(
                user_id=uid,
                points=points_value * multiplier,
                type=PointsTransaction.Type.ADJUSTMENT,
                status=PointsTransaction.Status.CREDITED,
                reference=self._adjustment_reference(),
            )
            for uid in valid_ids
        ])
        return ok(message=f"{len(valid_ids)} users' points updated!")

    # ── helpers ──

    @staticmethod
    def _adjustment_reference():
        # points_transactions.reference is unique and NOT NULL; the PHP table
        # tolerated blanks but Django enforces uniqueness, so mint a token.
        return f'ADJ_{uuid.uuid4().hex[:12]}'

    @staticmethod
    def _unique_referral_code():
        alphabet = string.ascii_uppercase + string.digits
        while True:
            code = ''.join(random.choices(alphabet, k=8))
            if not User.objects.filter(referral_code=code).exists():
                return code
