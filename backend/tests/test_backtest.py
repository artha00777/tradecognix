import pandas as pd
import pytest

from app.backtest import options as opt
from app.backtest.engine import run_backtest
from app.backtest.metrics import compute_metrics, detect_whipsaws, max_drawdown
from app.backtest.sweep import sweep
from app.data.candles import resample
from app.strategy.parser import RuleBasedParser

P = RuleBasedParser()
VIDEO = "Buy Nifty ATM call when 9 EMA crosses above 21 EMA on 15-min candles after 9:30 AM. Exit on opposite cross or 3:15 PM. Max 2 trades a day."


def _run(text, candles_5m, **kw):
    s = P.parse(text).strategy
    return s, run_backtest(s, resample(candles_5m, s.timeframe), "synthetic", **kw)


def test_ema_backtest_produces_trades_and_consistent_pnl(candles_5m):
    s, res = _run(VIDEO, candles_5m)
    m = res.metrics
    assert m["trades"] > 5
    for t in res.trades:
        assert t["net_pnl"] == pytest.approx(t["gross_pnl"] - t["charges"]["total"] - t["slippage"], abs=0.05)
        assert t["exit_time"][11:16] <= "15:15"
        assert t["entry_time"][11:16] >= "09:30"
        assert t["direction"] == "long" and t["legs"][0]["type"] == "CE" and t["legs"][0]["side"] == "buy"
    assert m["net_pnl"] == pytest.approx(sum(t["net_pnl"] for t in res.trades), abs=0.5)
    assert m["total_costs"] > 0
    assert "premium_proxy_black_scholes" in res.flags and "synthetic_underlying_data" in res.flags


def test_max_trades_per_day_respected(candles_5m):
    s, res = _run("Nifty 9/21 EMA crossover both directions on 5 min ATM buy, max 2 trades a day, exit on opposite cross", candles_5m)
    per_day = pd.Series([t["date"] for t in res.trades]).value_counts()
    assert per_day.max() <= 2


def test_atm_sell_sells_opposite_option(candles_5m):
    s, res = _run("Nifty 9/21 EMA crossover both directions on 15 min, ATM sell the opposite option", candles_5m)
    for t in res.trades:
        leg = t["legs"][0]
        assert leg["side"] == "sell"
        assert leg["type"] == ("PE" if t["direction"] == "long" else "CE")


def test_straddle_two_legs_one_trade_per_day(candles_5m):
    s, res = _run("Sell Nifty ATM straddle at 9:20 AM. Stop loss 25% on each leg. Exit everything at 3:10 PM. Max 1 trade a day.", candles_5m)
    assert res.trades
    assert all(len(t["legs"]) == 2 for t in res.trades)
    assert pd.Series([t["date"] for t in res.trades]).value_counts().max() == 1
    assert all(t["exit_time"][11:16] <= "15:10" for t in res.trades)


def test_daily_loss_cap_halts(candles_5m):
    s, res = _run("Nifty 9/21 EMA crossover both directions on 5 min ATM buy, max 10 trades a day, daily loss limit 1000", candles_5m)
    by_day = {}
    for t in res.trades:
        by_day.setdefault(t["date"], []).append(t)
    for day, ts in by_day.items():
        running = 0
        for k, t in enumerate(ts):
            assert running > -1000 or k == 0, f"traded after loss cap on {day}"
            running += t["net_pnl"]


def test_equity_rsi_and_supertrend_futures_run(candles_5m):
    _, r1 = _run("Buy Reliance when RSI 14 drops below 30 on 30-min candles and sell when RSI crosses above 60.", candles_5m)
    _, r2 = _run("Buy Nifty futures when Supertrend 10,3 turns green on 15-min candles. Exit when Supertrend turns red.", candles_5m)
    assert r2.metrics["trades"] > 0
    assert all(t["legs"][0]["type"] == "FUT" for t in r2.trades)
    assert r1.metrics["trades"] >= 0


def test_stop_loss_exits(candles_5m):
    _, res = _run("Buy Nifty ATM call when 9 EMA crosses above 21 EMA on 5 min. Stop loss 10%, target 20%.", candles_5m)
    reasons = {t["exit_reason"] for t in res.trades}
    assert reasons & {"stop_loss", "target"}
    for t in res.trades:
        leg = t["legs"][0]
        if leg["exit_reason"] == "stop_loss":
            # fills at the stop, or at the open when the bar gaps through it (never better than the stop)
            assert leg["exit_ref"] <= leg["entry_ref"] * 0.9 + 0.02


