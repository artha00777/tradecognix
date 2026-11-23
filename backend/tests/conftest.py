import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet

os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["LIVE_TRADING_ENABLED"] = "false"
os.environ["BETA_PLAN"] = "free"
os.environ["ADMIN_EMAILS"] = "admin@devcognix.com"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["GEMINI_API_KEY"] = ""
os.environ["OPENAI_BASE_URL"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import SQLModel, create_engine  # noqa: E402

from app import db as dbmod  # noqa: E402
from app.data.synthetic import generate_candles  # noqa: E402


@pytest.fixture(scope="session")
def candles_5m():
    return generate_candles("NIFTY", days=60, seed=7)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    dbmod.set_engine(eng)
    from app import models  # noqa: F401

    SQLModel.metadata.create_all(eng)
    from app.engine.live import engine as live_engine
    from app.main import app

    from app.ratelimit import limiter

    live_engine.sessions.clear()
    limiter.reset()
    with TestClient(app) as c:
        yield c
    live_engine.sessions.clear()


def register(client, email="trader@example.com", password="s3cret-pass"):
    r = client.post("/api/auth/register", json={"email": email, "password": password, "name": "T"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def set_plan(email, plan, expires_at=None):
    """Test helper: change a user's plan directly in the DB."""
    from sqlmodel import Session, select

    from app.db import get_engine
    from app.models import User

    with Session(get_engine()) as db:
        u = db.exec(select(User).where(User.email == email)).one()
        u.plan, u.plan_expires_at = plan, expires_at
        db.add(u)
        db.commit()
