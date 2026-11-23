"""Engine / runner / broker-adapter hardening tests."""
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from app.brokers.base import BrokerError, Instrument, OrderResult, candles_frame, normalize_status, pick_contract
from app.brokers.paper import PaperBroker
from app.engine.feeds import SyntheticFeed
from app.engine.runner import StrategyRunner
from app.security import mask, scrub
from app.strategy.parser import RuleBasedParser

P = RuleBasedParser()


class FlakyBroker(PaperBroker):
    """Paper broker that can reject chosen orders (by call number) and fake live order states."""

    def __init__(self, *a, reject_calls=(), pending_then=None, **kw):
        super().__init__(*a, **kw)
        self.calls = 0
        self.reject_calls = set(reject_calls)
        self.pending_then = pending_then  # status the order book reports for a pending order
        self.book = {}

    def place_order(self, req):
        self.calls += 1
        if self.calls in self.reject_calls:
            return OrderResult(order_id=f"R{self.calls}", status="rejected", message="RMS: insufficient margin")
        res = super().place_order(req)
        if self.pending_then:
            self.book[res.order_id] = {"order_id": res.order_id, "status": self.pending_then, "avg_price": res.avg_price, "raw": {"text": "x"}}
            return OrderResult(order_id=res.order_id, status="open")
        return res

    def order_book(self):
        return list(self.book.values())


def _setup(text, broker_cls=PaperBroker, mode="paper", **bkw):
    s = P.parse(text).strategy
    feed = SyntheticFeed(s.underlying, seed=3, days=5)
    broker = broker_cls(price_source=feed.price, slippage_pct=0.5, **bkw)
    trades, orders = [], []
    r = StrategyRunner(s, broker, "TCTEST", mode, on_trade=trades.append, on_order=orders.append)
    r.sleep = lambda _s: None
    day = feed._base_for(datetime(2026, 11, 20, 15, 30)).index.normalize().unique()[-1]
    return s, feed, r, trades, orders, day


