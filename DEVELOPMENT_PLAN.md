# Varthaai Django — Development Plan

Living plan for porting the live PHP admin panel + storefront (`/opt/homebrew/var/www`)
to Django. Keep this file updated as phases complete — it is the source of truth that
survives across sessions. The full porting spec is in `CLAUDE.md` (this repo).

---

## 1. Locked decisions

| Decision | Choice | Notes |
|----------|--------|-------|
| Python | 3.10 (existing venv) | No 3.12-only syntax. Django 5.2 runs fine. |
| Database | PostgreSQL (Neon cloud) | Creds in `.env` (git-ignored). Env-driven in `settings.py`. |
| Admin identity | `AUTH_USER_MODEL = 'accounts.AdminUser'` | Custom user; session auth. |
| API layer | Django REST Framework | All APIs return `{success, message, data}`. |
| Frontend | Ported verbatim | Bootstrap 5.3, FA 6.5, jQuery 3.6, Plus Jakarta Sans. `dash-style.css` + all `js/admin/*.js` copied into `static/`. |
| Table names | Match PHP exactly | Every model sets `db_table` to the live table name. |

---

## 2. App structure (8 apps — consolidated from the guide's 9)

| App | Models | Ported from guide's |
|-----|--------|---------------------|
| `core` | Brand, Setting, ActivityLog | core |
| `accounts` | AdminUser, User, OTP, PointsTransaction | accounts + (points from marketing) |
| `products` | Flavor, FlavorPack, Vendor, Stock, StockMovement, StockAlert | catalog + inventory |
| `orders` | Coupon, Order, OrderItem, B2BOffer, B2BOrder, B2BOrderItem, B2BPayment | orders + b2b-orders + (coupon from marketing) |
| `crm` | B2BCategory, B2BCompany, B2BContact, B2BActivity | b2b-crm |
| `finance` | ExpenseCategory, Expense, ExpenseAttachment, Investment | finance |
| `marketing` | Review, Blog, Feedback, MarketingSource, SourceTracking | marketing |
| `storefront` | (views/APIs only, no models) | storefront |

Cross-app FKs use string refs (`'core.Brand'`, `'crm.B2BCompany'`) — no import cycles.
FKs to the admin user use `settings.AUTH_USER_MODEL` with `related_name='+'`.

---

## 3. Progress tracker

- [x] **Phase 0 — Foundation setup**: deps installed (DRF, psycopg, razorpay, Pillow,
      python-dotenv), 8 apps scaffolded, static CSS/JS ported, `settings.py` configured,
      `AdminUser` model, `.env`/`.env.example`/`requirements.txt`/`.gitignore`. `check` clean.
- [x] **Phase 1 — Models + migration**: all ~29 models written across the 8 apps;
      migrations generated + applied to Neon (33 domain tables, PHP-aligned `db_table`
      names). Seeded: Brand "Varthaai" (id=1, prefix `VO`) + super_admin `admin` / `admin123`
      (CHANGE THIS PASSWORD).
- [x] **Phase 2 — Auth** (parallel agent): login/logout/brand-switch views + login.html.
      Shared foundation: `core/api.py` (ok/err + HasModulePermission + `api_exception_handler`
      → 401 for unauth), `core/auth.py` (establish_admin_session, require_module,
      admin_login_required), `core/context_processors.py`.
- [x] **Phase 3 — Admin shell** (parallel agent): `base.html` (sidebar/topbar gated by
      `nav_perms`), `csrf.js` (X-CSRFToken header), utils.js 401 redirect → `/admin/`,
      `alert-modal.js` wired for `showAlertModal`.
