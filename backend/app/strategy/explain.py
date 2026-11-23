"""Strategy DSL -> plain-English summary (shown back to the user for confirmation)."""
from __future__ import annotations

from .dsl import Condition, Strategy

OP_WORDS = {
    "crosses_above": "crosses above", "crosses_below": "crosses below", "gt": "is above", "lt": "is below",
    "gte": "is at or above", "lte": "is at or below",
}
MODE_WORDS = {
    "atm_buy": "buy the ATM", "itm_buy": "buy the ITM", "otm_buy": "buy the OTM",
    "atm_sell": "sell the ATM", "itm_sell": "sell the ITM", "otm_sell": "sell the OTM",
}
TF_WORDS = {"1m": "1-minute", "3m": "3-minute", "5m": "5-minute", "10m": "10-minute", "15m": "15-minute", "30m": "30-minute", "1h": "hourly", "1d": "daily"}


def operand_words(s: Strategy, x) -> str:
    if isinstance(x, (int, float)):
        return f"{x:g}"
    base, _, attr = str(x).partition(".")
    spec = next((i for i in s.indicators if i.id == base), None)
    if spec is None:
        return {"close": "price", "hl2": "the candle midpoint"}.get(base, base)
    if spec.kind == "supertrend":
        return f"Supertrend ({spec.period},{spec.multiplier:g}) direction"
    if spec.kind == "orb":
        return f"the {spec.start}-{spec.end} opening-range {attr or 'mid'}"
    if spec.kind == "vwap":
        return "VWAP"
    return f"{spec.kind.upper()} {spec.period}"


def condition_words(s: Strategy, c: Condition) -> str:
    if c.op == "at_time":
        return f"the clock reaches {c.right}"
    left = operand_words(s, c.left)
    if c.left.endswith(".dir") and c.right in (0, 0.0):
        return "Supertrend turns green" if c.op == "crosses_above" else "Supertrend turns red"
    return f"{left} {OP_WORDS[c.op]} {operand_words(s, c.right)}"


def _join(s: Strategy, conds: list[Condition], logic: str = "all") -> str:
    parts = [condition_words(s, c) for c in conds]
    if len(parts) <= 1:
        return "".join(parts)
    return (", ".join(parts[:-1]) + (" and " if logic == "all" else " or ") + parts[-1])


def _action(s: Strategy, direction: str) -> str:
    if s.instrument_type == "option":
        m = s.option.mode
        if m.startswith(("straddle", "strangle")):
            kind = "ATM straddle" if m.startswith("straddle") else f"strangle {s.option.strike_steps} strike(s) OTM"
            return ("sell" if m.endswith("sell") else "buy") + f" the {kind} (CE + PE)"
        side = "sell" if m.endswith("sell") else "buy"
        opt = ("CE" if direction == "long" else "PE") if side == "buy" else ("PE" if direction == "long" else "CE")
        word = "call" if opt == "CE" else "put"
        steps = f" ({s.option.strike_steps} strike{'s' if s.option.strike_steps > 1 else ''} away)" if not m.startswith("atm") else ""
        return f"{MODE_WORDS[m]} {word} ({opt}){steps}"
    if s.instrument_type in ("future", "index"):
        return ("buy" if direction == "long" else "sell") + f" {s.underlying} futures"
    return ("buy" if direction == "long" else "short-sell") + f" {s.underlying}"


def _cap(x: str) -> str:
    return x[:1].upper() + x[1:]


