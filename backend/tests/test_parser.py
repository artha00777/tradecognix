import pytest

from app.strategy.explain import explain, rules_table
from app.strategy.parser import RuleBasedParser, parse_strategy

P = RuleBasedParser()
VIDEO = "Buy Nifty ATM call when 9 EMA crosses above 21 EMA on 15-min candles after 9:30 AM. Exit on opposite cross or 3:15 PM. Max 2 trades a day."


def test_video_ema_crossover():
    s = P.parse(VIDEO).strategy
    assert s.underlying == "NIFTY" and s.instrument_type == "option"
    assert s.option.mode == "atm_buy" and s.direction == "long"
    assert s.timeframe == "15m"
    assert {i.id for i in s.indicators} == {"ema9", "ema21"}
    c = s.entry.long[0]
    assert (c.left, c.op, c.right) == ("ema9", "crosses_above", "ema21")
    assert s.entry_window.start == "09:30"
    assert s.hard_exit == "15:15"
    assert s.exit.on_opposite_signal is True
    assert s.risk.max_trades_per_day == 2


def test_straddle_fixed_time():
    s = P.parse("Sell BankNifty ATM straddle at 9:20 AM. Stop loss 25% on each leg. Exit everything at 3:10 PM. Max 1 trade a day.").strategy
    assert s.underlying == "BANKNIFTY" and s.option.mode == "straddle_sell" and s.direction == "neutral"
    assert s.entry.long[0].op == "at_time" and s.entry.long[0].right == "09:20"
    assert s.exit.stop_loss_pct == 25 and s.hard_exit == "15:10" and s.risk.max_trades_per_day == 1
    assert s.option.expiry == "monthly"  # BankNifty has no weeklies


def test_orb_put():
    s = P.parse("Buy Nifty ATM put when price breaks below the 9:15 to 9:30 opening range on 5-min candles. Stop loss 1%, target 2%. Exit by 3:15 PM.").strategy
    assert s.direction == "short" and s.option.mode == "atm_buy"
    orb = next(i for i in s.indicators if i.kind == "orb")
    assert (orb.start, orb.end) == ("09:15", "09:30")
    assert s.entry.short[0].right == "orb.low"
    assert s.exit.stop_loss_pct == 1 and s.exit.target_pct == 2


def test_rsi_equity_with_loss_cap():
    s = P.parse("Buy Reliance when RSI 14 drops below 30 on 30-min candles and sell when RSI crosses above 60. Max 3 trades a day, daily loss limit 5000.").strategy
    assert s.underlying == "RELIANCE" and s.instrument_type == "equity" and s.timeframe == "30m"
    assert s.entry.long[0].left == "rsi14" and s.entry.long[0].op == "crosses_below" and s.entry.long[0].right == 30
    assert s.exit.long[0].op == "crosses_above" and s.exit.long[0].right == 60
    assert s.risk.daily_loss_cap == 5000


def test_supertrend_futures():
    s = P.parse("Buy Nifty futures when Supertrend 10,3 turns green on 30-min candles. Exit when Supertrend turns red.").strategy
    assert s.instrument_type == "future"
    st = s.indicators[0]
    assert st.kind == "supertrend" and st.period == 10 and st.multiplier == 3
    assert s.entry.long[0].left == "st.dir" and s.exit.long[0].op == "crosses_below"


def test_vwap_pullback_with_ema_filter():
    s = P.parse("Buy HDFC Bank when price pulls back to VWAP on 5-min candles after 10:00 AM while above 21 EMA. Stop loss 0.5%, exit at 3:15 PM.").strategy
    assert s.underlying == "HDFCBANK" and s.entry_window.start == "10:00"
    kinds = {i.kind for i in s.indicators}
    assert kinds == {"vwap", "ema"}
    assert len(s.entry.long) == 3


@pytest.mark.parametrize("text,mode,direction", [
    ("Nifty options 9/21 EMA crossover both directions on 5 min, ATM sell the opposite option, hard exit 3:15", "atm_sell", "both"),
    ("Buy Nifty ITM put when 9 EMA crosses below 21 EMA on 5 min", "itm_buy", "short"),
    ("Sell Nifty ATM put when 9 EMA crosses above 21 EMA on 15 minute candles", "atm_sell", "long"),
    ("Sensex 9 21 ema crossover on 5m both ways, atm buy", "atm_buy", "both"),
])
def test_option_modes(text, mode, direction):
    s = P.parse(text).strategy
    assert s.option.mode == mode and s.direction == direction


def test_lots_times_and_timeframes():
    s = P.parse("Buy FinNifty ATM call when 20 EMA crosses above 50 EMA on 1 hour candles between 10:00 and 2:30 pm, square off 3:00 pm, 3 lots, SL 30%").strategy
    assert s.underlying == "FINNIFTY" and s.timeframe == "1h" and s.risk.lots == 3
    assert s.entry_window.start == "10:00" and s.entry_window.end == "14:30" and s.hard_exit == "15:00"
    assert s.exit.stop_loss_pct == 30


def test_hinglish():
    s = P.parse("Nifty ITM put kharido jab 9 ema 21 ema ke neeche cross kare, 15 min").strategy
    assert s.option.mode == "itm_buy" and s.direction == "short" and s.timeframe == "15m"


def test_unparseable_raises():
    with pytest.raises(ValueError):
        P.parse("make me money please")


def test_parse_strategy_falls_back_without_llm():
    res = parse_strategy(VIDEO)
    assert res.method == "rules"


def test_llm_failure_falls_back(monkeypatch):
    import app.strategy.llm as llm

    monkeypatch.setattr(llm, "llm_available", lambda: True)
    monkeypatch.setattr(llm, "llm_parse", lambda text: (_ for _ in ()).throw(RuntimeError("quota")))
    res = parse_strategy(VIDEO)
    assert res.method == "rules" and "LLM parse failed" in res.warnings[0]


def test_llm_json_validated(monkeypatch):
    import app.strategy.llm as llm

    spec = P.parse(VIDEO).strategy.model_dump()
    monkeypatch.setattr(llm, "llm_available", lambda: True)
    monkeypatch.setattr(llm, "call_gemini", lambda text: "```json\n" + __import__("json").dumps(spec) + "\n```")
    monkeypatch.setattr(llm, "get_settings", lambda: type("S", (), {"gemini_api_key": "x"})())
    res = parse_strategy(VIDEO)
    assert res.method == "llm" and res.strategy.timeframe == "15m"


def test_explain_round_trip():
    s = P.parse(VIDEO).strategy
    text = explain(s)
    assert "EMA 9 crosses above EMA 21" in text and "15:15" in text and "call (CE)" in text
    assert rules_table(s)["Timeframe"] == "15m"
