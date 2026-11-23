"""Regression tests for the correctness fixes made in the Nov 2026 audit (indicators, data,
backtester, metrics, parser, DSL, costs)."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from app import indicators as ind
from app.backtest.costs import apply_slippage
from app.backtest.engine import run_backtest
from app.backtest.metrics import compute_metrics
from app.data.candles import base_timeframe_for, read_csv, resample
from app.data.synthetic import generate_candles
from app.strategy.dsl import Condition, Strategy
from app.strategy.evaluate import bar_close_times
from app.strategy.parser import RuleBasedParser

P = RuleBasedParser()


def _series(vals, start="2026-01-05 09:15"):
    return pd.Series(vals, index=pd.date_range(start, periods=len(vals), freq="5min"), dtype=float)


def _frame(rows, start="2026-01-05 09:15", freq="5min"):
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], dtype=float)
    df.index = pd.date_range(start, periods=len(rows), freq=freq)
    return df


# ---------------- indicators ----------------
def test_rsi_is_sma_seeded_wilder():
    rng = np.random.default_rng(3)
    s = _series(100 + rng.normal(0, 1, 60).cumsum())
    d = s.diff().to_numpy()
    g, l = np.clip(d, 0, None), np.clip(-d, 0, None)
    ag, al = g[1:15].mean(), l[1:15].mean()  # first 14 deltas
    expected = [100 - 100 / (1 + ag / al)]
    for i in range(15, len(s)):
        ag = (ag * 13 + g[i]) / 14
        al = (al * 13 + l[i]) / 14
        expected.append(100 - 100 / (1 + ag / al))
    r = ind.rsi(s, 14)
    assert r.iloc[:14].isna().all()
    assert np.allclose(r.iloc[14:].to_numpy(), expected)


def test_rsi_flat_market_is_neutral():
    assert ind.rsi(_series([100.0] * 30), 14).iloc[-1] == 50


def test_atr_seed_is_sma_of_true_range(candles_5m):
    df = candles_5m.iloc[:50]
    tr = ind.true_range(df)
    a = ind.atr(df, 14)
    assert a.iloc[:13].isna().all()
    assert a.iloc[13] == pytest.approx(tr.iloc[:14].mean())
    assert a.iloc[14] == pytest.approx((a.iloc[13] * 13 + tr.iloc[14]) / 14)


def _reference_supertrend(df, period, mult):
    """Straight port of TradingView's ta.supertrend (direction +1 = up here)."""
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy()
    a = ind.atr(df, period).to_numpy()
    close = df["close"].to_numpy()
    up_b, lo_b = hl2 + mult * a, hl2 - mult * a
    fu, fl, d = np.full(len(df), np.nan), np.full(len(df), np.nan), np.zeros(len(df))
    for i in range(len(df)):
        if np.isnan(a[i]):
            continue
        if i == 0 or np.isnan(fu[i - 1]):
            fu[i], fl[i] = up_b[i], lo_b[i]
            d[i] = 1 if close[i] >= hl2[i] else -1
            continue
        fu[i] = up_b[i] if (up_b[i] < fu[i - 1] or close[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lo_b[i] if (lo_b[i] > fl[i - 1] or close[i - 1] < fl[i - 1]) else fl[i - 1]
        if d[i - 1] == -1:
            d[i] = 1 if close[i] > fu[i] else -1
        else:
            d[i] = -1 if close[i] < fl[i] else 1
    return d


def test_supertrend_flips_on_current_band(candles_5m):
    df = candles_5m.iloc[:1500]
    st = ind.supertrend(df, 10, 3)
    assert np.array_equal(st["direction"].to_numpy(), _reference_supertrend(df, 10, 3))
    # invariant that only holds with the current-band rule: green line never above close
    up = st["direction"] == 1
    assert (st["supertrend"][up] <= df["close"][up] + 1e-9).all()
    assert (st["supertrend"][~up & st["supertrend"].notna()] >= df["close"][~up & st["supertrend"].notna()] - 1e-9).all()


def test_vwap_volume_weighted_and_zero_volume_days():
    df = _frame([[10, 12, 8, 10, 100], [10, 14, 10, 12, 300], [12, 12, 12, 12, 0]])
    v = ind.vwap(df)
    tp = (df.high + df.low + df.close) / 3
    assert v.iloc[1] == pytest.approx((tp.iloc[0] * 100 + tp.iloc[1] * 300) / 400)
    assert v.iloc[2] == pytest.approx(v.iloc[1])  # a zero-volume bar adds nothing
    idx = _frame([[10, 12, 8, 10, 0], [10, 14, 10, 12, 0]])  # index candles: no volume at all
    assert ind.vwap(idx).iloc[1] == pytest.approx(((10 + 12 + 8) / 3 + (14 + 10 + 12) / 3) / 2)


# ---------------- data / timeframes ----------------
def test_resample_daily_labelled_at_open_and_upsample_refused(candles_5m):
    d = resample(candles_5m, "1d")
    assert (d.index.strftime("%H:%M") == "09:15").all()
    with pytest.raises(ValueError):
        resample(candles_5m, "1m")
    assert base_timeframe_for("3m") == "1m" and base_timeframe_for("30m") == "5m" and base_timeframe_for("1d") == "1d"


def test_resample_converts_utc_index_to_ist(candles_5m):
    utc = candles_5m.iloc[:150].copy()
    utc.index = utc.index.tz_localize("Asia/Kolkata").tz_convert("UTC")
    out = resample(utc, "15m")
    assert out.index.tz is None and out.index[0].strftime("%H:%M") == "09:15"


def test_hourly_last_bar_closes_at_1530(candles_5m):
    h = resample(candles_5m.iloc[:75], "1h")
    ct = bar_close_times(h.index, "1h")
    assert ct.iloc[-1] == "15:30" and h.index[-1].strftime("%H:%M") == "15:15"


def test_read_csv_validates_and_handles_offsets(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text("timestamp,open,high,low,close\n2026-11-20T03:45:00+00:00,1,2,0.5,1.5\n")
    df = read_csv(str(p))
    assert df.index[0] == pd.Timestamp("2026-11-20 09:15") and df["volume"].iloc[0] == 0
    (tmp_path / "bad.csv").write_text("timestamp,open\n2026-01-01,1\n")
    with pytest.raises(ValueError):
        read_csv(str(tmp_path / "bad.csv"))


def test_synthetic_one_minute_bars():
    df = generate_candles("NIFTY", days=2, seed=1, end=date(2026, 11, 20), bar_minutes=1)
    assert len(df) == 2 * 375 and (df.index[1] - df.index[0]) == pd.Timedelta(minutes=1)


# ---------------- DSL / parser ----------------
def test_daily_timeframe_is_positional_and_trades():
    s = P.parse("Buy Reliance when 20 EMA crosses above 50 EMA on daily candles").strategy
    assert s.timeframe == "1d" and s.intraday is False
    c = generate_candles("RELIANCE", days=250, seed=5, end=date(2026, 11, 20))
    res = run_backtest(s, resample(c, "1d"))
    assert res.metrics["trades"] >= 1  # previously always 0


def test_dsl_rejects_number_vs_number_and_nan():
    with pytest.raises(ValueError):
        Condition(left="5", op="gt", right=3)
    with pytest.raises(ValueError):
        Strategy.model_validate({"entry": {"long": [{"left": "close", "op": "gt", "right": "nan"}]}})
    with pytest.raises(ValueError):
        Strategy.model_validate({"indicators": [{"id": "close", "kind": "ema", "period": 9}], "entry": {"long": [{"left": "close", "op": "gt", "right": 1}]}})


@pytest.mark.parametrize("text,tf,cap", [
    ("Nifty opening range breakout first 15 min on 5 min candles", "5m", None),
    ("Buy Nifty ATM call when 9 EMA crosses above 21 EMA on 5 min. stop loss 200 points", "5m", None),
    ("Buy Nifty ATM call when 9 EMA crosses above 21 EMA on 5 min. daily loss limit 5000", "5m", 5000),
    ("Buy nifty call when 9/21 ema cross on 15 min. max loss 2k", "15m", 2000),
])
def test_parser_timeframe_and_loss_cap_edge_cases(text, tf, cap):
    s = P.parse(text).strategy
    assert s.timeframe == tf and s.risk.daily_loss_cap == cap


def test_parser_rejects_huge_input():
    with pytest.raises(ValueError):
        P.parse("buy nifty " * 1000)


# ---------------- costs ----------------
def test_slippage_rounds_against_trader():
    assert apply_slippage(100.02, "buy", 0) == 100.05  # rounds up for buys
    assert apply_slippage(100.04, "sell", 0) == 100.0  # rounds down for sells
    assert apply_slippage(100.0, "buy", 0.5) == 100.5  # exact ticks untouched (float noise)


# ---------------- backtester fills ----------------
def _eq_strategy(**exit_kw):
    return Strategy.model_validate({
        "underlying": "TESTEQ", "instrument_type": "equity", "timeframe": "5m", "direction": "long",
        "entry": {"long": [{"left": "time", "op": "at_time", "right": "09:20"}]},
        "exit": {"on_opposite_signal": False, **exit_kw}, "risk": {"lots": 10, "max_trades_per_day": 1},
    })


def test_gap_through_stop_fills_at_open():
    s = _eq_strategy(stop_loss_pct=1)
    df = _frame([[100, 100, 100, 100, 1], [100, 100, 100, 100, 1], [95, 96, 94, 95, 1], [95, 95, 95, 95, 1]])
    res = run_backtest(s, df, cost_cfg=__import__("app.backtest.costs", fromlist=["CostConfig"]).CostConfig(slippage_pct=0))
    leg = res.trades[0]["legs"][0]
    assert res.trades[0]["exit_reason"] == "stop_loss"
    assert leg["exit_ref"] == 95  # opened at 95, below the 99 stop: filled at the open, not 99


def test_trailing_stop_has_no_same_bar_hindsight():
    from app.backtest.costs import CostConfig

    s = _eq_strategy(trailing_sl_pct=5)
    # entry at 100 on the 09:15 bar (closes 09:20). Bar 2: high 110 then low 99 -> must NOT exit there (hindsight)
    df = _frame([[100, 100, 100, 100, 1], [100, 100, 100, 100, 1], [100, 110, 99, 108, 1], [108, 108, 104, 104, 1], [104, 104, 104, 104, 1]])
    res = run_backtest(s, df, cost_cfg=CostConfig(slippage_pct=0))
    t = res.trades[0]
    assert t["exit_reason"] == "trailing_stop" and t["bars_held"] == 3  # exits on bar 3, not bar 2
    assert t["legs"][0]["exit_ref"] == pytest.approx(104.5)


# ---------------- metrics ----------------
def _t(net, d="2026-01-01", reason="exit_signal"):
    return {"net_pnl": net, "gross_pnl": net, "bars_held": 3, "exit_reason": reason, "date": d, "exit_time": d, "charges": {}, "slippage": 0}


def test_profit_factor_without_losses_and_breakeven():
    m, _ = compute_metrics([_t(100), _t(0), _t(50, "2026-01-02")], 100000)
    assert m["profit_factor"] is None and m["profit_factor_display"] == "∞"
    assert m["losses"] == 0 and m["breakeven"] == 1 and m["wins"] == 2
    m0, _ = compute_metrics([], 100000)
    assert m0["profit_factor"] is None and m0["profit_factor_display"] == "–" and m0["trades"] == 0


def test_sharpe_counts_idle_days_and_drawdown_pct_vs_peak():
    trades = [_t(1000, "2026-01-01"), _t(-500, "2026-01-02"), _t(800, "2026-01-05")]
    m_trade_days, _ = compute_metrics(trades, 100000)
    days = ["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
    m_all, _ = compute_metrics(trades, 100000, session_days=days)
    assert m_all["sharpe_daily_annualised"] < m_trade_days["sharpe_daily_annualised"]
    assert m_all["session_days"] == 6
    assert m_all["max_drawdown"] == 500 and m_all["max_drawdown_pct"] == pytest.approx(500 / 101000 * 100, abs=0.01)
