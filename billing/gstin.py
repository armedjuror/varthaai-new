"""GSTIN format + checksum validation (15 chars: SS PPPPPPPPPP E Z C)."""
import re

from billing.gst_states import STATE_CODES

GSTIN_RE = re.compile(r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$')
_CHARSET = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'


def normalise_gstin(value):
    return re.sub(r'\s+', '', str(value or '')).upper()


def gstin_checksum(first14):
    total = 0
    for i, ch in enumerate(first14):
        value = _CHARSET.index(ch) * (2 if i % 2 else 1)
        total += value // 36 + value % 36
    return _CHARSET[(36 - total % 36) % 36]


def validate_gstin(value):
    """Return (normalised_gstin, error_message). Empty input is allowed."""
    gstin = normalise_gstin(value)
    if not gstin:
        return '', None
    if not GSTIN_RE.match(gstin):
        return gstin, 'GSTIN must be 15 characters, e.g. 29ABCDE1234F1Z5.'
    if gstin[:2] not in STATE_CODES:
        return gstin, f'GSTIN starts with an unknown state code ({gstin[:2]}).'
    if gstin_checksum(gstin[:14]) != gstin[14]:
        return gstin, 'GSTIN check digit does not match — please re-check the number.'
    return gstin, None


def state_code_from_gstin(gstin):
    gstin = normalise_gstin(gstin)
    return gstin[:2] if len(gstin) == 15 else ''
