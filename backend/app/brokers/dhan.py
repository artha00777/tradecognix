"""Dhan adapter (dhanhq SDK v2). Best-effort.

Dhan uses a client id + access token generated on web.dhan.co (valid 24h, or a long-lived token
for API users). Security ids come from Dhan's scrip master CSV.
Docs: https://dhanhq.co/docs/v2/
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Any

import httpx
import pandas as pd

from .base import BrokerAdapter, BrokerError, CredentialField, Instrument, OrderRequest, OrderResult, candles_frame, pick_contract, sanitize_tag

SCRIP_CSV = "https://images.dhan.co/api-data/api-scrip-master.csv"
INDEX = {"NIFTY": "13", "BANKNIFTY": "25", "FINNIFTY": "27", "MIDCPNIFTY": "442", "SENSEX": "51"}
SEGMENT = {"NSE": "NSE_EQ", "NFO": "NSE_FNO", "BSE": "BSE_EQ", "BFO": "BSE_FNO", "IDX": "IDX_I"}
TF = {"1m": 1, "5m": 5, "15m": 15, "25m": 25, "1h": 60}  # other intervals: BrokerFeed resamples 1m/5m


class DhanAdapter(BrokerAdapter):
    key = "dhan"
    display_name = "Dhan"
    api_name = "DhanHQ v2"
    status = "beta"
    docs_url = "https://dhanhq.co/docs/v2/"
    credential_fields = [CredentialField("client_id", "Client ID"), CredentialField("access_token", "Access token", secret=True)]

    def __init__(self, credentials=None):
        super().__init__(credentials)
        self._dhan = None
        self._master: list[dict] | None = None

    def _client(self):
        if self._dhan is None:
            self.login()
        return self._dhan

    def login(self) -> dict[str, Any]:
        self.require("client_id", "access_token")
        try:
            from dhanhq import dhanhq
        except ImportError as e:
            raise BrokerError("pip install dhanhq") from e
        try:
            from dhanhq import DhanContext

            self._dhan = dhanhq(DhanContext(self.credentials["client_id"], self.credentials["access_token"]))
        except ImportError:  # dhanhq < 2.1
            self._dhan = dhanhq(self.credentials["client_id"], self.credentials["access_token"])
        self.session = {"client_id": self.credentials["client_id"]}
        return {"ok": True}

    @staticmethod
    def _ok(resp: dict, what: str) -> Any:
        if not isinstance(resp, dict) or resp.get("status") != "success":
            raise BrokerError(f"Dhan {what} failed: {resp.get('remarks') if isinstance(resp, dict) else resp}")
        return resp.get("data")

    def profile(self) -> dict[str, Any]:
        r = httpx.get("https://api.dhan.co/v2/profile", headers={"access-token": self.credentials.get("access_token", "")}, timeout=15)
        d = r.json() if r.status_code == 200 else {}
        return {"client_code": d.get("dhanClientId", self.credentials.get("client_id")), "name": d.get("dhanClientName", ""), "raw": d}

    def funds(self) -> dict[str, Any]:
        d = self._ok(self._client().get_fund_limits(), "funds") or {}
        return {"available": float(d.get("availabelBalance", d.get("availableBalance", 0))), "used": float(d.get("utilizedAmount", 0)), "raw": d}

    def _scrips(self) -> list[dict]:
        if self._master is None:
            r = httpx.get(SCRIP_CSV, timeout=60)
            r.raise_for_status()
            self._master = list(csv.DictReader(io.StringIO(r.text)))
        return self._master

    @staticmethod
    def _to_inst(r: dict) -> Instrument:
        exp = None
        if r.get("SEM_EXPIRY_DATE"):
            try:
                exp = datetime.strptime(r["SEM_EXPIRY_DATE"][:10], "%Y-%m-%d").date()
            except ValueError:
                pass
        seg = {"NSE": {"E": "NSE_EQ", "D": "NSE_FNO", "I": "IDX_I"}, "BSE": {"E": "BSE_EQ", "D": "BSE_FNO", "I": "IDX_I"}}.get(r.get("SEM_EXM_EXCH_ID", ""), {}).get(r.get("SEM_SEGMENT", ""), "")
        return Instrument(symbol=r.get("SEM_TRADING_SYMBOL", ""), token=r.get("SEM_SMST_SECURITY_ID", ""), exchange=seg,
                          name=r.get("SM_SYMBOL_NAME", ""), instrument_type=r.get("SEM_INSTRUMENT_NAME", ""), expiry=exp,
                          strike=float(r["SEM_STRIKE_PRICE"]) if r.get("SEM_STRIKE_PRICE") else None,
                          option_type=r.get("SEM_OPTION_TYPE") if r.get("SEM_OPTION_TYPE") in ("CE", "PE") else None,
                          lot_size=int(float(r.get("SEM_LOT_UNITS") or 1)), tick_size=float(r.get("SEM_TICK_SIZE") or 0.05))

    def search_instruments(self, query: str, exchange: str = "NSE") -> list[Instrument]:
        q = query.upper()
        return [self._to_inst(r) for r in self._scrips() if q in r.get("SEM_TRADING_SYMBOL", "").upper()][:50]

    def index_instrument(self, underlying: str) -> Instrument:
        return Instrument(symbol=underlying.upper(), token=INDEX[underlying.upper()], exchange="IDX_I", name=underlying.upper(), instrument_type="INDEX")

    def resolve_option(self, underlying: str, expiry: date, strike: float, option_type: str) -> Instrument:
        cands = [self._to_inst(r) for r in self._scrips() if r.get("SEM_INSTRUMENT_NAME") == "OPTIDX" and r.get("SEM_TRADING_SYMBOL", "").startswith(underlying.upper() + "-")
                 and r.get("SEM_OPTION_TYPE") == option_type and abs(float(r.get("SEM_STRIKE_PRICE") or 0) - strike) < 0.01]
        if not cands:
            raise BrokerError(f"No {underlying} {strike} {option_type} near {expiry}")
        return pick_contract(cands, expiry)

    def historical_candles(self, instrument: Instrument, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        if timeframe not in TF:
            raise BrokerError(f"Dhan intraday supports {list(TF)}; resample 5m for {timeframe}")
        inst_type = "INDEX" if instrument.exchange == "IDX_I" else ("OPTIDX" if instrument.option_type else "EQUITY")
        d = self._ok(self._client().intraday_minute_data(instrument.token, instrument.exchange, inst_type, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"), TF[timeframe]), "intraday_minute_data") or {}
        rows = list(zip(d.get("timestamp", []), d.get("open", []), d.get("high", []), d.get("low", []), d.get("close", []), d.get("volume", [0] * len(d.get("open", [])))))
        return candles_frame(rows, ts_unit="s")

    def ltp(self, instrument: Instrument) -> float:
        d = self._ok(self._client().ticker_data({instrument.exchange: [int(instrument.token)]}), "ltp") or {}
        inner = d.get("data", d)
        return float(inner[instrument.exchange][str(instrument.token)]["last_price"])

    def place_order(self, req: OrderRequest) -> OrderResult:
        c = self._client()
        resp = c.place_order(security_id=req.instrument.token, exchange_segment=req.instrument.exchange,
                             transaction_type=c.BUY if req.side == "BUY" else c.SELL, quantity=req.quantity,
                             order_type={"MARKET": c.MARKET, "LIMIT": c.LIMIT, "SL": c.SL, "SL-M": c.SLM}[req.order_type],
                             product_type={"INTRADAY": c.INTRA, "DELIVERY": c.CNC, "CARRYFORWARD": c.MARGIN}[req.product],
                             price=req.price, trigger_price=req.trigger_price, tag=sanitize_tag(req.tag, 25))
        d = self._ok(resp, "place_order") or {}
        return OrderResult(order_id=str(d.get("orderId", "")), status=d.get("orderStatus", "submitted").lower(), raw=d)

    def cancel_order(self, order_id: str) -> OrderResult:
        self._ok(self._client().cancel_order(order_id), "cancel_order")
        return OrderResult(order_id=order_id, status="cancelled")

    def positions(self) -> list[dict[str, Any]]:
        rows = self._ok(self._client().get_positions(), "positions") or []
        return [{"symbol": r.get("tradingSymbol"), "net_qty": int(r.get("netQty", 0)), "avg_price": float(r.get("buyAvg") or 0),
                 "pnl": float(r.get("realizedProfit", 0)) + float(r.get("unrealizedProfit", 0)), "product": r.get("productType"),
                 "instrument": Instrument(symbol=r.get("tradingSymbol", ""), token=str(r.get("securityId", "")), exchange=r.get("exchangeSegment", ""))} for r in rows]

    def order_book(self) -> list[dict[str, Any]]:
        rows = self._ok(self._client().get_order_list(), "orders") or []
        return [{"order_id": r.get("orderId"), "symbol": r.get("tradingSymbol"), "side": r.get("transactionType"), "qty": r.get("quantity"),
                 "status": r.get("orderStatus"), "avg_price": r.get("price"), "tag": r.get("correlationId"), "time": r.get("createTime"), "raw": r} for r in rows]
