import numpy as np
import pandas as pd

from app import indicators as ind


def _series(vals):
    return pd.Series(vals, index=pd.date_range("2026-01-05 09:15", periods=len(vals), freq="5min"), dtype=float)


def test_ema_matches_recursive_definition():
    s = _series(np.arange(1, 31))
    e = ind.ema(s, 9)
    alpha = 2 / 10
    manual = [1.0]
    for v in s.iloc[1:]:
        manual.append(alpha * v + (1 - alpha) * manual[-1])
    assert np.allclose(e.values, manual)


def test_sma():
    s = _series([1, 2, 3, 4, 5])
    assert ind.sma(s, 3).iloc[-1] == 4
    assert np.isnan(ind.sma(s, 3).iloc[1])


def test_rsi_bounds_and_extremes():
    up = _series(np.arange(1, 50))
    assert ind.rsi(up, 14).iloc[-1] == 100
    rng = np.random.default_rng(1)
    noisy = _series(100 + rng.normal(0, 1, 300).cumsum())
    r = ind.rsi(noisy, 14).dropna()
    assert ((r >= 0) & (r <= 100)).all()
    down = _series(np.arange(50, 1, -1))
    assert ind.rsi(down, 14).iloc[-1] < 1


def test_crossovers():
    a = _series([1, 2, 3, 4, 3, 2])
    b = _series([2.5] * 6)
    assert ind.crossed_above(a, b).tolist() == [False, False, True, False, False, False]
    assert ind.crossed_below(a, b).tolist() == [False, False, False, False, False, True]
    assert ind.crossed_above(a, 2.5).sum() == 1


def test_vwap_resets_daily(candles_5m):
    v = ind.vwap(candles_5m)
    first_bars = candles_5m.index.strftime("%H:%M") == "09:15"
    tp = (candles_5m["high"] + candles_5m["low"] + candles_5m["close"]) / 3
    assert np.allclose(v[first_bars], tp[first_bars])


def test_atr_and_supertrend(candles_5m):
    a = ind.atr(candles_5m, 14).dropna()
    assert (a > 0).all()
    st = ind.supertrend(candles_5m, 10, 3)
    assert set(np.unique(st["direction"].iloc[20:])) <= {-1.0, 1.0}
    up = st["direction"] == 1
    # when green the line is below price, when red above
    assert (st["supertrend"][up].dropna() <= candles_5m["close"][up][st["supertrend"][up].notna()] + 1e-9).all()


def test_opening_range(candles_5m):
    orb = ind.opening_range(candles_5m, "09:15", "09:30")
    day = candles_5m.index.normalize() == candles_5m.index.normalize()[0]
    d0 = candles_5m[day]
    assert orb[day]["high"].iloc[:3].isna().all()  # not available inside the range
    assert orb[day]["high"].iloc[3] == d0["high"].iloc[:3].max()
    assert orb[day]["low"].iloc[5] == d0["low"].iloc[:3].min()
