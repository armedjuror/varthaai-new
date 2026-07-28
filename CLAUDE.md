# Varthaai — Django Migration Guide

You are building a Django port of an existing PHP admin panel + storefront for Varthaai, a food brand (peanut butter etc.) with B2B and B2C sales. The existing app is live at varthaai.com.

## Tech Stack

| Layer | Choice |
|-------|--------|
| Backend | Django 5.x, Python 3.12+ |
| Database | PostgreSQL 16 |
| Auth | Django session auth (admin panel), OTP-based mobile auth (storefront) |
| Frontend | Keep IDENTICAL — Bootstrap 5.3.0 (CDN), Font Awesome 6.5.0, jQuery 3.6.0, Plus Jakarta Sans |
| CSS | Port the existing `dash-style.css` exactly. Do NOT use a new design system |
| API pattern | Django REST Framework — JSON responses `{success, message, data}` |
| Payments | Razorpay integration (webhooks + verify) |
| File uploads | Django media files for images, receipts |

## Project Structure

```
varthaai/
  manage.py
  varthaai/            # project config
    settings.py
    urls.py
    wsgi.py
  core/                # shared: Brand model, settings, helpers
  accounts/            # AdminUser, User (customer), OTP, auth
  catalog/             # Flavor, FlavorPack, Vendor
  inventory/           # Stock, StockMovement, StockAlert
  orders/              # Order, OrderItem (B2C)
  b2b/                 # B2B: Company, Contact, Activity, B2BOrder, B2BOrderItem, B2BPayment, B2BOffer, B2BCategory
  finance/             # Expense, ExpenseCategory, ExpenseAttachment, Investment
  marketing/           # Coupon, Review, Blog, Feedback, MarketingSource, SourceTracking, PointsTransaction
  storefront/          # Public-facing views + APIs (shop, checkout, OTP login)
  templates/
    admin/             # All admin templates — port the PHP HTML exactly
    storefront/        # Public pages
  static/
    css/dash-style.css # Port EXACTLY from the PHP project
    js/admin/          # Port all JS files exactly
```

---

## Database Models (PostgreSQL)

Port every table below. Use Django model conventions (AutoField PK, etc.) but keep column names/types aligned. Use `django.contrib.postgres.fields.JSONField` where needed.

### core.Brand
```
id, name, logo, website, instagram, order_prefix (default 'ORD'), is_active, created_at, updated_at
```

### core.Setting
```
id, setting_key (unique), setting_value (text), setting_type (string|number|boolean|json), description, category (default 'general'), is_public, created_at, updated_at
```

### core.ActivityLog
```
id, user (FK User nullable), admin (FK AdminUser nullable), action, module, details (text), ip_address, user_agent (text), created_at
```

### accounts.AdminUser
Django custom user model or separate model. Fields:
```
id, username (unique), password (hashed), name, role (super_admin|admin|staff), brand_permissions (JSONField nullable), telegram_user_id (bigint unique nullable), last_login, created_at, updated_at
```
- `brand_permissions` JSON format: `{"1": ["all","orders","customers"], "2": ["orders"]}` — brand_id keys, permission arrays as values
- `"all"` in permissions = full access (wildcard)
- NULL brand_permissions on non-super_admin = NO access (denied at login)
- super_admin bypasses all permission checks

### accounts.User (Customer)
```
id, brand (FK Brand), mobile (unique with brand), name, address (text), designation, pincode, referral_code (unique nullable), loyalty_points (default 0), created_at, updated_at
```

### accounts.OTP
```
id, mobile, otp (6 char), is_used (default false), created_at
```

### catalog.Flavor
```
id, brand (FK Brand), name, description (text), price_per_kg (float default 1490), sale_price_per_kg (float default 990), reorder_level_grams (int default 5000), is_active (default true), ingredients (text), nutrition_fact (JSONField), image, created_at, updated_at
```

### catalog.FlavorPack
```
id, flavor (FK Flavor CASCADE), weight_grams, label (varchar 50), mrp (decimal 10,2), selling_price (decimal 10,2), cost_price (decimal 10,2), sku (nullable), is_active (default true), created_at, updated_at
UNIQUE: (flavor_id, weight_grams)
```

### catalog.Vendor
```
id, name, code (varchar 3 unique), fssai_number, contact_name, phone, email, address (text), notes (text), is_active, created_at, updated_at
```

