"""IST clock helpers. Everything market-related runs on IST wall-clock, tz-naive datetimes.

The server may run in UTC (Render, most clouds), so never use naive `datetime.now()` /
`date.today()` for market decisions.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SESSION_OPEN = "09:15"
SESSION_CLOSE = "15:30"


def ist_now() -> datetime:
    """Current IST wall-clock time, tz-naive, truncated to the minute."""
    return datetime.now(IST).replace(tzinfo=None, second=0, microsecond=0)


def ist_today() -> date:
    return datetime.now(IST).date()


def to_ist_naive(dt: datetime) -> datetime:
    """Aware datetime -> IST wall clock (naive). Naive input is assumed to already be IST."""
    return dt.astimezone(IST).replace(tzinfo=None) if dt.tzinfo is not None else dt
