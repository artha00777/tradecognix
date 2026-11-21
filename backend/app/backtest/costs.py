"""Indian transaction cost model (per order leg). Rates live in rates.py."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Literal, Optional

from . import rates as R

Segment = Literal["options", "futures", "equity_intraday", "equity_delivery"]


@dataclass
class CostConfig:
    brokerage_per_order: float = R.BROKERAGE_FLAT_PER_ORDER
    slippage_pct: float = 0.5  # % of price per fill, applied against you
    stt_override: Optional[float] = None  # fraction; None = date-aware schedule
    exchange_override: Optional[float] = None
    include_gst: bool = True


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
    cfg = cfg or CostConfig()
    trade_date = trade_date or date.today()
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
    """Worsen a fill by slippage_pct and round to the exchange tick."""
    adj = price * (1 + slippage_pct / 100) if side == "buy" else price * (1 - slippage_pct / 100)
    adj = max(tick, adj)
    return round(round(adj / tick) * tick, 2)


def segment_for(instrument_type: str, intraday: bool = True) -> Segment:
    if instrument_type == "option":
        return "options"
    if instrument_type in ("future", "index"):
        return "futures"
    return "equity_intraday" if intraday else "equity_delivery"