### inventory.Stock
```
id, flavor (FK Flavor CASCADE), is_active_batch (default false), quantity_grams (default 0), reserved_quantity_grams (default 0), cost_price_per_kg (decimal 10,2), supplier_name, supplier_contact, vendor (FK Vendor SET_NULL nullable), last_restocked_date, expiry_date, batch_number, storage_location, notes (text), created_at, updated_at
```

### inventory.StockMovement
```
id, flavor (FK Flavor CASCADE), stock (FK Stock SET_NULL nullable), movement_type (in|out|adjustment|reserved|released), quantity_grams, previous_quantity_grams, new_quantity_grams, reference_type (purchase|sale|wastage|adjustment|order_reserve|order_release|b2b_sale|b2b_reversal), reference_id (varchar 50), cost_price_per_kg (nullable), notes (text), created_by (FK AdminUser SET_NULL), created_at
```

### inventory.StockAlert
```
id, flavor (FK Flavor CASCADE), alert_type (low_stock|out_of_stock|expiring_soon|expired), current_quantity_grams, reorder_level_grams, expiry_date, is_acknowledged (default false), acknowledged_by (FK AdminUser nullable), acknowledged_at, created_at
```

### orders.Order
```
id (varchar 20 PK — format: PREFIX_uniqid e.g. "VO_669f1ff5"), brand (FK Brand), user (FK User nullable), status (pending|confirmed|processing|shipped|delivered|cancelled|failed|deleted default pending), payment_status (pending|paid|failed|refund_initiated|refunded|cancelled default pending), name, mobile, address (text), pincode, delivery_charge (float default 0), referral (FK User nullable), coupon (FK Coupon SET_NULL nullable), coupon_code, coupon_discount (float default 0), stock_deducted (default false), order_date, razorpay_order_id, razorpay_payment_id, payment_date, updated_at
```

### orders.OrderItem
```
id, order (FK Order CASCADE), flavor (FK Flavor SET_NULL nullable), flavor_pack (FK FlavorPack SET_NULL nullable), quantity (int — in grams), price_per_kg (float), sale_price_per_kg (float), flavor_name, pack_label (nullable), created_at
```

### b2b.B2BCategory
```
id, brand (FK Brand CASCADE), name (varchar 100), is_active (default true), created_at
```

### b2b.B2BCompany
```
id, brand (FK Brand CASCADE), category (FK B2BCategory SET_NULL nullable), company_name, address (text), city, state, pincode, gst_number, stage (lead|contacted|negotiation|converted|lost default lead), source, assigned_to (FK AdminUser SET_NULL nullable), discount_type (percentage|amount nullable), discount_value (decimal nullable), credit_limit (decimal default 0), payment_terms_days (int default 0), notes (text), is_active (default true), converted_at (nullable), created_at, updated_at
```

### b2b.B2BContact
```
id, company (FK B2BCompany CASCADE), name, phone, email, designation, is_primary (default false), is_active (default true), created_at, updated_at
```

### b2b.B2BActivity
```
id, company (FK B2BCompany CASCADE), contact (FK B2BContact SET_NULL nullable), admin_user (FK AdminUser CASCADE), type (call|meeting|email|whatsapp|note|stage_change|order|payment|return), subject, description (text), old_stage, new_stage, follow_up_date (nullable), is_follow_up_done (default false), created_at
```

### b2b.B2BOffer
```
id, brand (FK Brand CASCADE), company (FK B2BCompany CASCADE nullable — NULL = global), name, description (text), type (buy_x_get_y|discount_percent|flat_discount), min_quantity (nullable), free_quantity (nullable), discount_value (decimal nullable), is_stackable (default false), valid_from (date nullable), valid_to (date nullable), is_active (default true), created_at, updated_at
```

### b2b.B2BOrder
```
id (varchar 50 PK), brand (FK Brand CASCADE), company (FK B2BCompany CASCADE), contact (FK B2BContact SET_NULL nullable), status (draft|confirmed|dispatched|delivered|cancelled default draft), payment_status (pending|partial|paid default pending), subtotal (decimal 12,2), discount_type, discount_value, discount_amount, offer_discount_amount, total_amount, paid_amount, balance_amount, due_date (nullable), invoice_number, source_order (FK self SET_NULL nullable), notes (text), stock_deducted (default false), created_by (FK AdminUser), order_date, updated_at
```

### b2b.B2BOrderItem
```
id, b2b_order (FK B2BOrder CASCADE), flavor (FK Flavor), flavor_pack (FK FlavorPack SET_NULL nullable), stock (FK Stock SET_NULL nullable), quantity, weight_grams, total_weight_grams, mrp (decimal nullable), selling_price (decimal), cost_price (decimal nullable), is_free_item (default false), offer (FK B2BOffer SET_NULL nullable), flavor_name, pack_label (nullable), created_at
```

