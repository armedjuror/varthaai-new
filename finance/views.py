"""
Finance slice: the Expenses admin page plus its combined API.

Mirrors the PHP `admin/expenses.php` + `admin/api/expenses.php`. A single API
view handles both GET (dispatched on `?action=`) and POST (dispatched on the
JSON body `action`), matching the PHP request shape. Everything is wrapped in
the shared {success, message, data} envelope via ok()/err(); the PHP put some
keys at the top level, so those now live under `data` and the ported
expenses.js reads them from `res.data`.

Brand scoping: expenses carry a nullable brand, so the default queryset shows
the active brand's rows plus global (brand IS NULL) rows. An explicit
`brand_id` filter param overrides this (mirrors the PHP filter). Investments
and expense categories are not brand-scoped; order income is scoped to the
active brand.
"""
from datetime import date

from dateutil.relativedelta import relativedelta
from django.db.models import (
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    FloatField,
    Q,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, TruncMonth
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView

from core.api import HasModulePermission, current_brand_id, err, ok
from core.auth import admin_login_required, require_module
from core.models import Brand
from finance.models import Expense, ExpenseCategory, Investment
from orders.models import Order, OrderItem
from products.models import Stock

# oi.quantity (grams) * oi.sale_price_per_kg / 1000 == rupees for the line.
REVENUE = ExpressionWrapper(
    F('quantity') * F('sale_price_per_kg') / 1000.0,
    output_field=FloatField(),
)
INACTIVE_ORDER_STATUSES = ['cancelled', 'deleted']
PER_PAGE_CHOICES = (10, 20, 50)
INVEST_PER_PAGE = 20


@admin_login_required
@require_module('expenses')
@ensure_csrf_cookie
def expenses_page(request):
    return render(request, 'admin/expenses.html')


def _fnum(value):
    """Decimal/None -> float for JSON."""
    return float(value or 0)


def _serialize_expense(e):
    return {
        'id': e.id,
        'brand_id': e.brand_id,
        'brand_name': e.brand.name if e.brand_id else None,
        'category_id': e.category_id,
        'category_name': e.category.name if e.category_id else None,
        'category_color': e.category.color if e.category_id else None,
        'stock_id': e.stock_id,
        'batch_number': e.stock.batch_number if e.stock_id else None,
        'stock_flavor_name': e.stock.flavor.name if e.stock_id else None,
        'title': e.title,
        'description': e.description,
        'amount': _fnum(e.amount),
        'tax_amount': _fnum(e.tax_amount),
        'expense_date': e.expense_date.isoformat() if e.expense_date else '',
        'vendor_name': e.vendor_name,
        'vendor_contact': e.vendor_contact,
        'payment_method': e.payment_method,
        'payment_status': e.payment_status,
        'invoice_number': e.invoice_number,
        'is_recurring': 1 if e.is_recurring else 0,
        'recurring_period': e.recurring_period,
        'tags': e.tags,
        'notes': e.notes,
        'created_by_name': e.created_by.name if e.created_by_id else None,
    }


def _serialize_investment(i):
    return {
        'id': i.id,
        'partner_name': i.partner_name,
        'amount': _fnum(i.amount),
        'investment_date': i.investment_date.isoformat() if i.investment_date else '',
        'description': i.description,
        'payment_method': i.payment_method,
        'reference_number': i.reference_number,
        'notes': i.notes,
        'created_by_name': i.created_by.name if i.created_by_id else None,
    }


class ExpensesAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'expenses'

    # ─────────────────────────────── GET ───────────────────────────────
    def get(self, request):
        action = request.query_params.get('action', 'list')
        handler = {
            'list': self._list,
            'get': self._get_one,
            'categories': self._categories,
            'stats': self._stats,
            'investments': self._investments,
            'income': self._income,
            'stock_batches': self._stock_batches,
            'brands': self._brands,
            'overview': self._overview,
        }.get(action)
        if handler is None:
            return err('Unknown action.')
        return handler(request)

    def _base_expense_qs(self, request):
        """Active-brand rows plus global (brand IS NULL) rows."""
        bid = current_brand_id(request)
        return Expense.objects.filter(Q(brand_id=bid) | Q(brand__isnull=True))

    def _list(self, request):
        p = request.query_params
        page = max(1, int(p.get('page') or 1))
        per_page = int(p.get('per_page') or 0)
        if per_page not in PER_PAGE_CHOICES:
            per_page = 20

        qs = self._base_expense_qs(request)

        if p.get('category_id'):
            qs = qs.filter(category_id=int(p['category_id']))
        if p.get('status'):
            qs = qs.filter(payment_status=p['status'])
        if p.get('date_from'):
            qs = qs.filter(expense_date__gte=p['date_from'])
        if p.get('date_to'):
            qs = qs.filter(expense_date__lte=p['date_to'])
        brand = p.get('brand_id', '')
        if brand != '':
            qs = qs.filter(brand__isnull=True) if brand == '0' else qs.filter(brand_id=int(brand))
        if p.get('search'):
            s = p['search'].strip()
            qs = qs.filter(
                Q(title__icontains=s)
                | Q(vendor_name__icontains=s)
                | Q(invoice_number__icontains=s),
            )

        total = qs.count()
        offset = (page - 1) * per_page
        rows = (
            qs.select_related('brand', 'category', 'stock', 'stock__flavor', 'created_by')
            .order_by('-expense_date', '-id')[offset:offset + per_page]
        )
        return ok({
            'items': [_serialize_expense(e) for e in rows],
            'total': total,
            'page': page,
            'per_page': per_page,
        })

    def _get_one(self, request):
        exp_id = int(request.query_params.get('id') or 0)
        if not exp_id:
            return err('ID required.')
        e = (
            self._base_expense_qs(request)
            .select_related('brand', 'category', 'stock', 'stock__flavor', 'created_by')
            .filter(id=exp_id)
            .first()
        )
        if not e:
            return err('Not found.')
        return ok(_serialize_expense(e))

    def _categories(self, request):
        cats = ExpenseCategory.objects.filter(is_active=True).order_by('name')
        return ok([
            {'id': c.id, 'name': c.name, 'color': c.color, 'description': c.description}
            for c in cats
        ])

    def _stats(self, request):
        now = timezone.localdate()
        base = self._base_expense_qs(request)
        total_field = ExpressionWrapper(
            F('amount') + F('tax_amount'), output_field=DecimalField(max_digits=12, decimal_places=2),
        )

        this_month = base.filter(
            expense_date__year=now.year, expense_date__month=now.month,
        ).aggregate(t=Coalesce(Sum(total_field), Value(0, output_field=DecimalField())))['t']

        pending = base.filter(payment_status='pending').aggregate(
            t=Coalesce(Sum(total_field), Value(0, output_field=DecimalField())),
        )['t']

        cat_count = ExpenseCategory.objects.filter(is_active=True).count()
        total_count = base.count()

        cat_breakdown = (
            base.filter(expense_date__year=now.year, expense_date__month=now.month)
            .values('category__name', 'category__color')
            .annotate(total=Sum('amount'))
            .filter(total__gt=0)
            .order_by('-total')
        )
        breakdown = [
            {'name': r['category__name'], 'color': r['category__color'], 'total': _fnum(r['total'])}
            for r in cat_breakdown
        ]

        start = (now.replace(day=1) - relativedelta(months=11))
        trend_rows = (
            base.filter(expense_date__gte=start)
            .annotate(m=TruncMonth('expense_date'))
            .values('m')
            .annotate(total=Sum('amount'))
            .order_by('m')
        )
        trend = [
            {'month': r['m'].strftime('%Y-%m'), 'label': r['m'].strftime('%b %Y'), 'total': _fnum(r['total'])}
            for r in trend_rows
        ]

        return ok({
            'this_month': _fnum(this_month),
            'pending': _fnum(pending),
            'category_count': cat_count,
            'total_count': total_count,
            'category_breakdown': breakdown,
            'trend': trend,
        })

    def _investments(self, request):
        page = max(1, int(request.query_params.get('page') or 1))
        partner = (request.query_params.get('partner') or '').strip()

        qs = Investment.objects.all()
        if partner:
            qs = qs.filter(partner_name=partner)

        total = qs.count()
        offset = (page - 1) * INVEST_PER_PAGE
        rows = (
            qs.select_related('created_by')
            .order_by('-investment_date', '-id')[offset:offset + INVEST_PER_PAGE]
        )

        partner_totals = (
            Investment.objects.values('partner_name')
            .annotate(total=Sum('amount'))
            .order_by('partner_name')
        )
        grand_total = Investment.objects.aggregate(
            t=Coalesce(Sum('amount'), Value(0, output_field=DecimalField())),
        )['t']

        return ok({
            'items': [_serialize_investment(i) for i in rows],
            'total': total,
            'page': page,
            'per_page': INVEST_PER_PAGE,
            'partner_totals': [
                {'partner_name': r['partner_name'], 'total': _fnum(r['total'])}
                for r in partner_totals
            ],
            'grand_total': _fnum(grand_total),
        })

    def _active_orders(self, request):
        bid = current_brand_id(request)
        return Order.objects.exclude(status__in=INACTIVE_ORDER_STATUSES).filter(brand_id=bid)

    def _income(self, request):
        p = request.query_params
        orders = self._active_orders(request)
        if p.get('date_from'):
            orders = orders.filter(order_date__date__gte=p['date_from'])
        if p.get('date_to'):
            orders = orders.filter(order_date__date__lte=p['date_to'])
        if p.get('brand_id') and int(p['brand_id']) > 0:
            orders = orders.filter(brand_id=int(p['brand_id']))

        items = OrderItem.objects.filter(order__in=orders)
        order_count = orders.count()
        total_revenue = items.aggregate(r=Sum(REVENUE))['r'] or 0.0

        monthly = (
            items.annotate(m=TruncMonth('order__order_date'))
            .values('m')
            .annotate(orders=Count('order', distinct=True), revenue=Sum(REVENUE))
            .order_by('-m')[:12]
        )
        by_brand = (
            items.values('order__brand__name', 'order__brand_id')
            .annotate(orders=Count('order', distinct=True), revenue=Sum(REVENUE))
            .order_by('-revenue')
        )

        return ok({
            'summary': {'order_count': order_count, 'total_revenue': float(total_revenue)},
            'monthly': [
                {
                    'month': r['m'].strftime('%Y-%m'),
                    'label': r['m'].strftime('%b %Y'),
                    'orders': r['orders'],
                    'revenue': float(r['revenue'] or 0),
                }
                for r in monthly
            ],
            'by_brand': [
                {
                    'brand_name': r['order__brand__name'],
                    'brand_id': r['order__brand_id'],
                    'orders': r['orders'],
                    'revenue': float(r['revenue'] or 0),
                }
                for r in by_brand
            ],
        })

    def _stock_batches(self, request):
        qs = Stock.objects.select_related('flavor')
        rows = qs.order_by('-last_restocked_date')
        return ok([
            {
                'id': s.id,
                'batch_number': s.batch_number,
                'flavor_name': s.flavor.name,
                'quantity_kg': round((s.quantity_grams or 0) / 1000, 2),
                'cost_price_per_kg': _fnum(s.cost_price_per_kg),
                'last_restocked_date': s.last_restocked_date.isoformat() if s.last_restocked_date else None,
            }
            for s in rows
        ])

    def _brands(self, request):
        brands = Brand.objects.filter(is_active=True).order_by('name')
        return ok([{'id': b.id, 'name': b.name} for b in brands])

    def _overview(self, request):
        base = self._base_expense_qs(request)
        total_field = ExpressionWrapper(
            F('amount') + F('tax_amount'), output_field=DecimalField(max_digits=12, decimal_places=2),
        )
        total_expenses = base.aggregate(
            t=Coalesce(Sum(total_field), Value(0, output_field=DecimalField())),
        )['t']
        total_investments = Investment.objects.aggregate(
            t=Coalesce(Sum('amount'), Value(0, output_field=DecimalField())),
        )['t']
        total_income = OrderItem.objects.filter(
            order__in=self._active_orders(request),
        ).aggregate(r=Sum(REVENUE))['r'] or 0.0

        months = []
        first_of_month = timezone.localdate().replace(day=1)
        for i in range(5, -1, -1):
            m = first_of_month - relativedelta(months=i)
            exp = base.filter(expense_date__year=m.year, expense_date__month=m.month).aggregate(
                t=Coalesce(Sum(total_field), Value(0, output_field=DecimalField())),
            )['t']
            inc = OrderItem.objects.filter(
                order__in=self._active_orders(request).filter(
                    order_date__year=m.year, order_date__month=m.month,
                ),
            ).aggregate(r=Sum(REVENUE))['r'] or 0.0
            inv = Investment.objects.filter(
                investment_date__year=m.year, investment_date__month=m.month,
            ).aggregate(t=Coalesce(Sum('amount'), Value(0, output_field=DecimalField())))['t']
            months.append({
                'month': m.strftime('%Y-%m'),
                'label': m.strftime('%b %Y'),
                'expenses': _fnum(exp),
                'income': float(inc),
                'investments': _fnum(inv),
            })

        return ok({
            'total_expenses': _fnum(total_expenses),
            'total_investments': _fnum(total_investments),
            'total_income': float(total_income),
            'net_profit': float(total_income) - _fnum(total_expenses),
            'monthly': months,
        })

    # ─────────────────────────────── POST ──────────────────────────────
    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        action = body.get('action', '')
        handler = {
            'create': self._create_expense,
            'update': self._update_expense,
            'delete': self._delete_expense,
            'create_investment': self._create_investment,
            'update_investment': self._update_investment,
            'delete_investment': self._delete_investment,
        }.get(action)
        if handler is None:
            return err('Unknown action.')
        return handler(request, body)

    @staticmethod
    def _expense_fields(body):
        brand = body.get('brand_id', '')
        stock = body.get('stock_id', '')
        is_recurring = bool(int(body.get('is_recurring') or 0))
        return {
            'brand_id': int(brand) if brand not in ('', None) else None,
            'category_id': int(body.get('category_id') or 0),
            'stock_id': int(stock) if stock not in ('', None) else None,
            'title': (body.get('title') or '').strip(),
            'description': (body.get('description') or '').strip(),
            'amount': float(body.get('amount') or 0),
            'tax_amount': float(body.get('tax_amount') or 0),
            'expense_date': (body.get('expense_date') or '').strip(),
            'vendor_name': (body.get('vendor_name') or '').strip(),
            'vendor_contact': (body.get('vendor_contact') or '').strip(),
            'payment_method': body.get('payment_method') or 'cash',
            'payment_status': body.get('payment_status') or 'pending',
            'invoice_number': (body.get('invoice_number') or '').strip(),
            'is_recurring': is_recurring,
            'recurring_period': (body.get('recurring_period') or None) if is_recurring else None,
            'tags': (body.get('tags') or '').strip(),
            'notes': (body.get('notes') or '').strip(),
        }

    def _create_expense(self, request, body):
        f = self._expense_fields(body)
        if not f['category_id'] or not f['title'] or f['amount'] <= 0 or not f['expense_date']:
            return err('Category, title, amount and date are required.')
        e = Expense.objects.create(created_by=request.user, **f)
        return ok({'id': e.id}, 'Expense created.')

    def _update_expense(self, request, body):
        exp_id = int(body.get('id') or 0)
        f = self._expense_fields(body)
        if not exp_id or not f['category_id'] or not f['title'] or f['amount'] <= 0 or not f['expense_date']:
            return err('Required fields missing.')
        e = self._base_expense_qs(request).filter(id=exp_id).first()
        if not e:
            return err('Not found.')
        for k, v in f.items():
            setattr(e, k, v)
        e.save()
        return ok(message='Expense updated.')

    def _delete_expense(self, request, body):
        exp_id = int(body.get('id') or 0)
        if not exp_id:
            return err('ID required.')
        e = self._base_expense_qs(request).filter(id=exp_id).first()
        if not e:
            return err('Not found.')
        e.delete()  # ExpenseAttachment rows cascade.
        return ok(message='Expense deleted.')

    @staticmethod
    def _investment_fields(body):
        return {
            'partner_name': (body.get('partner_name') or '').strip(),
            'amount': float(body.get('amount') or 0),
            'investment_date': (body.get('investment_date') or '').strip(),
            'description': (body.get('description') or '').strip(),
            'payment_method': body.get('payment_method') or 'bank_transfer',
            'reference_number': (body.get('reference_number') or '').strip(),
            'notes': (body.get('notes') or '').strip(),
        }

    def _create_investment(self, request, body):
        f = self._investment_fields(body)
        if not f['partner_name'] or f['amount'] <= 0 or not f['investment_date']:
            return err('Partner name, amount and date are required.')
        i = Investment.objects.create(created_by=request.user, **f)
        return ok({'id': i.id}, 'Investment recorded.')

    def _update_investment(self, request, body):
        inv_id = int(body.get('id') or 0)
        f = self._investment_fields(body)
        if not inv_id or not f['partner_name'] or f['amount'] <= 0 or not f['investment_date']:
            return err('Required fields missing.')
        i = Investment.objects.filter(id=inv_id).first()
        if not i:
            return err('Not found.')
        for k, v in f.items():
            setattr(i, k, v)
        i.save()
        return ok(message='Investment updated.')

    def _delete_investment(self, request, body):
        inv_id = int(body.get('id') or 0)
        if not inv_id:
            return err('ID required.')
        deleted, _ = Investment.objects.filter(id=inv_id).delete()
        if not deleted:
            return err('Not found.')
        return ok(message='Investment deleted.')
