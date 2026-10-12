# GST billing (billing app) — go-live and operations

Phase 1 of the GST invoicing plan: numbered GST tax invoices at dispatch,
credit notes for returns / corrections, Order Summary pages, GST records for
the CA.

## Server prerequisites

WeasyPrint renders the stored PDFs and needs Pango:

```bash
sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 fonts-dejavu-core
./venv/bin/pip install -r requirements.txt
./venv/bin/pip install --no-deps -r requirements-nodeps.txt
./venv/bin/python -c "from weasyprint import HTML; HTML(string='<p>ok ₹</p>').write_pdf()"
```

If WeasyPrint cannot be installed, invoices are still issued; the PDF is
generated on first download instead (and the error is logged).

**Private PDFs.** Invoice and credit-note PDFs are stored under
`PRIVATE_MEDIA_ROOT` (default `<project>/private_media/`), which nginx does
**not** serve — they are only streamed by permission-checked views. Do not
add a nginx `location` for it. Include it in backups: records are kept for
6 years.

## Go-live checklist

1. Deploy, then `./venv/bin/python manage.py migrate`. The migration seeds the
   legal entity "Varthaai Foods LLP" (Kerala, 32), links both brands to it, and
   adds the settings `gst_effective_date = 2026-10-01`,
   `gst_price_mode_b2b = exclusive`, `gst_price_mode_b2c = inclusive`.
   Nothing changes for users yet.
2. **Settings → GST & Invoicing** (`/admin/gst/settings/`):
   - Add the HSN code(s) and the GST rate effective from 1 Oct 2026 (from the CA).
   - Map every flavor to its HSN.
   - Fill in the business details from the GST certificate (address, FSSAI,
     bank, jurisdiction).
3. Fill in the delivery **state** on B2C orders dispatched since 1 Oct and the
   **GSTIN / state** of B2B companies you invoice (B2B pipeline → company).
4. **Save the GSTIN last.** Saving the business GSTIN switches GST billing on:
   from then on every dispatch (B2C *shipped/delivered*, B2B
   *dispatched/delivered*) issues an invoice, or is blocked with a message
   saying what is missing (HSN, rate, state, address).
5. Backfill October, before anyone dispatches again:
   ```bash
   ./venv/bin/python manage.py backfill_invoices --from 2026-10-01 --dry-run
   # correct any dispatch date the dry run got wrong:
   ./venv/bin/python manage.py backfill_invoices --from 2026-10-01 --date VOB_xxx=2026-10-04
   ```
   Backfilled invoices are inclusive (GST carved out of what the customer
   already paid). `--exclusive ORDER_ID` puts GST on top for an unpaid B2B
   order the buyer agreed to.
6. Create the CA login: Settings → Admin Users → permission **GST Records**
   only. The CA lands on `/admin/gst/records/` and can open nothing else.

## How it behaves

- **Numbers**: `26-27/0000001` invoices, `CN/26-27/0001` credit notes, one
  series per financial year for the GSTIN (both brands). Allocated under a
  row lock inside the issuing transaction — no gaps, no duplicates. The
  GST Records page flags any gap.
- **Dates** use the IST calendar date (the server runs in UTC).
- **Tax type** follows the place of supply: buyer GSTIN's state for
  registered B2B buyers, otherwise the delivery state. Kerala → CGST+SGST,
  elsewhere → IGST. A B2B-module order for a company without GSTIN gets a
  B2C-type invoice.
- **Price mode** is copied onto each order when it is created. B2B
  exclusive orders carry the GST in `total_amount`; B2C stays inclusive in
  Phase 1 (the settings page refuses exclusive for B2C until the storefront
  checkout supports it).
- **Freeze**: once invoiced, items, prices, discount, coupon, delivery
  charge and addresses are locked, and the order can only be
  dispatched/delivered (B2B) or shipped/delivered (B2C). Corrections go
  through **Return / credit note** or **Cancel & reissue**.
- **Returns (B2B)**: on an invoiced order the return records goods and stock
  immediately (restock is per line) and the screen offers **Generate credit
  note**. Skipping leaves it *credit note pending* (badge on the order, card
  on the B2B dashboard, tab on GST Records); the bill is adjusted when the
  credit note is generated.
- **Returns / cancellation (B2C)**: order page → GST Invoice → Return /
  Credit note. Crediting everything cancels the order (paid orders move to
  *refund initiated*).
- **Order Summary** (renamed old invoice pages, admin + storefront) shows the
  GST, the tax invoice number, credit notes and a PAID seal. It says "This
  is not a tax invoice".
- Customers download the Order Summary PDF and, once the order is shipped
  and paid, the stored Tax Invoice PDF (and credit notes) from their dashboard.
- If GST billing is off for an order (no GSTIN saved yet, before the start
  date, or not in the pilot list), dispatching says so ("No GST invoice: …")
  and the order screen shows the reason.

## Trying it on the live site (pilot mode)

