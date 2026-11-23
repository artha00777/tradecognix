"""Razorpay checkout stub: create an order, verify the payment signature, verify webhooks.

Uses Razorpay's REST API directly (no SDK). Needs RAZORPAY_KEY_ID + RAZORPAY_KEY_SECRET; when they
are absent `configured()` is False and the billing API answers 503.
Docs: https://razorpay.com/docs/payments/server-integration/python/
Signature rules: payment = HMAC_SHA256(order_id + "|" + payment_id, key_secret);
webhook = HMAC_SHA256(raw_body, webhook_secret).
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any

import httpx

from ..config import get_settings

API = "https://api.razorpay.com/v1"


class RazorpayError(RuntimeError):
    pass


def configured() -> bool:
    s = get_settings()
    return bool(s.razorpay_key_id and s.razorpay_key_secret)


def _hmac_hex(secret: str, msg: bytes) -> str:
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def create_order(amount_paise: int, receipt: str, notes: dict[str, Any] | None = None, client: httpx.Client | None = None) -> dict:
    s = get_settings()
    if not configured():
        raise RazorpayError("Razorpay is not configured")
    body = {"amount": int(amount_paise), "currency": "INR", "receipt": receipt[:40], "notes": notes or {}}
    http = client or httpx.Client(timeout=15)
    try:
        r = http.post(f"{API}/orders", json=body, auth=(s.razorpay_key_id, s.razorpay_key_secret))
    except httpx.HTTPError as e:
        raise RazorpayError(f"Razorpay unreachable: {type(e).__name__}") from e
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("description", "")
        except ValueError:
            msg = ""
        raise RazorpayError(f"Razorpay order failed (HTTP {r.status_code}) {msg}".strip())
    return r.json()


def verify_payment_signature(order_id: str, payment_id: str, signature: str, secret: str | None = None) -> bool:
    secret = secret if secret is not None else get_settings().razorpay_key_secret
    if not (secret and order_id and payment_id and signature):
        return False
    return hmac.compare_digest(_hmac_hex(secret, f"{order_id}|{payment_id}".encode()), signature)


def verify_webhook_signature(raw_body: bytes, signature: str, secret: str | None = None) -> bool:
    secret = secret if secret is not None else get_settings().razorpay_webhook_secret
    if not (secret and signature):
        return False
    return hmac.compare_digest(_hmac_hex(secret, raw_body), signature)
