"""StrategyRunner: executes one Strategy against one broker on candle close.

Pure logic with injected broker + callbacks, so the same code runs paper, live and tests.
State is a JSON-serialisable dict so sessions survive restarts (persisted on LiveSession.state).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable, Optional

import pandas as pd

from ..backtest import options as opt
from ..backtest.costs import order_costs, segment_for
from ..brokers.base import BrokerAdapter, Instrument, OrderRequest
from ..data.candles import TF_MINUTES
from ..strategy.dsl import Strategy
from ..strategy.evaluate import build_signals
from .guardrails import Guardrails

EventCb = Callable[[dict], None]


def _inst_to_dict(i: Instrument) -> dict:
    return i.as_dict()


def _inst_from_dict(d: dict) -> Instrument:
    d = dict(d)
    if d.get("expiry"):
        d["expiry"] = date.fromisoformat(d["expiry"])
    return Instrument(**d)


class StrategyRunner:
    def __init__(self, strategy: Strategy, broker: BrokerAdapter, tag: str, mode: str = "paper",
                 state: Optional[dict] = None, on_order: EventCb | None = None, on_trade: EventCb | None = None, on_event: EventCb | None = None):
        self.s = strategy
        self.broker = broker
        self.tag = tag
        self.mode = mode
        self.state: dict[str, Any] = state or {}
        self.state.setdefault("position", None)
        self.state.setdefault("day", None)
        self.state.setdefault("trades_today", 0)
        self.state.setdefault("pnl_today", 0.0)
        self.state.setdefault("halted", False)
        self.state.setdefault("kill_switch", False)
        self.state.setdefault("last_bar", None)
        self.state.setdefault("events", [])
        self.on_order = on_order or (lambda e: None)
        self.on_trade = on_trade or (lambda e: None)
        self.on_event = on_event or (lambda e: None)
        self.guards = Guardrails(strategy.risk.max_trades_per_day, strategy.risk.daily_loss_cap, strategy.hard_exit,
                                 strategy.entry_window.start, strategy.entry_window.end)
        self.segment = segment_for(strategy.instrument_type, strategy.intraday)

    # ---------- helpers ----------
    def _log(self, kind: str, msg: str, now: datetime, **extra) -> None:
        ev = {"t": now.isoformat(timespec="seconds"), "kind": kind, "msg": msg, **extra}
        self.state["events"] = (self.state["events"] + [ev])[-50:]
        self.on_event(ev)

    def _roll_day(self, now: datetime) -> None:
        d = now.date().isoformat()
        if self.state["day"] != d:
            self.state.update(day=d, trades_today=0, pnl_today=0.0, halted=False)

    def _qty(self) -> int:
        if self.s.instrument_type in ("option", "future", "index"):
            return self.s.risk.lots * opt.lot_size(self.s.underlying)
        return self.s.risk.lots

    def _instruments_for(self, direction: str, spot: float, now: datetime) -> list[tuple[Instrument, str]]:
        s = self.s
        if s.instrument_type == "option":
            step = opt.strike_step(s.underlying)
            expiry = opt.next_expiry(s.underlying, now.date(), s.option.expiry)
            out = []
            for leg in opt.legs_for(s.option.mode, direction, s.option.strike_steps):
                strike = opt.select_strike(spot, step, leg.opt_type, leg.moneyness_steps)
                out.append((self.broker.resolve_option(s.underlying, expiry, strike, leg.opt_type), leg.side))
            return out
        if s.instrument_type == "equity":
            inst = self.broker.search_instruments(s.underlying, "NSE")[0]
            return [(inst, "buy" if direction == "long" else "sell")]
        raise NotImplementedError("Live futures routing needs a futures contract resolver (phase 2); backtest/paper on options or equity.")

    def _order(self, inst: Instrument, side: str, qty: int, now: datetime, reason: str) -> dict:
        res = self.broker.place_order(OrderRequest(inst, "BUY" if side == "buy" else "SELL", qty, tag=self.tag))
        price = res.avg_price
        if not price:
            try:
                price = self.broker.ltp(inst)
            except Exception:
                price = 0.0
        ev = {"time": now.isoformat(timespec="seconds"), "symbol": inst.symbol, "side": side.upper(), "qty": qty, "price": price,
              "order_id": res.order_id, "status": res.status, "message": res.message, "tag": self.tag, "reason": reason, "mode": self.mode}
        self.on_order(ev)
        return ev

    # ---------- position management ----------
    def _open(self, direction: str, spot: float, now: datetime) -> None:
        qty = self._qty()
        legs = []
        for inst, side in self._instruments_for(direction, spot, now):
            ev = self._order(inst, side, qty, now, "entry")
            legs.append({"instrument": _inst_to_dict(inst), "side": side, "qty": qty, "entry": ev["price"], "open": True, "exit": None})
        self.state["position"] = {"direction": direction, "entry_time": now.isoformat(timespec="seconds"), "entry_spot": spot, "legs": legs}
        self._log("entry", f"{direction} entry: " + ", ".join(f"{l['side']} {l['instrument']['symbol']} @ {l['entry']}" for l in legs), now)

    def _leg_pnl(self, leg: dict, price: float) -> float:
        sign = 1 if leg["side"] == "buy" else -1
        return (price - leg["entry"]) * sign * leg["qty"]

    def _open_pnl(self) -> float:
        pos = self.state["position"]
        if not pos:
            return 0.0
        total = 0.0
        for leg in pos["legs"]:
            px = leg["exit"] if not leg["open"] else self.broker.ltp(_inst_from_dict(leg["instrument"]))
            total += self._leg_pnl(leg, px)
        return total

    def _close_leg(self, leg: dict, now: datetime, reason: str) -> None:
        inst = _inst_from_dict(leg["instrument"])
        ev = self._order(inst, "sell" if leg["side"] == "buy" else "buy", leg["qty"], now, reason)
        leg.update(open=False, exit=ev["price"], exit_reason=reason)

    def _close(self, now: datetime, reason: str) -> None:
        pos = self.state["position"]
        if not pos:
            return
        for leg in pos["legs"]:
            if leg["open"]:
                self._close_leg(leg, now, reason)
        gross = sum(self._leg_pnl(l, l["exit"]) for l in pos["legs"])
        costs = 0.0
        exch = "BSE" if self.s.exchange == "BSE" else "NSE"
        for l in pos["legs"]:
            costs += order_costs(l["side"], l["entry"], l["qty"], self.segment, exch, now.date()).total
            costs += order_costs("sell" if l["side"] == "buy" else "buy", l["exit"], l["qty"], self.segment, exch, now.date()).total
        net = gross - costs
        trade = {
            "entry_time": pos["entry_time"], "exit_time": now.isoformat(timespec="seconds"), "direction": pos["direction"],
            "symbol": " + ".join(l["instrument"]["symbol"] for l in pos["legs"]), "side": "/".join(sorted({l["side"] for l in pos["legs"]})),
            "qty": pos["legs"][0]["qty"], "entry_price": round(sum(l["entry"] for l in pos["legs"]), 2),
            "exit_price": round(sum(l["exit"] for l in pos["legs"]), 2), "gross_pnl": round(gross, 2), "costs": round(costs, 2),
            "net_pnl": round(net, 2), "exit_reason": reason, "tag": self.tag, "mode": self.mode,
        }
        self.state["position"] = None
        self.state["trades_today"] += 1
        self.state["pnl_today"] = round(self.state["pnl_today"] + net, 2)
        self.on_trade(trade)
        self._log("exit", f"exit ({reason}): net {net:,.2f}", now)
        if self.s.risk.daily_loss_cap and self.state["pnl_today"] <= -self.s.risk.daily_loss_cap:
            self.state["halted"] = True
            self._log("halt", "daily loss cap hit; no more entries today", now)

    def _check_stops(self, now: datetime) -> None:
        pos = self.state["position"]
        sl, tgt = self.s.exit.stop_loss_pct, self.s.exit.target_pct
        if not pos or not (sl or tgt):
            return
        for leg in pos["legs"]:
            if not leg["open"]:
                continue
            px = self.broker.ltp(_inst_from_dict(leg["instrument"]))
            sign = 1 if leg["side"] == "buy" else -1
            chg = (px - leg["entry"]) / leg["entry"] * 100 * sign if leg["entry"] else 0
            if sl and chg <= -sl:
                self._close_leg(leg, now, "stop_loss")
            elif tgt and chg >= tgt:
                self._close_leg(leg, now, "target")
        if all(not l["open"] for l in pos["legs"]):
            self._close(now, pos["legs"][0].get("exit_reason", "stop"))

    # ---------- public API ----------
    def on_minute(self, now: datetime) -> None:
        """Risk checks between candle closes: stops, hard exit, loss cap, kill switch."""
        self._roll_day(now)
        hhmm = now.strftime("%H:%M")
        if self.state["position"]:
            self._check_stops(now)
        if self.state["position"]:
            reason = self.guards.flatten_reason(hhmm, self.state["pnl_today"] + self._open_pnl(), self.state["kill_switch"])
            if reason:
                self._close(now, reason)
                if reason == "daily_loss_cap":
                    self.state["halted"] = True

    def on_candle_close(self, candles: pd.DataFrame, now: datetime) -> dict:
        """Evaluate the strategy on closed candles (last row = the bar that just closed)."""
        self._roll_day(now)
        self.on_minute(now)
        if candles.empty:
            return {"action": "none", "reason": "no_data"}
        last_ts = candles.index[-1]
        if self.state["last_bar"] == last_ts.isoformat():
            return {"action": "none", "reason": "bar_already_processed"}
        self.state["last_bar"] = last_ts.isoformat()
        sig = build_signals(candles, self.s)
        i = -1
        spot = float(candles["close"].iloc[i])
        hhmm = (last_ts + pd.Timedelta(minutes=min(TF_MINUTES[self.s.timeframe], 375))).strftime("%H:%M")
        pos = self.state["position"]
        if pos:
            if (pos["direction"] == "long" and bool(sig.long_exit.iloc[i])) or (pos["direction"] == "short" and bool(sig.short_exit.iloc[i])):
                self._close(now, "exit_signal")
        if self.state["position"] is None:
            block = self.guards.entry_block_reason(hhmm, self.state["trades_today"], self.state["pnl_today"], self.state["halted"], self.state["kill_switch"])
            want = None
            if self.s.direction in ("long", "both", "neutral") and bool(sig.long_entry.iloc[i]):
                want = "long"
            elif self.s.direction in ("short", "both") and bool(sig.short_entry.iloc[i]):
                want = "short"
            if want and block:
                self._log("skip", f"{want} signal skipped: {block}", now)
                return {"action": "skip", "reason": block}
            if want:
                self._open(want, spot, now)
                return {"action": "entry", "direction": want}
        return {"action": "hold" if self.state["position"] else "none"}

    def kill(self, now: datetime) -> None:
        self.state["kill_switch"] = True
        if self.state["position"]:
            self._close(now, "kill_switch")
        self._log("kill", "kill switch: positions squared off, session halted", now)

    def status(self) -> dict:
        pos = self.state["position"]
        open_pnl = None
        if pos:
            try:
                open_pnl = round(self._open_pnl(), 2)
            except Exception:
                open_pnl = None
        return {"position": pos, "open_pnl": open_pnl, "trades_today": self.state["trades_today"], "pnl_today": self.state["pnl_today"],
                "halted": self.state["halted"], "kill_switch": self.state["kill_switch"], "events": self.state["events"][-10:]}
