"""Shared signal evaluation used by both the backtester and the live engine."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..data.candles import TF_MINUTES
from .dsl import Condition, Strategy


@dataclass
class Signals:
    frame: pd.DataFrame  # candles + indicator columns
    long_entry: pd.Series
    short_entry: pd.Series
    long_exit: pd.Series
    short_exit: pd.Series
    close_time: pd.Series  # "HH:MM" bar close time


def compute_indicators(df: pd.DataFrame, strategy: Strategy) -> pd.DataFrame:
    out = df.copy()
    out["hl2"] = (out["high"] + out["low"]) / 2
    for spec in strategy.indicators:
        src = out[spec.source]
        if spec.kind == "ema":
            out[spec.id] = ind.ema(src, spec.period)
        elif spec.kind == "sma":
            out[spec.id] = ind.sma(src, spec.period)
        elif spec.kind == "rsi":
            out[spec.id] = ind.rsi(src, spec.period)
        elif spec.kind == "vwap":
            out[spec.id] = ind.vwap(out)
        elif spec.kind == "atr":
            out[spec.id] = ind.atr(out, spec.period)
        elif spec.kind == "supertrend":
            st = ind.supertrend(out, spec.period, spec.multiplier)
            out[spec.id] = st["supertrend"]
            out[f"{spec.id}.dir"] = st["direction"]
        elif spec.kind == "orb":
            orb = ind.opening_range(out, spec.start, spec.end)
            out[f"{spec.id}.high"] = orb["high"]
            out[f"{spec.id}.low"] = orb["low"]
            out[spec.id] = (orb["high"] + orb["low"]) / 2
    return out


def bar_close_times(index: pd.DatetimeIndex, timeframe: str) -> pd.Series:
    """"HH:MM" close time of each bar, capped at the 15:30 session close.

    The last bar of a session can be shorter than the timeframe (e.g. the 15:15 hourly bar ends at
    15:30, not 16:15), and a daily bar closes at 15:30.
    """
    return pd.Series(bar_close_datetimes(index, timeframe).strftime("%H:%M"), index=index)


def bar_close_datetimes(index: pd.DatetimeIndex, timeframe: str) -> pd.DatetimeIndex:
    """Bar close timestamps (IST naive), capped at that day's 15:30 session close."""
    mins = TF_MINUTES[timeframe]
    session_close = index.normalize() + pd.Timedelta(hours=15, minutes=30)
    if mins >= 1440:
        return session_close
    close = index + pd.Timedelta(minutes=mins)
    return close.where(close <= session_close, session_close)


def _operand(frame: pd.DataFrame, x) -> pd.Series | float:
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(x)
    except (TypeError, ValueError):
        pass
    if x not in frame.columns:
        raise KeyError(f"unknown operand {x!r}")
    return frame[x]


def eval_condition(frame: pd.DataFrame, cond: Condition, close_time: pd.Series) -> pd.Series:
    if cond.op == "at_time":
        hit = close_time >= str(cond.right)
        day = frame.index.normalize()
        first = hit & ~hit.groupby(day).shift(1, fill_value=False).astype(bool)
        return first.astype(bool)
    a = _operand(frame, cond.left)
    b = _operand(frame, cond.right)
    if isinstance(a, float):  # put the series on the left
        a, b = b, a
        flip = {"gt": "lt", "lt": "gt", "gte": "lte", "lte": "gte", "crosses_above": "crosses_below", "crosses_below": "crosses_above"}
        op = flip[cond.op]
    else:
        op = cond.op
    if op == "crosses_above":
        r = ind.crossed_above(a, b)
    elif op == "crosses_below":
        r = ind.crossed_below(a, b)
    elif op == "gt":
        r = a > b
    elif op == "lt":
        r = a < b
    elif op == "gte":
        r = a >= b
    else:
        r = a <= b
    return pd.Series(np.asarray(r, dtype=bool), index=frame.index)


def combine(frame: pd.DataFrame, conds: list[Condition], logic: str, close_time: pd.Series) -> pd.Series:
    if not conds:
        return pd.Series(False, index=frame.index)
    parts = [eval_condition(frame, c, close_time) for c in conds]
    out = parts[0]
    for p in parts[1:]:
        out = (out & p) if logic == "all" else (out | p)
    return out.astype(bool)


def build_signals(df: pd.DataFrame, strategy: Strategy) -> Signals:
    frame = compute_indicators(df, strategy)
    ct = bar_close_times(frame.index, strategy.timeframe)
    logic = strategy.entry.logic
    long_entry = combine(frame, strategy.long_conditions(), logic, ct) if strategy.direction in ("long", "both", "neutral") else pd.Series(False, index=frame.index)
    short_entry = combine(frame, strategy.short_conditions(), logic, ct) if strategy.direction in ("short", "both") else pd.Series(False, index=frame.index)
    long_exit = combine(frame, strategy.exit.long, "any", ct)
    short_exit = combine(frame, strategy.exit.short, "any", ct)
    if strategy.exit.on_opposite_signal and strategy.direction != "neutral":
        # opposite signal = the other side's entry conditions (mirrored if not given)
        long_exit = long_exit | combine(frame, strategy.opposite_of_long(), logic, ct)
        short_exit = short_exit | combine(frame, strategy.opposite_of_short(), logic, ct)
    return Signals(frame, long_entry, short_entry, long_exit, short_exit, ct)
