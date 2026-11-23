from __future__ import annotations

from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from ..brokers.base import BrokerAdapter, BrokerError
from ..brokers.registry import adapter_class, get_adapter, list_brokers
from ..config import get_settings
from ..db import get_session
import logging
import time

from ..models import BrokerAccount, LiveSession, User, as_utc, utcnow
from ..security import CredentialVault, mask, scrub
from .deps import current_user

log = logging.getLogger("tradecognix.brokers")

router = APIRouter(prefix="/api/brokers", tags=["brokers"])

# account_id -> logged-in adapter (keeps sessions warm; tokens never leave the server)
_ADAPTERS: dict[int, BrokerAdapter] = {}


class AccountIn(BaseModel):
    broker: str = Field(max_length=32)
    label: str = Field("", max_length=80)
    credentials: dict[str, Any] = Field(max_length=20)
    test: bool = True


def account_out(a: BrokerAccount) -> dict:
    return {"id": a.id, "broker": a.broker, "label": a.label, "status": a.status, "last_error": a.last_error,
            "created_at": as_utc(a.created_at), "updated_at": as_utc(a.updated_at)}


def _safe_error(e: Exception, creds: dict) -> str:
    """Broker/SDK errors can echo request params; never store or return credential values."""
    return scrub(f"{e}" or type(e).__name__, creds)[:500]


def adapter_for(db: Session, account: BrokerAccount, fresh: bool = False) -> BrokerAdapter:
    if not fresh and account.id in _ADAPTERS:
        return _ADAPTERS[account.id]
    creds = CredentialVault().decrypt(account.credentials_enc)
    adapter = get_adapter(account.broker, creds)
    _ADAPTERS[account.id] = adapter
    return adapter


def get_account(db: Session, user: User, account_id: int) -> BrokerAccount:
    """Owner only - including admins: a broker account can place real orders and holds secrets."""
    acc = db.get(BrokerAccount, account_id)
    if not acc or acc.user_id != user.id:
        raise HTTPException(404, "Broker account not found")
    return acc


def _test(db: Session, acc: BrokerAccount) -> dict:
    creds: dict = {}
    try:
        creds = CredentialVault().decrypt(acc.credentials_enc)
        adapter = adapter_for(db, acc, fresh=True)
        adapter.login()
        prof = adapter.profile()
        acc.status, acc.last_error = "connected", ""
        result = {"ok": True, "profile": {k: v for k, v in prof.items() if k != "raw"}}
    except NotImplementedError as e:
        acc.status, acc.last_error = "error", str(e)
        result = {"ok": False, "error": str(e), "skeleton": True}
    except Exception as e:
        msg = _safe_error(e, creds)
        acc.status, acc.last_error = "error", msg
        _ADAPTERS.pop(acc.id, None)
        log.warning("broker test failed for account %s (%s): %s", acc.id, acc.broker, msg)
        result = {"ok": False, "error": msg}
    acc.updated_at = utcnow()
    db.add(acc)
    db.commit()
    return result


@router.get("")
def brokers():
    return {"brokers": list_brokers()}


_IP_CACHE: dict[str, Any] = {"ip": None, "at": 0.0}


@router.get("/static-ip")
def static_ip(user: User = Depends(current_user)):
    """Shows the configured static IP and the server's current public egress IP (to whitelist with your broker).

    Signed-in only and cached for 5 minutes (it calls an external service)."""
    if time.monotonic() - _IP_CACHE["at"] > 300:
        try:
            _IP_CACHE["ip"] = httpx.get("https://api.ipify.org", timeout=5).text.strip()[:64]
        except Exception:
            _IP_CACHE["ip"] = None
        _IP_CACHE["at"] = time.monotonic()
    detected = _IP_CACHE["ip"]
    configured = get_settings().static_ip
    return {"configured": configured, "detected": detected, "match": bool(configured and detected and configured == detected),
            "note": "SEBI's retail-algo framework requires API orders from a static IP registered with your broker."}


@router.get("/accounts")
def accounts(user: User = Depends(current_user), db: Session = Depends(get_session)):
    rows = db.exec(select(BrokerAccount).where(BrokerAccount.user_id == user.id)).all()
    return {"accounts": [account_out(a) for a in rows]}


@router.post("/accounts")
def connect(body: AccountIn, user: User = Depends(current_user), db: Session = Depends(get_session)):
    try:
        cls = adapter_class(body.broker)
    except BrokerError as e:
        raise HTTPException(400, str(e))
    required = [f.key for f in cls.credential_fields if f.required]
    missing = [k for k in required if not str(body.credentials.get(k, "")).strip()]
    if missing:
        raise HTTPException(422, f"Missing fields: {', '.join(missing)}")
    allowed = {f.key for f in cls.credential_fields}
    creds = {k: str(v).strip()[:512] for k, v in body.credentials.items() if v not in (None, "") and k in allowed}
    try:
        enc = CredentialVault().encrypt(creds)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    acc = BrokerAccount(user_id=user.id, broker=body.broker, label=body.label or cls.display_name, credentials_enc=enc)
    db.add(acc)
    db.commit()
    db.refresh(acc)
    out = {"account": account_out(acc)}
    if body.test:
        out["test"] = _test(db, acc)
        out["account"] = account_out(acc)
    return out


@router.post("/accounts/{account_id}/test")
def test_account(account_id: int, user: User = Depends(current_user), db: Session = Depends(get_session)):
    acc = get_account(db, user, account_id)
    return {"test": _test(db, acc), "account": account_out(acc)}


@router.get("/accounts/{account_id}/funds")
def funds(account_id: int, user: User = Depends(current_user), db: Session = Depends(get_session)):
    acc = get_account(db, user, account_id)
    try:
        f = adapter_for(db, acc).funds()
    except Exception as e:
        creds = {}
        try:
            creds = CredentialVault().decrypt(acc.credentials_enc)
        except Exception:
            pass
        raise HTTPException(502, f"{acc.broker}: {_safe_error(e, creds)}")
    return {"available": f.get("available"), "used": f.get("used"), "broker": acc.broker}


@router.get("/accounts/{account_id}/credentials")
def masked_credentials(account_id: int, user: User = Depends(current_user), db: Session = Depends(get_session)):
    acc = get_account(db, user, account_id)
    creds = CredentialVault().decrypt(acc.credentials_enc)
    return {"credentials": {k: mask(str(v)) for k, v in creds.items()}}


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, user: User = Depends(current_user), db: Session = Depends(get_session)):
    acc = get_account(db, user, account_id)
    if db.exec(select(LiveSession).where(LiveSession.broker_account_id == acc.id, LiveSession.status == "running")).first():
        raise HTTPException(409, "Stop the sessions using this broker account first")
    for sess in db.exec(select(LiveSession).where(LiveSession.broker_account_id == acc.id)).all():
        sess.broker_account_id = None  # keep session history, drop the FK
        db.add(sess)
    ad: Optional[BrokerAdapter] = _ADAPTERS.pop(acc.id, None)
    if ad:
        try:
            ad.logout()
        except Exception:
            pass
    db.delete(acc)
    db.commit()
    return {"ok": True}
