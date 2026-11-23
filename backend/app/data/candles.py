"""Candle loading and resampling helpers."""
from __future__ import annotations

from datetime import date

import pandas as pd

from .synthetic import generate_candles

TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}
AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
DAILY_BAR_OFFSET = pd.Timedelta(hours=9, minutes=15)  # daily bars are labelled at the session open


def base_timeframe_for(timeframe: str) -> str:
    """Finest broker timeframe to fetch so any strategy timeframe can be resampled from it.

    Not every broker serves every interval (Dhan: 1/5/15/25/60), but all serve 1m and 5m.
    """
    mins = TF_MINUTES[timeframe]
    if mins >= 1440:
        return "1d"
    return "5m" if mins % 5 == 0 else "1m"


def to_ist_naive_index(df: pd.DataFrame) -> pd.DataFrame:
    """Candle frames must be IST wall-clock and tz-naive; convert tz-aware (e.g. UTC) input."""
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df = df.copy()
        df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)
    return df


def bar_spacing_minutes(df: pd.DataFrame) -> int | None:
    """Most common spacing between consecutive bars within a day (robust to day gaps)."""
    if len(df) < 2:
        return None
    diffs = pd.Series(df.index[1:] - df.index[:-1])
    diffs = diffs[(diffs > pd.Timedelta(0)) & (diffs < pd.Timedelta(hours=6))]
    if diffs.empty:
        return 1440
    return int(diffs.mode().iloc[0].total_seconds() // 60)


def resample(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Resample intraday candles to `timeframe`, anchored at the 09:15 session open.

    Raises ValueError when asked to *up*sample (e.g. 5-minute data to 1-minute bars), which would
    silently produce bars with the wrong duration.
    """
    mins = TF_MINUTES[timeframe]
    df = to_ist_naive_index(df).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    if df.empty:
        return df
    cur = bar_spacing_minutes(df)
    if mins == 1440:
        if cur == 1440 and (df.index.normalize() + DAILY_BAR_OFFSET == df.index).all():
            return df
        out = df.resample("1D").agg(AGG).dropna(subset=["open"])
        out.index = out.index + DAILY_BAR_OFFSET
        return out
    if cur is not None and cur > mins:
        raise ValueError(f"Cannot build {timeframe} candles from {cur}-minute data (needs finer data).")
    if cur == mins:
        t = df.index.strftime("%H:%M")
        return df[(t >= "09:15") & (t < "15:30")]
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
    base = generate_candles(underlying, days=days, seed=seed, end=end, bar_minutes=synthetic_bar_minutes(timeframe))
    return resample(base, timeframe), "synthetic"


def synthetic_bar_minutes(timeframe: str) -> int:
    return 1 if base_timeframe_for(timeframe) == "1m" else 5


def read_csv(path: str) -> pd.DataFrame:
    """Read a candle CSV with a `timestamp` column (IST, or tz-aware which is converted to IST)."""
    df = pd.read_csv(path)
    missing = {"timestamp", "open", "high", "low", "close"} - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing columns: {', '.join(sorted(missing))}")
    if "volume" not in df.columns:
        df["volume"] = 0
    idx = pd.to_datetime(df["timestamp"], utc=False)
    df = df.drop(columns=["timestamp"])
    df.index = pd.DatetimeIndex(idx)
    df.index.name = None
    return to_ist_naive_index(df)[["open", "high", "low", "close", "volume"]].astype(float).sort_index()
