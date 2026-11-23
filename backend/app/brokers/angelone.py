"""Angel One SmartAPI adapter (smartapi-python + pyotp).

Login = API key + client code + PIN (MPIN) + TOTP generated from the TOTP secret shown when you
enable TOTP at https://smartapi.angelbroking.com/enable-totp. Orders must originate from the
static IP registered on your SmartAPI app (SEBI retail-algo framework).
Docs: https://smartapi.angelbroking.com/docs
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from ..engine.clock import ist_now, ist_today
from .base import BrokerAdapter, BrokerError, CredentialField, Instrument, OrderRequest, OrderResult, candles_frame, pick_contract, sanitize_tag

SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
INTERVALS = {"1m": "ONE_MINUTE", "3m": "THREE_MINUTE", "5m": "FIVE_MINUTE", "10m": "TEN_MINUTE", "15m": "FIFTEEN_MINUTE",
             "30m": "THIRTY_MINUTE", "1h": "ONE_HOUR", "1d": "ONE_DAY"}
# Max days per getCandleData request (SmartAPI limits)
MAX_DAYS = {"1m": 30, "3m": 60, "5m": 100, "10m": 100, "15m": 200, "30m": 200, "1h": 400, "1d": 2000}
INDEX_TOKENS = {
    "NIFTY": ("NSE", "99926000", "Nifty 50"), "BANKNIFTY": ("NSE", "99926009", "Nifty Bank"),
    "FINNIFTY": ("NSE", "99926037", "Nifty Fin Service"), "MIDCPNIFTY": ("NSE", "99926074", "NIFTY MID SELECT"),
    "SENSEX": ("BSE", "99919000", "SENSEX"),
}
PRODUCT = {"INTRADAY": "INTRADAY", "DELIVERY": "DELIVERY", "CARRYFORWARD": "CARRYFORWARD"}
CACHE_DIR = Path(os.environ.get("TC_CACHE_DIR", ".cache"))


class AngelOneAdapter(BrokerAdapter):
    key = "angelone"
    display_name = "Angel One"
    api_name = "SmartAPI"
    status = "live"
    docs_url = "https://smartapi.angelbroking.com/docs"
    credential_fields = [
        CredentialField("api_key", "API key", secret=True),
        CredentialField("client_code", "Client code"),
        CredentialField("pin", "PIN", secret=True),
        CredentialField("totp_secret", "TOTP secret", secret=True, help="Base32 secret from Angel's Enable TOTP page"),
    ]

    def __init__(self, credentials=None):
        super().__init__(credentials)
        self._api = None
        self._master: list[dict] | None = None

    # ---------- session ----------
    def _client(self):
        if self._api is None:
            self.login()
        return self._api

    def login(self) -> dict[str, Any]:
        self.require("api_key", "client_code", "pin", "totp_secret")
        try:
            import pyotp
            from SmartApi import SmartConnect
        except ImportError as e:
            raise BrokerError("Install broker SDKs: pip install -r requirements-brokers.txt") from e
        api = SmartConnect(api_key=self.credentials["api_key"])
        totp = pyotp.TOTP(self.credentials["totp_secret"].replace(" ", "")).now()
        data = api.generateSession(self.credentials["client_code"], self.credentials["pin"], totp)
        if not data or not data.get("status"):
            raise BrokerError(f"Angel One login failed: {(data or {}).get('message', 'unknown error')}")
        self._api = api
        self.session = {
            "jwt": data["data"]["jwtToken"], "refresh_token": data["data"]["refreshToken"],
            "feed_token": api.getfeedToken(), "logged_in_at": ist_now().isoformat(),
        }
        return {"ok": True, "client_code": self.credentials["client_code"]}

    def logout(self) -> None:
        if self._api is not None:
            try:
                self._api.terminateSession(self.credentials["client_code"])
            except Exception:
                pass
        self._api = None
        super().logout()

    @staticmethod
    def _ok(resp: Any, what: str) -> Any:
        if isinstance(resp, dict):
            if resp.get("status") is False or (resp.get("errorcode") and resp.get("errorcode") != ""):
                raise BrokerError(f"Angel One {what} failed: {resp.get('message')} ({resp.get('errorcode')})")
            return resp.get("data")
        return resp

    # ---------- account ----------
    def profile(self) -> dict[str, Any]:
        d = self._ok(self._client().getProfile(self.session["refresh_token"]), "profile")
        return {"client_code": d.get("clientcode"), "name": d.get("name"), "email": d.get("email"),
                "exchanges": d.get("exchanges"), "products": d.get("products"), "raw": d}

    def funds(self) -> dict[str, Any]:
        d = self._ok(self._client().rmsLimit(), "funds") or {}
        return {"available": float(d.get("availablecash") or d.get("net") or 0), "used": float(d.get("utiliseddebits") or 0), "raw": d}

    # ---------- instruments ----------
    def _scrip_master(self) -> list[dict]:
        if self._master is not None:
            return self._master
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = CACHE_DIR / f"angel_scrip_master_{ist_today().isoformat()}.json"
        if not path.exists():
            import httpx

            r = httpx.get(SCRIP_MASTER_URL, timeout=60)
            r.raise_for_status()
            path.write_bytes(r.content)
            for old in CACHE_DIR.glob("angel_scrip_master_*.json"):
                if old != path:
                    old.unlink(missing_ok=True)
        self._master = json.loads(path.read_text())
        return self._master

    @staticmethod
    def _to_instrument(r: dict) -> Instrument:
        exp = None
        if r.get("expiry"):
            try:
                exp = datetime.strptime(r["expiry"], "%d%b%Y").date()
            except ValueError:
                exp = None
        strike = float(r.get("strike") or 0) / 100 or None
        sym = r.get("symbol", "")
        return Instrument(
            symbol=sym, token=str(r["token"]), exchange=r.get("exch_seg", ""), name=r.get("name", ""),
            instrument_type=r.get("instrumenttype", ""), expiry=exp, strike=strike,
            option_type=sym[-2:] if sym.endswith(("CE", "PE")) else None,
            lot_size=int(float(r.get("lotsize") or 1)), tick_size=float(r.get("tick_size") or 5) / 100,
        )

    def search_instruments(self, query: str, exchange: str = "NSE") -> list[Instrument]:
        q = query.upper().replace(" ", "")
        exch = {"NSE": "NSE", "NFO": "NFO", "BSE": "BSE", "BFO": "BFO"}.get(exchange.upper(), exchange.upper())
        out = [self._to_instrument(r) for r in self._scrip_master() if r.get("exch_seg") == exch and q in r.get("symbol", "").upper()]
        return out[:50]

    def index_instrument(self, underlying: str) -> Instrument:
        exch, token, name = INDEX_TOKENS[underlying.upper()]
        return Instrument(symbol=name, token=token, exchange=exch, name=underlying.upper(), instrument_type="INDEX")

    def resolve_option(self, underlying: str, expiry: date, strike: float, option_type: str) -> Instrument:
        exch = "BFO" if underlying.upper() == "SENSEX" else "NFO"
        candidates = [
            r for r in self._scrip_master()
            if r.get("exch_seg") == exch and r.get("name") == underlying.upper() and r.get("instrumenttype") == "OPTIDX"
            and r.get("symbol", "").endswith(option_type) and abs(float(r.get("strike") or 0) / 100 - strike) < 0.01
        ]
        if not candidates:
            raise BrokerError(f"No {underlying} {strike} {option_type} contract in scrip master")
        # previously fell back to the LAST listed contract when none matched, which could be expired
        return pick_contract([self._to_instrument(r) for r in candidates], expiry)

    # ---------- market data ----------
    def historical_candles(self, instrument: Instrument, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        api = self._client()
        frames, cur = [], start
        step = timedelta(days=MAX_DAYS[timeframe])
        while cur < end:
            chunk_end = min(end, cur + step)
            resp = api.getCandleData({
                "exchange": instrument.exchange, "symboltoken": instrument.token, "interval": INTERVALS[timeframe],
                "fromdate": cur.strftime("%Y-%m-%d %H:%M"), "todate": chunk_end.strftime("%Y-%m-%d %H:%M"),
            })
            rows = self._ok(resp, "getCandleData") or []
            frames.append(candles_frame(rows))
            cur = chunk_end + timedelta(minutes=1)
            time.sleep(0.35)  # SmartAPI historical rate limit is ~3 req/s
        df = pd.concat(frames) if frames else candles_frame([])
        return df[~df.index.duplicated()].sort_index()

    def ltp(self, instrument: Instrument) -> float:
        d = self._ok(self._client().ltpData(instrument.exchange, instrument.symbol, instrument.token), "ltpData")
        return float(d["ltp"])

    # ---------- orders ----------
    def place_order(self, req: OrderRequest) -> OrderResult:
        params = {
            "variety": "NORMAL" if req.order_type in ("MARKET", "LIMIT") else "STOPLOSS",
            "tradingsymbol": req.instrument.symbol, "symboltoken": req.instrument.token,
            "transactiontype": req.side, "exchange": req.instrument.exchange,
            "ordertype": {"MARKET": "MARKET", "LIMIT": "LIMIT", "SL": "STOPLOSS_LIMIT", "SL-M": "STOPLOSS_MARKET"}[req.order_type],
            "producttype": PRODUCT[req.product], "duration": "DAY",
            "price": str(req.price or 0), "triggerprice": str(req.trigger_price or 0), "quantity": str(req.quantity),
            "ordertag": sanitize_tag(req.tag),
        }
        try:
            resp = self._client().placeOrderFullResponse(params)
        except AttributeError:  # older SDKs
            resp = self._client().placeOrder(params)
        if isinstance(resp, str):
            return OrderResult(order_id=resp, raw={"orderid": resp})
        data = self._ok(resp, "placeOrder") or {}
        return OrderResult(order_id=str(data.get("orderid", "")), message=resp.get("message", ""), raw=resp)

    def cancel_order(self, order_id: str) -> OrderResult:
        resp = self._client().cancelOrder(order_id, "NORMAL")
        self._ok(resp, "cancelOrder")
        return OrderResult(order_id=order_id, status="cancelled", raw=resp if isinstance(resp, dict) else {})

    def positions(self) -> list[dict[str, Any]]:
        rows = self._ok(self._client().position(), "position") or []
        out = []
        for r in rows:
            inst = Instrument(symbol=r.get("tradingsymbol", ""), token=str(r.get("symboltoken", "")), exchange=r.get("exchange", ""),
                              lot_size=int(float(r.get("lotsize") or 1)))
            out.append({"symbol": inst.symbol, "net_qty": int(float(r.get("netqty") or 0)), "avg_price": float(r.get("netprice") or 0),
                        "pnl": float(r.get("pnl") or 0), "ltp": float(r.get("ltp") or 0), "product": r.get("producttype"), "instrument": inst})
        return out

    def order_book(self) -> list[dict[str, Any]]:
        rows = self._ok(self._client().orderBook(), "orderBook") or []
        return [{"order_id": r.get("orderid"), "symbol": r.get("tradingsymbol"), "side": r.get("transactiontype"), "qty": int(float(r.get("quantity") or 0)),
                 "status": r.get("status"), "avg_price": float(r.get("averageprice") or 0), "tag": r.get("ordertag"), "time": r.get("updatetime"), "raw": r}
                for r in rows]
