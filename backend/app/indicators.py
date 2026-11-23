"""Technical indicators on OHLCV DataFrames (pandas/numpy only).

All functions take/return pandas objects indexed like the input. Candle frames use
columns: open, high, low, close, volume and a tz-naive DatetimeIndex (IST wall clock).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average, alpha = 2/(n+1), recursive and seeded from the first value.

    Note: TradingView's ta.ema seeds with the SMA of the first `period` values instead; the two
    converge after ~3-4x `period` bars, so only the first bars of a data set differ. Leading NaNs
    (e.g. an indicator used as source) are skipped rather than poisoning the series.
    """
    return series.ewm(span=period, adjust=False, ignore_na=True).mean()


def wilder(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing (RMA): seed = SMA of the first `period` valid values, then
    avg_t = (avg_{t-1} * (period - 1) + x_t) / period. NaN until `period` values exist.

    This is the textbook definition used by RSI/ATR on TradingView and most terminals.
    """
    x = series.to_numpy(dtype=float)
    out = np.full(len(x), np.nan)
    valid = np.flatnonzero(~np.isnan(x))
    if len(valid) < period:
        return pd.Series(out, index=series.index)
    first = valid[0]
    seed_end = first + period  # assumes no gaps after the first valid value (true for diff/TR)
    if np.isnan(x[first:seed_end]).any():
        return series.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg = x[first:seed_end].mean()
    out[seed_end - 1] = avg
    for i in range(seed_end, len(x)):
        if not np.isnan(x[i]):
            avg = (avg * (period - 1) + x[i]) / period
        out[i] = avg
    return pd.Series(out, index=series.index)


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI (SMA-seeded Wilder smoothing of gains/losses).

    Edge cases: no losses -> 100, no gains -> 0, a completely flat window -> 50 (neutral, rather
    than the 100 some platforms print, which would fire "RSI > 70" signals on a dead market).
    """
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = wilder(gain, period)
    avg_loss = wilder(loss, period)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out = out.where(avg_loss != 0, 100.0)
    out = out.where(~((avg_loss == 0) & (avg_gain == 0)), 50.0)
    return out.where(avg_gain.notna())


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session VWAP, resetting every trading day (IST calendar day). Uses typical price.

    Index candles carry no volume: if a whole day has zero/missing volume every bar weighs 1
    (i.e. a cumulative average of typical price). Days with real volume use it as-is; a few
    zero-volume bars inside such a day simply add nothing.
    """
    tp = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.tz_convert("Asia/Kolkata").normalize() if df.index.tz is not None else df.index.normalize()
    vol = df["volume"].astype(float).fillna(0.0).clip(lower=0.0)
    day_has_volume = vol.groupby(day).transform("sum") > 0
    vol = vol.where(day_has_volume, 1.0)
    pv = (tp * vol).groupby(day).cumsum()
    cv = vol.groupby(day).cumsum()
    return pv / cv.replace(0, np.nan)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ATR: SMA of the first `period` true ranges, then Wilder smoothing.

    The first bar's true range is high-low (no previous close).
    """
    return wilder(true_range(df), period)


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """Supertrend. Returns DataFrame with 'supertrend' line and 'direction' (+1 up/green, -1 down/red).

    Matches TradingView's ta.supertrend: bands ratchet only toward price, and the trend flips when
    the close crosses the *current* bar's final band (not the previous bar's).
    """
    hl2 = (df["high"] + df["low"]) / 2
    a = atr(df, period)
    upper = (hl2 + multiplier * a).to_numpy()
    lower = (hl2 - multiplier * a).to_numpy()
    close = df["close"].to_numpy()
    n = len(df)
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    st = np.full(n, np.nan)
    direction = np.zeros(n)
    for i in range(n):
        if np.isnan(upper[i]):
            continue
        if i == 0 or np.isnan(final_upper[i - 1]):
            final_upper[i], final_lower[i] = upper[i], lower[i]
            direction[i] = 1 if close[i] >= hl2.iloc[i] else -1
        else:
            final_upper[i] = upper[i] if (upper[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]) else final_upper[i - 1]
            final_lower[i] = lower[i] if (lower[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]) else final_lower[i - 1]
            prev_dir = direction[i - 1]
            if prev_dir == -1 and close[i] > final_upper[i]:
                direction[i] = 1
            elif prev_dir == 1 and close[i] < final_lower[i]:
                direction[i] = -1
            else:
                direction[i] = prev_dir
        st[i] = final_lower[i] if direction[i] == 1 else final_upper[i]
    return pd.DataFrame({"supertrend": st, "direction": direction}, index=df.index)


def opening_range(df: pd.DataFrame, start: str = "09:15", end: str = "09:30") -> pd.DataFrame:
    """Opening range high/low per day, available only from the first bar that *closes* after `end`.

    Bars are labelled by their start time; a bar is inside the range if it starts >= start and
    starts < end.
    """
    t = df.index.strftime("%H:%M")
    in_range = (t >= start) & (t < end)
    day = df.index.normalize()
    hi = df["high"].where(in_range).groupby(day).transform("max")
    lo = df["low"].where(in_range).groupby(day).transform("min")
    avail = t >= end
    return pd.DataFrame({"high": hi.where(avail), "low": lo.where(avail)}, index=df.index)


def crossed_above(a: pd.Series, b: pd.Series | float) -> pd.Series:
    """True on the bar where a moves from <= b to > b. NaN on either bar -> False."""
    b_s = b if isinstance(b, pd.Series) else pd.Series(b, index=a.index)
    return ((a > b_s) & (a.shift(1) <= b_s.shift(1))).fillna(False).astype(bool)


def crossed_below(a: pd.Series, b: pd.Series | float) -> pd.Series:
    b_s = b if isinstance(b, pd.Series) else pd.Series(b, index=a.index)
    return ((a < b_s) & (a.shift(1) >= b_s.shift(1))).fillna(False).astype(bool)
