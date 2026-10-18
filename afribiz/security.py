"""Signed links (dashboard, invoice files, lender view) and webhook signature checks."""

from __future__ import annotations

import base64
import hashlib
import hmac


def sign(secret: str, purpose: str, value: str | int, length: int = 24) -> str:
    digest = hmac.new(secret.encode(), f"{purpose}:{value}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")[:length]


def verify(secret: str, purpose: str, value: str | int, token: str, length: int = 24) -> bool:
    return hmac.compare_digest(sign(secret, purpose, value, length), token or "")


def verify_meta_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """WhatsApp Cloud API: X-Hub-Signature-256: sha256=<hex hmac of raw body with the app secret>."""
    if not app_secret:
        return True  # not configured (development); production deployments must set WHATSAPP_APP_SECRET
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


def verify_twilio_signature(auth_token: str, url: str, params: dict[str, str], header: str | None) -> bool:
    """Twilio: base64(HMAC-SHA1(url + concatenated sorted key/value pairs))."""
    if not auth_token:
        return True
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    expected = base64.b64encode(hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(expected, header or "")
