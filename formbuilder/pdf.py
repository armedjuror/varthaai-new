"""Printable QR sheet: one QR per link, labelled with its key value."""
import segno

from billing.pdf import render_html_pdf

PER_PAGE_CHOICES = (1, 2, 4, 6, 12)


def qr_svg(url):
    # Medium error correction keeps a QR scannable when printed small or slightly scuffed.
    return segno.make(url, error='m').svg_inline(scale=6, border=2, dark='#000', light='#fff')


def qr_sheet_pdf(form, general_url, links, per_page=6, include_general=False):
    items = []
    if include_general:
        items.append({'label': 'General link', 'svg': qr_svg(general_url)})
    for row in links:
        items.append({'label': row['value'], 'svg': qr_svg(row['url'])})
    per_page = per_page if per_page in PER_PAGE_CHOICES else 6
    pages = [items[i:i + per_page] for i in range(0, len(items), per_page)]
    return render_html_pdf('formbuilder/qr_sheet.html', {
        'form': form, 'pages': pages, 'per_page': per_page})
