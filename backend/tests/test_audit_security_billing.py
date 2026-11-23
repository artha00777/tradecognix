"""Security hardening, IDOR, plan limits and Razorpay billing tests."""
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from conftest import register, set_plan

from app.config import Settings, get_settings, production_problems
from app.db import add_missing_columns, get_engine
from app.models import BacktestRun, Payment, User
from app.security import create_access_token, decode_token, password_problems

STRAT = "Buy Nifty ATM call when 9 EMA crosses above 21 EMA on 15-min candles after 9:30 AM. Max 2 trades a day."


# ---------------- startup / config ----------------
def test_production_refuses_unsafe_settings():
    from app.main import check_startup_settings

    bad = Settings(environment="production", jwt_secret="change-me-in-production", cors_origins="*", fernet_key="")
    assert len(production_problems(bad)) == 3
    with pytest.raises(RuntimeError, match="Unsafe production configuration"):
        check_startup_settings(bad)
    good = Settings(environment="production", jwt_secret="x" * 48, cors_origins="https://app.tradecognix.in", fernet_key="k")
    assert production_problems(good) == []
    check_startup_settings(good)
    check_startup_settings(Settings(environment="development"))  # dev only warns


def test_security_headers(client):
    r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert client.get("/api/billing/plans").headers["cache-control"] == "no-store"


# ---------------- auth ----------------
def test_password_policy(client):
    assert password_problems("abcdefgh") and password_problems("12345678") and password_problems("password1")
    assert not password_problems("Nifty-2026-ok")
    r = client.post("/api/auth/register", json={"email": "weak@example.com", "password": "aaaaaaaa"})
    assert r.status_code == 422
    r = client.post("/api/auth/register", json={"email": "longname@example.com", "password": "longname-99"})
    assert r.status_code == 422 and "email" in r.json()["detail"]