def _at(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    return (day + pd.Timedelta(hours=h, minutes=m)).to_pydatetime()


def test_rejected_entry_leaves_no_position():
    s, feed, r, trades, orders, day = _setup("Buy Nifty ATM call at 9:30 AM, exit at 3:15 pm", FlakyBroker, reject_calls={1})
    now = _at(day, "09:30")
    feed.now = now
    res = r.on_candle_close(feed.candles("5m", now), now)
    assert res["action"] == "error" and r.state["position"] is None
    assert orders[0]["status"] == "rejected"


def test_partial_multi_leg_entry_is_unwound():
    # straddle: CE fills, PE rejected -> CE must be bought back, no naked short call left
    s, feed, r, trades, orders, day = _setup("Sell Nifty ATM straddle at 9:20 AM. Exit everything at 3:10 PM.", FlakyBroker, reject_calls={2})
    now = _at(day, "09:20")
    feed.now = now
    r.on_candle_close(feed.candles("5m", now), now)
    assert r.state["position"] is None
    assert [o["reason"] for o in orders] == ["entry", "entry", "entry_unwind"]
    pos = {p["symbol"]: p["net_qty"] for p in r.broker.positions()}
    assert all(q == 0 for q in pos.values())


def test_rejected_exit_keeps_position_and_retries():
    s, feed, r, trades, orders, day = _setup("Buy Nifty ATM call at 9:30 AM, exit at 3:15 pm", FlakyBroker, reject_calls={2})
    t = _at(day, "09:30")
    feed.now = t
    r.on_candle_close(feed.candles("5m", t), t)
    t2 = _at(day, "15:15")
    feed.now = t2
    r.on_minute(t2)  # hard exit rejected
    assert r.state["position"] is not None and not trades
    t3 = _at(day, "15:16")
    feed.now = t3
    r.on_minute(t3)  # retried next minute
    assert r.state["position"] is None and trades[0]["exit_reason"] == "hard_exit"


def test_live_mode_confirms_fill_via_order_book():
    s, feed, r, trades, orders, day = _setup("Buy Nifty ATM call at 9:30 AM, exit at 3:15 pm", FlakyBroker, mode="live", pending_then="complete")
    t = _at(day, "09:30")
    feed.now = t
    assert r.on_candle_close(feed.candles("5m", t), t)["action"] == "entry"
    assert orders[0]["status"] == "filled"


def test_live_mode_order_rejected_later_is_not_a_position():
    s, feed, r, trades, orders, day = _setup("Buy Nifty ATM call at 9:30 AM, exit at 3:15 pm", FlakyBroker, mode="live", pending_then="REJECTED")
    t = _at(day, "09:30")
    feed.now = t
    assert r.on_candle_close(feed.candles("5m", t), t)["action"] == "error"
    assert r.state["position"] is None


def test_stale_bar_is_never_traded():
    s, feed, r, trades, orders, day = _setup("Buy Nifty ATM call at 9:30 AM, exit at 3:15 pm")
    t = _at(day, "09:30")
    feed.now = t
    candles = feed.candles("5m", t)
    later = t + timedelta(minutes=30)  # same candles seen half an hour later (feed stalled)
    assert r.on_candle_close(candles, later)["reason"] == "stale_data"
    assert r.state["position"] is None


def test_runner_trailing_stop():
    s, feed, r, trades, orders, day = _setup("Buy Nifty ATM call at 9:30 AM, trailing stop 10%, exit at 3:15 pm")
    assert s.exit.trailing_sl_pct == 10
    px = {"v": 100.0}
    r.broker.price_source = lambda inst: px["v"]
    t = _at(day, "09:30")
    feed.now = t
    r.on_candle_close(feed.candles("5m", t), t)
    entry = r.state["position"]["legs"][0]["entry"]
    for v, mins in ((120.0, 1), (125.0, 2)):
        px["v"] = v
        r.on_minute(t + timedelta(minutes=mins))
    assert r.state["position"] is not None
    px["v"] = 125 * 0.89  # >10% off the 125 peak
    r.on_minute(t + timedelta(minutes=3))
    assert r.state["position"] is None and trades[0]["exit_reason"] == "trailing_stop" and entry > 0


def test_engine_tick_market_closed_and_no_double_processing(client):
    from conftest import register

    h = register(client)
    admin = register(client, "admin@devcognix.com")
    s = client.post("/api/strategies", json={"prompt": "Buy Nifty ATM call at 9:30 AM. Exit at 3:15 PM. Max 1 trade a day."}, headers=h).json()
    sid = client.post("/api/engine/start", json={"strategy_id": s["id"], "mode": "paper"}, headers=h).json()["session_id"]
    d = datetime.now().date()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    early = datetime.combine(d, datetime.strptime("08:00", "%H:%M").time())
    res = client.post("/api/engine/tick", json={"now": early.isoformat()}, headers=admin).json()["results"]
    assert res[str(sid)]["reason"] == "market_closed"
    t = datetime.combine(d, datetime.strptime("09:30", "%H:%M").time())
    first = client.post("/api/engine/tick", json={"now": t.isoformat()}, headers=admin).json()["results"][str(sid)]
    second = client.post("/api/engine/tick", json={"now": t.isoformat()}, headers=admin).json()["results"][str(sid)]
    assert first["action"] == "entry" and second["reason"] == "bar_already_processed"
    assert len(client.get("/api/engine/orders", headers=h).json()["orders"]) == 1


# ---------------- broker helpers ----------------
def _opt(exp):
    return Instrument("X", "1", "NFO", expiry=exp, strike=24600, option_type="CE")


def test_pick_contract_handles_holiday_shift_and_expired():
    today = date(2026, 11, 20)
    computed = date(2026, 11, 24)  # Tuesday; holiday moves the real expiry to Monday 23rd
    c = pick_contract([_opt(date(2026, 11, 23)), _opt(date(2026, 12, 1))], computed, today)
    assert c.expiry == date(2026, 11, 23)  # old code picked next week's 1 Dec contract
    with pytest.raises(BrokerError):
        pick_contract([_opt(date(2026, 11, 17))], computed, today)  # never an expired contract


def test_normalize_status_and_candles_frame_offsets():
    assert normalize_status("COMPLETE") == "filled" and normalize_status("Rejected") == "rejected"
    assert normalize_status("open") == "pending" and normalize_status("cancelled") == "cancelled"
    df = candles_frame([["2026-11-20T09:15:00+05:30", 1, 2, 0.5, 1.5, None]])
    assert df.index[0] == pd.Timestamp("2026-11-20 09:15") and df["volume"].iloc[0] == 0
    assert candles_frame([]).empty


def test_upstox_expiry_is_ist_date():
    from app.brokers.upstox import UpstoxAdapter

    # 2026-11-24 00:00 IST == 2026-11-23 18:30 UTC
    ms = int(pd.Timestamp("2026-11-24 00:00", tz="Asia/Kolkata").timestamp() * 1000)
    inst = UpstoxAdapter._to_inst({"instrument_key": "NSE_FO|1", "expiry": ms, "instrument_type": "CE"})
    assert inst.expiry == date(2026, 11, 24)


def test_paper_broker_rejects_bad_orders():
    from app.brokers.base import OrderRequest

    b = PaperBroker(price_source=lambda i: 0.0)
    assert b.place_order(OrderRequest(Instrument("A", "1", "NSE"), "BUY", 1)).status == "rejected"
    assert b.place_order(OrderRequest(Instrument("A", "1", "NSE"), "BUY", 0)).status == "rejected"


def test_mask_and_scrub():
    assert mask("1234") == "••••" and mask("ABCDEFGHIJKL").endswith("IJKL") and mask("ABCDEFGH") == "••••••GH"
    assert scrub("login failed for key SECRETKEY99 pin 1234", {"api_key": "SECRETKEY99", "pin": "1234"}) == "login failed for key *** pin ***"
