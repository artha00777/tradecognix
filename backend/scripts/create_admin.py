"""Create (or promote) an admin user.

    python -m scripts.create_admin admin@devcognix.com 'a-strong-password'
"""
from __future__ import annotations

import sys

from sqlmodel import Session, select

from app.db import get_engine, init_db
from app.models import User
from app.security import hash_password


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    email, password = sys.argv[1].lower(), sys.argv[2]
    init_db()
    with Session(get_engine()) as db:
        u = db.exec(select(User).where(User.email == email)).first()
        if u:
            u.role = "admin"
        else:
            u = User(email=email, name="Admin", password_hash=hash_password(password), role="admin")
        db.add(u)
        db.commit()
    print(f"{email} is an admin")


if __name__ == "__main__":
    main()
