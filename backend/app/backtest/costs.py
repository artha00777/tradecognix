"""Indian transaction cost model (per order leg). Rates live in rates.py."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date
from typing import Literal, Optional

from . import rates as R

Segment = Literal["options", "futures", "equity_intraday", "equity_delivery"]


# Default slippage per fill, as % of the traded price, by segment. Option premiums are small and
# spreads wide, so 0.5% of premium is realistic. Index futures trade at ~0.25-1 tick of spread on
# a price near 25,000, so 0.5% (~125 points a side) wildly overstated costs; 0.01% is ~2.5 points.
DEFAULT_SLIPPAGE_PCT: dict[str, float] = {
    "options": 0.5,
    "futures": 0.01,
    "equity_intraday": 0.05,
    "equity_delivery": 0.1,
}


@dataclass
class CostConfig:
    brokerage_per_order: float = R.BROKERAGE_FLAT_PER_ORDER
    slippage_pct: Optional[float] = None  # % of price per fill against you; None = per-segment default
    stt_override: Optional[float] = None  # fraction; None = date-aware schedule
    exchange_override: Optional[float] = None
    include_gst: bool = True

    def slippage_for(self, segment: str) -> float:
        if self.slippage_pct is not None:
            return self.slippage_pct
        return DEFAULT_SLIPPAGE_PCT.get(segment, 0.05)


@dataclass
class CostBreakdown:
    brokerage: float = 0.0
    stt: float = 0.0
    exchange: float = 0.0
    sebi: float = 0.0
    stamp: float = 0.0
    gst: float = 0.0

    @property
    def total(self) -> float:
        return self.brokerage + self.stt + self.exchange + self.sebi + self.stamp + self.gst

    def __add__(self, o: "CostBreakdown") -> "CostBreakdown":
        return CostBreakdown(*(a + b for a, b in zip(asdict(self).values(), asdict(o).values())))

    def as_dict(self) -> dict:
        d = {k: round(v, 2) for k, v in asdict(self).items()}
        d["total"] = round(self.total, 2)
        return d


def order_costs(
    side: Literal["buy", "sell"],
    price: float,
    quantity: int,
    segment: Segment = "options",
    exchange: str = "NSE",
    trade_date: date | None = None,
    cfg: CostConfig | None = None,
) -> CostBreakdown:
    """Statutory + brokerage charges for ONE order (one leg, one side). Rates: rates.py.

    GST (18%) applies to brokerage + exchange transaction charges + SEBI fee; STT and stamp duty
    are not GST-able. Stamp duty is buy-side only; STT is sell-side only except equity delivery.
    """
    cfg = cfg or CostConfig()
    if trade_date is None:
        from ..engine.clock import ist_today

        trade_date = ist_today()
    if quantity < 0:
        raise ValueError("quantity must be >= 0")
    turnover = abs(price) * quantity

    if segment == "equity_delivery":
        brokerage = R.BROKERAGE_DELIVERY
    elif segment in R.BROKERAGE_PCT_CAP:
        brokerage = min(cfg.brokerage_per_order, turnover * R.BROKERAGE_PCT_CAP[segment])
    else:
        brokerage = cfg.brokerage_per_order

    if cfg.stt_override is not None:
        stt_r = cfg.stt_override if (side == "sell" or segment == "equity_delivery") else 0.0
    elif segment == "options":
        stt_r = R.stt_rate("options_sell", trade_date) if side == "sell" else 0.0
    elif segment == "futures":
        stt_r = R.stt_rate("futures_sell", trade_date) if side == "sell" else 0.0
    elif segment == "equity_intraday":
        stt_r = R.stt_rate("equity_intraday_sell", trade_date) if side == "sell" else 0.0
    else:
        stt_r = R.stt_rate("equity_delivery_both", trade_date)
    stt = turnover * stt_r

    exch_r = cfg.exchange_override if cfg.exchange_override is not None else R.EXCHANGE_TXN.get((exchange, segment), 0.0) + R.IPFT[segment]
    exchange_chg = turnover * exch_r
    sebi = turnover * R.SEBI_FEE
    stamp = turnover * R.STAMP_BUY[segment] if side == "buy" else 0.0
    gst = (brokerage + exchange_chg + sebi) * R.GST if cfg.include_gst else 0.0
    return CostBreakdown(brokerage, stt, exchange_chg, sebi, stamp, gst)


def apply_slippage(price: float, side: Literal["buy", "sell"], slippage_pct: float, tick: float = 0.05) -> float:
    """Worsen a fill by slippage_pct and round to the exchange tick *against* the trader
    (buys round up, sells round down), never below one tick."""
    tick = tick or 0.05
    adj = price * (1 + slippage_pct / 100) if side == "buy" else price * (1 - slippage_pct / 100)
    steps = adj / tick
    steps = math.ceil(steps - 1e-9) if side == "buy" else math.floor(steps + 1e-9)
    return round(max(tick, steps * tick), 2)


def segment_for(instrument_type: str, intraday: bool = True) -> Segment:
    if instrument_type == "option":
        return "options"
    if instrument_type in ("future", "index"):
        return "futures"
    return "equity_intraday" if intraday else "equity_delivery"
