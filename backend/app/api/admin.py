from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, select

from ..billing.plans import PLAN_KEYS, effective_plan_key
from ..config import get_settings
from ..db import get_session
from ..engine.live import engine
from ..models import BacktestRun, BrokerAccount, JournalEntry, LiveSession, OrderLog, Payment, StrategyRecord, User, WaitlistEntry, as_utc
from .deps import admin_user

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users")
def users(_: User = Depends(admin_user), db: Session = Depends(get_session)):
    rows = db.exec(select(User).order_by(User.created_at.desc())).all()
    counts = dict(db.exec(select(StrategyRecord.user_id, func.count()).group_by(StrategyRecord.user_id)).all())
    brokers = {}
    for a in db.exec(select(BrokerAccount)).all():
        brokers.setdefault(a.user_id, []).append({"broker": a.broker, "status": a.status})
    return {"users": [{"id": u.id, "email": u.email, "name": u.name, "role": u.role, "is_active": u.is_active, "created_at": as_utc(u.created_at),
                       "plan": effective_plan_key(u), "stored_plan": u.plan, "plan_expires_at": as_utc(u.plan_expires_at),
                       "referral_code": u.referral_code, "referred_by": u.referred_by,
                       "strategies": counts.get(u.id, 0), "brokers": brokers.get(u.id, [])} for u in rows]}


class UserPatch(BaseModel):
    role: Optional[Literal["client", "admin"]] = None
    is_active: Optional[bool] = None
    plan: Optional[Literal["free", "pro", "elite"]] = None
    plan_expires_at: Optional[datetime] = Field(None, description="Omit/null for no expiry (admin grant)")


@router.patch("/users/{uid}")
def patch_user(uid: int, body: UserPatch, admin: User = Depends(admin_user), db: Session = Depends(get_session)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "User not found")
    if u.id == admin.id and (body.role == "client" or body.is_active is False):
        raise HTTPException(400, "You cannot demote or disable yourself")
    if body.role:
        u.role = body.role
    if body.is_active is not None:
        u.is_active = body.is_active
        if not u.is_active:
            engine.kill_user(u.id)
    if body.plan is not None:
        u.plan = body.plan
        exp = body.plan_expires_at
        u.plan_expires_at = as_utc(exp) if exp else None
        if body.plan == "free":
            u.plan_expires_at = None
            # downgrade: live sessions are no longer allowed on this plan
            for sid, rs in list(engine.sessions.items()):
                if rs.user_id == u.id and rs.mode == "live":
                    engine.kill(sid)
    db.add(u)
    db.commit()
    return {"ok": True, "plan": effective_plan_key(u)}


@router.get("/strategies")
def strategies(_: User = Depends(admin_user), db: Session = Depends(get_session)):
    rows = db.exec(select(StrategyRecord, User.email).join(User, User.id == StrategyRecord.user_id).order_by(StrategyRecord.updated_at.desc())).all()
    return {"strategies": [{"id": s.id, "name": s.name, "owner": email, "tag": s.tag, "live_confirmed": s.live_confirmed,
                            "underlying": s.spec.get("underlying"), "timeframe": s.spec.get("timeframe"), "updated_at": s.updated_at} for s, email in rows]}


@router.get("/waitlist")
def waitlist(_: User = Depends(admin_user), db: Session = Depends(get_session)):
    """Paid-plan waitlist (payments are off during beta). Newest first, flags emails that already have an account."""
    rows = db.exec(select(WaitlistEntry).order_by(WaitlistEntry.created_at.desc())).all()
    have = {e.lower() for e in db.exec(select(User.email)).all()}
    by_plan: dict[str, int] = {}
    for r in rows:
        by_plan[r.plan] = by_plan.get(r.plan, 0) + 1
    return {"total": len(rows), "by_plan": by_plan,
            "entries": [{"id": r.id, "email": r.email, "plan": r.plan, "source": r.source, "has_account": r.email.lower() in have,
                         "created_at": as_utc(r.created_at)} for r in rows]}


@router.get("/sessions")
def sessions(_: User = Depends(admin_user), db: Session = Depends(get_session)):
    running = engine.status()
    emails = {u.id: u.email for u in db.exec(select(User)).all()}
    hist = db.exec(select(LiveSession).order_by(LiveSession.started_at.desc()).limit(200)).all()
    return {"running": running, "history": [{**h.model_dump(exclude={"state"}), "owner": emails.get(h.user_id)} for h in hist]}


@router.post("/sessions/{sid}/kill")
def kill_one(sid: int, _: User = Depends(admin_user)):
    engine.kill(sid)
    return {"ok": True}


@router.post("/kill-all")
def kill_all(_: User = Depends(admin_user)):
    """Platform kill switch: square off and halt every running session."""
    return {"ok": True, "sessions_killed": engine.kill_all()}


@router.get("/stats")
def stats(_: User = Depends(admin_user), db: Session = Depends(get_session)):
    def count(model, *where):
        q = select(func.count()).select_from(model)
        for w in where:
            q = q.where(w)
        return db.exec(q).one()

    net = db.exec(select(func.coalesce(func.sum(JournalEntry.net_pnl), 0.0))).one()
    return {
        "users": count(User), "admins": count(User, User.role == "admin"), "strategies": count(StrategyRecord),
        "live_confirmed_strategies": count(StrategyRecord, StrategyRecord.live_confirmed == True),  # noqa: E712
        "broker_accounts": count(BrokerAccount), "connected_brokers": count(BrokerAccount, BrokerAccount.status == "connected"),
        "backtests": count(BacktestRun), "orders": count(OrderLog), "journal_trades": count(JournalEntry),
        "plans": {k: count(User, User.plan == k) for k in PLAN_KEYS},
        "paid_payments": count(Payment, Payment.status == "paid"),
        "journal_net_pnl": round(float(net or 0), 2), "running_sessions": len(engine.sessions),
        "running_live": sum(1 for s in engine.sessions.values() if s.mode == "live"),
        "live_trading_enabled": get_settings().live_trading_enabled,
    }
