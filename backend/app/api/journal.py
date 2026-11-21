from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..models import JournalEntry, User
from .deps import current_user

IST = ZoneInfo("Asia/Kolkata")
router = APIRouter(prefix="/api/journal", tags=["journal"])


def _query(user: User, strategy_tag: Optional[str], mode: Optional[str], date_from: Optional[date], date_to: Optional[date]):
    q = select(JournalEntry).where(JournalEntry.user_id == user.id)
    if strategy_tag:
        q = q.where(JournalEntry.strategy_tag == strategy_tag)
    if mode:
        q = q.where(JournalEntry.mode == mode)
    if date_from:
        q = q.where(JournalEntry.exit_time >= datetime.combine(date_from, time.min, IST))
    if date_to:
        q = q.where(JournalEntry.exit_time <= datetime.combine(date_to, time.max, IST))
    return q.order_by(JournalEntry.exit_time.desc())


@router.get("")
def list_entries(strategy_tag: Optional[str] = None, mode: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
                 user: User = Depends(current_user), db: Session = Depends(get_session)):
    rows = db.exec(_query(user, strategy_tag, mode, date_from, date_to).limit(1000)).all()
    return {"entries": [r.model_dump() for r in rows]}


@router.get("/summary")
def summary(mode: Optional[str] = None, user: User = Depends(current_user), db: Session = Depends(get_session)):
    """Per-strategy-tag scorecard."""
    rows = db.exec(_query(user, None, mode, None, None)).all()
    agg: dict[str, dict[str, Any]] = defaultdict(lambda: {"trades": 0, "wins": 0, "net_pnl": 0.0, "gross_pnl": 0.0, "costs": 0.0})
    for r in rows:
        a = agg[r.strategy_tag or "untagged"]
        a["trades"] += 1
        a["wins"] += r.net_pnl > 0
        a["net_pnl"] += r.net_pnl
        a["gross_pnl"] += r.gross_pnl
        a["costs"] += r.costs
    return {"by_strategy": [{"strategy_tag": k, **{kk: round(vv, 2) if isinstance(vv, float) else vv for kk, vv in v.items()},
                             "win_rate": round(v["wins"] / v["trades"] * 100, 2) if v["trades"] else 0} for k, v in agg.items()]}


class EntryPatch(BaseModel):
    tags: Optional[list[str]] = None
    notes: Optional[str] = None


@router.patch("/{eid}")
def patch(eid: int, body: EntryPatch, user: User = Depends(current_user), db: Session = Depends(get_session)):
    e = db.get(JournalEntry, eid)
    if not e or e.user_id != user.id:
        raise HTTPException(404, "Entry not found")
    if body.tags is not None:
        e.tags = body.tags
    if body.notes is not None:
        e.notes = body.notes
    db.add(e)
    db.commit()
    return e.model_dump()


@router.get("/export.csv")
def export(strategy_tag: Optional[str] = None, mode: Optional[str] = None, user: User = Depends(current_user), db: Session = Depends(get_session)):
    rows = db.exec(_query(user, strategy_tag, mode, None, None)).all()
    buf = io.StringIO()
    cols = ["id", "strategy_tag", "mode", "symbol", "side", "quantity", "entry_time", "exit_time", "entry_price", "exit_price", "gross_pnl", "costs", "net_pnl", "exit_reason", "notes"]
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        d = r.model_dump()
        w.writerow([d[c] for c in cols])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=tradecognix-journal.csv"})
