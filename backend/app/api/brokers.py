from __future__ import annotations

from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..brokers.base import BrokerAdapter, BrokerError
from ..brokers.registry import adapter_class, get_adapter, list_brokers
from ..config import get_settings
from ..db import get_session
from ..models import BrokerAccount, User, utcnow
from ..security import CredentialVault, mask
from .deps import current_user

router = APIRouter(prefix="/api/brokers", tags=["brokers"])

# account_id -> logged-in adapter (keeps sessions warm; tokens never leave the server)
_ADAPTERS: dict[int, BrokerAdapter] = {}


class AccountIn(BaseModel):
    broker: str
    label: str = ""
    credentials: dict[str, Any]
    test: bool = True


def account_out(a: BrokerAccount) -> dict:
    return {"id": a.id, "broker": a.broker, "label": a.label, "status": a.status, "last_error": a.last_error,
            "created_at": a.created_at, "updated_at": a.updated_at}


def adapter_for(db: Session, account: BrokerAccount, fresh: bool = False) -> BrokerAdapter:
    if not fresh and account.id in _ADAPTERS:
        return _ADAPTERS[account.id]
    creds = CredentialVault().decrypt(account.credentials_enc)
    adapter = get_adapter(account.broker, creds)
    _ADAPTERS[account.id] = adapter
    return adapter


def get_account(db: Session, user: User, account_id: int) -> BrokerAccount:
    acc = db.get(BrokerAccount, account_id)
    if not acc or (acc.user_id != user.id and user.role != "admin"):
        raise HTTPException(404, "Broker account not found")
    return acc


def _test(db: Session, acc: BrokerAccount) -> dict:
    try:
        adapter = adapter_for(db, acc, fresh=True)
        adapter.login()
        prof = adapter.profile()
        acc.status, acc.last_error = "connected", ""
        result = {"ok": True, "profile": {k: v for k, v in prof.items() if k != "raw"}}
    except NotImplementedError as e:
        acc.status, acc.last_error = "error", str(e)
        result = {"ok": False, "error": str(e), "skeleton": True}
    except Exception as e:
        acc.status, acc.last_error = "error", str(e)[:500]
        _ADAPTERS.pop(acc.id, None)
        result = {"ok": False, "error": str(e)[:500]}
    acc.updated_at = utcnow()
    db.add(acc)
    db.commit()
    return result


@router.get("")
def brokers():
    return {"brokers": list_brokers()}


@router.get("/static-ip")
def static_ip():
    """Shows the configured static IP and the server's current public egress IP (to whitelist with your broker)."""
    detected = None
    try:
        detected = httpx.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception:
        pass
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
    creds = {k: str(v).strip() for k, v in body.credentials.items() if v not in (None, "")}
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
        raise HTTPException(502, f"{acc.broker}: {e}")
    return {"available": f.get("available"), "used": f.get("used"), "broker": acc.broker}


@router.get("/accounts/{account_id}/credentials")
def masked_credentials(account_id: int, user: User = Depends(current_user), db: Session = Depends(get_session)):
    acc = get_account(db, user, account_id)
    creds = CredentialVault().decrypt(acc.credentials_enc)
    return {"credentials": {k: mask(str(v)) for k, v in creds.items()}}


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, user: User = Depends(current_user), db: Session = Depends(get_session)):
    acc = get_account(db, user, account_id)
    ad: Optional[BrokerAdapter] = _ADAPTERS.pop(acc.id, None)
    if ad:
        try:
            ad.logout()
        except Exception:
            pass
    db.delete(acc)
    db.commit()
    return {"ok": True}
