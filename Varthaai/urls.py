"""URL configuration for the Varthaai project."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from storefront.sitemaps import SITEMAPS
from storefront.views import robots_txt

urlpatterns = [
    path('admin/', include('accounts.urls')),          # login, logout, brand switch
    path('admin/', include('core.urls')),              # dashboard + dashboard API
    path('admin/', include('products.urls')),          # flavors, packs, stocks
    path('admin/', include('orders.urls')),            # B2C + B2B orders, coupons, offers
    path('admin/', include('crm.urls')),               # B2B pipeline
    path('admin/', include('finance.urls')),           # expenses, investments
    path('admin/', include('marketing.urls')),         # reviews, blogs
    path('admin/', include('content.urls')),           # Content Studio (Phase 0/1/2/3 — see content-generator-plan.md)
    path('admin/', include('debugger.urls')),          # Debugger Agent (super_admin)
    path('admin/', include('sessions_tracking.urls')), # Employee sessions + performance dashboard
    path('sitemap.xml', sitemap, {'sitemaps': SITEMAPS}, name='sitemap'),
    path('robots.txt', robots_txt, name='robots_txt'),
    path('', include('storefront.urls')),              # Phase 15 — public storefront
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / 'static')
