"""Event-driven backtester on candles.

Signals are evaluated at each bar close (same as the live engine, which runs on candle close).
Fills happen at the signal bar's close plus slippage. Stops/targets are checked intrabar using
the underlying's high/low mapped to premium, conservatively: the stop is assumed to trade before
the target in the same bar, a bar that *opens* through a stop/target fills at the open (gap), and
a trailing stop only ratchets after the bar is checked (no same-bar high-then-low hindsight).
Intraday positions are always squared off at the hard-exit time or the last bar of the day.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Literal, Optional

import pandas as pd

from ..data.candles import TF_MINUTES
from ..strategy.dsl import Strategy
from ..strategy.evaluate import bar_close_datetimes, build_signals
from . import options as opt
from .costs import CostBreakdown, CostConfig, apply_slippage, order_costs, segment_for
from .metrics import compute_metrics

OptionPriceFn = Callable[[str, datetime], Optional[float]]


@dataclass
class Leg:
    symbol: str
    kind: Literal["CE", "PE", "FUT", "EQ"]
    side: Literal["buy", "sell"]
    qty: int
    strike: Optional[int] = None
    expiry: Optional[object] = None
    entry_ref: float = 0.0  # price before slippage
    entry_fill: float = 0.0
    exit_ref: float = 0.0
    exit_fill: float = 0.0
    open: bool = True
    exit_reason: str = ""
    peak: float = 0.0  # best favourable price, for trailing SL

    @property
    def sign(self) -> int:
        return 1 if self.side == "buy" else -1


@dataclass
class Position:
    direction: Literal["long", "short"]
    entry_time: datetime
    entry_index: int
    entry_spot: float
    legs: list[Leg]
    costs: CostBreakdown = field(default_factory=CostBreakdown)
    slippage: float = 0.0


@dataclass
class BacktestResult:
    trades: list[dict]
    metrics: dict
    equity: list[dict]
    flags: list[str]
    data_source: str
    bars: int

    def to_dict(self) -> dict:
        return {"trades": self.trades, "metrics": self.metrics, "equity": self.equity, "flags": self.flags, "data_source": self.data_source, "bars": self.bars}


class Backtester:
    def __init__(
        self,
        strategy: Strategy,
        cost_cfg: CostConfig | None = None,
        iv: float | None = None,
        option_price_fn: OptionPriceFn | None = None,
        whipsaw_bars: int = 2,
    ):
        self.s = strategy
        self.cfg = cost_cfg or CostConfig()
        self.iv = iv
        self.option_price_fn = option_price_fn
        self.whipsaw_bars = whipsaw_bars
        self.segment = segment_for(strategy.instrument_type, strategy.intraday)
        self.flags: set[str] = set()

    # ---------------- pricing ----------------
    def _leg_price(self, leg: Leg, spot: float, ts: datetime) -> float:
        if leg.kind in ("CE", "PE"):
            if self.option_price_fn:
                p = self.option_price_fn(leg.symbol, ts)
                if p is not None:
                    return p
            self.flags.add(opt.PROXY_FLAG)
            exp = leg.expiry
            return max(0.05, round(opt.bs_price(spot, leg.strike, opt.years_to_expiry(ts, exp), self.iv or opt.R.DEFAULT_IV, opt.R.RISK_FREE, leg.kind), 2))
        if leg.kind == "FUT":
            self.flags.add("futures_priced_at_spot")
        return round(spot, 2)

    def _qty(self) -> int:
        if self.s.instrument_type in ("option", "future", "index"):
            return self.s.risk.lots * opt.lot_size(self.s.underlying)
        return self.s.risk.lots

    def _open(self, direction: str, ts: datetime, i: int, spot: float) -> Position:
        s = self.s
        qty = self._qty()
        legs: list[Leg] = []
        if s.instrument_type == "option":
            step = opt.strike_step(s.underlying)
            expiry = opt.next_expiry(s.underlying, ts.date(), s.option.expiry)
            for t in opt.legs_for(s.option.mode, direction, s.option.strike_steps):
                strike = opt.select_strike(spot, step, t.opt_type, t.moneyness_steps)
                legs.append(Leg(opt.option_symbol(s.underlying, expiry, strike, t.opt_type), t.opt_type, t.side, qty, strike, expiry))
        else:
            kind = "FUT" if s.instrument_type in ("future", "index") else "EQ"
            side = "buy" if direction == "long" else "sell"
            legs.append(Leg(s.underlying + ("-FUT" if kind == "FUT" else ""), kind, side, qty))
        pos = Position(direction, ts, i, spot, legs)
        for leg in legs:
            ref = self._leg_price(leg, spot, ts)
            fill = apply_slippage(ref, leg.side, self.cfg.slippage_for(self.segment))
            leg.entry_ref, leg.entry_fill, leg.peak = ref, fill, ref
            pos.costs = pos.costs + order_costs(leg.side, fill, qty, self.segment, s.exchange, ts.date(), self.cfg)
            pos.slippage += abs(fill - ref) * qty
        return pos

    def _close_leg(self, pos: Position, leg: Leg, ref: float, ts: datetime, reason: str) -> None:
        exit_side = "sell" if leg.side == "buy" else "buy"
        fill = apply_slippage(ref, exit_side, self.cfg.slippage_for(self.segment))
        leg.exit_ref, leg.exit_fill, leg.open, leg.exit_reason = ref, fill, False, reason
        pos.costs = pos.costs + order_costs(exit_side, fill, leg.qty, self.segment, self.s.exchange, ts.date(), self.cfg)
        pos.slippage += abs(fill - ref) * leg.qty

    def _check_stops(self, pos: Position, bar: pd.Series, ts: datetime) -> None:
        sl, tgt, trail = self.s.exit.stop_loss_pct, self.s.exit.target_pct, self.s.exit.trailing_sl_pct
        if not (sl or tgt or trail):
            return
        for leg in pos.legs:
            if not leg.open:
                continue
            # adverse / favourable underlying extreme for this leg
            # a leg gains when the underlying rises if: long CE/FUT/EQ or short PE
            bullish_leg = (leg.kind in ("CE", "FUT", "EQ")) == (leg.side == "buy")
            adverse_spot = bar["low"] if bullish_leg else bar["high"]
            fav_spot = bar["high"] if bullish_leg else bar["low"]
            adverse_px = self._leg_price(leg, adverse_spot, ts)
            fav_px = self._leg_price(leg, fav_spot, ts)
            open_px = self._leg_price(leg, float(bar["open"]), ts)
            e = leg.entry_ref
            # stop loss (checked first: conservative). Trailing uses the peak *before* this bar.
            stop_px, reason = None, "stop_loss"
            if sl:
                stop_px = e * (1 - sl / 100) if leg.sign > 0 else e * (1 + sl / 100)
            if trail:
                tpx = leg.peak * (1 - trail / 100) if leg.sign > 0 else leg.peak * (1 + trail / 100)
                tighter = stop_px is None or (tpx > stop_px if leg.sign > 0 else tpx < stop_px)
                if tighter:
                    stop_px, reason = tpx, "trailing_stop"
            if stop_px is not None and ((leg.sign > 0 and adverse_px <= stop_px) or (leg.sign < 0 and adverse_px >= stop_px)):
                # a bar that opens beyond the stop (gap) fills at the open, not at the stop
                fill = min(stop_px, open_px) if leg.sign > 0 else max(stop_px, open_px)
                self._close_leg(pos, leg, round(fill, 2), ts, reason)
                continue
            if tgt:
                tgt_px = e * (1 + tgt / 100) if leg.sign > 0 else e * (1 - tgt / 100)
                if (leg.sign > 0 and fav_px >= tgt_px) or (leg.sign < 0 and fav_px <= tgt_px):
                    fill = max(tgt_px, open_px) if leg.sign > 0 else min(tgt_px, open_px)
                    self._close_leg(pos, leg, round(fill, 2), ts, "target")
                    continue
            if trail:  # ratchet only after this bar survived
                leg.peak = max(leg.peak, fav_px) if leg.sign > 0 else min(leg.peak, fav_px)

    def _mtm(self, pos: Position, spot: float, ts: datetime) -> float:
        pnl = 0.0
        for leg in pos.legs:
            px = leg.exit_ref if not leg.open else self._leg_price(leg, spot, ts)
            pnl += (px - leg.entry_ref) * leg.sign * leg.qty
        return pnl - pos.costs.total - pos.slippage

    def _finish(self, pos: Position, ts: datetime, i: int, spot: float, reason: str) -> dict:
        for leg in pos.legs:
            if leg.open:
                self._close_leg(pos, leg, self._leg_price(leg, spot, ts), ts, reason)
        gross = sum((l.exit_ref - l.entry_ref) * l.sign * l.qty for l in pos.legs)
        charges = pos.costs.total
        net = gross - charges - pos.slippage
        reasons = {l.exit_reason for l in pos.legs}
        return {
            "entry_time": pos.entry_time.isoformat(),
            "exit_time": ts.isoformat(),
            "date": pos.entry_time.date().isoformat(),
            "direction": pos.direction,
            "instrument": " + ".join(l.symbol for l in pos.legs),
            "side": "/".join(sorted({l.side for l in pos.legs})),
            "legs": [
                {"symbol": l.symbol, "type": l.kind, "side": l.side, "qty": l.qty, "strike": l.strike,
                 "entry": l.entry_fill, "exit": l.exit_fill, "entry_ref": l.entry_ref, "exit_ref": l.exit_ref, "exit_reason": l.exit_reason}
                for l in pos.legs
            ],
            "entry_price": round(sum(l.entry_fill for l in pos.legs), 2),
            "exit_price": round(sum(l.exit_fill for l in pos.legs), 2),
            "entry_spot": round(pos.entry_spot, 2),
            "exit_spot": round(spot, 2),
            "qty": pos.legs[0].qty,
            "bars_held": i - pos.entry_index,
            "gross_pnl": round(gross, 2),
            "charges": pos.costs.as_dict(),
            "slippage": round(pos.slippage, 2),
            "costs_total": round(charges + pos.slippage, 2),
            "net_pnl": round(net, 2),
            "exit_reason": reasons.pop() if len(reasons) == 1 else "+".join(sorted(reasons)),
        }

    # ---------------- main loop ----------------
    def run(self, candles: pd.DataFrame, data_source: str = "synthetic") -> BacktestResult:
        s = self.s
        if candles.empty:
            raise ValueError("no candles to backtest")
        sig = build_signals(candles, s)
        f = sig.frame
        idx = f.index
        days = idx.normalize()
        last_of_day = pd.Series(days, index=idx).shift(-1) != pd.Series(days, index=idx)
        trades: list[dict] = []
        pos: Position | None = None
        cur_day = None
        trades_today = 0
        pnl_today = 0.0
        halted = False
        win_start, win_end, hard = s.entry_window.start, s.entry_window.end, s.hard_exit

        close_dt = bar_close_datetimes(idx, s.timeframe)
        daily = TF_MINUTES[s.timeframe] >= 1440
        session_days = sorted({d.date().isoformat() for d in days})
        for i in range(len(f)):
            ts = close_dt[i].to_pydatetime()  # decisions happen at bar close (capped at 15:30)
            bar = f.iloc[i]
            spot = float(bar["close"])
            ct = sig.close_time.iloc[i]
            if days[i] != cur_day:
                cur_day, trades_today, pnl_today, halted = days[i], 0, 0.0, False

            # ---- manage open position ----
            if pos is not None:
                if i > pos.entry_index:
                    self._check_stops(pos, bar, ts)
                reason = None
                if all(not l.open for l in pos.legs):
                    reason = pos.legs[0].exit_reason
                elif s.intraday and (ct >= hard or bool(last_of_day.iloc[i])):
                    reason = "hard_exit" if ct >= hard else "end_of_day"
                elif pos.direction == "long" and bool(sig.long_exit.iloc[i]):
                    reason = "exit_signal"
                elif pos.direction == "short" and bool(sig.short_exit.iloc[i]):
                    reason = "exit_signal"
                elif s.risk.daily_loss_cap and pnl_today + self._mtm(pos, spot, ts) <= -s.risk.daily_loss_cap:
                    reason, halted = "daily_loss_cap", True
                if reason:
                    t = self._finish(pos, ts, i, spot, reason)
                    trades.append(t)
                    pnl_today += t["net_pnl"]
                    trades_today += 1
                    pos = None
                    if s.risk.daily_loss_cap and pnl_today <= -s.risk.daily_loss_cap:
                        halted = True

            # ---- look for entries ----
            if pos is None and not halted and trades_today < s.risk.max_trades_per_day:
                if daily:  # one bar per day: entry-window/hard-exit clock rules do not apply
                    in_window = i < len(f) - 1
                else:
                    in_window = win_start <= ct <= win_end and ct < hard and not (s.intraday and bool(last_of_day.iloc[i]))
                if in_window:
                    if s.direction in ("long", "both", "neutral") and bool(sig.long_entry.iloc[i]):
                        pos = self._open("long", ts, i, spot)
                    elif s.direction in ("short", "both") and bool(sig.short_entry.iloc[i]):
                        pos = self._open("short", ts, i, spot)

        if pos is not None:  # close anything left at the final bar
            trades.append(self._finish(pos, close_dt[-1].to_pydatetime(), len(f) - 1, float(f["close"].iloc[-1]), "end_of_data"))

        metrics, equity = compute_metrics(trades, s.risk.capital, self.whipsaw_bars, session_days=session_days)
        if data_source == "synthetic":
            self.flags.add("synthetic_underlying_data")
        metrics["period"] = {"from": idx[0].isoformat(), "to": idx[-1].isoformat(), "bars": len(f), "timeframe": s.timeframe}
        return BacktestResult(trades, metrics, equity, sorted(self.flags), data_source, len(f))


def run_backtest(strategy: Strategy, candles: pd.DataFrame, data_source: str = "synthetic", **kw) -> BacktestResult:
    return Backtester(strategy, **kw).run(candles, data_source)
