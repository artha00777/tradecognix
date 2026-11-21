"""Kotak Neo adapter: documented skeleton (raises NotImplementedError).

API: Neo Trade API
Docs: https://github.com/Kotak-Neo/kotak-neo-api
Implementation notes:
- Auth: NeoAPI(consumer_key, consumer_secret, environment='prod'); login(mobilenumber, password) then session_2fa(OTP/MPIN); newer versions support TOTP.
- Orders: client.place_order(exchange_segment='nse_fo', product='MIS', price, order_type='MKT', quantity, validity='DAY', trading_symbol, transaction_type='B'/'S', tag=...)
- Scrip master via client.scrip_master().
"""
from __future__ import annotations

from ._skeleton import SkeletonAdapter
from .base import CredentialField


class KotakNeoAdapter(SkeletonAdapter):
    key = "kotak"
    display_name = "Kotak Neo"
    api_name = "Neo Trade API"
    docs_url = "https://github.com/Kotak-Neo/kotak-neo-api"
    sdk_hint = "pip install 'git+https://github.com/Kotak-Neo/kotak-neo-api'"
    credential_fields = [
        CredentialField("consumer_key", "Consumer key", secret=True),
        CredentialField("consumer_secret", "Consumer secret", secret=True),
        CredentialField("mobile", "Mobile number"),
        CredentialField("mpin", "MPIN", secret=True),
        CredentialField("totp_secret", "TOTP secret", secret=True, required=False),
    ]
