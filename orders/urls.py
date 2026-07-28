from orders.urls_b2b import urlpatterns as b2b_patterns
from orders.urls_b2b_dashboard import urlpatterns as b2b_dashboard_patterns
from orders.urls_b2c import urlpatterns as b2c_patterns

app_name = 'orders'

# Three agents own separate submodule files so none touch the same file:
#   urls_b2c (Phase 7: B2C orders + coupons)
#   urls_b2b (Phase 10: B2B orders + offers)
#   urls_b2b_dashboard (Phase 11: B2B dashboard)
urlpatterns = b2c_patterns + b2b_patterns + b2b_dashboard_patterns
