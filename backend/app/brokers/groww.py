"""Groww adapter: documented skeleton (raises NotImplementedError).

API: Groww Trade API
Docs: https://groww.in/trade-api/docs
Implementation notes:
- Auth: API key + secret -> daily access token (TOTP flow supported by the SDK).
- Orders: GrowwAPI.place_order(trading_symbol, quantity, validity, exchange, segment, product, order_type, transaction_type, order_reference_id)
- Use order_reference_id for the strategy tag.
- Historical: get_historical_candle_data(trading_symbol, exchange, segment, start_time, end_time, interval_in_minutes).
"""
from __future__ import annotations

from ._skeleton import SkeletonAdapter
from .base import CredentialField


class GrowwAdapter(SkeletonAdapter):
    key = "groww"
    display_name = "Groww"
    api_name = "Groww Trade API"
    docs_url = "https://groww.in/trade-api/docs"
    sdk_hint = "pip install growwapi (official Python SDK)"
    credential_fields = [
        CredentialField("api_key", "API key", secret=True),
        CredentialField("api_secret", "API secret", secret=True),
        CredentialField("totp_secret", "TOTP secret", secret=True, required=False),
    ]
