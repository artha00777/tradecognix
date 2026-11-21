"""Shoonya (Finvasia) adapter: documented skeleton (raises NotImplementedError).

API: Shoonya / Noren API
Docs: https://github.com/Shoonya-Dev/ShoonyaApi-py
Implementation notes:
- Auth: ShoonyaApiPy().login(userid, password, twoFA=pyotp.TOTP(secret).now(), vendor_code, api_secret, imei).
- Orders: api.place_order(buy_or_sell='B', product_type='I', exchange='NFO', tradingsymbol, quantity, discloseqty=0, price_type='MKT', price=0, retention='DAY', remarks=tag)
- Historical: api.get_time_price_series(exchange, token, starttime, endtime, interval=5).
"""
from __future__ import annotations

from ._skeleton import SkeletonAdapter
from .base import CredentialField


class ShoonyaAdapter(SkeletonAdapter):
    key = "shoonya"
    display_name = "Shoonya (Finvasia)"
    api_name = "Shoonya / Noren API"
    docs_url = "https://github.com/Shoonya-Dev/ShoonyaApi-py"
    sdk_hint = "pip install NorenRestApiPy (from the ShoonyaApi-py repo)"
    credential_fields = [
        CredentialField("user_id", "User ID"),
        CredentialField("password", "Password", secret=True),
        CredentialField("totp_secret", "TOTP secret", secret=True),
        CredentialField("vendor_code", "Vendor code"),
        CredentialField("api_secret", "API secret", secret=True),
        CredentialField("imei", "IMEI / device id", required=False),
    ]
