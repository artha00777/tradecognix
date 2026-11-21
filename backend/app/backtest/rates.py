"""Indian F&O / equity charge rates and contract specs, in ONE place.

!!! VERIFY CURRENT RATES before relying on numbers. Exchanges, SEBI and the Union Budget
change these. Last reviewed: 2026-11-22.

Sources used for the defaults:
- STT revision effective 1 Apr 2026 (Budget 2026-27; NSE circular NSE/FATAX/73524):
  options sell 0.10% -> 0.15% of premium, futures sell 0.02% -> 0.05%.
  https://zerodha.com/marketintel/bulletin/445377/revision-in-stt-securities-transaction-tax-from-1st-april-2026
- NSE options transaction charge 0.03503% of premium + IPFT 0.0005% (= Rs 3,553/crore total);
  futures 0.00173% + IPFT 0.0001% (= Rs 183/crore).
- SEBI turnover fee Rs 10/crore; stamp duty 0.003% (options, buy side), 0.002% (futures, buy),
  0.003% (equity intraday, buy), 0.015% (equity delivery, buy); GST 18% on brokerage +
  exchange charges + SEBI fee.
Rates are date-aware: a backtest trade before 1 Apr 2026 uses the FY26 STT (0.1% options),
trades on/after use the FY27 rate (0.15%). Override anything via CostConfig.
"""
from __future__ import annotations

from datetime import date

# ---- STT schedule: list of (effective_from, rate_fraction) per segment/side, newest last ----
STT_SCHEDULE: dict[str, list[tuple[date, float]]] = {
    "options_sell": [(date(2024, 10, 1), 0.001), (date(2026, 4, 1), 0.0015)],
    "futures_sell": [(date(2024, 10, 1), 0.0002), (date(2026, 4, 1), 0.0005)],
    "equity_intraday_sell": [(date(2000, 1, 1), 0.00025)],
    "equity_delivery_both": [(date(2000, 1, 1), 0.001)],
}

# ---- Exchange transaction charges (fraction of turnover), incl. IPFT shown separately ----
EXCHANGE_TXN = {
    ("NSE", "options"): 0.0003503,
    ("NSE", "futures"): 0.0000173,
    ("NSE", "equity_intraday"): 0.0000297,
    ("NSE", "equity_delivery"): 0.0000297,
    ("BSE", "options"): 0.000325,  # SENSEX / BANKEX options
    ("BSE", "futures"): 0.0,
    ("BSE", "equity_intraday"): 0.0000375,
    ("BSE", "equity_delivery"): 0.0000375,
}
IPFT = {"options": 0.000005, "futures": 0.000001, "equity_intraday": 0.000001, "equity_delivery": 0.000001}

SEBI_FEE = 10 / 1e7  # Rs 10 per crore
STAMP_BUY = {"options": 0.00003, "futures": 0.00002, "equity_intraday": 0.00003, "equity_delivery": 0.00015}
GST = 0.18

# ---- Brokerage defaults (discount-broker style). Configurable per run. ----
BROKERAGE_FLAT_PER_ORDER = 20.0
BROKERAGE_PCT_CAP = {"equity_intraday": 0.0003}  # min(Rs 20, 0.03%) style
BROKERAGE_DELIVERY = 0.0

# ---- Contract specs (VERIFY: NSE revises lot sizes periodically) ----
LOT_SIZE = {"NIFTY": 65, "BANKNIFTY": 30, "FINNIFTY": 60, "MIDCPNIFTY": 120, "SENSEX": 20}
STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50, "MIDCPNIFTY": 25, "SENSEX": 100}
# Weekly expiry weekday (Mon=0). Only NIFTY (NSE) and SENSEX (BSE) have weeklies; others monthly.
WEEKLY_EXPIRY_WEEKDAY = {"NIFTY": 1, "SENSEX": 3}
MONTHLY_EXPIRY_WEEKDAY = {"NIFTY": 1, "BANKNIFTY": 1, "FINNIFTY": 1, "MIDCPNIFTY": 1, "SENSEX": 3}

# ---- Option premium proxy ----
DEFAULT_IV = 0.14  # "VIX-ish" annualised IV used when no option history / VIX series
RISK_FREE = 0.065

# Index tokens / symbols per broker are in the broker adapters.


def stt_rate(key: str, on: date) -> float:
    rate = 0.0
    for eff, r in STT_SCHEDULE[key]:
        if on >= eff:
            rate = r
    return rate
