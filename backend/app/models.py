"""Persistent tables (SQLModel)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """Timezone-aware UTC now. All datetimes are written in UTC.

    Note: SQLite (and Postgres `timestamp without time zone`) hand values back tz-naive; use
    `as_utc()` before comparing or serialising them.
    """
    return datetime.now(timezone.utc)


def as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Treat a naive datetime read from the DB as UTC; convert aware ones to UTC."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    name: str = ""
    password_hash: str
    role: str = Field(default="client", index=True)  # client | admin
    is_active: bool = True
    plan: str = Field(default="free", index=True)  # free | pro | elite (see app/billing/plans.py)
    plan_expires_at: Optional[datetime] = None  # None = no expiry (admin-granted / free)
    referral_code: Optional[str] = Field(default=None, index=True, unique=True)
    referred_by: Optional[int] = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=utcnow)


class BrokerAccount(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    broker: str = Field(index=True)  # registry key e.g. "angelone"
    label: str = ""
    # Fernet-encrypted JSON blob of credentials (api key, client code, pin, totp secret, tokens...)
    credentials_enc: str
    status: str = "disconnected"  # disconnected | connected | error
    last_error: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class StrategyRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    name: str
    prompt: str = ""
    spec: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    tag: str = Field(default="", index=True)  # order tag / journal tag
    live_confirmed: bool = False  # per-strategy explicit live confirmation
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class BacktestRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    strategy_id: Optional[int] = Field(default=None, foreign_key="strategyrecord.id", index=True)
    spec: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    params: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    metrics: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    equity: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    trades: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    data_source: str = "synthetic"
    flags: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)


class LiveSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    strategy_id: int = Field(foreign_key="strategyrecord.id", index=True)
    broker_account_id: Optional[int] = Field(default=None, foreign_key="brokeraccount.id")
    mode: str = "paper"  # paper | live
    status: str = Field(default="running", index=True)  # running | stopped | killed | halted
    halt_reason: str = ""
    trades_today: int = 0
    realised_pnl_today: float = 0.0
    state: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    started_at: datetime = Field(default_factory=utcnow)
    stopped_at: Optional[datetime] = None


class OrderLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: Optional[int] = Field(default=None, foreign_key="livesession.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    broker: str
    mode: str
    symbol: str
    side: str
    quantity: int
    price: float = 0.0
    broker_order_id: str = ""
    tag: str = ""
    status: str = ""
    message: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class JournalEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    strategy_id: Optional[int] = Field(default=None, foreign_key="strategyrecord.id", index=True)
    session_id: Optional[int] = Field(default=None, foreign_key="livesession.id")
    strategy_tag: str = Field(default="", index=True)
    mode: str = "paper"
    symbol: str = ""
    side: str = ""
    quantity: int = 0
    entry_time: Optional[datetime] = None
    exit_time: Optional[datetime] = None
    entry_price: float = 0.0
    exit_price: float = 0.0
    gross_pnl: float = 0.0
    costs: float = 0.0
    net_pnl: float = 0.0
    exit_reason: str = ""
    tags: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    notes: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class UsageEvent(SQLModel, table=True):
    """Metered actions for plan limits (backtests, sweeps). Counted per IST day."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    kind: str = Field(index=True)  # backtest | sweep
    created_at: datetime = Field(default_factory=utcnow, index=True)


class Payment(SQLModel, table=True):
    """A Razorpay order created by checkout; becomes `paid` after signature verification."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    provider: str = "razorpay"
    order_id: str = Field(index=True, unique=True)
    payment_id: str = ""
    plan: str
    interval: str  # monthly | yearly
    amount_paise: int
    currency: str = "INR"
    status: str = Field(default="created", index=True)  # created | paid | failed
    created_at: datetime = Field(default_factory=utcnow)
    paid_at: Optional[datetime] = None


class WaitlistEntry(SQLModel, table=True):
    """Paid-plan waitlist while payments are disabled during beta. One row per email."""

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    plan: str = "pro"  # pro | elite (latest interest)
    source: str = "web"
    created_at: datetime = Field(default_factory=utcnow)