- [x] **Phase 4 — Dashboard** (parallel agent): dashboard page + protected DRF stats API
      (13 stat keys, brand-scoped), Chart.js trend, dashboard.js pointed at `/admin/api/dashboard/`.

  **Verified end-to-end (test client): login 200 → POST 302 → dashboard 200 → API 200 envelope
  → brand switch 200 → unauth API 401.** Seed login: `admin` / `admin123`.
  URL/name contract: `accounts:login` (/admin/),Do phase 7 `accounts:logout`, `accounts:brand_switch`,
  `core:dashboard` (/admin/dashboard/), `core:dashboard_api` (/admin/api/dashboard/).
  base.html blocks: title, page_title, extra_css, content, extra_js.

  Known follow-ups: sidebar toggle wiring (admin.js) not loaded yet; `openChangePasswordModal`
  is a placeholder (real modal is Phase 14); role label shows `Super_admin` (needs a
  `str_replace('_',' ')` filter for exact PHP parity).
  **Wave 5–13 note:** launched 7 parallel agents; ALL hit the account monthly spend
  limit mid-run and were finished by the orchestrator inline. Shared stock service
  `products/services.py` (FIFO deduct/revert/available + alert refresh) added for the
  order agents. Dep added: `python-dateutil`.

- [x] **Phase 5 — Products/flavors**: flavors+packs CRUD API + `flavors.html`. Verified page 200 + API `success`.
- [x] **Phase 6 — Stock**: batches/movements/alerts/vendors API + `stocks.html`. Verified page 200 + API `success`.
- [x] **Phase 7 — Orders (B2C)**: BUILT (agent completed before the session died; tracker was
      stale). `orders/views_b2c.py` (871 lines: OrdersAPI, CreateOrderAPI, CouponsAPI) +
      `orders/urls_b2c.py` + templates (orders, view-order, edit-order, print-invoice, coupons).
      Verified end-to-end (test client, rolled back): pages 200, GET APIs `success`, create_order
      → order persisted (₹239 = 199 pack + 40 delivery), coupon add `success`. Coupon actions:
      `add`/`edit`/`toggle`/`delete`. Sidebar Orders + Coupons links wired in base.html.
- [x] **Phase 8 — Customers**: list/view/edit pages + API. Fixed annotation collision
      (`loyalty_points`→`credited_points`). Verified pages 200 + API `success`.
- [x] **Phase 9 — CRM**: pipeline + company-detail pages + API. Verified pages 200 + API `success`.
- [x] **Phase 10 — B2B orders**: BUILT (tracker was stale). `orders/views_b2b.py` (804 lines:
      B2BOrdersAPI + B2BOffersAPI) + `orders/urls_b2b.py` + templates (b2b-orders,
      b2b-order-create, b2b-offers, print-b2b-invoice). Verified end-to-end (test client,
      rolled back): create_order (draft) → confirm_order DEDUCTS stock (10000→8000g, 4×500g)
      → cancel REVERTS stock (8000→10000g, stock_deducted flag cleared) → add_payment `success`;
      offer add `success`. B2B order actions: `create_order`/`update_order`/`confirm_order`/
      `update_status`/`add_payment`/`repeat_order`/`delete_order`. Offer actions: `add`/`edit`/
      `toggle`/`delete`. Uses `products.services.available_grams/deduct_stock/revert_stock`.
      Sidebar B2B Orders + Offers links wired in base.html.
- [x] **Phase 11 — B2B dashboard**: BUILT (tracker was stale). `orders/views_b2b_dashboard.py`
      (199 lines: page + B2BDashboardStatsAPI) + `orders/urls_b2b_dashboard.py` +
      `b2b-dashboard.html`. Verified: page 200, stats API `success`. Sidebar B2B Dashboard link
      wired in base.html. Balances computed from `total_amount - paid_amount` per the spec.
- [x] **Phase 12 — Finance**: expenses/categories/investments API + `expenses.html`. Verified page 200 + API `success`.
- [x] **Phase 13 — Marketing (reviews + blogs)**: pages + API. Verified pages 200 + API `success`.
      NOTE: coupons live in the orders app (Phase 7) and ARE built + verified as of 2026-07-19.

  **Verified working (test client, seed admin/admin123):** flavors, stocks, customers (+detail/edit),
  b2b pipeline (+company detail), expenses, reviews, blogs — all pages 200; flavors/stocks/customers/
  expenses/reviews/b2b GET APIs all return the `success` envelope. Sidebar nav wired for these six.
  UPDATE (2026-07-19): Orders, Coupons, B2B Dashboard, B2B Orders, Offers sidebar links now wired
  (base.html) — Phases 7/10/11 are built and verified. Only Brands + Migrations remain `#`.