def test_login_rate_limited(client):
    register(client)
    codes = [client.post("/api/auth/login", json={"email": "trader@example.com", "password": "wrong-pass-1"}).status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429
    # the limit is per IP+email; registration has its own bucket
    r = client.post("/api/auth/login", json={"email": "trader@example.com", "password": "s3cret-pass"})
    assert r.status_code == 429 and "retry-after" in r.headers


def test_register_rate_limited(client):
    codes = [client.post("/api/auth/register", json={"email": f"u{i}@example.com", "password": "s3cret-pass"}).status_code for i in range(11)]
    assert codes.count(200) == 10 and codes[-1] == 429


def test_tokens_garbage_and_expired(client):
    assert decode_token("not-a-jwt") is None
    from jose import jwt

    s = get_settings()
    assert decode_token(jwt.encode({"sub": "abc", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, s.jwt_secret)) is None
    expired = create_access_token(1, "client", expires_minutes=-1)
    assert decode_token(expired) is None
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    forged = jwt.encode({"sub": "1", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, "other-secret")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_referral_codes(client):
    h = register(client)
    me = client.get("/api/auth/me", headers=h).json()
    assert me["referral_code"].startswith("TC") and me["plan"] == "free"
    r = client.post("/api/auth/register", json={"email": "friend@example.com", "password": "s3cret-pass", "referral_code": me["referral_code"].lower()})
    assert r.status_code == 200
    with Session(get_engine()) as db:
        friend = db.exec(select(User).where(User.email == "friend@example.com")).one()
        assert friend.referred_by == me["id"] and friend.referral_code != me["referral_code"]


# ---------------- websocket ----------------
def test_websocket_auth(client):
    h = register(client)
    token = h["Authorization"].split()[1]
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/live") as ws:
            ws.receive_json()
    with client.websocket_connect("/ws/live", subprotocols=["tc-auth", token]) as ws:
        assert ws.receive_json()["type"] == "hello"
    with client.websocket_connect(f"/ws/live?token={token}") as ws:  # legacy web client
        assert ws.receive_json()["type"] == "hello"


# ---------------- IDOR / admin ----------------
def test_users_cannot_touch_each_others_data(client):
    a = register(client, "alice@example.com")
    b = register(client, "bob@example.com")
    s = client.post("/api/strategies", json={"prompt": STRAT}, headers=a).json()
    run = client.post("/api/backtests/run", json={"strategy_id": s["id"], "days": 10}, headers=a).json()
    acc = client.post("/api/brokers/accounts", json={"broker": "angelone", "test": False,
                      "credentials": {"api_key": "KEY12345", "client_code": "A1", "pin": "1234", "totp_secret": "JBSWY3DPEHPK3PXP"}}, headers=a).json()["account"]
    for method, url, body in [
        ("get", f"/api/strategies/{s['id']}", None), ("put", f"/api/strategies/{s['id']}", {"name": "x"}),
        ("delete", f"/api/strategies/{s['id']}", None), ("get", f"/api/backtests/{run['id']}", None),
        ("get", f"/api/backtests/{run['id']}/trades", None), ("get", f"/api/brokers/accounts/{acc['id']}/credentials", None),
        ("post", f"/api/brokers/accounts/{acc['id']}/test", None), ("delete", f"/api/brokers/accounts/{acc['id']}", None),
    ]:
        r = getattr(client, method)(url, headers=b, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (method, url, r.status_code)
    assert client.post("/api/backtests/run", json={"strategy_id": s["id"], "days": 10}, headers=b).status_code == 404
    assert client.post("/api/engine/start", json={"strategy_id": s["id"]}, headers=b).status_code == 404
    # bob cannot use alice's broker account with his own strategy either
    sb = client.post("/api/strategies", json={"prompt": STRAT}, headers=b).json()
    assert client.post("/api/engine/start", json={"strategy_id": sb["id"], "broker_account_id": acc["id"]}, headers=b).status_code == 404
    assert client.get("/api/strategies", headers=b).json()["strategies"][0]["id"] == sb["id"]


def test_admin_is_read_only_on_user_strategies_and_has_no_broker_access(client):
    a = register(client)
    admin = register(client, "admin@devcognix.com")
    s = client.post("/api/strategies", json={"prompt": STRAT}, headers=a).json()
    assert client.get(f"/api/strategies/{s['id']}", headers=admin).status_code == 200
    assert client.post(f"/api/strategies/{s['id']}/confirm-live", json={"confirm_text": "LIVE", "acknowledge_risk": True}, headers=admin).status_code == 404
    assert client.delete(f"/api/strategies/{s['id']}", headers=admin).status_code == 404
    acc = client.post("/api/brokers/accounts", json={"broker": "groww", "test": False, "credentials": {"api_key": "k1234567", "api_secret": "s1234567"}}, headers=a).json()["account"]
    assert client.get(f"/api/brokers/accounts/{acc['id']}/credentials", headers=admin).status_code == 404
    # admin routes need admin
    for url in ("/api/admin/users", "/api/admin/stats", "/api/admin/sessions", "/api/admin/strategies"):
        assert client.get(url, headers=a).status_code == 403
        assert client.get(url).status_code == 401
    assert client.post("/api/engine/tick", json={}, headers=a).status_code == 403


def test_delete_strategy_guards_running_session_and_detaches_history(client):
    h = register(client)
    s = client.post("/api/strategies", json={"prompt": STRAT}, headers=h).json()
    run = client.post("/api/backtests/run", json={"strategy_id": s["id"], "days": 10}, headers=h).json()
    sid = client.post("/api/engine/start", json={"strategy_id": s["id"]}, headers=h).json()["session_id"]
    assert client.delete(f"/api/strategies/{s['id']}", headers=h).status_code == 409
    client.post(f"/api/engine/{sid}/stop", headers=h)
    assert client.delete(f"/api/strategies/{s['id']}", headers=h).status_code == 200
    with Session(get_engine()) as db:
        assert db.get(BacktestRun, run["id"]).strategy_id is None
    assert client.get(f"/api/backtests/{run['id']}", headers=h).status_code == 200


def test_public_endpoints_rate_limited(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_public", "3/600")
    codes = [client.post("/api/strategies/parse", json={"text": STRAT}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    h = register(client)  # signed-in users are not subject to the anonymous limit
    assert client.post("/api/strategies/parse", json={"text": STRAT}, headers=h).status_code == 200


# ---------------- plans / billing ----------------
def test_plans_endpoint_prices(client):
    j = client.get("/api/billing/plans").json()
    p = {x["key"]: x for x in j["plans"]}
    assert j["currency"] == "INR" and p["free"]["price_monthly_inr"] == 0
    assert p["pro"]["price_monthly_inr"] == 999 and p["pro"]["price_yearly_inr"] == 9990 and p["pro"]["price_monthly_paise"] == 99900
    assert p["elite"]["price_monthly_inr"] == 2499 and p["elite"]["max_strategies"] is None
    assert p["free"]["live_trading"] is False and p["free"]["sweeps"] is False and p["free"]["backtests_per_day"] == 5


def test_free_plan_backtest_quota_and_usage(client):
    h = register(client)
    body = {"text": STRAT, "days": 5, "save": False}  # unsaved runs still count
    codes = [client.post("/api/backtests/run", json=body, headers=h).status_code for _ in range(6)]
    assert codes == [200] * 5 + [402]
    u = client.get("/api/billing/usage", headers=h).json()
    assert u["plan"] == "free" and u["usage"]["backtests_today"] == 5 and u["limits"]["backtests_per_day"] == 5
    set_plan("trader@example.com", "pro")
    assert client.post("/api/backtests/run", json=body, headers=h).status_code == 200


def test_strategy_limit_and_expired_plan(client):
    h = register(client)
    assert client.post("/api/strategies", json={"prompt": STRAT}, headers=h).status_code == 200
    r = client.post("/api/strategies", json={"prompt": STRAT}, headers=h)
    assert r.status_code == 402 and r.json()["detail"]["feature"] == "strategies"
    set_plan("trader@example.com", "pro")
    assert client.post("/api/strategies", json={"prompt": STRAT}, headers=h).status_code == 200
    set_plan("trader@example.com", "pro", datetime.now(timezone.utc) - timedelta(days=1))  # lapsed
    assert client.get("/api/billing/usage", headers=h).json()["plan"] == "free"
    assert client.post("/api/strategies", json={"prompt": STRAT}, headers=h).status_code == 402


def test_admin_sets_plan(client):
    h = register(client)
    admin = register(client, "admin@devcognix.com")
    uid = client.get("/api/auth/me", headers=h).json()["id"]
    assert client.patch(f"/api/admin/users/{uid}", json={"plan": "elite"}, headers=h).status_code == 403
    r = client.patch(f"/api/admin/users/{uid}", json={"plan": "elite"}, headers=admin)
    assert r.status_code == 200 and r.json()["plan"] == "elite"
    assert client.get("/api/auth/me", headers=h).json()["plan"] == "elite"
    assert client.patch(f"/api/admin/users/{uid}", json={"plan": "platinum"}, headers=admin).status_code == 422
    users = client.get("/api/admin/users", headers=admin).json()["users"]
    assert next(u for u in users if u["id"] == uid)["plan"] == "elite"


def test_checkout_503_without_keys(client):
    h = register(client)
    assert client.post("/api/billing/checkout", json={"plan": "pro"}, headers=h).status_code == 503
    assert client.post("/api/billing/checkout", json={"plan": "pro"}).status_code == 401
    assert client.get("/api/billing/plans").json()["checkout_available"] is False


@pytest.fixture()
def razorpay_keys(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "razorpay_key_id", "rzp_test_abc")
    monkeypatch.setattr(s, "razorpay_key_secret", "topsecret")
    monkeypatch.setattr(s, "razorpay_webhook_secret", "whsecret")
    from app.billing import razorpay

    created = []

    def fake_create(amount, receipt, notes=None, client=None):
        oid = f"order_{len(created) + 1}"
        created.append({"id": oid, "amount": amount, "notes": notes})
        return {"id": oid, "amount": amount, "currency": "INR"}

    monkeypatch.setattr(razorpay, "create_order", fake_create)
    return created


def _sig(secret, msg):
    return hmac.new(secret.encode(), msg.encode() if isinstance(msg, str) else msg, hashlib.sha256).hexdigest()


def test_checkout_and_verify(client, razorpay_keys):
    h = register(client)
    assert client.post("/api/billing/checkout", json={"plan": "elite", "interval": "yearly"}, headers=h).status_code == 422
    co = client.post("/api/billing/checkout", json={"plan": "pro", "interval": "yearly"}, headers=h).json()
    assert co["amount"] == 999000 and co["key_id"] == "rzp_test_abc" and razorpay_keys[0]["notes"]["plan"] == "pro"
    bad = {"razorpay_order_id": co["order_id"], "razorpay_payment_id": "pay_1", "razorpay_signature": "deadbeef"}
    assert client.post("/api/billing/verify", json=bad, headers=h).status_code == 400
    other = register(client, "other@example.com")
    good = {**bad, "razorpay_signature": _sig("topsecret", f"{co['order_id']}|pay_1")}
    assert client.post("/api/billing/verify", json=good, headers=other).status_code == 404  # not their order
    r = client.post("/api/billing/verify", json=good, headers=h).json()
    assert r["ok"] and r["plan"] == "pro"
    exp = datetime.fromisoformat(r["plan_expires_at"])
    assert timedelta(days=364) < exp - datetime.now(timezone.utc) <= timedelta(days=365)
    assert client.post("/api/billing/verify", json=good, headers=h).status_code == 200  # idempotent
    assert client.get("/api/billing/usage", headers=h).json()["limits"]["live_trading"] is True


def test_webhook_activates_plan(client, razorpay_keys):
    h = register(client)
    co = client.post("/api/billing/checkout", json={"plan": "elite"}, headers=h).json()
    payload = json.dumps({"event": "payment.captured", "payload": {"payment": {"entity": {"id": "pay_9", "order_id": co["order_id"], "amount": co["amount"]}}}}).encode()
    assert client.post("/api/billing/webhook", content=payload, headers={"x-razorpay-signature": "nope"}).status_code == 400
    r = client.post("/api/billing/webhook", content=payload, headers={"x-razorpay-signature": _sig("whsecret", payload)})
    assert r.status_code == 200 and r.json()["ok"]
    assert client.get("/api/auth/me", headers=h).json()["plan"] == "elite"
    with Session(get_engine()) as db:
        assert db.exec(select(Payment).where(Payment.order_id == co["order_id"])).one().status == "paid"


def test_razorpay_signature_helpers():
    from app.billing.razorpay import verify_payment_signature, verify_webhook_signature

    sig = _sig("k", "order_1|pay_1")
    assert verify_payment_signature("order_1", "pay_1", sig, secret="k")
    assert not verify_payment_signature("order_1", "pay_2", sig, secret="k")
    assert not verify_payment_signature("order_1", "pay_1", sig, secret="")
    assert verify_webhook_signature(b"{}", _sig("w", b"{}"), secret="w") and not verify_webhook_signature(b"{}", "x", secret="w")


# ---------------- migrations ----------------
def test_add_missing_columns_upgrades_old_user_table(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as c:
        c.execute(text('CREATE TABLE "user" (id INTEGER PRIMARY KEY, email VARCHAR NOT NULL, name VARCHAR NOT NULL, password_hash VARCHAR NOT NULL, '
                       'role VARCHAR NOT NULL, is_active BOOLEAN NOT NULL, created_at DATETIME NOT NULL)'))
        c.execute(text("INSERT INTO \"user\" VALUES (1, 'a@b.c', '', 'x', 'client', 1, '2026-01-01 00:00:00')"))
    added = add_missing_columns(eng)
    assert {"user.plan", "user.plan_expires_at", "user.referral_code", "user.referred_by"} <= set(added)
    cols = {c["name"] for c in inspect(eng).get_columns("user")}
    assert "plan" in cols
    with eng.begin() as c:
        assert c.execute(text('SELECT plan FROM "user"')).scalar() == "free"
