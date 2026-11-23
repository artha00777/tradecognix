"""Synthetic NSE index candles so the demo and tests run without a broker.

Generates 5-minute bars 09:15-15:30 IST (75 bars/day) on weekdays using a GBM with a
U-shaped intraday volatility profile, overnight gaps, mild regime switching (trend vs
chop) and synthetic volume. Deterministic for a given seed. NOT real market data.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

BASE_LEVELS = {"NIFTY": 24500.0, "BANKNIFTY": 52000.0, "FINNIFTY": 23800.0, "MIDCPNIFTY": 12700.0, "SENSEX": 80800.0}


def trading_days(start: date, end: date) -> list[date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def generate_candles(
    symbol: str = "NIFTY",
    days: int = 60,
    end: date | None = None,
    seed: int = 7,
    annual_vol: float = 0.14,
    start_price: float | None = None,
    bar_minutes: int = 5,
) -> pd.DataFrame:
    """Synthetic intraday candles. `bar_minutes` must divide the 375-minute session (1, 3, 5, 15, 25...)."""
    if bar_minutes <= 0 or 375 % bar_minutes:
        raise ValueError("bar_minutes must divide 375 (the 09:15-15:30 session)")
    rng = np.random.default_rng(seed)
    if end is None:
        from ..engine.clock import ist_today

        end = ist_today()
    # walk back enough calendar days to collect `days` weekdays
    all_days = trading_days(end - timedelta(days=days * 2 + 10), end)[-days:]
    price = start_price or BASE_LEVELS.get(symbol.upper(), 20000.0)
    bars_per_day = 375 // bar_minutes
    # U-shaped intraday vol multiplier
    x = np.linspace(-1, 1, bars_per_day)
    u_shape = 0.7 + 0.9 * x**2
    per_bar_sigma = annual_vol / np.sqrt(252 * bars_per_day)
    rows = []
    regime_drift = 0.0
    for d in all_days:
        if rng.random() < 0.3:  # regime switch: trending day or choppy day
            regime_drift = rng.choice([-1, 0, 0, 1]) * per_bar_sigma * 0.15
        price *= float(np.exp(rng.normal(0, annual_vol / np.sqrt(252) * 0.35)))  # overnight gap
        ts0 = pd.Timestamp(d) + pd.Timedelta(hours=9, minutes=15)
        for i in range(bars_per_day):
            sig = per_bar_sigma * u_shape[i]
            ret = rng.normal(regime_drift, sig)
            o = price
            c = o * float(np.exp(ret))
            wick = abs(rng.normal(0, sig * 0.6))
            h = max(o, c) * (1 + wick)
            lo = min(o, c) * (1 - abs(rng.normal(0, sig * 0.6)))
            vol = int(rng.lognormal(11, 0.4) * u_shape[i] * bar_minutes / 5)
            rows.append((ts0 + pd.Timedelta(minutes=bar_minutes * i), o, h, lo, c, vol))
            price = c
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")
    df.index.name = None
    return df.round({"open": 2, "high": 2, "low": 2, "close": 2})


def save_csv(df: pd.DataFrame, path: str) -> None:
    df.to_csv(path, index_label="timestamp")
