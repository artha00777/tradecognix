"""Password hashing (bcrypt), JWT tokens, and Fernet encryption of broker credentials."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt

from .config import get_settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:72], hashed.encode())
    except ValueError:
        return False


def create_access_token(subject: str | int, role: str, expires_minutes: int | None = None) -> str:
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes or s.jwt_expire_minutes)
    return jwt.encode({"sub": str(subject), "role": role, "exp": exp}, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any] | None:
    s = get_settings()
    try:
        return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except JWTError:
        return None


class CredentialVault:
    """Encrypts per-user broker credentials with a Fernet key from FERNET_KEY."""

    def __init__(self, key: str | None = None):
        key = key if key is not None else get_settings().fernet_key
        if not key:
            raise RuntimeError(
                "FERNET_KEY is not set. Generate one with: python -c \"from cryptography.fernet import Fernet;"
                "print(Fernet.generate_key().decode())\""
            )
        self._f = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, data: dict[str, Any]) -> str:
        return self._f.encrypt(json.dumps(data).encode()).decode()

    def decrypt(self, token: str) -> dict[str, Any]:
        try:
            return json.loads(self._f.decrypt(token.encode()).decode())
        except InvalidToken as e:  # wrong key or tampered
            raise ValueError("Could not decrypt credentials (FERNET_KEY changed?)") from e


def mask(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    return "•" * max(0, len(value) - keep) + value[-keep:]
