"""Paid-plan waitlist (payments disabled during beta)."""
from sqlmodel import Session, select

from app import db as dbmod
from app.models import WaitlistEntry


def test_waitlist_join_is_idempotent_and_normalised(client):
    r = client.post("/api/waitlist", json={"email": "Trader@Example.com", "plan": "pro"})
    assert r.status_code == 200 and r.json()["ok"] is True
    r = client.post("/api/waitlist", json={"email": "trader@example.com", "plan": "elite"})
    assert r.status_code == 200
    with Session(dbmod.get_engine()) as s:
        rows = s.exec(select(WaitlistEntry)).all()
    assert len(rows) == 1 and rows[0].email == "trader@example.com" and rows[0].plan == "elite"


def test_waitlist_validates_input(client):
    assert client.post("/api/waitlist", json={"email": "not-an-email"}).status_code == 422
    assert client.post("/api/waitlist", json={"email": "a@example.com", "plan": "gold"}).status_code == 422


def test_waitlist_rate_limited(client, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "rate_limit_public", "2/600")
    codes = [client.post("/api/waitlist", json={"email": f"u{i}@example.com"}).status_code for i in range(3)]
    assert codes == [200, 200, 429]


def test_beta_plan_upgrades_free_users(monkeypatch):
    from app.billing import plans
    from app.models import User

    class S:
        beta_plan = "pro"

    monkeypatch.setattr("app.config.get_settings", lambda: S())
    assert plans.effective_plan_key(User(email="a@b.co", password_hash="x", plan="free")) == "pro"
    assert plans.effective_plan_key(User(email="a@b.co", password_hash="x", plan="elite")) == "elite"
