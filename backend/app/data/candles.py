"""Candle loading and resampling helpers."""
from __future__ import annotations

from datetime import date

import pandas as pd

from .synthetic import generate_candles

TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}


def resample(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Resample intraday candles to `timeframe`, anchored at the 09:15 session open."""
    mins = TF_MINUTES[timeframe]
    if mins == 1440:
        out = df.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        return out.dropna(subset=["open"])
    if len(df) > 1:
        cur = int((df.index[1] - df.index[0]).total_seconds() // 60)
        if cur == mins:
            return df
    out = df.resample(f"{mins}min", origin="start_day", offset="15min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    out = out.dropna(subset=["open"])
    t = out.index.strftime("%H:%M")
    return out[(t >= "09:15") & (t < "15:30")]


def load_candles(
    underlying: str, timeframe: str, days: int = 60, seed: int = 7, end: date | None = None, base_df: pd.DataFrame | None = None
) -> tuple[pd.DataFrame, str]:
    """Return (candles, source). Uses provided frame (e.g. from a broker) else synthetic data."""
    if base_df is not None and not base_df.empty:
        return resample(base_df, timeframe), "broker"
    base = generate_candles(underlying, days=days, seed=seed, end=end)
    return resample(base, timeframe), "synthetic"


def read_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"]).set_index("timestamp")
    df.index.name = None
    return df