### b2b.B2BPayment
```
id, company (FK B2BCompany CASCADE), b2b_order (FK B2BOrder SET_NULL nullable), amount (decimal 12,2), payment_type (payment|refund default payment), payment_method (cash|upi|bank_transfer|cheque|credit_adjustment), reference_number, notes (text), payment_date (date), created_by (FK AdminUser), created_at
```

### finance.ExpenseCategory
```
id, name, description (text), color (default '#007bff'), is_active (default true), created_at, updated_at
```

### finance.Expense
```
id, brand (FK Brand SET_NULL nullable), category (FK ExpenseCategory), stock (FK Stock SET_NULL nullable), title, description (text), amount (decimal 10,2), expense_date, vendor_name, vendor_contact, payment_method (cash|bank_transfer|upi|cheque|card), payment_status (pending|paid|overdue), invoice_number, receipt_image, tax_amount (decimal default 0), is_recurring (default false), recurring_period (weekly|monthly|quarterly|yearly nullable), tags (text), notes (text), created_by (FK AdminUser SET_NULL), created_at, updated_at
```

### finance.ExpenseAttachment
```
id, expense (FK Expense CASCADE), filename, original_filename, file_path, file_size, mime_type, created_at
```

### finance.Investment
```
id, partner_name, amount (decimal 12,2), investment_date, description (text), payment_method, reference_number, notes (text), created_by (FK AdminUser SET_NULL), created_at, updated_at
```

### marketing.Coupon
```
id, brand (FK Brand), code (unique with brand), discount_amount (float nullable), discount_percentage (int nullable), min_order_value (float default 0), min_quantity (float default 0), valid_flavors (JSONField nullable), max_discount_amount (decimal nullable), max_uses (nullable), used_count (default 0), is_active (default true), start_date, expiry_date, created_at, updated_at
```

### marketing.Review
```
id, brand (FK Brand), user (FK User nullable), rating (int 1-5), review (text), is_approved (default false), created_at, updated_at
```

### marketing.PointsTransaction
```
id, user (FK User), points (int), type (purchase|review|feedback|referral|redemption|adjustment), status (pending|credited|cancelled default pending), reference (varchar 50 unique), transaction_date
```

### marketing.Blog
```
id, title, slug (unique), content (text), featured_image, excerpt (text), is_published (default false), published_at, created_by (FK AdminUser SET_NULL), created_at, updated_at
```

### marketing.Feedback
```
id, user (FK User nullable), feedback (text), is_reviewed (default false), created_at, updated_at
```

### marketing.MarketingSource
```
id, source_name, source_code (unique), qr_image, created_at, updated_at
```

### marketing.SourceTracking
```
id, source, referral, timestamp
```

---

## Admin Panel Pages

Every page below must be ported with IDENTICAL UI. Use Django templates extending a base layout with the same sidebar, topbar, data cards, stat cards, tables, modals, and filters.

