from datetime import datetime

import pytest

from app.brokers.paper import PaperBroker
from app.config import Settings
from app.engine.feeds import SyntheticFeed
from app.engine.guardrails import GuardrailError, assert_live_allowed
from app.engine.runner import StrategyRunner
from app.strategy.parser import RuleBasedParser

P = RuleBasedParser()


def _runner(text, trades, orders):
    s = P.parse(text).strategy
    feed = SyntheticFeed(s.underlying, seed=3, days=5)
    broker = PaperBroker(price_source=feed.price, slippage_pct=0.5)
    r = StrategyRunner(s, broker, "TC1TEST", "paper", on_trade=trades.append, on_order=orders.append)
    return s, feed, r


def test_live_guardrails():
    off = Settings(live_trading_enabled=False)
    on = Settings(live_trading_enabled=True)
    with pytest.raises(GuardrailError, match="disabled"):
        assert_live_allowed(off, True, "LIVE", "angelone", "live")
    with pytest.raises(GuardrailError, match="confirmed"):
        assert_live_allowed(on, False, "LIVE", "angelone", "live")
    with pytest.raises(GuardrailError, match="Type LIVE"):
        assert_live_allowed(on, True, "live?", "angelone", "live")
    with pytest.raises(GuardrailError, match="paper"):
        assert_live_allowed(on, True, "LIVE", "paper", "live")
    with pytest.raises(GuardrailError, match="not implemented"):
        assert_live_allowed(on, True, "LIVE", "groww", "skeleton")
    assert_live_allowed(on, True, "LIVE", "angelone", "live")


def test_paper_runner_full_day_replay():
    trades, orders = [], []
    s, feed, r = _runner("Sell Nifty ATM straddle at 9:20 AM. Stop loss 25% on each leg. Exit everything at 3:10 PM. Max 1 trade a day.", trades, orders)
    day = feed._base_for(datetime(2026, 11, 20, 15, 30)).index.normalize().unique()[-1]
    t = day + __import__("pandas").Timedelta(hours=9, minutes=20)
    end = day + __import__("pandas").Timedelta(hours=15, minutes=30)
    while t <= end:
        now = t.to_pydatetime()
        feed.now = now
        r.on_candle_close(feed.candles("5m", now), now)
        t += __import__("pandas").Timedelta(minutes=5)
    assert len(trades) == 1
    assert orders[0]["tag"] == "TC1TEST" and orders[0]["reason"] == "entry"
    assert len([o for o in orders if o["reason"] == "entry"]) == 2  # two legs
    assert trades[0]["exit_time"][11:16] <= "15:10"
    assert r.state["position"] is None and r.state["trades_today"] == 1


def test_kill_switch_squares_off_and_blocks():
    trades, orders = [], []
    s, feed, r = _runner("Buy Nifty ATM call at 9:30 AM, exit at 3:15 pm", trades, orders)
    now = datetime.combine(feed._base_for(datetime.now()).index[-1].date(), datetime.min.time()).replace(hour=9, minute=30)
    feed.now = now
    r.on_candle_close(feed.candles("5m", now), now)
    assert r.state["position"] is not None
    r.kill(now)
    assert r.state["position"] is None and trades[-1]["exit_reason"] == "kill_switch"
    later = now.replace(hour=10)
    feed.now = later
    res = r.on_candle_close(feed.candles("5m", later), later)
    assert r.state["position"] is None and res["action"] in ("none", "skip")


def test_paper_broker_accounting():
    from app.brokers.base import Instrument, OrderRequest

    px = {"v": 100.0}
    b = PaperBroker(price_source=lambda i: px["v"], slippage_pct=0)
    inst = Instrument("NIFTYTEST", "1", "NFO", option_type="CE", strike=24600, instrument_type="OPTIDX")
    b.place_order(OrderRequest(inst, "BUY", 65, tag="T"))
    px["v"] = 110.0
    b.place_order(OrderRequest(inst, "SELL", 65, tag="T"))
    pos = b.positions()[0]
    assert pos["net_qty"] == 0 and pos["realised"] == pytest.approx(650)
    assert pos["pnl"] == pytest.approx(650 - pos["costs"])
    assert len(b.order_book()) == 2
