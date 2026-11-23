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


# A real bcrypt hash used to burn the same CPU time when the email does not exist, so login
# response time does not reveal which emails are registered.
_DUMMY_HASH = bcrypt.hashpw(b"timing-equaliser", bcrypt.gensalt()).decode()


def burn_password_check(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


COMMON_PASSWORDS = {
    "password", "password1", "password123", "12345678", "123456789", "1234567890", "qwerty123", "qwertyuiop",
    "iloveyou", "11111111", "00000000", "abcd1234", "letmein1", "welcome1", "admin123", "trading123", "nifty123",
    "sensex123", "india123", "passw0rd",
}


def password_problems(password: str, email: str = "", min_length: int | None = None) -> list[str]:
    """Minimum password policy: length, not all letters/all digits, not common, not the email."""
    min_length = min_length or get_settings().password_min_length
    problems = []
    if len(password) < min_length:
        problems.append(f"at least {min_length} characters")
    if password.isalpha() or password.isdigit():
        problems.append("mix letters with digits or symbols")
    if password.lower() in COMMON_PASSWORDS or len(set(password)) < 4:
        problems.append("not a common or repetitive password")
    local = email.split("@")[0].lower()
    if local and len(local) >= 4 and local in password.lower():
        problems.append("must not contain your email name")
    return problems


def create_access_token(subject: str | int, role: str, expires_minutes: int | None = None) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=expires_minutes or s.jwt_expire_minutes)
    return jwt.encode({"sub": str(subject), "role": role, "iat": int(now.timestamp()), "exp": exp, "typ": "access"},
                      s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any] | None:
    """Return the claims of a valid, unexpired access token, else None (never raises)."""
    s = get_settings()
    try:
        payload = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except (JWTError, ValueError, TypeError):
        return None
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub.isdigit() or payload.get("typ", "access") != "access":
        return None
    return payload


class CredentialVault:
    """Encrypts per-user broker credentials with a Fernet key from FERNET_KEY."""

    def __init__(self, key: str | None = None):
        key = key if key is not None else get_settings().fernet_key
        if not key:
            raise RuntimeError(
                "FERNET_KEY is not set. Generate one with: python -c \"from cryptography.fernet import Fernet;"
                "print(Fernet.generate_key().decode())\""
            )
        raw = key.encode() if isinstance(key, str) else key
        try:
            self._f = Fernet(raw)
        except (ValueError, TypeError):
            # Accept any long random secret (e.g. a host-generated value) by deriving a valid Fernet key from it.
            if len(raw) < 32:
                raise RuntimeError("FERNET_KEY must be a Fernet key or a random secret of at least 32 characters")
            import base64, hashlib
            self._f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw).digest()))

    def encrypt(self, data: dict[str, Any]) -> str:
        return self._f.encrypt(json.dumps(data).encode()).decode()

    def decrypt(self, token: str) -> dict[str, Any]:
        try:
            return json.loads(self._f.decrypt(token.encode()).decode())
        except InvalidToken as e:  # wrong key or tampered
            raise ValueError("Could not decrypt credentials (FERNET_KEY changed?)") from e


def mask(value: str, keep: int = 4) -> str:
    """Mask a secret, revealing at most `keep` trailing chars and never more than a third of it.
    Secrets shorter than 8 characters are fully masked.

    Short secrets (PINs) are therefore fully masked: "1234" -> "••••".
    """
    if not value:
        return ""
    keep = 0 if len(value) < 8 else max(0, min(keep, len(value) // 3))
    return "•" * (len(value) - keep) + (value[-keep:] if keep else "")


def scrub(text: str, secrets: dict[str, Any] | list[Any] | None) -> str:
    """Remove any secret values (len >= 4) from a message before it is logged / stored / returned."""
    if not text or not secrets:
        return text
    values = secrets.values() if isinstance(secrets, dict) else secrets
    for v in sorted((str(v) for v in values if v is not None), key=len, reverse=True):
        if len(v) >= 4:
            text = text.replace(v, "***")
    return text
