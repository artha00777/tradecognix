"""Create (or promote) an admin user.

    python -m scripts.create_admin admin@devcognix.com            # prompts for the password (recommended)
    python -m scripts.create_admin admin@devcognix.com 'a-strong-password'

Passing the password as an argument leaves it in shell history / the process list.
"""
from __future__ import annotations

import getpass
import sys

from sqlmodel import Session, select

from app.db import get_engine, init_db
from app.models import User
from app.api.auth import new_referral_code
from app.security import hash_password, password_problems


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or "@" not in argv[0]:
        sys.exit(__doc__)
    email = argv[0].strip().lower()
    password = argv[1] if len(argv) > 1 else None
    init_db()
    with Session(get_engine()) as db:
        u = db.exec(select(User).where(User.email == email)).first()
        if u:
            u.role = "admin"
            if password:
                print("note: existing user promoted; password left unchanged")
        else:
            password = password or getpass.getpass("New admin password: ")
            problems = password_problems(password, email)
            if problems:
                sys.exit("Password too weak: " + "; ".join(problems))
            u = User(email=email, name="Admin", password_hash=hash_password(password), role="admin", referral_code=new_referral_code(db))
        db.add(u)
        db.commit()
    print(f"{email} is an admin")


if __name__ == "__main__":
    main()
