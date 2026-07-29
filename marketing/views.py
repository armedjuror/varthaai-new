"""
Marketing slice: reviews moderation and blog management.

Both mirror the PHP endpoints (`admin/api/reviews.php`, `api/blogs.php`) using
the GET-list + POST-with-`action` convention and the {success, message, data}
envelope. Reviews are brand-scoped; blogs are global (no brand column in the
schema). Approving/rejecting a review cascades to its loyalty points
transaction, matching the PHP (`points_transactions.reference` stores the
review id as a string).
"""
from django.db.models import Avg, Count, Q
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.html import strip_tags
from django.utils.text import slugify
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView

from accounts.models import PointsTransaction
from core.api import HasModulePermission, current_brand_id, err, ok
from core.auth import admin_login_required, require_module

from marketing.models import Blog, Review

PER_PAGE_CHOICES = (10, 20, 50, 100)


# ── Reviews ────────────────────────────────────────────────────────────────

@admin_login_required
@require_module('reviews')
@ensure_csrf_cookie
def reviews_page(request):
    return render(request, 'admin/reviews.html')


@admin_login_required
@require_module('blogs')
@ensure_csrf_cookie
def blogs_page(request):
    return render(request, 'admin/blogs.html')


def _sync_points_status(review_id, status):
    """Mirror the PHP: keep the review's points transaction in step."""
    PointsTransaction.objects.filter(reference=str(review_id)).update(status=status)


class ReviewsAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'reviews'

    def get(self, request):
        brand_id = current_brand_id(request)
        params = request.query_params

        status_filter = params.get('status', 'all')
        rating_filter = params.get('rating', 'all')
        search = (params.get('search') or '').strip()
        date_from = params.get('date_from') or ''
        date_to = params.get('date_to') or ''
        page = max(1, int(params.get('page') or 1))
        per_page = int(params.get('per_page') or 20)
        if per_page not in PER_PAGE_CHOICES:
            per_page = 20

        qs = Review.objects.filter(brand_id=brand_id).select_related('user')

        if status_filter == 'approved':
            qs = qs.filter(is_approved=True)
        elif status_filter == 'pending':
            qs = qs.filter(is_approved=False)

        if rating_filter != 'all':
            try:
                qs = qs.filter(rating=int(rating_filter))
            except (TypeError, ValueError):
                pass

        if search:
            qs = qs.filter(Q(user__name__icontains=search) | Q(review__icontains=search))

        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        total = qs.count()
        offset = (page - 1) * per_page
        rows = [
            {
                'id': r.id,
                'user_id': r.user_id,
                'user_name': r.user.name if r.user else None,
                'rating': r.rating,
                'review': r.review,
                'is_approved': 1 if r.is_approved else 0,
                'created_at': r.created_at.isoformat(),
            }
            for r in qs.order_by('-created_at')[offset:offset + per_page]
        ]

        agg = Review.objects.filter(brand_id=brand_id).aggregate(
            total=Count('id'),
            approved=Count('id', filter=Q(is_approved=True)),
            pending=Count('id', filter=Q(is_approved=False)),
            avg_rating=Avg('rating'),
        )
        stats = {
            'total': agg['total'] or 0,
            'approved': agg['approved'] or 0,
            'pending': agg['pending'] or 0,
            'avg_rating': round(agg['avg_rating'], 1) if agg['avg_rating'] is not None else None,
        }

        return ok({
            'reviews': rows,
            'total': total,
            'page': page,
            'per_page': per_page,
            'stats': stats,
        })

    def post(self, request):
        brand_id = current_brand_id(request)
        action = request.data.get('action')

        if action == 'bulk_approve':
            ids = [int(i) for i in (request.data.get('ids') or [])]
            if not ids:
                return err('No reviews selected.')
            updated = Review.objects.filter(brand_id=brand_id, id__in=ids).update(is_approved=True)
            for rid in ids:
                _sync_points_status(rid, PointsTransaction.Status.CREDITED)
            return ok(message=f'{updated} reviews approved!')

        try:
            review = Review.objects.get(id=int(request.data.get('id') or 0), brand_id=brand_id)
        except (Review.DoesNotExist, TypeError, ValueError):
            return err('Review not found.', status=404)

        if action == 'approve':
            review.is_approved = True
            review.save(update_fields=['is_approved', 'updated_at'])
            _sync_points_status(review.id, PointsTransaction.Status.CREDITED)
            return ok(message='Review approved successfully!')

        if action == 'reject':
            review.is_approved = False
            review.save(update_fields=['is_approved', 'updated_at'])
            _sync_points_status(review.id, PointsTransaction.Status.CANCELLED)
            return ok(message='Review rejected successfully!')

        if action == 'delete':
            rid = review.id
            review.delete()
            PointsTransaction.objects.filter(reference=str(rid)).delete()
            return ok(message='Review deleted successfully!')

        return err('Unknown action.')


