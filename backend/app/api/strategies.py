from __future__ import annotations

import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session, select

from ..billing.plans import check_strategy_limit
from ..config import get_settings
from ..db import get_session
from ..models import BacktestRun, JournalEntry, LiveSession, OrderLog, StrategyRecord, User, as_utc, utcnow
from ..ratelimit import client_ip, enforce
from ..strategy.dsl import Strategy, strategy_json_schema
from ..strategy.explain import explain, rules_table
from ..strategy.parser import parse_strategy
from .deps import current_user, optional_user

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


class ParseIn(BaseModel):
    text: str = Field(min_length=5, max_length=4000)
    use_llm: bool = True


class StrategyIn(BaseModel):
    name: Optional[str] = Field(None, max_length=120)
    prompt: str = Field("", max_length=4000)
    spec: Optional[dict[str, Any]] = None


class ConfirmLiveIn(BaseModel):
    confirm_text: str
    acknowledge_risk: bool = False


def make_tag(rec_id: int, name: str) -> str:
    initials = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", name))[:8].upper()
    return f"TC{rec_id}{initials}"[:20]


def out(rec: StrategyRecord) -> dict:
    base = {"id": rec.id, "name": rec.name, "prompt": rec.prompt, "tag": rec.tag, "live_confirmed": rec.live_confirmed, "spec": rec.spec,
            "created_at": as_utc(rec.created_at), "updated_at": as_utc(rec.updated_at)}
    try:  # a spec saved under older, looser validation must not break listing
        s = Strategy.model_validate(rec.spec)
    except ValidationError as e:
        return {**base, "explanation": "", "rules": {}, "spec_error": e.errors(include_url=False)[0]["msg"]}
    return {**base, "explanation": explain(s), "rules": rules_table(s)}


def validate_spec(spec: dict) -> Strategy:
    try:
        return Strategy.model_validate(spec)
    except ValidationError as e:
        raise HTTPException(422, {"message": "Invalid strategy", "errors": e.errors(include_url=False)})


@router.get("/schema")
def schema():
    return strategy_json_schema()


def limit_anonymous(request: Request, user: Optional[User], bucket: str) -> None:
    """Public endpoints are rate-limited per IP for anonymous callers (LLM + CPU cost)."""
    if user is None:
        enforce(f"{bucket}:{client_ip(request)}", get_settings().rate_limit_public)


@router.post("/parse")
def parse(body: ParseIn, request: Request, user: Optional[User] = Depends(optional_user)):
    """Plain English -> strategy DSL. Public (no login) so the marketing demo works."""
    limit_anonymous(request, user, "parse")
    try:
        res = parse_strategy(body.text, use_llm=body.use_llm)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {**res.to_dict(), "explanation": explain(res.strategy), "rules": rules_table(res.strategy)}


@router.post("/explain")
def explain_spec(spec: dict[str, Any]):
    s = validate_spec(spec)
    return {"explanation": explain(s), "rules": rules_table(s)}


@router.get("")
def list_strategies(user: User = Depends(current_user), db: Session = Depends(get_session)):
    rows = db.exec(select(StrategyRecord).where(StrategyRecord.user_id == user.id).order_by(StrategyRecord.updated_at.desc())).all()
    return {"strategies": [out(r) for r in rows]}


@router.post("")
def create(body: StrategyIn, user: User = Depends(current_user), db: Session = Depends(get_session)):
    check_strategy_limit(db, user)
    if body.spec is None:
        if not body.prompt:
            raise HTTPException(422, "Provide spec or prompt")
        try:
            s = parse_strategy(body.prompt).strategy
        except ValueError as e:
            raise HTTPException(422, str(e))
    else:
        s = validate_spec(body.spec)
    rec = StrategyRecord(user_id=user.id, name=body.name or s.name, prompt=body.prompt or s.description, spec=s.model_dump())
    db.add(rec)
    db.commit()
    db.refresh(rec)
    rec.tag = make_tag(rec.id, rec.name)
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return out(rec)


def _get(db: Session, user: User, sid: int, write: bool = False) -> StrategyRecord:
    """Owner can read/write. Admins can read (support) but never edit, delete or confirm live."""
    rec = db.get(StrategyRecord, sid)
    if not rec or (rec.user_id != user.id and (write or user.role != "admin")):
        raise HTTPException(404, "Strategy not found")
    return rec


@router.get("/{sid}")
def get_one(sid: int, user: User = Depends(current_user), db: Session = Depends(get_session)):
    return out(_get(db, user, sid))


@router.put("/{sid}")
def update(sid: int, body: StrategyIn, user: User = Depends(current_user), db: Session = Depends(get_session)):
    rec = _get(db, user, sid, write=True)
    if body.spec is not None:
        s = validate_spec(body.spec)
        if s.model_dump() != rec.spec:
            rec.live_confirmed = False  # any rule change requires re-confirmation for live
        rec.spec = s.model_dump()
    if body.name:
        rec.name = body.name
    if body.prompt:
        rec.prompt = body.prompt
    rec.updated_at = utcnow()
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return out(rec)


@router.delete("/{sid}")
def delete(sid: int, user: User = Depends(current_user), db: Session = Depends(get_session)):
    rec = _get(db, user, sid, write=True)
    if db.exec(select(LiveSession).where(LiveSession.strategy_id == rec.id, LiveSession.status == "running")).first():
        raise HTTPException(409, "Stop the running session for this strategy before deleting it")
    # Keep backtests, orders and journal rows (audit trail) but detach them; FK-safe on Postgres.
    for run in db.exec(select(BacktestRun).where(BacktestRun.strategy_id == rec.id)).all():
        run.strategy_id = None
        db.add(run)
    for j in db.exec(select(JournalEntry).where(JournalEntry.strategy_id == rec.id)).all():
        j.strategy_id = None
        db.add(j)
    for sess in db.exec(select(LiveSession).where(LiveSession.strategy_id == rec.id)).all():
        for o in db.exec(select(OrderLog).where(OrderLog.session_id == sess.id)).all():
            o.session_id = None
            db.add(o)
        for j in db.exec(select(JournalEntry).where(JournalEntry.session_id == sess.id)).all():
            j.session_id = None
            db.add(j)
        db.delete(sess)
    db.flush()
    db.delete(rec)
    db.commit()
    return {"ok": True}


@router.post("/{sid}/confirm-live")
def confirm_live(sid: int, body: ConfirmLiveIn, user: User = Depends(current_user), db: Session = Depends(get_session)):
    rec = _get(db, user, sid, write=True)  # only the owner can consent to real-money trading
    if body.confirm_text.strip() != "LIVE" or not body.acknowledge_risk:
        raise HTTPException(400, "Type LIVE and acknowledge the risk to enable live trading for this strategy")
    rec.live_confirmed = True
    rec.updated_at = utcnow()
    db.add(rec)
    db.commit()
    return out(rec)


@router.post("/{sid}/revoke-live")
def revoke_live(sid: int, user: User = Depends(current_user), db: Session = Depends(get_session)):
    rec = _get(db, user, sid)  # admins may revoke (safety), never confirm
    rec.live_confirmed = False
    rec.updated_at = utcnow()
    db.add(rec)
    db.commit()
    return out(rec)
