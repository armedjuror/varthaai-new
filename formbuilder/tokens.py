"""
Opaque prefill tokens for form links.

A link looks like /f/<public_id>/?p=<token>. The token is the AES-SIV
encryption of a small JSON object {field_slug: value}, bound to the form's
public id as associated data, so:

  - nothing about the field or its value is readable in the URL or the QR;
  - any change to the token, or reuse on another form, fails to decrypt;
  - encryption is deterministic, so the same (form, field, value) always gives
    the same URL - a reprinted QR code matches the one already on a shelf.

The key is derived from FORM_LINK_SECRET (falls back to SECRET_KEY). Rotating
it invalidates every issued link.
"""
import base64
import json

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

QUERY_PARAM = 'p'
MAX_TOKEN_LENGTH = 600


def _key():
    secret = getattr(settings, 'FORM_LINK_SECRET', '') or settings.SECRET_KEY
    return HKDF(
        algorithm=hashes.SHA256(), length=64, salt=None, info=b'formbuilder-link-v1',
    ).derive(secret.encode())


def make_token(public_id, prefill):
    """prefill: {field_slug: value} -> url-safe token."""
    payload = json.dumps(prefill, separators=(',', ':'), sort_keys=True, ensure_ascii=False).encode()
    sealed = AESSIV(_key()).encrypt(payload, [public_id.encode()])
    return base64.urlsafe_b64encode(sealed).rstrip(b'=').decode()


def read_token(public_id, token):
    """Return the prefill dict, or None for anything missing, malformed or tampered."""
    if not token or len(token) > MAX_TOKEN_LENGTH:
        return None
    try:
        raw = base64.urlsafe_b64decode(token + '=' * (-len(token) % 4))
        data = json.loads(AESSIV(_key()).decrypt(raw, [public_id.encode()]))
    except Exception:  # bad base64, bad tag, bad JSON: all the same to the caller
        return None
    if not isinstance(data, dict):
        return None
    return {str(k): v for k, v in data.items()}


def form_url(form, base_url, prefill=None):
    url = f"{base_url.rstrip('/')}/f/{form.public_id}/"
    if prefill:
        url += f'?{QUERY_PARAM}={make_token(form.public_id, prefill)}'
    return url
