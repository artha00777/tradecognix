from __future__ import annotations

import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session, select

from ..db import get_session
from ..models import StrategyRecord, User, utcnow
from ..strategy.dsl import Strategy, strategy_json_schema
from ..strategy.explain import explain, rules_table
from ..strategy.parser import parse_strategy
from .deps import current_user

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


class ParseIn(BaseModel):
    text: str = Field(min_length=5, max_length=4000)
    use_llm: bool = True


class StrategyIn(BaseModel):
    name: Optional[str] = None
    prompt: str = ""
    spec: Optional[dict[str, Any]] = None


class ConfirmLiveIn(BaseModel):
    confirm_text: str
    acknowledge_risk: bool = False


def make_tag(rec_id: int, name: str) -> str:
    initials = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", name))[:8].upper()
    return f"TC{rec_id}{initials}"[:20]


def out(rec: StrategyRecord) -> dict:
    s = Strategy.model_validate(rec.spec)
    return {"id": rec.id, "name": rec.name, "prompt": rec.prompt, "tag": rec.tag, "live_confirmed": rec.live_confirmed, "spec": rec.spec,
            "explanation": explain(s), "rules": rules_table(s), "created_at": rec.created_at, "updated_at": rec.updated_at}


def validate_spec(spec: dict) -> Strategy:
    try:
        return Strategy.model_validate(spec)
    except ValidationError as e:
        raise HTTPException(422, {"message": "Invalid strategy", "errors": e.errors(include_url=False)})


@router.get("/schema")
def schema():
    return strategy_json_schema()


@router.post("/parse")
def parse(body: ParseIn):
    """Plain English -> strategy DSL. Public (no login) so the marketing demo works."""
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


def _get(db: Session, user: User, sid: int) -> StrategyRecord:
    rec = db.get(StrategyRecord, sid)
    if not rec or (rec.user_id != user.id and user.role != "admin"):
        raise HTTPException(404, "Strategy not found")
    return rec


@router.get("/{sid}")
def get_one(sid: int, user: User = Depends(current_user), db: Session = Depends(get_session)):
    return out(_get(db, user, sid))


@router.put("/{sid}")
def update(sid: int, body: StrategyIn, user: User = Depends(current_user), db: Session = Depends(get_session)):
    rec = _get(db, user, sid)
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
    rec = _get(db, user, sid)
    db.delete(rec)
    db.commit()
    return {"ok": True}


@router.post("/{sid}/confirm-live")
def confirm_live(sid: int, body: ConfirmLiveIn, user: User = Depends(current_user), db: Session = Depends(get_session)):
    rec = _get(db, user, sid)
    if body.confirm_text.strip() != "LIVE" or not body.acknowledge_risk:
        raise HTTPException(400, "Type LIVE and acknowledge the risk to enable live trading for this strategy")
    rec.live_confirmed = True
    rec.updated_at = utcnow()
    db.add(rec)
    db.commit()
    return out(rec)


@router.post("/{sid}/revoke-live")
def revoke_live(sid: int, user: User = Depends(current_user), db: Session = Depends(get_session)):
    rec = _get(db, user, sid)
    rec.live_confirmed = False
    db.add(rec)
    db.commit()
    return out(rec)
