"""Candle/price feeds for running sessions: a real broker, or a synthetic demo feed."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from ..backtest import options as opt
from ..brokers.base import BrokerAdapter, Instrument
from ..data.candles import TF_MINUTES, base_timeframe_for, resample, synthetic_bar_minutes, to_ist_naive_index
from ..data.synthetic import generate_candles
from ..strategy.evaluate import bar_close_datetimes
from .clock import ist_now


class SyntheticFeed:
    """Deterministic synthetic underlying + Black-Scholes option premiums. Demo/paper only."""

    def __init__(self, underlying: str, seed: int = 11, days: int = 10):
        self.underlying = underlying.upper()
        self.seed = seed
        self.days = days
        self._cache_key = None
        self._base: pd.DataFrame | None = None
        self.bar_minutes = 5
        self.now: datetime | None = None

    def _base_for(self, now: datetime, bar_minutes: int | None = None) -> pd.DataFrame:
        bm = bar_minutes or self.bar_minutes
        key = (now.date(), bm)
        if self._cache_key != key or self._base is None:
            self._base = generate_candles(self.underlying, days=self.days, seed=self.seed, end=now.date(), bar_minutes=bm)
            self._cache_key = key
            self.bar_minutes = bm
        return self._base

    def candles(self, timeframe: str, now: datetime) -> pd.DataFrame:
        self.now = now
        bm = synthetic_bar_minutes(timeframe)
        base = self._base_for(now, bm)
        closed = base[base.index + pd.Timedelta(minutes=bm) <= now]
        if closed.empty:
            return closed
        df = resample(closed, timeframe)
        mins = TF_MINUTES[timeframe]
        if mins < 1440:
            df = df[bar_close_datetimes(df.index, timeframe) <= now]
        return df

    def spot(self, now: datetime | None = None) -> float:
        now = now or self.now or ist_now()
        base = self._base_for(now)
        closed = base[base.index + pd.Timedelta(minutes=self.bar_minutes) <= now]
        return float((closed if not closed.empty else base)["close"].iloc[-1])

    def price(self, inst: Instrument) -> float:
        now = self.now or ist_now()
        s = self.spot(now)
        if inst.option_type and inst.strike and inst.expiry:
            return opt.bs_price(s, float(inst.strike), opt.years_to_expiry(now, inst.expiry), opt.R.DEFAULT_IV, opt.R.RISK_FREE, inst.option_type)
        return s


class BrokerFeed:
    """Real candles from a broker's historical API (index/equity instrument)."""

    def __init__(self, broker: BrokerAdapter, instrument: Instrument):
        self.broker = broker
        self.instrument = instrument

    def candles(self, timeframe: str, now: datetime) -> pd.DataFrame:
        """Closed candles up to `now` (IST naive). Fetches a base interval every broker supports
        (1m/5m/1d) and resamples, so 3m/10m/30m strategies work on brokers without those intervals.
        Indicator warm-up: 5 calendar days for <=15m bars, 15 days for slower ones, 400 for daily."""
        mins = TF_MINUTES[timeframe]
        base_tf = base_timeframe_for(timeframe)
        lookback = timedelta(days=400 if mins >= 1440 else (5 if mins <= 15 else 15))
        df = self.broker.historical_candles(self.instrument, base_tf, now - lookback, now)
        if df is None or df.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = to_ist_naive_index(df)
        if mins < 1440:
            base_mins = TF_MINUTES[base_tf]
            df = df[df.index + pd.Timedelta(minutes=base_mins) <= now]  # only closed base bars
            df = resample(df, timeframe)
            df = df[bar_close_datetimes(df.index, timeframe) <= now]  # drop the still-forming bar
        else:
            df = resample(df, timeframe)
            df = df[df.index.normalize() < pd.Timestamp(now.date())] if now.hour * 60 + now.minute < 15 * 60 + 30 else df
        return df
