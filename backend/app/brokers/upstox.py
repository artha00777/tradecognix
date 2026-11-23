"""Upstox adapter using the REST API v2 (and v3 historical) directly via httpx. Best-effort.

Upstox uses OAuth: exchange the `code` from the login redirect for an access token (valid until
~3:30 AM next day), or paste an access token directly.
Docs: https://upstox.com/developer/api-documentation/
"""
from __future__ import annotations

import gzip
import json
from datetime import date, datetime
from typing import Any

import httpx
import pandas as pd

from .base import BrokerAdapter, BrokerError, CredentialField, Instrument, OrderRequest, OrderResult, candles_frame, pick_contract, sanitize_tag

from ..engine.clock import IST

API = "https://api.upstox.com"
HFT = "https://api-hft.upstox.com"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
INDEX_KEYS = {"NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank", "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
              "MIDCPNIFTY": "NSE_INDEX|NIFTY MID SELECT", "SENSEX": "BSE_INDEX|SENSEX"}
V3_UNITS = {"1m": ("minutes", 1), "3m": ("minutes", 3), "5m": ("minutes", 5), "10m": ("minutes", 10), "15m": ("minutes", 15),
            "30m": ("minutes", 30), "1h": ("hours", 1), "1d": ("days", 1)}


class UpstoxAdapter(BrokerAdapter):
    key = "upstox"
    display_name = "Upstox"
    api_name = "Upstox API v2"
    status = "beta"
    docs_url = "https://upstox.com/developer/api-documentation/"
    credential_fields = [
        CredentialField("api_key", "API key (client id)", secret=True),
        CredentialField("api_secret", "API secret", secret=True, required=False),
        CredentialField("redirect_uri", "Redirect URI", required=False),
        CredentialField("auth_code", "Auth code", required=False, help="From the login redirect"),
        CredentialField("access_token", "Access token", secret=True, required=False),
    ]

    def __init__(self, credentials=None, client: httpx.Client | None = None):
        super().__init__(credentials)
        self._http = client or httpx.Client(timeout=20)
        self._instruments: list[dict] | None = None

    def _headers(self) -> dict:
        if not self.session.get("access_token"):
            self.login()
        return {"Authorization": f"Bearer {self.session['access_token']}", "Accept": "application/json", "Content-Type": "application/json"}

    def _req(self, method: str, url: str, **kw) -> Any:
        r = self._http.request(method, url, headers=self._headers(), **kw)
        try:
            body = r.json()
        except ValueError:
            raise BrokerError(f"Upstox {url}: HTTP {r.status_code}")
        if not isinstance(body, dict):
            raise BrokerError(f"Upstox {url}: unexpected response")
        if r.status_code >= 400 or body.get("status") == "error":
            errs = body.get("errors") or [{}]
            raise BrokerError(f"Upstox: {errs[0].get('message', r.text[:200])}")
        return body.get("data")

    def login(self) -> dict[str, Any]:
        if self.credentials.get("access_token"):
            self.session = {"access_token": self.credentials["access_token"]}
            return {"ok": True}
        self.require("api_key", "api_secret", "redirect_uri", "auth_code")
        r = self._http.post(f"{API}/v2/login/authorization/token", data={
            "code": self.credentials["auth_code"], "client_id": self.credentials["api_key"], "client_secret": self.credentials["api_secret"],
            "redirect_uri": self.credentials["redirect_uri"], "grant_type": "authorization_code"}, headers={"Accept": "application/json"})
        if r.status_code >= 400:
            raise BrokerError(f"Upstox login failed: {r.text[:200]}")
        self.session = {"access_token": r.json()["access_token"]}
        return {"ok": True}

    def profile(self) -> dict[str, Any]:
        d = self._req("GET", f"{API}/v2/user/profile")
        return {"client_code": d.get("user_id"), "name": d.get("user_name"), "email": d.get("email"), "raw": d}

    def funds(self) -> dict[str, Any]:
        d = self._req("GET", f"{API}/v2/user/get-funds-and-margin", params={"segment": "SEC"}) or {}
        eq = d.get("equity", d)
        return {"available": float(eq.get("available_margin", 0)), "used": float(eq.get("used_margin", 0)), "raw": d}

    def _all_instruments(self) -> list[dict]:
        if self._instruments is None:
            r = self._http.get(INSTRUMENTS_URL, timeout=60)
            r.raise_for_status()
            self._instruments = json.loads(gzip.decompress(r.content))
        return self._instruments

    @staticmethod
    def _to_inst(r: dict) -> Instrument:
        # epoch ms -> IST date (server-local tz would shift it a day on UTC hosts)
        exp = datetime.fromtimestamp(r["expiry"] / 1000, tz=IST).date() if r.get("expiry") else None
        return Instrument(symbol=r.get("trading_symbol", ""), token=r["instrument_key"], exchange=r.get("segment", ""), name=r.get("name", ""),
                          instrument_type=r.get("instrument_type", ""), expiry=exp, strike=r.get("strike_price"),
                          option_type=r.get("instrument_type") if r.get("instrument_type") in ("CE", "PE") else None,
                          lot_size=int(r.get("lot_size") or 1), tick_size=float(r.get("tick_size") or 0.05))

    def search_instruments(self, query: str, exchange: str = "NSE") -> list[Instrument]:
        q = query.upper().replace(" ", "")
        seg = {"NSE": "NSE_EQ", "NFO": "NSE_FO", "BSE": "BSE_EQ", "BFO": "BSE_FO"}.get(exchange.upper(), exchange)
        return [self._to_inst(r) for r in self._all_instruments() if r.get("segment") == seg and q in r.get("trading_symbol", "").replace(" ", "")][:50]

    def index_instrument(self, underlying: str) -> Instrument:
        key = INDEX_KEYS[underlying.upper()]
        return Instrument(symbol=key.split("|")[1], token=key, exchange=key.split("|")[0], name=underlying.upper(), instrument_type="INDEX")

    def resolve_option(self, underlying: str, expiry: date, strike: float, option_type: str) -> Instrument:
        seg = "BSE_FO" if underlying.upper() == "SENSEX" else "NSE_FO"
        rows = [self._to_inst(r) for r in self._all_instruments() if r.get("segment") == seg and r.get("underlying_symbol") == underlying.upper()
                and r.get("instrument_type") == option_type and abs(float(r.get("strike_price") or 0) - strike) < 0.01]
        if not rows:
            raise BrokerError(f"No {underlying} {strike} {option_type} near {expiry}")
        return pick_contract(rows, expiry)

    def historical_candles(self, instrument: Instrument, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        unit, n = V3_UNITS[timeframe]
        key = httpx.URL(f"{API}/v3/historical-candle/{instrument.token}/{unit}/{n}/{end:%Y-%m-%d}/{start:%Y-%m-%d}")
        d = self._req("GET", str(key)) or {}
        rows = sorted(d.get("candles", []), key=lambda c: c[0])
        return candles_frame(rows)

    def ltp(self, instrument: Instrument) -> float:
        d = self._req("GET", f"{API}/v2/market-quote/ltp", params={"instrument_key": instrument.token}) or {}
        return float(next(iter(d.values()))["last_price"])

    def place_order(self, req: OrderRequest) -> OrderResult:
        body = {
            "quantity": req.quantity, "product": {"INTRADAY": "I", "DELIVERY": "D", "CARRYFORWARD": "D"}[req.product], "validity": "DAY",
            "price": req.price, "tag": sanitize_tag(req.tag), "instrument_token": req.instrument.token, "order_type": req.order_type,
            "transaction_type": req.side, "disclosed_quantity": 0, "trigger_price": req.trigger_price, "is_amo": False,
        }
        d = self._req("POST", f"{HFT}/v2/order/place", json=body) or {}
        return OrderResult(order_id=str(d.get("order_id", "")), raw=d)

    def cancel_order(self, order_id: str) -> OrderResult:
        self._req("DELETE", f"{HFT}/v2/order/cancel", params={"order_id": order_id})
        return OrderResult(order_id=order_id, status="cancelled")

    def positions(self) -> list[dict[str, Any]]:
        rows = self._req("GET", f"{API}/v2/portfolio/short-term-positions") or []
        return [{"symbol": r.get("trading_symbol"), "net_qty": int(r.get("quantity", 0)), "avg_price": float(r.get("average_price") or 0),
                 "pnl": float(r.get("pnl") or 0), "ltp": float(r.get("last_price") or 0), "product": r.get("product"),
                 "instrument": Instrument(symbol=r.get("trading_symbol", ""), token=r.get("instrument_token", ""), exchange=r.get("exchange", ""))} for r in rows]

    def order_book(self) -> list[dict[str, Any]]:
        rows = self._req("GET", f"{API}/v2/order/retrieve-all") or []
        return [{"order_id": r.get("order_id"), "symbol": r.get("trading_symbol"), "side": r.get("transaction_type"), "qty": r.get("quantity"),
                 "status": r.get("status"), "avg_price": r.get("average_price"), "tag": r.get("tag"), "time": r.get("order_timestamp"), "raw": r} for r in rows]
