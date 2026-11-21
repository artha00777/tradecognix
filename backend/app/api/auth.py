from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlmodel import Session, select

from ..config import get_settings
from ..db import get_session
from ..models import User
from ..security import create_access_token, hash_password, verify_password
from .deps import current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = ""


class LoginIn(BaseModel):
    email: EmailStr
    password: str


def _token_response(user: User) -> dict:
    return {"access_token": create_access_token(user.id, user.role), "token_type": "bearer",
            "user": {"id": user.id, "email": user.email, "name": user.name, "role": user.role}}


@router.post("/register")
def register(body: RegisterIn, db: Session = Depends(get_session)):
    email = body.email.lower()
    if db.exec(select(User).where(User.email == email)).first():
        raise HTTPException(409, "An account with this email already exists")
    role = "admin" if email in get_settings().admin_email_list else "client"
    user = User(email=email, name=body.name, password_hash=hash_password(body.password), role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return _token_response(user)


def _authenticate(db: Session, email: str, password: str) -> User:
    user = db.exec(select(User).where(User.email == email.lower())).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password")
    if not user.is_active:
        raise HTTPException(403, "Account disabled")
    return user


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_session)):
    return _token_response(_authenticate(db, body.email, body.password))


@router.post("/token", include_in_schema=True)
def token(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_session)):
    """OAuth2 password flow (used by the Swagger UI Authorize button)."""
    return _token_response(_authenticate(db, form.username, form.password))


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role, "created_at": user.created_at}
