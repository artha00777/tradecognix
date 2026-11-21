"""Candle/price feeds for running sessions: a real broker, or a synthetic demo feed."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from ..backtest import options as opt
from ..brokers.base import BrokerAdapter, Instrument
from ..data.candles import TF_MINUTES, resample
from ..data.synthetic import generate_candles


class SyntheticFeed:
    """Deterministic synthetic underlying + Black-Scholes option premiums. Demo/paper only."""

    def __init__(self, underlying: str, seed: int = 11, days: int = 10):
        self.underlying = underlying.upper()
        self.seed = seed
        self.days = days
        self._cache_day = None
        self._base: pd.DataFrame | None = None
        self.now: datetime | None = None

    def _base_for(self, now: datetime) -> pd.DataFrame:
        if self._cache_day != now.date() or self._base is None:
            self._base = generate_candles(self.underlying, days=self.days, seed=self.seed, end=now.date())
            self._cache_day = now.date()
        return self._base

    def candles(self, timeframe: str, now: datetime) -> pd.DataFrame:
        self.now = now
        base = self._base_for(now)
        closed = base[base.index + pd.Timedelta(minutes=5) <= now]
        df = resample(closed, timeframe)
        mins = TF_MINUTES[timeframe]
        if mins < 1440:
            df = df[df.index + pd.Timedelta(minutes=mins) <= now]
        return df

    def spot(self, now: datetime | None = None) -> float:
        now = now or self.now or datetime.now()
        base = self._base_for(now)
        closed = base[base.index + pd.Timedelta(minutes=5) <= now]
        return float((closed if not closed.empty else base)["close"].iloc[-1])

    def price(self, inst: Instrument) -> float:
        now = self.now or datetime.now()
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
        lookback = timedelta(days=5 if TF_MINUTES[timeframe] <= 15 else 15)
        df = self.broker.historical_candles(self.instrument, timeframe, now - lookback, now)
        mins = TF_MINUTES[timeframe]
        if mins < 1440 and not df.empty:
            df = df[df.index + pd.Timedelta(minutes=mins) <= now]  # drop the still-forming bar
        return df
