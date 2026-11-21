"""Alice Blue adapter: documented skeleton (raises NotImplementedError).

API: ANT API
Docs: https://v2api.aliceblueonline.com/
Implementation notes:
- Auth: Aliceblue(user_id, api_key).get_session_id() (vendor login flow).
- Orders: alice.place_order(transaction_type=TransactionType.Buy, instrument=alice.get_instrument_by_symbol('NFO', sym), quantity, order_type=OrderType.Market, product_type=ProductType.Intraday, order_tag=tag)
- Historical: alice.get_historical(instrument, from_datetime, to_datetime, interval='1', indices=True).
"""
from __future__ import annotations

from ._skeleton import SkeletonAdapter
from .base import CredentialField


class AliceBlueAdapter(SkeletonAdapter):
    key = "aliceblue"
    display_name = "Alice Blue"
    api_name = "ANT API"
    docs_url = "https://v2api.aliceblueonline.com/"
    sdk_hint = "pip install pya3"
    credential_fields = [
        CredentialField("user_id", "User ID"),
        CredentialField("api_key", "API key", secret=True),
    ]