# ── Blogs ──────────────────────────────────────────────────────────────────

def _unique_slug(title, exclude_id=None):
    base = slugify(title) or 'blog'
    qs = Blog.objects.filter(slug=base)
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    if qs.exists():
        return f'{base}-{int(timezone.now().timestamp())}'
    return base


def _auto_excerpt(content):
    text = strip_tags(content or '').rstrip()
    excerpt = text[:200].rstrip()
    if len(text) > 200:
        excerpt += '...'
    return excerpt


def _serialize_blog(b):
    return {
        'id': b.id,
        'title': b.title,
        'slug': b.slug,
        'content': b.content,
        'excerpt': b.excerpt,
        'featured_image': b.featured_image.url if b.featured_image else None,
        'is_published': 1 if b.is_published else 0,
        'published_at': b.published_at.isoformat() if b.published_at else None,
        'author_name': b.created_by.name if b.created_by else None,
        'created_at': b.created_at.isoformat(),
    }


class BlogsAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'blogs'

    def get(self, request):
        blog_id = request.query_params.get('id')
        if blog_id:
            try:
                blog = Blog.objects.select_related('created_by').get(id=int(blog_id))
            except (Blog.DoesNotExist, TypeError, ValueError):
                return err('Blog not found.', status=404)
            return ok(_serialize_blog(blog))

        status_filter = request.query_params.get('status', 'all')
        search = (request.query_params.get('search') or '').strip()

        qs = Blog.objects.select_related('created_by')
        if status_filter == 'published':
            qs = qs.filter(is_published=True)
        elif status_filter == 'draft':
            qs = qs.filter(is_published=False)
        if search:
            qs = qs.filter(
                Q(title__icontains=search)
                | Q(content__icontains=search)
                | Q(excerpt__icontains=search),
            )

        rows = [_serialize_blog(b) for b in qs.order_by('-created_at')]
        agg = Blog.objects.aggregate(
            total=Count('id'),
            published=Count('id', filter=Q(is_published=True)),
            draft=Count('id', filter=Q(is_published=False)),
        )
        stats = {
            'total': agg['total'] or 0,
            'published': agg['published'] or 0,
            'draft': agg['draft'] or 0,
        }
        return ok({'blogs': rows, 'total': stats['total'], 'stats': stats})

    def post(self, request):
        action = request.data.get('action')

        if action == 'delete':
            blog = self._get_blog(request)
            if blog is None:
                return err('Blog not found.', status=404)
            blog.delete()
            return ok(message='Blog deleted successfully!')

        if action == 'publish':
            blog = self._get_blog(request)
            if blog is None:
                return err('Blog not found.', status=404)
            publish = bool(int(request.data.get('is_published') or 0))
            blog.is_published = publish
            blog.published_at = timezone.now() if publish else None
            blog.save(update_fields=['is_published', 'published_at', 'updated_at'])
            return ok(message='Blog status updated successfully!')

        if action in ('create', 'update'):
            return self._save(request, action)

        return err('Unknown action.')

    @staticmethod
    def _get_blog(request):
        try:
            return Blog.objects.get(id=int(request.data.get('id') or 0))
        except (Blog.DoesNotExist, TypeError, ValueError):
            return None

    def _save(self, request, action):
        data = request.data
        title = (data.get('title') or '').strip()
        content = data.get('content') or ''
        if not title or not content:
            return err('Title and content are required.')

        excerpt = (data.get('excerpt') or '').strip() or _auto_excerpt(content)
        is_published = bool(int(data.get('is_published') or 0))
        published_at = None
        raw_published = data.get('published_at')
        if raw_published:
            published_at = parse_datetime(raw_published)
        elif is_published:
            published_at = timezone.now()
        if action == 'create':
            blog = Blog(created_by=request.user)
        else:
            blog = self._get_blog(request)
            if blog is None:
                return err('Blog not found.', status=404)

        blog.title = title
        blog.content = content
        blog.excerpt = excerpt
        blog.slug = _unique_slug(title, exclude_id=blog.id)
        blog.is_published = is_published
        blog.published_at = published_at
        # Featured image: replace only when a new file is uploaded; clear on
        # explicit remove; otherwise keep the existing one.
        image = request.FILES.get('featured_image')
        if image:
            blog.featured_image = image
        elif str(data.get('remove_featured_image') or '') == '1':
            blog.featured_image = None

        blog.save()
        message = 'Blog created successfully!' if action == 'create' else 'Blog updated successfully!'
        return ok(_serialize_blog(blog), message=message)
