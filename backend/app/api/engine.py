from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..brokers.registry import adapter_class
from ..config import get_settings
from ..db import get_session
from ..engine.guardrails import GuardrailError, assert_live_allowed
from ..engine.live import engine
from ..models import BrokerAccount, LiveSession, OrderLog, StrategyRecord, User
from .brokers import adapter_for, get_account
from .deps import admin_user, current_user

router = APIRouter(prefix="/api/engine", tags=["paper/live"])


class StartIn(BaseModel):
    strategy_id: int
    mode: Literal["paper", "live"] = "paper"
    broker_account_id: Optional[int] = None
    confirm_text: Optional[str] = None


def broker_factory(db: Session, sess: LiveSession):
    if not sess.broker_account_id:
        return None, "paper"
    acc = db.get(BrokerAccount, sess.broker_account_id)
    return adapter_for(db, acc), acc.broker


@router.post("/start")
def start(body: StartIn, user: User = Depends(current_user), db: Session = Depends(get_session)):
    rec = db.get(StrategyRecord, body.strategy_id)
    if not rec or rec.user_id != user.id:
        raise HTTPException(404, "Strategy not found")
    broker, broker_key = None, "paper"
    if body.broker_account_id:
        acc = get_account(db, user, body.broker_account_id)
        broker_key = acc.broker
        if acc.status != "connected":
            raise HTTPException(400, f"{acc.broker} account is not connected; test the connection first")
        broker = adapter_for(db, acc)
    if body.mode == "live":
        try:
            assert_live_allowed(get_settings(), rec.live_confirmed, body.confirm_text, broker_key, adapter_class(broker_key).status)
        except GuardrailError as e:
            raise HTTPException(403, str(e))
    existing = db.exec(select(LiveSession).where(LiveSession.strategy_id == rec.id, LiveSession.status == "running")).first()
    if existing:
        raise HTTPException(409, f"Strategy already running (session {existing.id})")
    sess = LiveSession(user_id=user.id, strategy_id=rec.id, broker_account_id=body.broker_account_id, mode=body.mode)
    db.add(sess)
    db.commit()
    db.refresh(sess)
    try:
        engine.register(engine.build_runner(rec, sess, broker, broker_key))
    except Exception as e:
        sess.status, sess.halt_reason = "halted", str(e)[:300]
        db.add(sess)
        db.commit()
        raise HTTPException(400, f"Could not start: {e}")
    return {"session_id": sess.id, "mode": sess.mode, "broker": broker_key, "tag": rec.tag, "status": "running",
            "note": "Runs on candle close during market hours (Mon-Fri 09:15-15:30 IST)."}


def _own_session(db: Session, user: User, sid: int) -> LiveSession:
    s = db.get(LiveSession, sid)
    if not s or (s.user_id != user.id and user.role != "admin"):
        raise HTTPException(404, "Session not found")
    return s


@router.post("/{sid}/stop")
def stop(sid: int, user: User = Depends(current_user), db: Session = Depends(get_session)):
    _own_session(db, user, sid)
    engine.stop(sid)
    return {"ok": True, "session_id": sid, "status": "stopped"}


@router.post("/kill")
def kill_switch(user: User = Depends(current_user)):
    """Kill switch: square off and halt every running session of this user."""
    n = engine.kill_user(user.id)
    return {"ok": True, "sessions_killed": n}


@router.get("/status")
def status(user: User = Depends(current_user)):
    s = get_settings()
    return {"live_trading_enabled": s.live_trading_enabled, "static_ip": s.static_ip, "sessions": engine.status(user.id)}


@router.get("/sessions")
def sessions(user: User = Depends(current_user), db: Session = Depends(get_session)):
    rows = db.exec(select(LiveSession).where(LiveSession.user_id == user.id).order_by(LiveSession.started_at.desc()).limit(100)).all()
    return {"sessions": [r.model_dump(exclude={"state"}) for r in rows]}


@router.get("/orders")
def orders(user: User = Depends(current_user), db: Session = Depends(get_session), session_id: Optional[int] = None):
    q = select(OrderLog).where(OrderLog.user_id == user.id)
    if session_id:
        q = q.where(OrderLog.session_id == session_id)
    return {"orders": [o.model_dump() for o in db.exec(q.order_by(OrderLog.created_at.desc()).limit(500)).all()]}


class TickIn(BaseModel):
    now: Optional[datetime] = None


@router.post("/tick")
def manual_tick(body: TickIn, user: User = Depends(admin_user)):
    """Admin/dev: force one engine tick (optionally at a simulated IST time) - handy for demos and tests."""
    return {"results": engine.tick(body.now)}
