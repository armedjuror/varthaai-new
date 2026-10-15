"""Fill B2BCompany.latitude/longitude for leads that only have a pasted
Google Maps link.

    python manage.py backfill_company_coordinates [--dry-run] [--limit N]

For each company with a location_url and no coordinates (and not set by
hand), follow the link's redirects and read the coordinates from the URL;
if that fails, ask Nominatim using the address, city and pincode. Only
points inside Bengaluru are accepted. Sets geo_status to resolved or failed.
Safe to re-run: resolved and manual companies are never touched, and failed
ones are tried again.
"""
from django.core.management.base import BaseCommand

from crm.geocoding import Geocoder
from crm.models import B2BCompany


class Command(BaseCommand):
    help = 'Backfill B2B company coordinates from their Google Maps links (Nominatim as fallback).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Look up coordinates but save nothing.')
        parser.add_argument('--limit', type=int, default=None, help='Process at most N companies.')

    def handle(self, *args, dry_run=False, limit=None, **options):
        candidates = (
            B2BCompany.objects
            .filter(latitude__isnull=True)
            .exclude(geo_status=B2BCompany.GeoStatus.MANUAL)
            .order_by('id')
        )
        with_link = candidates.exclude(location_url='')
        skipped = candidates.filter(location_url='').count()
        if limit is not None:
            with_link = with_link[:limit]

        geocoder = Geocoder()
        resolved, failed = [], []
        for company in with_link:
            source = 'link'
            coords = geocoder.from_url(company.location_url)
            if not coords:
                source = 'address'
                coords = geocoder.from_address(company)

            if coords:
                resolved.append(company.id)
                self.stdout.write(f'  #{company.id} {company.company_name}: {coords[0]},{coords[1]} (from {source})')
                if not dry_run:
                    company.latitude, company.longitude = coords
                    company.geo_status = B2BCompany.GeoStatus.RESOLVED
                    company.save(update_fields=['latitude', 'longitude', 'geo_status', 'updated_at'])
            else:
                failed.append(company.id)
                self.stdout.write(f'  #{company.id} {company.company_name}: not found')
                if not dry_run:
                    company.geo_status = B2BCompany.GeoStatus.FAILED
                    company.save(update_fields=['geo_status', 'updated_at'])

        prefix = '[dry run] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}Resolved: {len(resolved)} | Failed: {len(failed)} | Skipped (no link): {skipped}',
        ))
        if failed:
            self.stdout.write(f'{prefix}Failed company IDs: {", ".join(str(i) for i in failed)}')
