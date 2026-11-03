"""Turn a lead's pasted Google Maps link (or, failing that, its address) into
coordinates. Used by the backfill_company_coordinates command.

Network calls go through `requests.get` with a throttle and retry/backoff so
the command stays polite to Google and to Nominatim (whose usage policy is
max 1 request/second with an identifying User-Agent).
"""
import re
import time
from decimal import Decimal
from urllib.parse import unquote

import requests

# Coordinates outside this box are rejected — every lead is in Bengaluru, so
# anything else is a wrong match (a city centroid, a head office, a typo).
BLR_LAT = (Decimal('12.7'), Decimal('13.3'))
BLR_LNG = (Decimal('77.3'), Decimal('77.9'))

BROWSER_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
)
NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search'
NOMINATIM_UA = 'VarthaaiAdmin/1.0 (+https://varthaai.com) lead-location backfill'

_NUM = r'(-?\d{1,3}\.\d+)'
# Tried in order: the place pin (!3d/!4d) is the business itself; @lat,lng
# is only the map's viewport centre; q/ll/query are explicit search points.
_PATTERNS = [
    re.compile(r'!3d' + _NUM + r'!4d' + _NUM),
    re.compile(r'@' + _NUM + r',' + _NUM),
    re.compile(r'[?&](?:q|ll|query)=' + _NUM + r'(?:,|%2C)\s*\+?' + _NUM, re.I),
]


def _sleep(seconds):
    time.sleep(seconds)  # looked up at call time, so tests can patch time.sleep


def in_bengaluru(lat, lng):
    return BLR_LAT[0] < lat < BLR_LAT[1] and BLR_LNG[0] < lng < BLR_LNG[1]


def _q(value):
    return Decimal(value).quantize(Decimal('0.000001'))


def parse_coords(url):
    """(lat, lng) as Decimals from a Google Maps URL, or None. Only
    coordinates inside Bengaluru count. The URL is also tried percent-decoded,
    which catches links wrapped in consent.google.com?continue=…"""
    if not url:
        return None
    candidates = [url]
    decoded = unquote(url)
    if decoded != url:
        candidates.append(decoded)
    for pattern in _PATTERNS:
        for text in candidates:
            for m in pattern.finditer(text):
                lat, lng = _q(m.group(1)), _q(m.group(2))
                if in_bengaluru(lat, lng):
                    return lat, lng
    return None


class Throttle:
    """Wait so consecutive calls are at least `interval` seconds apart."""

    def __init__(self, interval, sleep=_sleep, clock=time.monotonic):
        self.interval, self.sleep, self.clock = interval, sleep, clock
        self._last = None

    def wait(self):
        if self._last is not None:
            gap = self.interval - (self.clock() - self._last)
            if gap > 0:
                self.sleep(gap)
        self._last = self.clock()


def get_with_retry(url, throttle, *, params=None, headers=None, attempts=3, backoff=2.0,
                   timeout=15, sleep=_sleep):
    """requests.get behind the throttle, retrying network errors, 429 and
    5xx with exponential backoff (2s, 4s, …). Returns the response or None."""
    for attempt in range(attempts):
        throttle.wait()
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout, allow_redirects=True)
        except requests.RequestException:
            resp = None
        if resp is not None and resp.status_code != 429 and resp.status_code < 500:
            return resp
        if attempt < attempts - 1:
            sleep(backoff * (2 ** attempt))
    return None


class Geocoder:
    def __init__(self, sleep=_sleep):
        self.sleep = sleep
        self.google_throttle = Throttle(2.0, sleep=sleep)
        self.nominatim_throttle = Throttle(1.0, sleep=sleep)

    def from_url(self, url):
        """Coordinates from a Maps link: parse it as-is first (full links need
        no request), else follow the short-link redirects and parse every URL
        along the way. The page body is not parsed: it holds many unrelated
        coordinates and a wrong pin is worse than none."""
        coords = parse_coords(url)
        if coords:
            return coords
        resp = get_with_retry(
            url, self.google_throttle, headers={'User-Agent': BROWSER_UA, 'Accept-Language': 'en'},
            sleep=self.sleep,
        )
        if resp is None:
            return None
        for hop in [*(r.url for r in getattr(resp, 'history', []) or []), resp.url]:
            coords = parse_coords(hop)
            if coords:
                return coords
        for r in getattr(resp, 'history', []) or []:
            location = (getattr(r, 'headers', None) or {}).get('Location')
            coords = parse_coords(location)
            if coords:
                return coords
        return None

    def from_address(self, company):
        """Nominatim search on address, city and pincode. Needs a street
        address or a pincode — a bare city would only give its centre."""
        if not (company.address.strip() or company.pincode.strip()):
            return None
        query = ', '.join(p for p in [
            company.address.strip(), company.city.strip() or 'Bengaluru', company.pincode.strip(), 'India',
        ] if p)
        resp = get_with_retry(
            NOMINATIM_URL, self.nominatim_throttle,
            params={
                'q': query, 'format': 'jsonv2', 'limit': 1, 'countrycodes': 'in',
                'viewbox': '77.3,13.3,77.9,12.7', 'bounded': 1,
            },
            headers={'User-Agent': NOMINATIM_UA}, sleep=self.sleep,
        )
        if resp is None or resp.status_code != 200:
            return None
        try:
            results = resp.json()
        except ValueError:
            return None
        if not results:
            return None
        lat, lng = _q(results[0]['lat']), _q(results[0]['lon'])
        return (lat, lng) if in_bengaluru(lat, lng) else None
