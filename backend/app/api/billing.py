"""Plans, usage and Razorpay checkout.

Flow: POST /checkout -> Razorpay order (open Razorpay Checkout on the client with the returned
key_id + order_id) -> POST /verify with the three razorpay_* fields -> plan activated.
The webhook (order.paid / payment.captured) activates the plan too, in case the browser never
calls /verify. Activation is idempotent.
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from ..billing import razorpay
from ..billing.plans import INTERVALS, PLANS, effective_plan_key, price_paise, usage_summary
from ..config import get_settings
from ..db import get_session
from ..models import Payment, User, as_utc, utcnow
from .deps import current_user

log = logging.getLogger("tradecognix.billing")
router = APIRouter(prefix="/api/billing", tags=["billing"])
PERIOD_DAYS = {"monthly": 30, "yearly": 365}


@router.get("/plans")
def plans():
    """Public price list (INR)."""
    return {"currency": "INR", "plans": [p.public() for p in PLANS.values()], "intervals": list(INTERVALS),
            "checkout_available": razorpay.configured(), "note": "Prices in INR. GST as applicable."}


@router.get("/usage")
def usage(user: User = Depends(current_user), db: Session = Depends(get_session)):
    return usage_summary(db, user)


class CheckoutIn(BaseModel):
    plan: Literal["pro", "elite"]
    interval: Literal["monthly", "yearly"] = "monthly"


@router.post("/checkout")
def checkout(body: CheckoutIn, user: User = Depends(current_user), db: Session = Depends(get_session)):
    if not razorpay.configured():
        raise HTTPException(503, "Payments are not configured on this server yet")
    amount = price_paise(body.plan, body.interval)
    if not amount:
        raise HTTPException(422, f"{PLANS[body.plan].name} is not offered {body.interval}")
    receipt = f"tc-{user.id}-{secrets.token_hex(6)}"
    try:
        order = razorpay.create_order(amount, receipt, {"user_id": str(user.id), "plan": body.plan, "interval": body.interval})
    except razorpay.RazorpayError as e:
        log.warning("razorpay order failed for user %s: %s", user.id, e)
        raise HTTPException(502, "Could not start checkout, please try again")
    db.add(Payment(user_id=user.id, order_id=order["id"], plan=body.plan, interval=body.interval, amount_paise=amount))
    db.commit()
    return {"order_id": order["id"], "amount": amount, "currency": "INR", "key_id": get_settings().razorpay_key_id,
            "plan": body.plan, "interval": body.interval, "name": "TradeCognix", "prefill": {"email": user.email, "name": user.name}}


def activate(db: Session, pay: Payment, payment_id: str) -> User:
    """Mark a payment paid and extend the user's plan. Idempotent."""
    user = db.get(User, pay.user_id)
    if pay.status == "paid":
        return user
    now = datetime.now(timezone.utc)
    current_exp = as_utc(user.plan_expires_at)
    start = current_exp if (effective_plan_key(user) == pay.plan and current_exp and current_exp > now) else now
    user.plan = pay.plan
    user.plan_expires_at = start + timedelta(days=PERIOD_DAYS.get(pay.interval, 30))
    pay.status, pay.payment_id, pay.paid_at = "paid", payment_id, utcnow()
    db.add(user)
    db.add(pay)
    db.commit()
    db.refresh(user)
    log.info("plan %s (%s) activated for user %s", pay.plan, pay.interval, user.id)
    return user


class VerifyIn(BaseModel):
    razorpay_order_id: str = Field(min_length=1, max_length=64)
    razorpay_payment_id: str = Field(min_length=1, max_length=64)
    razorpay_signature: str = Field(min_length=1, max_length=256)


@router.post("/verify")
def verify(body: VerifyIn, user: User = Depends(current_user), db: Session = Depends(get_session)):
    if not razorpay.configured():
        raise HTTPException(503, "Payments are not configured on this server yet")
    pay = db.exec(select(Payment).where(Payment.order_id == body.razorpay_order_id)).first()
    if not pay or pay.user_id != user.id:
        raise HTTPException(404, "Order not found")
    if not razorpay.verify_payment_signature(body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature):
        raise HTTPException(400, "Payment signature verification failed")
    if pay.status == "paid" and pay.payment_id and pay.payment_id != body.razorpay_payment_id:
        raise HTTPException(409, "Order already paid")
    u = activate(db, pay, body.razorpay_payment_id)
    return {"ok": True, "plan": u.plan, "plan_expires_at": as_utc(u.plan_expires_at)}


@router.post("/webhook", include_in_schema=False)
async def webhook(request: Request, db: Session = Depends(get_session)):
    if not get_settings().razorpay_webhook_secret:
        raise HTTPException(503, "Webhook not configured")
    raw = await request.body()
    if not razorpay.verify_webhook_signature(raw, request.headers.get("x-razorpay-signature", "")):
        raise HTTPException(400, "Bad signature")
    try:
        event = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Bad payload")
    if event.get("event") not in ("order.paid", "payment.captured"):
        return {"ok": True, "ignored": event.get("event")}
    payment = (event.get("payload", {}).get("payment", {}) or {}).get("entity", {}) or {}
    order_id = payment.get("order_id") or ((event.get("payload", {}).get("order", {}) or {}).get("entity", {}) or {}).get("id")
    pay = db.exec(select(Payment).where(Payment.order_id == order_id)).first() if order_id else None
    if not pay:
        return {"ok": True, "ignored": "unknown order"}
    if payment.get("amount") is not None and int(payment["amount"]) != pay.amount_paise:
        log.warning("webhook amount mismatch for order %s", order_id)
        return {"ok": False, "error": "amount mismatch"}
    activate(db, pay, payment.get("id", ""))
    return {"ok": True}
