"""Subscription plans and server-side plan limits (single source of truth).

Prices are INR. Razorpay amounts are in paise (1 INR = 100 paise). `None` limit = unlimited.
Admins are never limited. A paid plan whose `plan_expires_at` has passed behaves as `free`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import Session, select

from ..models import LiveSession, StrategyRecord, UsageEvent, User, as_utc

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    price_monthly_inr: int
    price_yearly_inr: Optional[int]  # None = not offered
    backtests_per_day: Optional[int]
    sweeps: bool
    live_trading: bool
    max_strategies: Optional[int]  # saved strategies (and therefore concurrently running sessions)
    max_running_sessions: Optional[int]
    blurb: str

    def public(self) -> dict:
        d = asdict(self)
        d["currency"] = "INR"
        d["price_monthly_paise"] = self.price_monthly_inr * 100
        d["price_yearly_paise"] = self.price_yearly_inr * 100 if self.price_yearly_inr is not None else None
        return d


PLANS: dict[str, Plan] = {
    "free": Plan("free", "Free", 0, 0, backtests_per_day=5, sweeps=False, live_trading=False, max_strategies=1,
                 max_running_sessions=1, blurb="Plain-English strategies, 5 backtests a day, paper trading."),
    "pro": Plan("pro", "Pro", 999, 9990, backtests_per_day=100, sweeps=True, live_trading=True, max_strategies=5,
                max_running_sessions=5, blurb="Live trading on your broker, parameter sweeps, 5 strategies."),
    "elite": Plan("elite", "Elite", 2499, None, backtests_per_day=None, sweeps=True, live_trading=True, max_strategies=None,
                  max_running_sessions=None, blurb="Unlimited strategies, backtests and sessions."),
}
PLAN_KEYS = tuple(PLANS)
INTERVALS = ("monthly", "yearly")


def price_paise(plan_key: str, interval: str) -> Optional[int]:
    p = PLANS[plan_key]
    inr = p.price_monthly_inr if interval == "monthly" else p.price_yearly_inr
    return None if inr is None else inr * 100


def effective_plan_key(user: User, now: datetime | None = None) -> str:
    key = user.plan if user.plan in PLANS else "free"
    from ..config import get_settings
    beta = getattr(get_settings(), "beta_plan", "free")
    order = list(PLANS)
    if beta in PLANS and order.index(beta) > order.index(key):
        return beta
    exp = as_utc(user.plan_expires_at)
    if key != "free" and exp is not None and exp <= (now or datetime.now(timezone.utc)):
        return "free"
    return key


def plan_for(user: User) -> Plan:
    return PLANS[effective_plan_key(user)]


def ist_day_start_utc(now: datetime | None = None) -> datetime:
    """Start of the current IST calendar day, as an aware UTC datetime (daily quotas reset at IST midnight)."""
    now_ist = (now or datetime.now(timezone.utc)).astimezone(IST)
    return datetime.combine(now_ist.date(), time.min, IST).astimezone(timezone.utc)


def usage_today(db: Session, user: User, kind: str) -> int:
    start = ist_day_start_utc()
    return db.exec(select(func.count()).select_from(UsageEvent).where(
        UsageEvent.user_id == user.id, UsageEvent.kind == kind, UsageEvent.created_at >= start)).one()


def record_usage(db: Session, user: User, kind: str) -> None:
    db.add(UsageEvent(user_id=user.id, kind=kind))
    db.commit()


def plan_error(message: str, feature: str, user: User) -> HTTPException:
    return HTTPException(402, {"code": "plan_limit", "feature": feature, "plan": effective_plan_key(user), "message": message,
                               "upgrade_url": "/pricing"})


def require_feature(user: User, feature: str) -> None:
    if user.role == "admin":
        return
    p = plan_for(user)
    if feature == "sweeps" and not p.sweeps:
        raise plan_error("Parameter sweeps need the Pro plan.", feature, user)
    if feature == "live_trading" and not p.live_trading:
        raise plan_error("Live trading needs the Pro plan. Free accounts can paper trade.", feature, user)


def check_backtest_quota(db: Session, user: User) -> None:
    if user.role == "admin":
        return
    p = plan_for(user)
    if p.backtests_per_day is not None and usage_today(db, user, "backtest") >= p.backtests_per_day:
        raise plan_error(f"Daily backtest limit reached ({p.backtests_per_day}/day on {p.name}). Resets at midnight IST.", "backtests", user)


def strategy_count(db: Session, user: User) -> int:
    return db.exec(select(func.count()).select_from(StrategyRecord).where(StrategyRecord.user_id == user.id)).one()


def running_count(db: Session, user: User) -> int:
    return db.exec(select(func.count()).select_from(LiveSession).where(LiveSession.user_id == user.id, LiveSession.status == "running")).one()


def check_strategy_limit(db: Session, user: User) -> None:
    if user.role == "admin":
        return
    p = plan_for(user)
    if p.max_strategies is not None and strategy_count(db, user) >= p.max_strategies:
        raise plan_error(f"{p.name} allows {p.max_strategies} saved strateg{'y' if p.max_strategies == 1 else 'ies'}.", "strategies", user)


def check_session_limit(db: Session, user: User) -> None:
    if user.role == "admin":
        return
    p = plan_for(user)
    if p.max_running_sessions is not None and running_count(db, user) >= p.max_running_sessions:
        raise plan_error(f"{p.name} allows {p.max_running_sessions} running session(s) at a time.", "sessions", user)


def usage_summary(db: Session, user: User) -> dict:
    p = plan_for(user)
    return {
        "plan": p.key, "plan_name": p.name, "stored_plan": user.plan, "plan_expires_at": as_utc(user.plan_expires_at),
        "unlimited": user.role == "admin",
        "resets_at": "00:00 IST",
        "usage": {
            "backtests_today": usage_today(db, user, "backtest"),
            "sweeps_today": usage_today(db, user, "sweep"),
            "strategies": strategy_count(db, user),
            "running_sessions": running_count(db, user),
        },
        "limits": {
            "backtests_per_day": p.backtests_per_day, "sweeps": p.sweeps, "live_trading": p.live_trading,
            "max_strategies": p.max_strategies, "max_running_sessions": p.max_running_sessions,
        },
    }