def test_strike_selection_and_expiry():
    assert opt.atm_strike(24612.35, 50) == 24600
    assert opt.select_strike(24612, 50, "CE", 1) == 24550  # ITM call is lower
    assert opt.select_strike(24612, 50, "PE", 1) == 24650  # ITM put is higher
    assert opt.select_strike(52180, 100, "CE", -2) == 52400  # OTM call
    from datetime import date
    assert opt.next_expiry("NIFTY", date(2026, 11, 20)).weekday() == 1  # Tuesday weekly
    m = opt.next_expiry("BANKNIFTY", date(2026, 11, 20), "weekly")
    assert m.month == 11 and m.weekday() == 1 and m.day > 23  # last Tuesday (monthly only)


def test_black_scholes_sanity():
    c = opt.bs_price(24600, 24600, 5 / 365, 0.14, 0.065, "CE")
    p = opt.bs_price(24600, 24600, 5 / 365, 0.14, 0.065, "PE")
    assert 100 < c < 400 and 100 < p < 400
    # put-call parity
    import math
    assert c - p == pytest.approx(24600 - 24600 * math.exp(-0.065 * 5 / 365), abs=0.01)


def test_metrics_and_whipsaw():
    trades = [
        {"net_pnl": 100, "gross_pnl": 150, "bars_held": 5, "exit_reason": "exit_signal", "date": "2026-01-01", "exit_time": "a", "charges": {"total": 50}, "slippage": 0},
        {"net_pnl": -50, "gross_pnl": -20, "bars_held": 1, "exit_reason": "exit_signal", "date": "2026-01-01", "exit_time": "b", "charges": {"total": 30}, "slippage": 0},
        {"net_pnl": -80, "gross_pnl": -60, "bars_held": 6, "exit_reason": "hard_exit", "date": "2026-01-02", "exit_time": "c", "charges": {"total": 20}, "slippage": 0},
        {"net_pnl": 200, "gross_pnl": 230, "bars_held": 3, "exit_reason": "target", "date": "2026-01-02", "exit_time": "d", "charges": {"total": 30}, "slippage": 0},
    ]
    m, eq = compute_metrics(trades, 100000)
    assert m["trades"] == 4 and m["win_rate"] == 50
    assert m["profit_factor"] == pytest.approx(300 / 130, abs=0.01)
    assert m["expectancy"] == pytest.approx(42.5)
    assert m["max_drawdown"] == pytest.approx(130)
    assert m["payoff_ratio"] == pytest.approx(150 / 65, abs=0.01)
    assert detect_whipsaws(trades) == [1]
    assert eq[-1]["equity"] == 170
    dd, peak, trough = max_drawdown(__import__("numpy").array([0, 100, 50, -30, 80]))
    assert dd == 130 and peak == 1 and trough == 3


def test_sweep_grid(candles_5m):
    s = P.parse(VIDEO).strategy
    rows = sweep(s, candles_5m, ["5m", "15m"], ["atm_buy", "atm_sell"], ["09:30", "10:00"])
    assert len(rows) == 8
    assert all("error" not in r for r in rows)
    nets = [r["net_pnl"] for r in rows]
    assert nets == sorted(nets, reverse=True)


def test_futures_backtest_slippage_is_sane(candles_5m):
    """Regression: a NIFTY futures EMA cross showed slippage ~8x the gross loss because 0.5%
    (meant for option premium) was applied to a ~25,000 futures price on every fill."""
    s, res = _run("Buy NIFTY futures when EMA 9 crosses above EMA 21 on 15 minute candles, exit on opposite cross, max 2 trades a day", candles_5m)
    assert s.instrument_type in ("future", "index")
    assert res.trades
    for t in res.trades:
        notional = t["legs"][0]["entry_ref"] * t["qty"]
        # two fills at the 0.01% futures default, plus up to a tick of rounding each side
        assert t["slippage"] <= notional * 0.0003 + 0.05 * 2 * t["qty"] + 0.01
        assert t["net_pnl"] == pytest.approx(t["gross_pnl"] - t["charges"]["total"] - t["slippage"], abs=0.05)
