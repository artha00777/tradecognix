"""Zerodha Kite Connect adapter (kiteconnect SDK). Best-effort implementation.

Kite login is an OAuth redirect: open https://kite.zerodha.com/connect/login?v=3&api_key=XXX, log in,
and pass the `request_token` from the redirect URL (or an already generated `access_token`). Tokens
expire daily around 6 AM. Historical data needs the paid Historical API add-on.
Docs: https://kite.trade/docs/connect/v3/
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from .base import BrokerAdapter, BrokerError, CredentialField, Instrument, OrderRequest, OrderResult, candles_frame, pick_contract, sanitize_tag

INTERVALS = {"1m": "minute", "3m": "3minute", "5m": "5minute", "10m": "10minute", "15m": "15minute", "30m": "30minute", "1h": "60minute", "1d": "day"}
MAX_DAYS = {"1m": 60, "3m": 100, "5m": 100, "10m": 100, "15m": 200, "30m": 200, "1h": 400, "1d": 2000}
INDEX = {"NIFTY": ("NSE", "NIFTY 50", "256265"), "BANKNIFTY": ("NSE", "NIFTY BANK", "260105"), "FINNIFTY": ("NSE", "NIFTY FIN SERVICE", "257801"),
         "MIDCPNIFTY": ("NSE", "NIFTY MID SELECT", "288009"), "SENSEX": ("BSE", "SENSEX", "265")}


class ZerodhaAdapter(BrokerAdapter):
    key = "zerodha"
    display_name = "Zerodha"
    api_name = "Kite Connect"
    status = "beta"
    docs_url = "https://kite.trade/docs/connect/v3/"
    credential_fields = [
        CredentialField("api_key", "API key", secret=True),
        CredentialField("api_secret", "API secret", secret=True),
        CredentialField("request_token", "Request token", required=False, help="From the Kite login redirect"),
        CredentialField("access_token", "Access token", secret=True, required=False, help="If you already generated one today"),
    ]

    def __init__(self, credentials=None):
        super().__init__(credentials)
        self._kite = None
        self._instruments: dict[str, list[dict]] = {}

    def _client(self):
        if self._kite is None:
            self.login()
        return self._kite

    def login(self) -> dict[str, Any]:
        self.require("api_key")
        try:
            from kiteconnect import KiteConnect
        except ImportError as e:
            raise BrokerError("pip install kiteconnect") from e
        kite = KiteConnect(api_key=self.credentials["api_key"])
        token = self.credentials.get("access_token")
        if not token:
            self.require("api_secret", "request_token")
            data = kite.generate_session(self.credentials["request_token"], api_secret=self.credentials["api_secret"])
            token = data["access_token"]
        kite.set_access_token(token)
        self._kite = kite
        self.session = {"access_token": token}
        return {"ok": True}

    def profile(self) -> dict[str, Any]:
        p = self._client().profile()
        return {"client_code": p.get("user_id"), "name": p.get("user_name"), "email": p.get("email"), "raw": p}

    def funds(self) -> dict[str, Any]:
        m = self._client().margins("equity")
        return {"available": float(m.get("available", {}).get("live_balance", m.get("net", 0))), "used": float(m.get("utilised", {}).get("debits", 0)), "raw": m}

    def _instr_dump(self, exchange: str) -> list[dict]:
        if exchange not in self._instruments:
            self._instruments[exchange] = self._client().instruments(exchange)
        return self._instruments[exchange]

    @staticmethod
    def _to_inst(r: dict) -> Instrument:
        return Instrument(symbol=r["tradingsymbol"], token=str(r["instrument_token"]), exchange=r["exchange"], name=r.get("name", ""),
                          instrument_type=r.get("instrument_type", ""), expiry=r.get("expiry") or None, strike=r.get("strike") or None,
                          option_type=r.get("instrument_type") if r.get("instrument_type") in ("CE", "PE") else None,
                          lot_size=int(r.get("lot_size") or 1), tick_size=float(r.get("tick_size") or 0.05))

    def search_instruments(self, query: str, exchange: str = "NSE") -> list[Instrument]:
        q = query.upper().replace(" ", "")
        return [self._to_inst(r) for r in self._instr_dump(exchange) if q in r["tradingsymbol"]][:50]

    def index_instrument(self, underlying: str) -> Instrument:
        exch, sym, token = INDEX[underlying.upper()]
        return Instrument(symbol=sym, token=token, exchange=exch, name=underlying.upper(), instrument_type="INDEX")

    def resolve_option(self, underlying: str, expiry: date, strike: float, option_type: str) -> Instrument:
        exch = "BFO" if underlying.upper() == "SENSEX" else "NFO"
        rows = [r for r in self._instr_dump(exch) if r.get("name") == underlying.upper() and r.get("instrument_type") == option_type
                and abs(float(r.get("strike") or 0) - strike) < 0.01 and r.get("expiry")]
        if not rows:
            raise BrokerError(f"No {underlying} {strike} {option_type} contract near {expiry}")
        return pick_contract([self._to_inst(r) for r in rows], expiry)

    def historical_candles(self, instrument: Instrument, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        frames, cur = [], start
        while cur < end:
            chunk_end = min(end, cur + timedelta(days=MAX_DAYS[timeframe]))
            rows = self._client().historical_data(int(instrument.token), cur, chunk_end, INTERVALS[timeframe])
            frames.append(candles_frame([[r["date"], r["open"], r["high"], r["low"], r["close"], r.get("volume", 0)] for r in rows]))
            cur = chunk_end + timedelta(minutes=1)
        df = pd.concat(frames) if frames else candles_frame([])
        return df[~df.index.duplicated()].sort_index()

    def ltp(self, instrument: Instrument) -> float:
        key = f"{instrument.exchange}:{instrument.symbol}"
        return float(self._client().ltp(key)[key]["last_price"])

    def place_order(self, req: OrderRequest) -> OrderResult:
        k = self._client()
        product = {"INTRADAY": k.PRODUCT_MIS, "DELIVERY": k.PRODUCT_CNC, "CARRYFORWARD": k.PRODUCT_NRML}[req.product]
        kwargs = dict(variety=k.VARIETY_REGULAR, exchange=req.instrument.exchange, tradingsymbol=req.instrument.symbol,
                      transaction_type=req.side, quantity=req.quantity, product=product, order_type=req.order_type,
                      tag=sanitize_tag(req.tag))
        if req.order_type == "MARKET":
            kwargs["market_protection"] = -1  # let Kite apply its default market protection
        if req.order_type in ("LIMIT", "SL"):
            kwargs["price"] = req.price
        if req.order_type in ("SL", "SL-M"):
            kwargs["trigger_price"] = req.trigger_price
        oid = k.place_order(**kwargs)
        return OrderResult(order_id=str(oid))

    def cancel_order(self, order_id: str) -> OrderResult:
        self._client().cancel_order(variety="regular", order_id=order_id)
        return OrderResult(order_id=order_id, status="cancelled")

    def positions(self) -> list[dict[str, Any]]:
        rows = self._client().positions().get("net", [])
        return [{"symbol": r["tradingsymbol"], "net_qty": int(r["quantity"]), "avg_price": float(r["average_price"]), "pnl": float(r["pnl"]),
                 "ltp": float(r["last_price"]), "product": r["product"],
                 "instrument": Instrument(symbol=r["tradingsymbol"], token=str(r["instrument_token"]), exchange=r["exchange"])} for r in rows]

    def order_book(self) -> list[dict[str, Any]]:
        return [{"order_id": r["order_id"], "symbol": r["tradingsymbol"], "side": r["transaction_type"], "qty": r["quantity"], "status": r["status"],
                 "avg_price": r.get("average_price", 0), "tag": r.get("tag"), "time": str(r.get("order_timestamp")), "raw": r} for r in self._client().orders()]
