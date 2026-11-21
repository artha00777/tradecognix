"""5paisa adapter: documented skeleton (raises NotImplementedError).

API: Xstream API
Docs: https://xstream.5paisa.com/dev-docs
Implementation notes:
- Auth: FivePaisaClient(cred={APP_NAME, APP_SOURCE, USER_ID, PASSWORD, USER_KEY, ENCRYPTION_KEY}); get_totp_session(client_code, totp, pin).
- Orders: client.place_order(OrderType='B'/'S', Exchange='N', ExchangeType='D', ScripCode=..., Qty=..., Price=0, IsIntraday=True, RemoteOrderID=tag)
- Historical: client.historical_data('N','D',scrip_code,'5m',from,to).
"""
from __future__ import annotations

from ._skeleton import SkeletonAdapter
from .base import CredentialField


class FivePaisaAdapter(SkeletonAdapter):
    key = "fivepaisa"
    display_name = "5paisa"
    api_name = "Xstream API"
    docs_url = "https://xstream.5paisa.com/dev-docs"
    sdk_hint = "pip install py5paisa"
    credential_fields = [
        CredentialField("app_name", "App name"),
        CredentialField("user_key", "User key", secret=True),
        CredentialField("encryption_key", "Encryption key", secret=True),
        CredentialField("user_id", "User ID"),
        CredentialField("password", "App password", secret=True),
        CredentialField("client_code", "Client code"),
        CredentialField("pin", "PIN", secret=True),
        CredentialField("totp_secret", "TOTP secret", secret=True),
    ]
