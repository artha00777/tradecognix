from datetime import date

import pytest

from app.backtest import rates as R
from app.backtest.costs import CostConfig, apply_slippage, order_costs


def test_option_sell_after_apr_2026_hand_computed():
    # Sell 65 NIFTY @ 100 on 2026-11-20 -> turnover 6500
    c = order_costs("sell", 100.0, 65, "options", "NSE", date(2026, 11, 20))
    assert c.brokerage == 20.0
    assert c.stt == pytest.approx(6500 * 0.0015)           # 9.75 (0.15% from 1 Apr 2026)
    assert c.exchange == pytest.approx(6500 * (0.0003503 + 0.000005))
    assert c.sebi == pytest.approx(6500 * 10 / 1e7)
    assert c.stamp == 0.0
    assert c.gst == pytest.approx((20 + c.exchange + c.sebi) * 0.18)
    assert c.total == pytest.approx(c.brokerage + c.stt + c.exchange + c.sebi + c.stamp + c.gst)


def test_option_sell_fy26_rate():
    c = order_costs("sell", 100.0, 65, "options", "NSE", date(2026, 1, 15))
    assert c.stt == pytest.approx(6500 * 0.001)


def test_option_buy_has_stamp_no_stt():
    c = order_costs("buy", 200.0, 75, "options", "NSE", date(2026, 6, 1))
    assert c.stt == 0
    assert c.stamp == pytest.approx(15000 * 0.00003)


def test_configurable_brokerage_and_stt_override():
    cfg = CostConfig(brokerage_per_order=0, stt_override=0.001)
    c = order_costs("sell", 100, 100, "options", "NSE", date(2026, 6, 1), cfg)
    assert c.brokerage == 0 and c.stt == pytest.approx(10)


def test_equity_intraday_brokerage_cap():
    small = order_costs("buy", 100, 10, "equity_intraday", "NSE", date(2026, 6, 1))
    assert small.brokerage == pytest.approx(1000 * 0.0003)
    big = order_costs("sell", 2500, 100, "equity_intraday", "NSE", date(2026, 6, 1))
    assert big.brokerage == 20 and big.stt == pytest.approx(250000 * 0.00025)


def test_futures_stt_schedule():
    assert R.stt_rate("futures_sell", date(2026, 3, 31)) == 0.0002
    assert R.stt_rate("futures_sell", date(2026, 4, 1)) == 0.0005


def test_slippage_direction_and_tick():
    assert apply_slippage(100.0, "buy", 0.5) == 100.5
    assert apply_slippage(100.0, "sell", 0.5) == 99.5
    assert apply_slippage(0.04, "sell", 50) == 0.05  # floored at one tick
    assert round(apply_slippage(123.33, "buy", 0.1) / 0.05, 6).is_integer()


def test_default_slippage_is_segment_aware():
    from app.backtest.costs import DEFAULT_SLIPPAGE_PCT
    cfg = CostConfig()
    assert cfg.slippage_for("options") == DEFAULT_SLIPPAGE_PCT["options"] == 0.5
    assert cfg.slippage_for("futures") == 0.01
    assert CostConfig(slippage_pct=0.2).slippage_for("futures") == 0.2  # explicit value wins