def explain(s: Strategy) -> str:
    kind = {"option": "options", "future": "futures", "index": "futures", "equity": "shares"}[s.instrument_type]
    lines = [f"Trade {s.underlying} {kind} on {TF_WORDS[s.timeframe]} candles ({'intraday' if s.intraday else 'positional'})."]
    if s.option:
        lines[0] = lines[0][:-1] + f", {s.option.expiry} expiry."
    longs, shorts = s.long_conditions(), s.short_conditions()
    if s.direction in ("long", "both", "neutral") and longs:
        lines.append(f"{_cap(_action(s, 'long'))} when {_join(s, longs, s.entry.logic)}.")
    if s.direction in ("short", "both") and shorts:
        lines.append(f"{_cap(_action(s, 'short'))} when {_join(s, shorts, s.entry.logic)}.")
    lines.append(f"New entries only between {s.entry_window.start} and {s.entry_window.end} IST.")
    exits = []
    if s.exit.on_opposite_signal and s.direction != "neutral":
        exits.append("on the opposite signal")
    if s.exit.long:
        exits.append(f"when {_join(s, s.exit.long, 'any')}" + (" (long side)" if s.direction == "both" else ""))
    if s.exit.short and s.direction in ("short", "both"):
        exits.append(f"when {_join(s, s.exit.short, 'any')} (short side)")
    if s.exit.stop_loss_pct:
        exits.append(f"at a {s.exit.stop_loss_pct:g}% stop loss" + (" per leg" if s.option and s.option.mode.startswith(("straddle", "strangle")) else ""))
    if s.exit.target_pct:
        exits.append(f"at a {s.exit.target_pct:g}% target")
    if s.exit.trailing_sl_pct:
        exits.append(f"on a {s.exit.trailing_sl_pct:g}% trailing stop")
    hard = f"and always by {s.hard_exit} IST" if s.intraday else ""
    lines.append(("Exit " + ", ".join(exits) + (", " + hard if exits and hard else hard) + ".") if (exits or hard) else "")
    unit = "lot" if s.instrument_type != "equity" else "share"
    risk = f"Risk: {s.risk.lots} {unit}{'s' if s.risk.lots > 1 else ''}, at most {s.risk.max_trades_per_day} trade{'s' if s.risk.max_trades_per_day > 1 else ''} a day"
    if s.risk.daily_loss_cap:
        risk += f", stop for the day after a ₹{s.risk.daily_loss_cap:,.0f} loss"
    lines.append(risk + ".")
    return " ".join(x for x in lines if x)


def rules_table(s: Strategy) -> dict[str, str]:
    """Compact key/value rules, matching the web Studio's editable rule rows."""
    side = "Neutral" if s.direction == "neutral" else s.direction.title()
    if s.option and s.direction == "both":
        instr = f"{s.underlying} options · {_action(s, 'long')} on bullish, {_action(s, 'short').split(' the ')[-1]} on bearish"
    elif s.option:
        instr = f"{s.underlying} options · {_action(s, 'short' if s.direction == 'short' else 'long')}"
    else:
        instr = f"{s.underlying} {s.instrument_type}"
    entry = []
    if s.direction in ("long", "both", "neutral"):
        entry.append(_join(s, s.long_conditions(), s.entry.logic))
    if s.direction in ("short", "both"):
        entry.append(_join(s, s.short_conditions(), s.entry.logic))
    ex = []
    if s.exit.on_opposite_signal and s.direction != "neutral":
        ex.append("opposite signal")
    if s.exit.long:
        ex.append(_join(s, s.exit.long, "any"))
    if s.exit.short and s.direction in ("short", "both"):
        ex.append(_join(s, s.exit.short, "any"))
    if s.exit.stop_loss_pct:
        ex.append(f"SL {s.exit.stop_loss_pct:g}%")
    if s.exit.target_pct:
        ex.append(f"target {s.exit.target_pct:g}%")
    if s.exit.trailing_sl_pct:
        ex.append(f"trail {s.exit.trailing_sl_pct:g}%")
    if s.intraday:
        ex.append(f"{s.hard_exit} hard exit")
    else:
        ex.append("positional (no daily square-off)")
    unit = "share" if s.instrument_type == "equity" else "lot"
    risk = f"{s.risk.max_trades_per_day} trades/day · {s.risk.lots} {unit}{'s' if s.risk.lots > 1 else ''}"
    if s.risk.daily_loss_cap:
        risk += f" · ₹{s.risk.daily_loss_cap:,.0f} daily loss cap"
    return {
        "Instrument": instr,
        "Timeframe": s.timeframe,
        "Direction": side,
        "Entry": " | ".join(entry),
        "Window": f"{s.entry_window.start}–{s.entry_window.end}",
        "Exit": " / ".join(ex),
        "Risk": risk,
    }
