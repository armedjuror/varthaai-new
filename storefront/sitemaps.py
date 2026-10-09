"""
Sitemap for the public storefront — audit flagged varthaai.com/sitemap.xml
as a 404, leaving search engines (and AI assistants) to discover pages only
by following links.
"""
from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from marketing.models import Blog


class StaticViewSitemap(Sitemap):
    changefreq = 'weekly'

    def items(self):
        return ['storefront:home', 'storefront:shop', 'storefront:blog', 'storefront:policy']

    def location(self, item):
        return reverse(item)

    def priority(self, item):
        return 1.0 if item == 'storefront:home' else 0.7


class BlogSitemap(Sitemap):
    changefreq = 'monthly'
    priority = 0.6

    def items(self):
        return Blog.objects.filter(is_published=True).order_by('-published_at')

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return f'/blog-detail/?slug={obj.slug}'


SITEMAPS = {
    'static': StaticViewSitemap,
    'blog': BlogSitemap,
}