### Auth
- `admin/index.php` → Login page (session-based, not Django's built-in admin)
- `admin/logout.php` → Destroy session, redirect to login

### Dashboard & Settings
- `admin/dashboard.php` → Main dashboard with stat cards, charts, recent orders
- `admin/settings.php` → Tabbed: Business, Loyalty, Security (change own password), Admin Users (super_admin: CRUD + reset password), System info
- `admin/brands.php` → Multi-brand management

### Orders (B2C)
- `admin/orders.php` → Orders list with filters, create order modal (flavor + pack selection), bulk status update
- `admin/view-order.php` → Order detail view
- `admin/edit-order.php` → Edit order: change items (add with pack selection), customer info, status, coupon
- `admin/print-invoice.php` → Printable invoice (opens in new window)

### Products
- `admin/flavors.php` → CRUD flavors + manage packs per flavor
- `admin/stocks.php` → Stock batches, movements, alerts, restock

### Customers
- `admin/customers.php` → Customer list with search
- `admin/view-customer.php` → Customer detail: orders, loyalty points
- `admin/edit-customer.php` → Edit customer info

### B2B CRM
- `admin/b2b-dashboard.php` → Stats (companies, active orders, outstanding, overdue, advance credit), pipeline bar, follow-ups, overdue payments, active orders, top companies
- `admin/b2b.php` → Pipeline kanban (lead/contacted/negotiation/converted/lost), company CRUD, categories
- `admin/view-b2b.php` → Company detail: info card, contacts, timeline (activities), orders tab, payments tab with balance summary (outstanding/overdue/advance/credit limit)
- `admin/b2b-orders.php` → B2B orders list with view modal, confirm (deduct stock), cancel (revert stock), delete (revert stock), payment recording
- `admin/b2b-order-create.php` → Create/edit B2B order: company + contact selection, flavor + batch + pack selection, custom weight, offers (buy X get Y), discount, due date
- `admin/b2b-offers.php` → CRUD B2B offers/schemes
- `admin/print-b2b-invoice.php` → B2B invoice

### Finance
- `admin/expenses.php` → Expenses list, categories, investments

### Marketing
- `admin/coupons.php` → CRUD coupons
- `admin/reviews.php` → Reviews list with detail modal, approve/reject, bulk approve
- `admin/blogs.php` → Blog management

### System
- `admin/migrations.php` → Database migrations runner (keep this working in Django too — or replace with Django's migrate)

---

## Admin API Endpoints

All APIs return JSON `{success: bool, message: string, data?: any}`. Use DRF ViewSets or function-based views.

| PHP File | Permission | Methods |
|----------|-----------|---------|
| `api/dashboard.php` | dashboard | GET: brand stats, order counts, revenue, recent orders |
| `api/orders.php` | orders | GET: list orders, order_form_data (flavors+packs). POST: update_status, update_payment_status, update, add_item, remove_item, bulk_update |
| `api/create_order.php` | orders | POST: create B2C order with pack support |
| `api/customers.php` | customers | GET: list/search. POST: update, delete |
| `api/flavors.php` | flavors | GET: list (with packs). POST: create, update, delete, add_pack, update_pack, delete_pack |
| `api/stocks.php` | stocks | GET: list with movements. POST: restock, adjust, acknowledge_alert |
| `api/coupons.php` | coupons | GET: list. POST: create, update, delete |
| `api/reviews.php` | reviews | GET: list with stats. POST: approve, reject, delete, bulk_approve |
| `api/expenses.php` | expenses | GET: list with stats. POST: create, update, delete |
| `api/settings.php` | settings | GET: settings + admins + brands. POST: update_settings, change_password, create_admin, update_admin, reset_admin_password, delete_admin |
| `api/b2b.php` | b2b | GET: dashboard, companies, categories, admins, company detail, activities. POST: add/edit company, change_stage, add/edit/delete contact, add activity, complete_follow_up, add/delete category |
| `api/b2b-orders.php` | b2b | GET: orders list, order detail, company_balance, order_form_data, offers. POST: create_order, confirm_order (deduct stock), update_status (revert stock on cancel), add_payment, delete_order (revert stock), update_order, repeat_order |
| `api/b2b-offers.php` | b2b | GET: list. POST: create, update, delete |
| `api/brands.php` | dashboard | GET: list. POST: create, update |
| `api/migrations.php` | (self-auth) | GET: status. POST: run_next, run_all, mark_applied |

---

## Storefront Pages

Public-facing pages (customer-facing website):

- `index.php` → Landing page
- `shop.php` → Product catalog (flavors with prices)
- `dashboard.php` → Customer dashboard (after OTP login): orders, profile, loyalty points
- `blog.php` / `blog-detail.php` → Blog listing and detail
- `policy.php` → Privacy/terms

### Storefront APIs
- `api/send_otp.php` → Send OTP to mobile
- `api/verify_otp.php` → Verify OTP, create/login user
- `api/get_flavors.php` → Public flavor listing
- `api/get_reviews.php` → Approved reviews
- `api/place_order.php` → Create order (with Razorpay)
- `api/verify_payment.php` → Verify Razorpay payment
- `api/razorpay_webhook.php` → Razorpay webhook handler
- `api/cancel_order.php` → Cancel order
- `api/track_order.php` → Order tracking
- `api/validate_coupon.php` → Validate coupon code
- `api/submit_review.php` → Submit review (earns points)
- `api/get_user_data.php` → User profile + orders + points
- `api/get_user_by_mobile.php` → Lookup user by mobile
- `api/update_profile.php` → Update user profile
- `api/get_dashboard_data.php` → Customer dashboard data
- `api/get_blogs.php` / `api/blogs.php` → Blog APIs
- `api/stocks.php` → Public stock availability
- `api/track_source.php` → Marketing source tracking

---

## UI Design System

### Brand
- Primary green: `#85AA4E`
- Yellow accent: `#FCE103`
- Font: Plus Jakarta Sans (Google Fonts)

### Layout
- Dark sidebar (`#111827`) with nav items, section labels, brand switcher
- Content area: `#f4f6f9` background
- Cards: white, `border-radius: 14px`, subtle shadow
- Tables: clean with hover, status badges as colored pills
- Modals: Bootstrap 5 modals for create/edit/view

### Components (port from dash-style.css)
- `.stat-card` — icon + value + label
- `.data-card` — card with `.data-card-header` (title + action button)
- `.filter-bar` — row of filter inputs above tables
- `.table-actions` — icon buttons (view/edit/delete) in table rows
- `.btn-icon` — small circular icon buttons (`.view`, `.edit`, `.delete` variants)
- `.nav-item` — sidebar navigation items with active state
- Status badges: colored pills for order status, payment status, pipeline stages

### JS Patterns (port exactly)
- `apiGet(url, params)` / `apiPost(url, data)` — jQuery AJAX helpers returning JSON
- `showAlertModal(message, type)` — Bootstrap modal for success/error feedback
- `showLoader(text)` / `hideLoader()` — Full-screen loading overlay
- `confirmThen(message, callback)` — Confirmation dialog
- `formatCurrency(n)` — Indian rupee formatting with `Intl.NumberFormat`
- `formatDate(d)` / `formatDateTime(d)` — Date formatting
- `escHtml(s)` — HTML entity escaping

---

## Key Business Logic

### Stock Management
- FIFO batch system: each flavor has multiple stock batches, one marked `is_active_batch`
- Stock deducted on B2C ship/deliver, on B2B confirm
- Stock reverted on B2B cancel/delete
- Auto-activate next batch when current empties
- Stock movements tracked for audit trail
- Alerts generated for low stock, out of stock, expiring

### B2B Outstanding/Overdue
- **Outstanding** = `SUM(total_amount - paid_amount)` where positive, status != cancelled
- **Overdue** = outstanding where `due_date IS NOT NULL AND due_date < today`
- **Advance credit** = `ABS(SUM(total_amount - paid_amount))` where negative (overpayments)
- Always calculate from `total_amount - paid_amount`, never use stored `balance_amount`

### Loyalty Points
- Points earned on purchase (% of order value), review submission, referrals, signup
- Points status: pending → credited (when order paid) or cancelled
- Synced when payment status changes

### Permissions
- `"all"` in permissions array = wildcard (access everything)
- `brand_permissions` JSON keyed by brand ID, values are permission arrays
- NULL `brand_permissions` on non-super_admin = no access (login denied)
- super_admin bypasses all checks
- On brand switch, session permissions update to that brand's array

### Order ID Format
- Generated with `uniqid(PREFIX . '_')` — e.g. `VO_669f1ff549de53`
- PREFIX comes from `brands.order_prefix`

### Pack Pricing (B2C)
- When a pack is selected, `price_per_kg = (pack.mrp / pack.weight_grams) * 1000`
- `sale_price_per_kg = (pack.selling_price / pack.weight_grams) * 1000`
- Stored in order_items for consistent per-kg calculations

---

## Migration Priorities

Build in this order:
1. **Models + migrations** — All tables above
2. **Auth** — Admin login (session), permission checks, brand context
3. **Admin layout** — Sidebar, topbar, base template with all CSS/JS
4. **Dashboard** — Stat cards, recent orders
5. **Flavors + Packs** — CRUD with pack management
6. **Stock** — Batches, movements, alerts
7. **Orders (B2C)** — List, create (with packs), view, edit, print invoice
8. **Customers** — List, view, edit
9. **B2B Pipeline** — Companies, contacts, activities, kanban
10. **B2B Orders** — Create with batch+pack selection, confirm (stock), cancel (revert), payments
11. **B2B Dashboard** — Stats, follow-ups, overdue, top companies
12. **Finance** — Expenses, investments
13. **Marketing** — Coupons, reviews, blogs
14. **Settings** — Business config, admin users, password management
15. **Storefront** — Public pages + OTP auth + Razorpay checkout
16. **Brands** — Multi-brand management, brand switcher

---

## Important Notes

- Port the PHP HTML templates to Django templates EXACTLY — same classes, same structure, same IDs
- Port all `js/admin/*.js` files as-is — they use jQuery + Bootstrap 5 and talk to APIs via `apiGet`/`apiPost`
- The API response format must stay `{success: bool, message: string, ...}` — the JS expects this
- Use Django's `{% csrf_token %}` properly — the jQuery AJAX helpers need the CSRF cookie
- Keep URLs similar: `/admin/api/orders/` instead of `/admin/api/orders.php`
- The `js/admin/utils.js` has all shared helpers — port the API base URL to match Django URLs