1. Settings → GST & Invoicing → **Pilot mode**: enter your test company and
   your own number, e.g. `company:123, mobile:9876543210` (company id is in the
   company page URL `/admin/b2b/123/`). **Save this before the GSTIN.**
2. Fill HSN, rates, flavor mapping, business details, then save the GSTIN.
   GST billing now applies only to the pilot company / mobile; every other
   order behaves exactly as before (no GST added, no invoice on dispatch).
3. Test: B2B order → dispatch → invoice → return → credit note → WhatsApp →
   pay from the link; B2C order from your number → ship → refund.
4. Clean up (dry run first, then `--yes`):
   ```bash
   ./venv/bin/python manage.py gst_test_cleanup --order VOB_xxx --order VO_yyy
   ./venv/bin/python manage.py gst_test_cleanup --order VOB_xxx --order VO_yyy --yes --delete-orders --restock
   ```
   It deletes those orders' invoices, credit notes, refunds records, links,
   payment attempts, message logs and PDFs, and resets each series to the last
   real number (0 → next invoice is `/0000001`). It refuses if any real
   invoice / credit note was issued after the test ones, or if a document is
   in a closed month. It never refunds money: refund Razorpay test payments in
   the Razorpay dashboard. Delete the test company from the pipeline yourself.
5. When ready for everyone: clear the pilot field, then run the October
   backfill (above) before anyone dispatches.

## Phase 2

**Order links** (`/o/<token>/`). Every order (B2C and B2B) can be shared as an
open link: Order Summary, PAID / PART PAID seal or a **Pay** button for the
balance, and the Tax Invoice / credit-note PDFs. Customers (link and
storefront dashboard) can download the Tax Invoice and credit notes only once
the order is paid; before that the link shows the invoice number with
"available once paid". Admins can always print / download from dispatch. The page shows the name,
items, amounts and delivery state/pincode only; the phone is masked. Links
are `noindex`, `no-referrer`, `no-store`, rate-limited per IP, and expire 60
days after the order is fully paid. **New link** on the order revokes the
old one. `SITE_URL` (env) is the host used when no request is available.

**Pay from the link.** A Razorpay order for the current balance (credit notes
reduce it first). The browser return and the existing webhook
(`/api/razorpay-webhook/`: `payment.captured`, `order.paid`,
`payment.failed`) both settle it, idempotent on the payment id. B2B records
a payment with method *Online*. Also subscribe the webhook to
`refund.processed` and `refund.failed`.

**WhatsApp.** *Send on WhatsApp* / *Send reminder* (B2C order page, B2B order
modal, B2B dashboard overdue list) open `wa.me` with the message and the
order link ready; the sender still taps Send. Texts are editable in
Settings → GST & Invoicing (placeholders `{name} {order_id} {amount}
{due_date} {link} {brand}`). Each click is logged (message log, B2B timeline,
activity log); the overdue list shows *last reminded*.

**B2C GST on top.** Settings → GST lets B2C switch to *exclusive* only when
every active flavor (per-kg web price) and pack satisfies
`selling price × (1 + rate) ≤ MRP`. While exclusive is on, flavor/pack edits
that break it are refused and checkout blocks a violating item. The cart
shows a GST line, the Razorpay amount includes it, and the GST is pinned on
the order. Checkout also takes an optional business **GSTIN** (B2B-type
invoice, place of supply = GSTIN state).

**Refunds (B2C).** From the credit note dialog or the order's *Refund*
button: through Razorpay (on the original payment) or recorded as manual.
`payment_status` is *refund initiated* while a Razorpay refund is pending
(or a fully credited order still owes money back) and *refunded* when
everything paid has gone back. Customers see refunds on their dashboard.

**Reports (GST Records → Reports).** GSTR-1 data as CSVs in the offline
tool's layout (B2B, B2CL, B2CS, CDNR, CDNUR, HSN split B2B/B2C, DOCS), sales
register, credit-note register and HSN summary. **Month close** (admins with
Settings access; reopening needs a super admin) blocks any invoice or credit
note dated in a closed month. The page also shows output GST vs input credit
for the period and turnover against the ₹5 crore e-invoicing limit (also on
the main dashboard for GST/Settings users).

**Accounting.** `Income.amount` is the gross received — for B2C now goods −
coupon + delivery (+ GST on top) − processed refunds — and
`Income.gst_amount` the GST inside it. Revenue/profit in Finance, the main
dashboard and the analytics MCP are net of GST. After deploying, run
`./venv/bin/python manage.py backfill_income` once to refresh existing rows.
Expenses take the supplier GSTIN, invoice date and CGST/SGST/IGST; eligible
input tax credit is not counted as a cost.

**Database guard.** A PostgreSQL trigger refuses UPDATE/DELETE on issued
invoices, credit notes and their lines, except the status / PDF / e-way bill
/ IRN columns.

Not built: automatic WhatsApp sending and scheduled reminders (need a
WhatsApp Business Platform account), e-invoicing (IRN) and e-way bill
generation, background ZIP export for very large periods.
