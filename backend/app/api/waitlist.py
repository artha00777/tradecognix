"""Paid-plan waitlist. Payments are disabled during beta; Pro/Elite buttons collect emails here.

POST /api/waitlist {email, plan} is public, rate-limited per IP and idempotent per email
(a repeat updates the plan of interest and never reveals whether the email was already listed).
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlmodel import Session, select

from ..config import get_settings
from ..db import get_session
from ..models import WaitlistEntry
from ..ratelimit import client_ip, enforce

router = APIRouter(prefix="/api", tags=["waitlist"])


class WaitlistIn(BaseModel):
    email: EmailStr
    plan: Literal["pro", "elite"] = "pro"
    source: str = Field(default="web", max_length=32)


@router.post("/waitlist")
def join_waitlist(body: WaitlistIn, request: Request, db: Session = Depends(get_session)):
    enforce(f"waitlist:{client_ip(request)}", get_settings().rate_limit_public)
    email = body.email.strip().lower()
    row = db.exec(select(WaitlistEntry).where(WaitlistEntry.email == email)).first()
    if row:
        row.plan = body.plan
    else:
        row = WaitlistEntry(email=email, plan=body.plan, source=body.source)
    db.add(row)
    db.commit()
    return {"ok": True, "plan": body.plan, "message": "You're on the list. Pro and Elite are free during beta."}
