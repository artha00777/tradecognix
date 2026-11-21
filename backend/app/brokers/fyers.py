"""Fyers API v3 adapter (fyers-apiv3 SDK). Best-effort.

Login: generate an auth code via the Fyers login URL, exchange it with SessionModel for an
access token (valid for the day), or paste an access token. Symbols look like NSE:NIFTY50-INDEX,
NSE:SBIN-EQ, NSE:NIFTY25N2524600CE.
Docs: https://myapi.fyers.in/docsv3
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from .base import BrokerAdapter, BrokerError, CredentialField, Instrument, OrderRequest, OrderResult, candles_frame, sanitize_tag

RES = {"1m": "1", "3m": "3", "5m": "5", "10m": "10", "15m": "15", "30m": "30", "1h": "60", "1d": "D"}
INDEX = {"NIFTY": "NSE:NIFTY50-INDEX", "BANKNIFTY": "NSE:NIFTYBANK-INDEX", "FINNIFTY": "NSE:FINNIFTY-INDEX",
         "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX", "SENSEX": "BSE:SENSEX-INDEX"}


class FyersAdapter(BrokerAdapter):
    key = "fyers"
    display_name = "Fyers"
    api_name = "Fyers API v3"
    status = "beta"
    docs_url = "https://myapi.fyers.in/docsv3"
    credential_fields = [
        CredentialField("client_id", "App ID (e.g. XXXX-100)"),
        CredentialField("secret_key", "Secret key", secret=True, required=False),
        CredentialField("redirect_uri", "Redirect URI", required=False),
        CredentialField("auth_code", "Auth code", required=False),
        CredentialField("access_token", "Access token", secret=True, required=False),
    ]

    def __init__(self, credentials=None):
        super().__init__(credentials)
        self._f = None

    def _client(self):
        if self._f is None:
            self.login()
        return self._f

    def login(self) -> dict[str, Any]:
        self.require("client_id")
        try:
            from fyers_apiv3 import fyersModel
        except ImportError as e:
            raise BrokerError("pip install fyers-apiv3") from e
        token = self.credentials.get("access_token")
        if not token:
            self.require("secret_key", "redirect_uri", "auth_code")
            sess = fyersModel.SessionModel(client_id=self.credentials["client_id"], secret_key=self.credentials["secret_key"],
                                           redirect_uri=self.credentials["redirect_uri"], response_type="code", grant_type="authorization_code")
            sess.set_token(self.credentials["auth_code"])
            resp = sess.generate_token()
            token = resp.get("access_token")
            if not token:
                raise BrokerError(f"Fyers login failed: {resp.get('message')}")
        self._f = fyersModel.FyersModel(client_id=self.credentials["client_id"], token=token, is_async=False, log_path="")
        self.session = {"access_token": token}
        return {"ok": True}

    @staticmethod
    def _ok(resp: dict, what: str) -> dict:
        if not isinstance(resp, dict) or resp.get("s") != "ok":
            raise BrokerError(f"Fyers {what} failed: {resp.get('message') if isinstance(resp, dict) else resp}")
        return resp

    def profile(self) -> dict[str, Any]:
        d = self._ok(self._client().get_profile(), "profile").get("data", {})
        return {"client_code": d.get("fy_id"), "name": d.get("name"), "email": d.get("email_id"), "raw": d}

    def funds(self) -> dict[str, Any]:
        rows = self._ok(self._client().funds(), "funds").get("fund_limit", [])
        by = {r.get("title"): r for r in rows}
        return {"available": float(by.get("Available Balance", {}).get("equityAmount", 0)), "used": float(by.get("Utilized Amount", {}).get("equityAmount", 0)), "raw": rows}

    def search_instruments(self, query: str, exchange: str = "NSE") -> list[Instrument]:
        # Fyers symbols are deterministic strings; full master at https://public.fyers.in/sym_details/NSE_FO.csv
        sym = query.upper()
        if ":" not in sym:
            sym = f"{exchange.upper()}:{sym}-EQ"
        return [Instrument(symbol=sym, token=sym, exchange=sym.split(":")[0])]

    def index_instrument(self, underlying: str) -> Instrument:
        s = INDEX[underlying.upper()]
        return Instrument(symbol=s, token=s, exchange=s.split(":")[0], name=underlying.upper(), instrument_type="INDEX")

    def resolve_option(self, underlying: str, expiry: date, strike: float, option_type: str) -> Instrument:
        # Monthly format: NSE:NIFTY25NOV24600CE ; weekly: NSE:NIFTY25N1124600CE (YY + M code + DD)
        exch = "BSE" if underlying.upper() == "SENSEX" else "NSE"
        yy = expiry.strftime("%y")
        month_code = {10: "O", 11: "N", 12: "D"}.get(expiry.month, str(expiry.month))
        weekly = f"{exch}:{underlying.upper()}{yy}{month_code}{expiry.day:02d}{int(strike)}{option_type}"
        monthly = f"{exch}:{underlying.upper()}{yy}{expiry.strftime('%b').upper()}{int(strike)}{option_type}"
        nxt = expiry + timedelta(days=7)
        sym = monthly if nxt.month != expiry.month else weekly
        return Instrument(symbol=sym, token=sym, exchange=exch, name=underlying.upper(), instrument_type="OPTIDX", expiry=expiry, strike=strike, option_type=option_type)

    def historical_candles(self, instrument: Instrument, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        frames, cur = [], start
        while cur < end:
            chunk_end = min(end, cur + timedelta(days=99))
            resp = self._ok(self._client().history(data={"symbol": instrument.token, "resolution": RES[timeframe], "date_format": "1",
                                                         "range_from": cur.strftime("%Y-%m-%d"), "range_to": chunk_end.strftime("%Y-%m-%d"), "cont_flag": "1"}), "history")
            frames.append(candles_frame(resp.get("candles", []), ts_unit="s"))
            cur = chunk_end + timedelta(days=1)
        df = pd.concat(frames) if frames else candles_frame([])
        return df[~df.index.duplicated()].sort_index()

    def ltp(self, instrument: Instrument) -> float:
        d = self._ok(self._client().quotes(data={"symbols": instrument.token}), "quotes")
        return float(d["d"][0]["v"]["lp"])

    def place_order(self, req: OrderRequest) -> OrderResult:
        data = {"symbol": req.instrument.token, "qty": req.quantity, "type": {"LIMIT": 1, "MARKET": 2, "SL-M": 3, "SL": 4}[req.order_type],
                "side": 1 if req.side == "BUY" else -1, "productType": {"INTRADAY": "INTRADAY", "DELIVERY": "CNC", "CARRYFORWARD": "MARGIN"}[req.product],
                "limitPrice": req.price, "stopPrice": req.trigger_price, "validity": "DAY", "disclosedQty": 0, "offlineOrder": False,
                "orderTag": sanitize_tag(req.tag)}
        d = self._ok(self._client().place_order(data=data), "place_order")
        return OrderResult(order_id=str(d.get("id", "")), message=d.get("message", ""), raw=d)

    def cancel_order(self, order_id: str) -> OrderResult:
        self._ok(self._client().cancel_order(data={"id": order_id}), "cancel_order")
        return OrderResult(order_id=order_id, status="cancelled")

    def positions(self) -> list[dict[str, Any]]:
        rows = self._ok(self._client().positions(), "positions").get("netPositions", [])
        return [{"symbol": r.get("symbol"), "net_qty": int(r.get("netQty", 0)), "avg_price": float(r.get("netAvg") or 0), "pnl": float(r.get("pl") or 0),
                 "ltp": float(r.get("ltp") or 0), "product": r.get("productType"),
                 "instrument": Instrument(symbol=r.get("symbol", ""), token=r.get("symbol", ""), exchange=str(r.get("symbol", ":")).split(":")[0])} for r in rows]

    def order_book(self) -> list[dict[str, Any]]:
        rows = self._ok(self._client().orderbook(), "orderbook").get("orderBook", [])
        return [{"order_id": r.get("id"), "symbol": r.get("symbol"), "side": "BUY" if r.get("side") == 1 else "SELL", "qty": r.get("qty"),
                 "status": r.get("status"), "avg_price": r.get("tradedPrice"), "tag": r.get("orderTag"), "time": r.get("orderDateTime"), "raw": r} for r in rows]
