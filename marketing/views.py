"""
Marketing slice: reviews moderation and blog management.

Both mirror the PHP endpoints (`admin/api/reviews.php`, `api/blogs.php`) using
the GET-list + POST-with-`action` convention and the {success, message, data}
envelope. Reviews and blogs are both brand-scoped to the admin's active
brand. Approving/rejecting a review cascades to its loyalty points
transaction, matching the PHP (`points_transactions.reference` stores the
review id as a string).
"""
import json

from django.conf import settings
from django.db.models import Avg, Count, Q
from django.http import StreamingHttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.html import strip_tags
from django.utils.text import slugify
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView

from accounts.models import PointsTransaction
from core.api import HasModulePermission, current_brand_id, err, ok
from core.auth import admin_login_required, require_module
from products.models import Flavor

from marketing import ai
from marketing.models import Blog, BlogDraftMessage, BlogDraftSession, Review

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


@admin_login_required
@require_module('blogs')
@ensure_csrf_cookie
def blog_editor_page(request, pk=None):
    brand_id = current_brand_id(request)
    blog = None
    if pk:
        blog = Blog.objects.filter(id=pk, brand_id=brand_id).first()
        if blog is None:
            return redirect('marketing:blogs')
    existing_tags = sorted({
        t for row in Blog.objects.filter(brand_id=brand_id).exclude(tags=[]).values_list('tags', flat=True)
        for t in (row or [])
    })
    flavors = list(Flavor.objects.filter(is_active=True).order_by('name').values('id', 'name'))
    return render(request, 'admin/blog_editor.html', {
        'blog': blog,
        'blog_tags_json': json.dumps(blog.tags if blog else []),
        'existing_tags': existing_tags,
        'flavors': flavors,
        'ai_assist_models': settings.AI_ASSIST_MODELS,
        'ai_assist_default_model': settings.AI_ASSIST_DEFAULT_MODEL,
    })


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


def _clean_tags(raw):
    """Normalize the `tags` field from a form/JSON POST into a deduped list
    of trimmed strings (accepts a JSON-encoded string, a comma-separated
    string, or an actual list — the JS client sends JSON, but this stays
    defensive against a plain form post)."""
    if raw is None:
        values = []
    elif isinstance(raw, (list, tuple)):
        values = list(raw)
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            values = parsed if isinstance(parsed, list) else [raw]
        except (TypeError, ValueError):
            values = raw.split(',')
    else:
        values = []
    seen = []
    for v in values:
        t = str(v).strip()
        if t and t not in seen:
            seen.append(t)
    return seen


def _serialize_blog(b):
    return {
        'id': b.id,
        'title': b.title,
        'slug': b.slug,
        'content': b.content,
        'excerpt': b.excerpt,
        'meta_title': b.meta_title,
        'meta_description': b.meta_description,
        'tags': b.tags,
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
        brand_id = current_brand_id(request)
        blog_id = request.query_params.get('id')
        if blog_id:
            try:
                blog = Blog.objects.select_related('created_by').get(id=int(blog_id), brand_id=brand_id)
            except (Blog.DoesNotExist, TypeError, ValueError):
                return err('Blog not found.', status=404)
            return ok(_serialize_blog(blog))

        status_filter = request.query_params.get('status', 'all')
        search = (request.query_params.get('search') or '').strip()
        tag_filter = (request.query_params.get('tag') or '').strip()

        qs = Blog.objects.filter(brand_id=brand_id).select_related('created_by')
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
        if tag_filter:
            qs = qs.filter(tags__contains=[tag_filter])

        rows = [_serialize_blog(b) for b in qs.order_by('-created_at')]
        agg = Blog.objects.filter(brand_id=brand_id).aggregate(
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
            return Blog.objects.get(id=int(request.data.get('id') or 0), brand_id=current_brand_id(request))
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
            blog = Blog(created_by=request.user, brand_id=current_brand_id(request))
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
        blog.meta_title = (data.get('meta_title') or '').strip()
        blog.meta_description = (data.get('meta_description') or '').strip()
        blog.tags = _clean_tags(data.get('tags'))
        # Featured image: replace only when a new file is uploaded; clear on
        # explicit remove; otherwise keep the existing one.
        image = request.FILES.get('featured_image')
        if image:
            blog.featured_image = image
        elif str(data.get('remove_featured_image') or '') == '1':
            blog.featured_image = None

        blog.save()
        session_id = data.get('session_id')
        if session_id:
            BlogDraftSession.objects.filter(id=session_id).update(
                blog=blog, status=BlogDraftSession.Status.APPLIED,
            )
        message = 'Blog created successfully!' if action == 'create' else 'Blog updated successfully!'
        return ok(_serialize_blog(blog), message=message)


# ── Blog AI writing assistant ───────────────────────────────────────────────

def _session_dict(s):
    return {
        'id': s.id,
        'blog_id': s.blog_id,
        'topic': s.topic,
        'target_audience': s.target_audience,
        'tone': s.tone,
        'word_count_target': s.word_count_target,
        'flavor_refs': s.flavor_refs,
        'status': s.status,
        'created_at': s.created_at.isoformat(),
        'updated_at': s.updated_at.isoformat(),
        'messages': [
            {
                'id': m.id,
                'role': m.role,
                'action': m.action,
                'content': m.content,
                'model': m.model,
                'created_at': m.created_at.isoformat(),
            }
            for m in s.messages.all()
        ],
    }


class BlogAISessionAPI(APIView):
    """Create a new AI drafting session, or resume an existing one with its
    full message history (so a half-finished session survives a refresh)."""
    permission_classes = [HasModulePermission]
    permission_module = 'blogs'

    def get(self, request):
        session_id = request.query_params.get('id')
        if not session_id:
            return err('Session id is required.')
        session = BlogDraftSession.objects.filter(id=session_id).prefetch_related('messages').first()
        if session is None:
            return err('Session not found.', status=404)
        return ok(_session_dict(session))

    def post(self, request):
        data = request.data
        session = BlogDraftSession.objects.create(
            blog_id=data.get('blog_id') or None,
            created_by=request.user,
            topic=(data.get('topic') or '').strip(),
            target_audience=(data.get('target_audience') or '').strip(),
            tone=(data.get('tone') or '').strip(),
            word_count_target=data.get('word_count_target') or None,
            flavor_refs=data.get('flavor_refs') or [],
        )
        return ok(_session_dict(session), message='Session created.')


class BlogAIStreamView(APIView):
    """Streams one assistant turn (SSE) for a drafting session and persists it."""
    permission_classes = [HasModulePermission]
    permission_module = 'blogs'

    def post(self, request):
        data = request.data
        session = BlogDraftSession.objects.filter(id=data.get('session_id')).first()
        if session is None:
            return err('Session not found.', status=404)
        action = data.get('action') or BlogDraftMessage.Action.CHAT
        if action not in BlogDraftMessage.Action.values:
            return err('Unknown action.')
        model_id = data.get('model_id') or settings.AI_ASSIST_DEFAULT_MODEL
        allowed_ids = {m['id'] for m in settings.AI_ASSIST_MODELS}
        if model_id not in allowed_ids:
            return err('Unknown or unavailable model.')
        response = StreamingHttpResponse(
            ai.stream_completion(
                session, action,
                (data.get('message') or '').strip(),
                model_id,
                data.get('blog_content') or '',
            ),
            content_type='text/event-stream',
        )
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'  # nginx: don't buffer this SSE response (see deploy/BLOG_AI.md)
        return response
