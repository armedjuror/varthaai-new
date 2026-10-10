# GST billing (billing app) — go-live and operations

Phase 1 of the GST invoicing plan: numbered GST tax invoices at dispatch,
credit notes for returns / corrections, Order Summary pages, GST records for
the CA.

## Server prerequisites

WeasyPrint renders the stored PDFs and needs Pango:

```bash
sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 fonts-dejavu-core
./venv/bin/pip install -r requirements.txt
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
- Customers download the Order Summary PDF and, once shipped, the stored Tax
  Invoice PDF (and credit notes) from their dashboard.

## Not in Phase 1

Share links / pay-from-link, WhatsApp buttons and message log, B2C GST-on-top
at checkout with the MRP guard, Razorpay refunds, CA CSV registers beyond the
ZIP export, finance net-of-GST income, ITC, e-invoicing.
