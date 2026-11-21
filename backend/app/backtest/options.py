"""Option strike selection, expiry calendar and a Black-Scholes premium proxy.

When real option candles are unavailable (the default without a broker history feed), option
premiums are *estimated* from the underlying with Black-Scholes and a constant, VIX-like IV.
Results computed this way are flagged `premium_proxy_black_scholes` and must be treated as
indicative only: real premiums include skew, IV crush/expansion and bid-ask spreads.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

from . import rates as R

OptType = Literal["CE", "PE"]
PROXY_FLAG = "premium_proxy_black_scholes"


@dataclass(frozen=True)
class LegTemplate:
    opt_type: OptType
    side: Literal["buy", "sell"]
    moneyness_steps: int  # +n = ITM by n steps, -n = OTM by n steps, 0 = ATM


def strike_step(underlying: str) -> int:
    return R.STRIKE_STEP.get(underlying.upper(), 50)


def lot_size(underlying: str) -> int:
    return R.LOT_SIZE.get(underlying.upper(), 1)


def atm_strike(spot: float, step: int) -> int:
    return int(round(spot / step) * step)


def select_strike(spot: float, step: int, opt_type: OptType, moneyness_steps: int = 0) -> int:
    """ATM +/- steps. ITM call = lower strike, ITM put = higher strike."""
    atm = atm_strike(spot, step)
    shift = moneyness_steps * step
    return atm - shift if opt_type == "CE" else atm + shift


def legs_for(mode: str, signal: Literal["long", "short"], strike_steps: int = 1) -> list[LegTemplate]:
    """Map an option mode + signal direction to legs.

    long (bullish) signal: buy CE, or sell PE when the mode is a sell mode ("sell the opposite option").
    short (bearish) signal: buy PE, or sell CE.
    """
    k = max(1, strike_steps)
    if mode == "straddle_sell":
        return [LegTemplate("CE", "sell", 0), LegTemplate("PE", "sell", 0)]
    if mode == "straddle_buy":
        return [LegTemplate("CE", "buy", 0), LegTemplate("PE", "buy", 0)]
    if mode == "strangle_sell":
        return [LegTemplate("CE", "sell", -k), LegTemplate("PE", "sell", -k)]
    if mode == "strangle_buy":
        return [LegTemplate("CE", "buy", -k), LegTemplate("PE", "buy", -k)]
    moneyness = {"atm": 0, "itm": k, "otm": -k}[mode.split("_")[0]]
    side = "sell" if mode.endswith("_sell") else "buy"
    if side == "buy":
        opt: OptType = "CE" if signal == "long" else "PE"
    else:
        opt = "PE" if signal == "long" else "CE"
    return [LegTemplate(opt, side, moneyness)]


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def next_expiry(underlying: str, on: date, kind: Literal["weekly", "monthly"] = "weekly") -> date:
    """Next expiry on/after `on`. Ignores exchange holidays (expiry would shift a day earlier)."""
    u = underlying.upper()
    if kind == "weekly" and u in R.WEEKLY_EXPIRY_WEEKDAY:
        wd = R.WEEKLY_EXPIRY_WEEKDAY[u]
        return on + timedelta(days=(wd - on.weekday()) % 7)
    wd = R.MONTHLY_EXPIRY_WEEKDAY.get(u, 1)
    e = _last_weekday_of_month(on.year, on.month, wd)
    if e < on:
        y, m = (on.year + 1, 1) if on.month == 12 else (on.year, on.month + 1)
        e = _last_weekday_of_month(y, m, wd)
    return e


def years_to_expiry(now: datetime, expiry: date) -> float:
    exp_dt = datetime.combine(expiry, time(15, 30))
    secs = (exp_dt - now).total_seconds()
    return max(secs, 15 * 60) / (365 * 24 * 3600)  # floor at 15 minutes


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(spot: float, strike: float, t: float, iv: float = R.DEFAULT_IV, r: float = R.RISK_FREE, opt_type: OptType = "CE") -> float:
    if t <= 0 or iv <= 0:
        intrinsic = max(0.0, spot - strike) if opt_type == "CE" else max(0.0, strike - spot)
        return intrinsic
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    if opt_type == "CE":
        return spot * _norm_cdf(d1) - strike * math.exp(-r * t) * _norm_cdf(d2)
    return strike * math.exp(-r * t) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def option_symbol(underlying: str, expiry: date, strike: int, opt_type: OptType) -> str:
    """Human-readable symbol, e.g. NIFTY25NOV2624600CE (not broker-specific)."""
    return f"{underlying.upper()}{expiry.strftime('%d%b%y').upper()}{strike}{opt_type}"


def premium(underlying: str, spot: float, strike: int, opt_type: OptType, now: datetime, expiry_kind: str = "weekly", iv: float | None = None) -> float:
    exp = next_expiry(underlying, now.date(), expiry_kind)  # type: ignore[arg-type]
    p = bs_price(spot, strike, years_to_expiry(now, exp), iv or R.DEFAULT_IV, R.RISK_FREE, opt_type)
    return max(0.05, round(p, 2))