- [x] **Phase 14 — Settings**: business config, admin users CRUD, password mgmt.
      `core/settings_views.py` (settings_page + SettingsAPI) + `templates/admin/settings.html`
      (extends base.html) + `static/js/admin/settings.js` (URLs -> `/admin/api/settings/`).
      Routes on `core:settings` (/admin/settings/) + `core:settings_api`. GET keeps PHP shape
      `{success, settings, stats, admins?, brands?}`; POST actions return HTTP 200 `{success,message}`.
      Sidebar Settings link + topbar Change-Password (-> #tabSecurity) wired in base.html.
      Verified end-to-end (test client, rolled back): login->page 200->GET envelope->update_settings
      ->change_password validation->create/update/reset/delete admin (super_admin nulls perms,
      self-delete blocked)->unauth 401.
- [x] **Phase 15 — Storefront**: public pages, OTP auth, Razorpay checkout + webhook.
      BUILT (orchestrator, inline). Lives entirely in the `storefront` app
      (own urls/views), imports existing models — no edits to the `orders` app so it
      runs safely parallel to the orders agent. Root urlconf `path('', include('storefront.urls'))`.

      **Backend DONE + verified (test client, rolled back) as of 2026-07-19:**
      - Settings/secrets moved to `.env` (env-driven, never hardcoded): `STOREFRONT_BRAND_ID`,
        `TWOFACTOR_API_KEY`/`OTP_TEMPLATE`, `RECAPTCHA_SITE_KEY`/`RECAPTCHA_SECRET`,
        `TELEGRAM_BOT_TOKEN`/`TELEGRAM_GROUP_CHAT_ID`, `WHATSAPP_BUSINESS_NUMBER`, points %.
        Documented in `.env.example`. Razorpay keys already present from Phase 0.
      - `storefront/services.py` — recaptcha, 2Factor OTP send/verify, mobile validate/clean,
        loyalty points (`add_points`, `calculate_purchase_points`), Telegram notify, order-id gen,
        and the storefront **customer session** (`storefront_user_id`, separate from admin session).
        External calls degrade gracefully when their secret is blank (skip/return config error).
      - `storefront/api_base.py` — `StorefrontAPIView` (public: `authentication_classes=[]`,
        `AllowAny`, no CSRF — matches PHP). Every endpoint returns its PHP JSON shape verbatim
        (NOT the admin `{success,message,data}` envelope).
      - `api_public.py` — flavors, reviews, blogs (list + `?slug=`), validate-coupon (reuses
        `orders.views_b2c._validate_coupon`), track-source, submit-review (+REVIEW_POINTS).
      - `api_auth.py` — send-otp, verify-otp (logs customer in / creates account), get-user-by-mobile, logout.
      - `api_checkout.py` — place-order (creates Order+items from authoritative Flavor prices +
        Razorpay order in one atomic block; delivery ₹50 if saleTotal<300; NO stock deduction —
        that happens at admin ship/deliver, per spec), verify-payment (signature verify → paid/
        confirmed → purchase points credited via `_sync_loyalty` → referral points → Telegram),
        razorpay-webhook (HMAC-verified; `payment.captured`/`order.paid`→paid, `payment.failed`→failed).
      - `api_account.py` — dashboard-data (GET, session user), user-data, update-profile (own only),
        track-order (public mobile+id lookup), cancel-order (pending only).
      - `storefront/urls.py` wired under `/` + `/api/...`; root urlconf enabled. `manage.py check` clean.

      **Marketing HTML pages DONE + verified as of 2026-07-22 (matches live PHP site):**
      - Verbatim port of the real PHP storefront (identical HTML/classes/IDs): `base.html` +
        shared partials `_navbar.html`/`_footer.html`/`_foot.html`, and pages `home.html`
        (index.php), `shop.html`, `blog.html`, `blog-detail.html` (reads `?slug=`), `policy.html`,
        `dashboard.html` (tabs, redirects to `/` when logged out), `print-invoice.html`
        (server-rendered from context, auth + ownership guard).
      - Reuses the ported jQuery `static/js/main.js`, repointed: 14 API URLs → `/api/...`
        (e.g. `get_flavors.php`→`/api/flavors/`, `place_order.php`→`/api/place-order/`), links
        `blog-detail.php?slug=`→`/blog-detail/?slug=`, `dashboard.php`→`/dashboard/`. The two
        nested-payload calls (place_order, validate_coupon) send JSON so DRF parses the item lists;
        flat POSTs stay form-urlencoded. `STATIC_URL`/`MEDIA_URL` given leading slashes so image
        `src` (`flavor.image`/`blog.featured_image`, now returned as `.url`) resolves on nested pages.
      - `storefront/views.py` page views + `storefront/urls.py` page routes (`''`, `shop/`, `blog/`,
        `blog-detail/`, `policy/`, `dashboard/`, `print-invoice/`, `logout/`).
      - Verified: `manage.py check` clean (venv + pyenv); page smoke test all green — home/shop/blog/
        blog-detail/policy → 200, dashboard 302 logged-out → 200 logged-in, print-invoice 200 valid →
        302 bad id (ownership guard); no leftover `.php` refs in storefront templates or main.js.

      **Remaining for Phase 15:** end-to-end checkout test against real Razorpay/2Factor keys in
      `.env` (reCAPTCHA needs localhost added to allowed domains, or a valid site key, for the OTP/
      checkout grecaptcha call to fire in dev — backend skips verification when the secret is blank).

      **Caveats — RESOLVED 2026-07-22 (runtime smoke test, temp data, rolled back):**
      1. RESOLVED — referral points now persist. PHP's global `UNIQUE(reference)` silently dropped
         the referrer row (same order id as the buyer's purchase row), so referral points never
         persisted in live PHP either. Changed `PointsTransaction` to a composite
         `UniqueConstraint(user, reference)` (migration `accounts/0003`) so the buyer's purchase row
         and the referrer's referral row coexist, while a given user is still credited only once per
         reference (idempotent — `add_points` swallows the duplicate). Smoke-verified both rows persist.
      2. Razorpay webhook normalizes `captured`→`payment_status='paid'` (PHP used the literal
         `captured`) so loyalty crediting via `_sync_loyalty` stays consistent with the admin. (Kept.)
      3. RESOLVED — dashboard prices. The dashboard order table's price column is labelled
         "Price/100g", so PHP's `÷10` was an intentional per-kg→per-100g conversion, NOT a bug. The
         port now divides `sale_price_per_kg`/`price_per_kg` by 10 for display while keeping
         `item_total` from the full per-kg price. (`user-data`/getUserData only reads user fields, so
         it is unaffected; `print-invoice` uses a "Price/kg" column and correctly keeps full per-kg.)
      4. RESOLVED (no change) — `failure_reason` never existed in the real `orders` schema; PHP's
         webhook `UPDATE ... failure_reason = ?` writes a non-existent column (a latent bug that
         errors in prod MySQL). Not porting it is the faithful choice — failures set status/
         payment_status only.

- [x] **Phase 16 — Brands**: multi-brand management (super_admin only). Ported from PHP
      `admin/brands.php` + `admin/api/brands.php`. `core/brands_views.py` (`brands_page`
      redirects non-super to dashboard + `BrandsAPI`: GET list in PHP shape
      `{success, data, current_brand_id}`, POST add/edit/toggle/delete super-admin-only,
      HTTP-200 business failures). Routes `core:brands` (/admin/brands/) + `core:brands_api`
      (/admin/api/brands/). `templates/admin/brands.html` (verbatim PHP port, inline JS
      repointed to `/admin/api/brands/`). Sidebar Brands link wired. `manage.py check` clean.
      Switcher itself stays on `accounts:brand_switch` (base.html topbar). Delete guarded
      when brand has users/orders. Runtime smoke: super_admin GET returns the list shape (2026-07-22).

---

## 4. Parallelization strategy

**Foundation is sequential; features fan out.** Phases 1–4 must be built and stable
before spawning parallel agents, because every feature imports the base template, the
permission decorator, the API response helper, and the brand context.

To make feature work conflict-free, the foundation establishes:
- **Per-app `urls.py`** — the root urlconf only `include()`s them, so no two agents edit
  the same urls file.
- **A shared read-only `base.html`** and `static/js/admin/utils.js` — feature agents
  extend/consume, never modify.
- **A shared API base** (`core/api.py`): `ok(data, message)` / `err(message)` helpers +
  a `require_permission('<perm>')` DRF permission class.

### Fan-out map (after Phase 4)

Each agent owns one vertical slice: template(s) + `app/urls.py` + DRF API view(s) +
wiring the already-ported JS. Independent, so they run in parallel:

| Agent | Scope | Phases |
|-------|-------|--------|
| A | Products: flavors + packs, stocks | 5, 6 |
| B | Orders (B2C): list/create/edit/view/invoice + customers | 7, 8 |
| C | CRM: pipeline, company detail, contacts, activities | 9 |
| D | B2B orders: orders, order-create, offers, B2B dashboard | 10, 11 |
| E | Finance: expenses, categories, investments | 12 |
| F | Marketing: coupons, reviews, blogs | 13 |
| G | Settings: business config, admin users, passwords | 14 |
| H | Storefront: public pages, OTP, Razorpay | 15 |

Coordination rules for agents:
1. Touch only your app's `views.py`, `urls.py`, `api.py`, `templates/<app>/`.
2. Import shared helpers from `core` — never redefine them.
3. Do not edit `Varthaai/settings.py`, root `urls.py`, or `base.html` (raise a note if you
   think they need changes; the orchestrator handles shared-file edits).
4. Match the PHP page's HTML/classes/IDs exactly; reuse the ported JS.

---

## 5. Environment / run notes

```bash
# from repo root, venv is ./venv
./venv/bin/python manage.py check
./venv/bin/python manage.py makemigrations
./venv/bin/python manage.py migrate
./venv/bin/python manage.py runserver
```

- DB creds live in `.env` (git-ignored). `.env.example` documents the keys.
- `AUTH_USER_MODEL` is custom — a superuser is created via
  `manage.py createsuperuser` (username-based) or a seed script.
- Media (flavor images, receipts, QR) → `MEDIA_ROOT`; static → `STATICFILES_DIRS`.

---

## 6. Key business logic (from CLAUDE.md — implement in shared services, not per-view)

- **Stock**: FIFO batches, one `is_active_batch`; deduct on B2C ship/deliver + B2B confirm;
  revert on B2B cancel/delete; auto-activate next batch when one empties; log movements.
- **B2B balances**: always compute from `total_amount - paid_amount` (never stored
  `balance_amount`). Outstanding = sum of positives (status != cancelled); Overdue =
  outstanding past `due_date`; Advance = abs(sum of negatives).
- **Loyalty**: earned on purchase/review/referral/signup; pending → credited on paid.
- **Permissions**: `"all"` = wildcard; `brand_permissions` JSON keyed by brand id;
  NULL on non-super_admin = denied; super_admin bypasses all; brand switch updates session perms.
- **Order id**: `PREFIX_uniqid()` where PREFIX = `brand.order_prefix`.
- **Pack pricing (B2C)**: `price_per_kg = pack.mrp / pack.weight_grams * 1000`;
  `sale_price_per_kg = pack.selling_price / pack.weight_grams * 1000`.
