"""ICICI Direct adapter: documented skeleton (raises NotImplementedError).

API: Breeze API
Docs: https://api.icicidirect.com/breezeapi/documents/index.html
Implementation notes:
- Auth: BreezeConnect(api_key).generate_session(api_secret, session_token) where session_token comes from the login redirect.
- Orders: breeze.place_order(stock_code, exchange_code='NFO', product='options', action, order_type, quantity, price, validity, expiry_date, right, strike_price, user_remark=tag)
- Historical: get_historical_data_v2(interval='5minute', from_date, to_date, stock_code, exchange_code, product_type).
"""
from __future__ import annotations

from ._skeleton import SkeletonAdapter
from .base import CredentialField


class IciciAdapter(SkeletonAdapter):
    key = "icici"
    display_name = "ICICI Direct"
    api_name = "Breeze API"
    docs_url = "https://api.icicidirect.com/breezeapi/documents/index.html"
    sdk_hint = "pip install breeze-connect"
    credential_fields = [
        CredentialField("api_key", "API key", secret=True),
        CredentialField("api_secret", "API secret", secret=True),
        CredentialField("session_token", "Session token", secret=True),
    ]
