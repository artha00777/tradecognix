from __future__ import annotations

import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlmodel import Session, select

from ..billing.plans import effective_plan_key
from ..config import get_settings
from ..db import get_session
from ..models import User, as_utc
from ..ratelimit import client_ip, enforce
from ..security import burn_password_check, create_access_token, hash_password, password_problems, verify_password
from .deps import current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])

_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field("", max_length=120)
    referral_code: Optional[str] = Field(None, max_length=32)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)


def user_out(user: User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role, "plan": effective_plan_key(user),
            "plan_expires_at": as_utc(user.plan_expires_at), "referral_code": user.referral_code}


def _token_response(user: User) -> dict:
    return {"access_token": create_access_token(user.id, user.role), "token_type": "bearer", "user": user_out(user)}


def new_referral_code(db: Session) -> str:
    for _ in range(10):
        code = "TC" + "".join(secrets.choice(_REF_ALPHABET) for _ in range(6))
        if not db.exec(select(User).where(User.referral_code == code)).first():
            return code
    raise RuntimeError("could not allocate a referral code")


def ensure_referral_code(db: Session, user: User) -> User:
    """Backfill a referral code for accounts created before referrals existed."""
    if not user.referral_code:
        user.referral_code = new_referral_code(db)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


@router.post("/register")
def register(body: RegisterIn, request: Request, db: Session = Depends(get_session)):
    s = get_settings()
    enforce(f"register:{client_ip(request)}", s.rate_limit_register)
    email = body.email.lower()
    problems = password_problems(body.password, email)
    if problems:
        raise HTTPException(422, "Password too weak: " + "; ".join(problems))
    if db.exec(select(User).where(User.email == email)).first():
        raise HTTPException(409, "An account with this email already exists")
    referrer = None
    if body.referral_code:
        referrer = db.exec(select(User).where(User.referral_code == body.referral_code.strip().upper())).first()
    role = "admin" if email in s.admin_email_list else "client"
    user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password), role=role,
                referral_code=new_referral_code(db), referred_by=referrer.id if referrer else None)
    db.add(user)
    db.commit()
    db.refresh(user)
    return _token_response(user)


def _authenticate(db: Session, request: Request, email: str, password: str) -> User:
    s = get_settings()
    email = email.lower().strip()
    ip = client_ip(request)
    enforce(f"login-ip:{ip}", s.rate_limit_login_ip)
    enforce(f"login:{ip}:{email}", s.rate_limit_login)
    user = db.exec(select(User).where(User.email == email)).first()
    if not user:
        burn_password_check(password)  # same timing as a wrong password
        raise HTTPException(401, "Incorrect email or password")
    if not verify_password(password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password")
    if not user.is_active:
        raise HTTPException(403, "Account disabled")
    return ensure_referral_code(db, user)


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_session)):
    return _token_response(_authenticate(db, request, body.email, body.password))


@router.post("/token", include_in_schema=True)
def token(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_session)):
    """OAuth2 password flow (used by the Swagger UI Authorize button)."""
    return _token_response(_authenticate(db, request, form.username, form.password))


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {**user_out(user), "created_at": as_utc(user.created_at)}
