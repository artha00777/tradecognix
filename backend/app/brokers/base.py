"""Broker adapter interface. Every Indian broker integration implements BrokerAdapter.

Conventions
- Times are IST wall-clock, tz-naive. Candle frames: index=timestamp, columns open/high/low/close/volume.
- `Instrument.token` is the broker's own instrument id (symboltoken, instrument_token, security id...).
- Orders default to MARKET / INTRADAY. Every order carries a `tag` (strategy tag) for SEBI-style
  traceability and the trade journal.
- SDK imports happen inside methods so the app runs without every broker SDK installed.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Optional

import pandas as pd


class BrokerError(RuntimeError):
    """A broker call failed (auth, network, rejection)."""


class BrokerNotImplemented(NotImplementedError):
    """Adapter is a documented skeleton; contributions welcome."""


@dataclass
class CredentialField:
    key: str
    label: str
    secret: bool = False
    required: bool = True
    help: str = ""


@dataclass
class Instrument:
    symbol: str
    token: str
    exchange: str
    name: str = ""
    instrument_type: str = ""  # INDEX | EQ | FUTIDX | OPTIDX | OPTSTK ...
    expiry: Optional[date] = None
    strike: Optional[float] = None
    option_type: Optional[str] = None  # CE | PE
    lot_size: int = 1
    tick_size: float = 0.05

    def as_dict(self) -> dict:
        d = asdict(self)
        d["expiry"] = self.expiry.isoformat() if self.expiry else None
        return d


@dataclass
class OrderRequest:
    instrument: Instrument
    side: Literal["BUY", "SELL"]
    quantity: int
    order_type: Literal["MARKET", "LIMIT", "SL", "SL-M"] = "MARKET"
    product: Literal["INTRADAY", "DELIVERY", "CARRYFORWARD"] = "INTRADAY"
    price: float = 0.0
    trigger_price: float = 0.0
    tag: str = ""


@dataclass
class OrderResult:
    order_id: str
    status: str = "submitted"  # submitted | filled | rejected | cancelled
    message: str = ""
    avg_price: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


FILLED_WORDS = {"filled", "complete", "completed", "traded", "executed"}
REJECTED_WORDS = {"rejected", "failed", "error", "expired"}
CANCELLED_WORDS = {"cancelled", "canceled"}


def normalize_status(status: str | None) -> str:
    """Map broker-specific order states to: filled | rejected | cancelled | pending."""
    s = (status or "").strip().lower().replace(" ", "_")
    if s in FILLED_WORDS:
        return "filled"
    if s in REJECTED_WORDS:
        return "rejected"
    if s in CANCELLED_WORDS:
        return "cancelled"
    return "pending"


def pick_contract(candidates: list["Instrument"], expiry: date, today: date | None = None) -> "Instrument":
    """Choose the live contract whose expiry is closest to the requested one.

    The expiry calendar ignores exchange holidays (a holiday moves expiry a day *earlier*), so
    "first expiry on/after the computed date" would silently pick next week's contract. Expired
    contracts (before today, IST) are never returned.
    """
    if today is None:
        from ..engine.clock import ist_today

        today = ist_today()
    live = [c for c in candidates if c.expiry and c.expiry >= today]
    if not live:
        raise BrokerError(f"No unexpired contract found near {expiry}")
    return min(live, key=lambda c: (abs((c.expiry - expiry).days), c.expiry))


def sanitize_tag(tag: str, max_len: int = 20) -> str:
    """Brokers restrict tags (Angel/Kite: <=20 chars, alphanumeric)."""
    return re.sub(r"[^A-Za-z0-9]", "", tag)[:max_len]


TIMEFRAMES = ("1m", "3m", "5m", "10m", "15m", "30m", "1h", "1d")


class BrokerAdapter(ABC):
    key: str = "base"
    display_name: str = "Broker"
    api_name: str = ""
    status: Literal["live", "beta", "skeleton"] = "skeleton"
    credential_fields: list[CredentialField] = []
    docs_url: str = ""

    def __init__(self, credentials: dict[str, Any] | None = None):
        self.credentials = credentials or {}
        self.session: dict[str, Any] = {}

    # ---- metadata ----
    @classmethod
    def meta(cls) -> dict:
        return {
            "key": cls.key, "name": cls.display_name, "api": cls.api_name, "status": cls.status, "docs": cls.docs_url,
            "fields": [asdict(f) for f in cls.credential_fields],
        }

    def require(self, *keys: str) -> None:
        missing = [k for k in keys if not self.credentials.get(k)]
        if missing:
            raise BrokerError(f"{self.display_name}: missing credentials {', '.join(missing)}")

    # ---- session ----
    @abstractmethod
    def login(self) -> dict[str, Any]:
        """Authenticate and return session info (tokens are kept on self.session)."""

    def logout(self) -> None:
        self.session = {}

    # ---- account ----
    @abstractmethod
    def profile(self) -> dict[str, Any]: ...

    @abstractmethod
    def funds(self) -> dict[str, Any]:
        """Return at least {'available': float, 'used': float, 'raw': {...}}."""

    # ---- market data ----
    @abstractmethod
    def historical_candles(self, instrument: Instrument, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame: ...

    @abstractmethod
    def ltp(self, instrument: Instrument) -> float: ...

    @abstractmethod
    def search_instruments(self, query: str, exchange: str = "NSE") -> list[Instrument]: ...

    def index_instrument(self, underlying: str) -> Instrument:
        raise BrokerNotImplemented(f"{self.display_name}: index lookup not implemented")

    def resolve_option(self, underlying: str, expiry: date, strike: float, option_type: str) -> Instrument:
        raise BrokerNotImplemented(f"{self.display_name}: option lookup not implemented")

    # ---- orders ----
    @abstractmethod
    def place_order(self, req: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> OrderResult: ...

    @abstractmethod
    def positions(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def order_book(self) -> list[dict[str, Any]]: ...

    def fetch_order(self, order_id: str) -> dict[str, Any] | None:
        """Latest state of one order: {'status': filled|rejected|cancelled|pending, 'avg_price', 'message'}.

        Default implementation scans order_book(); adapters with a single-order endpoint may override.
        """
        for o in self.order_book():
            if str(o.get("order_id")) == str(order_id):
                raw = o.get("raw") or {}
                msg = raw.get("text") or raw.get("status_message") or raw.get("omsErrorDescription") or raw.get("message") or ""
                return {"status": normalize_status(o.get("status")), "avg_price": float(o.get("avg_price") or 0), "message": str(msg)}
        return None

    # ---- helpers ----
    def square_off_all(self, tag: str = "KILL") -> list[OrderResult]:
        """Close every open intraday position with market orders (used by the kill switch)."""
        results = []
        for p in self.positions():
            qty = int(p.get("net_qty", 0))
            if qty == 0 or "instrument" not in p:
                continue
            side = "SELL" if qty > 0 else "BUY"
            results.append(self.place_order(OrderRequest(p["instrument"], side, abs(qty), tag=tag)))
        return results


def candles_frame(rows: list[list[Any]] | list[tuple], ts_unit: str | None = None) -> pd.DataFrame:
    """Build a standard candle frame from [ts, o, h, l, c, v] rows; converts to IST naive.

    Strings with an offset ("2026-11-20T09:15:00+05:30") and epoch numbers (UTC) are both handled.
    """
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex([]), dtype=float)
    df = pd.DataFrame([r[:6] for r in rows], columns=["ts", "open", "high", "low", "close", "volume"])
    ts = pd.to_datetime(df["ts"], unit=ts_unit) if ts_unit else pd.to_datetime(df["ts"], utc=_mixed_offsets(df["ts"]))
    if ts.dt.tz is not None:
        ts = ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    elif ts_unit:  # epoch seconds are UTC
        ts = ts.dt.tz_localize("UTC").dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df.index = pd.DatetimeIndex(ts)
    df.index.name = None
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def _mixed_offsets(ts: pd.Series) -> bool:
    """Strings carrying UTC offsets must be parsed with utc=True (pandas refuses mixed offsets)."""
    sample = ts.iloc[0]
    return isinstance(sample, str) and bool(re.search(r"(Z|[+-]\d{2}:?\d{2})$", sample.strip()))
