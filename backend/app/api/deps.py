from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session

from ..db import get_session
from ..models import User
from ..security import decode_token

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)


def user_from_token(token: Optional[str], db: Session) -> Optional[User]:
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    user = db.get(User, int(payload["sub"]))  # decode_token guarantees a numeric sub
    if not user or not user.is_active:
        return None
    return user


def optional_user(token: Optional[str] = Depends(oauth2), db: Session = Depends(get_session)) -> Optional[User]:
    return user_from_token(token, db)


def current_user(user: Optional[User] = Depends(optional_user)) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    return user


def owns(user: User, owner_id: int | None, allow_admin: bool = False) -> bool:
    """IDOR guard: True if `user` owns the row (or is an admin and admin access is allowed)."""
    return owner_id == user.id or (allow_admin and user.role == "admin")


def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return user
