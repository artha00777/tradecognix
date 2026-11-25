"""PaperBroker: simulates fills at LTP with slippage and Indian costs. No real orders.

Prices come from a pluggable `price_source(instrument) -> float`: typically a real broker's ltp()
(paper trading on live data), or a synthetic/Black-Scholes source for demos and tests.
"""
from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from ..backtest.costs import CostConfig, apply_slippage, order_costs
from ..engine.clock import ist_now, ist_today
from .base import BrokerAdapter, BrokerError, Instrument, OrderRequest, OrderResult

PriceSource = Callable[[Instrument], float]


@dataclass
class PaperPosition:
    instrument: Instrument
    net_qty: int = 0
    avg_price: float = 0.0
    realised: float = 0.0
    costs: float = 0.0
    last_price: float = 0.0


class PaperBroker(BrokerAdapter):
    key = "paper"
    display_name = "Paper trading"
    api_name = "Simulator"
    status = "live"
    credential_fields = []

    def __init__(self, credentials=None, price_source: PriceSource | None = None, slippage_pct: float | None = None,
                 starting_capital: float = 500000.0, data_broker: BrokerAdapter | None = None):
        super().__init__(credentials)
        self.data_broker = data_broker
        self.price_source = price_source or (data_broker.ltp if data_broker else None)
        self.cost_cfg = CostConfig(slippage_pct=slippage_pct)
        self.cash = starting_capital
        self.starting_capital = starting_capital
        self._positions: dict[str, PaperPosition] = {}
        self._orders: list[dict[str, Any]] = []
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def login(self):
        self.session = {"paper": True}
        return {"ok": True, "paper": True}

    def profile(self):
        return {"client_code": "PAPER", "name": "Paper account"}

    def funds(self):
        mtm = sum((p.last_price - p.avg_price) * p.net_qty for p in self._positions.values() if p.net_qty)
        return {"available": round(self.cash, 2), "used": 0.0, "mtm": round(mtm, 2), "raw": {}}

    def _price(self, inst: Instrument) -> float:
        if self.price_source is None:
            raise BrokerError("PaperBroker has no price source")
        return float(self.price_source(inst))

    def historical_candles(self, instrument, timeframe, start, end) -> pd.DataFrame:
        if self.data_broker:
            return self.data_broker.historical_candles(instrument, timeframe, start, end)
        raise BrokerError("PaperBroker has no historical data source")

    def ltp(self, instrument: Instrument) -> float:
        return self._price(instrument)

    def search_instruments(self, query, exchange="NSE"):
        if self.data_broker:
            return self.data_broker.search_instruments(query, exchange)
        return [Instrument(symbol=query.upper(), token=query.upper(), exchange=exchange)]

    def index_instrument(self, underlying):
        if self.data_broker:
            return self.data_broker.index_instrument(underlying)
        return Instrument(symbol=underlying.upper(), token=underlying.upper(), exchange="NSE", name=underlying.upper(), instrument_type="INDEX")

    def resolve_option(self, underlying, expiry, strike, option_type):
        if self.data_broker:
            return self.data_broker.resolve_option(underlying, expiry, strike, option_type)
        from ..backtest.options import lot_size, option_symbol

        sym = option_symbol(underlying, expiry, int(strike), option_type)
        return Instrument(symbol=sym, token=sym, exchange="NFO", name=underlying.upper(), instrument_type="OPTIDX", expiry=expiry,
                          strike=strike, option_type=option_type, lot_size=lot_size(underlying))

    def place_order(self, req: OrderRequest) -> OrderResult:
        if req.quantity <= 0:
            return OrderResult(order_id="", status="rejected", message="quantity must be positive")
        with self._lock:
            try:
                ref = self._price(req.instrument)
            except Exception as e:
                return OrderResult(order_id="", status="rejected", message=f"no price: {e}")
            if not ref or ref <= 0:
                return OrderResult(order_id="", status="rejected", message="no valid price")
            side = "buy" if req.side == "BUY" else "sell"
            segment = "options" if req.instrument.option_type else ("futures" if "FUT" in req.instrument.instrument_type else "equity_intraday")
            fill = apply_slippage(ref, side, self.cost_cfg.slippage_for(segment), req.instrument.tick_size or 0.05)
            exch = "BSE" if req.instrument.exchange in ("BSE", "BFO") else "NSE"
            costs = order_costs(side, fill, req.quantity, segment, exch, ist_today(), self.cost_cfg).total
            key = req.instrument.symbol
            pos = self._positions.setdefault(key, PaperPosition(req.instrument))
            signed = req.quantity if req.side == "BUY" else -req.quantity
            # realise P&L on the closing part
            if pos.net_qty and (pos.net_qty > 0) != (signed > 0):
                closing = min(abs(signed), abs(pos.net_qty))
                direction = 1 if pos.net_qty > 0 else -1
                pos.realised += (fill - pos.avg_price) * closing * direction
                new_qty = pos.net_qty + signed
                if new_qty == 0:
                    pos.avg_price = 0.0
                elif (new_qty > 0) != (pos.net_qty > 0):
                    pos.avg_price = fill
                pos.net_qty = new_qty
            else:
                total = abs(pos.net_qty) + abs(signed)
                pos.avg_price = (pos.avg_price * abs(pos.net_qty) + fill * abs(signed)) / total
                pos.net_qty += signed
            pos.costs += costs
            pos.last_price = fill
            self.cash -= signed * fill + costs
            oid = f"PAPER{next(self._ids):06d}"
            self._orders.append({"order_id": oid, "symbol": key, "side": req.side, "qty": req.quantity, "status": "complete", "avg_price": fill,
                                 "ref_price": ref, "costs": round(costs, 2), "tag": req.tag, "time": ist_now().isoformat(timespec="seconds")})
            return OrderResult(order_id=oid, status="filled", avg_price=fill, raw={"costs": costs, "ref_price": ref})

    def cancel_order(self, order_id: str) -> OrderResult:
        return OrderResult(order_id=order_id, status="rejected", message="Paper orders fill instantly; nothing to cancel")

    def positions(self) -> list[dict[str, Any]]:
        out = []
        for p in self._positions.values():
            try:
                p.last_price = self._price(p.instrument) if p.net_qty else p.last_price
            except Exception:
                pass
            unreal = (p.last_price - p.avg_price) * p.net_qty if p.net_qty else 0.0
            out.append({"symbol": p.instrument.symbol, "net_qty": p.net_qty, "avg_price": round(p.avg_price, 2), "ltp": p.last_price,
                        "realised": round(p.realised, 2), "unrealised": round(unreal, 2), "costs": round(p.costs, 2),
                        "pnl": round(p.realised + unreal - p.costs, 2), "instrument": p.instrument})
        return out

    def order_book(self) -> list[dict[str, Any]]:
        return list(self._orders)
