from datetime import datetime

from conftest import register, set_plan

VIDEO = "Buy Nifty ATM call when 9 EMA crosses above 21 EMA on 15-min candles after 9:30 AM. Exit on opposite cross or 3:15 PM. Max 2 trades a day."


def test_auth_flow(client):
    h = register(client)
    me = client.get("/api/auth/me", headers=h).json()
    assert me["role"] == "client"
    assert client.post("/api/auth/register", json={"email": "trader@example.com", "password": "another-pass"}).status_code == 409
    assert client.post("/api/auth/login", json={"email": "trader@example.com", "password": "wrong-pass"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "trader@example.com", "password": "s3cret-pass"}).status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_public_parse_and_backtest(client):
    r = client.post("/api/strategies/parse", json={"text": VIDEO})
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "rules" and body["rules"]["Timeframe"] == "15m"
    bt = client.post("/api/backtests/run", json={"strategy": body["strategy"], "days": 30})
    assert bt.status_code == 200
    j = bt.json()
    assert j["metrics"]["trades"] >= 1 and "id" not in j  # anonymous runs aren't saved
    assert client.post("/api/strategies/parse", json={"text": "hello there world"}).status_code == 422


def test_strategy_crud_backtest_and_journal(client):
    h = register(client)
    s = client.post("/api/strategies", json={"prompt": VIDEO}, headers=h).json()
    assert s["tag"].startswith(f"TC{s['id']}") and s["live_confirmed"] is False
    assert len(client.get("/api/strategies", headers=h).json()["strategies"]) == 1
    run = client.post("/api/backtests/run", json={"strategy_id": s["id"], "days": 30}, headers=h).json()
    assert run["id"]
    assert client.get(f"/api/backtests/{run['id']}/trades", headers=h).json()["total"] == run["trade_count"]
    sweep_req = {"strategy_id": s["id"], "days": 20, "timeframes": ["5m", "15m"], "modes": ["atm_buy"]}
    r = client.post("/api/backtests/sweep", json=sweep_req, headers=h)
    assert r.status_code == 402 and r.json()["detail"]["feature"] == "sweeps"  # free plan: no sweeps
    set_plan("trader@example.com", "pro")
    sweep = client.post("/api/backtests/sweep", json=sweep_req, headers=h).json()
    assert len(sweep["rows"]) == 2
    # editing rules revokes live confirmation
    client.post(f"/api/strategies/{s['id']}/confirm-live", json={"confirm_text": "LIVE", "acknowledge_risk": True}, headers=h)
    spec = s["spec"]
    spec["risk"]["max_trades_per_day"] = 1
    upd = client.put(f"/api/strategies/{s['id']}", json={"spec": spec}, headers=h).json()
    assert upd["live_confirmed"] is False
    # other users can't see it
    h2 = register(client, "other@example.com")
    assert client.get(f"/api/strategies/{s['id']}", headers=h2).status_code == 404


def test_live_blocked_without_server_flag(client):
    h = register(client)
    s = client.post("/api/strategies", json={"prompt": VIDEO}, headers=h).json()
    client.post(f"/api/strategies/{s['id']}/confirm-live", json={"confirm_text": "LIVE", "acknowledge_risk": True}, headers=h)
    r = client.post("/api/engine/start", json={"strategy_id": s["id"], "mode": "live", "confirm_text": "LIVE"}, headers=h)
    assert r.status_code == 402 and r.json()["detail"]["feature"] == "live_trading"  # free plan is paper-only
    set_plan("trader@example.com", "pro")
    r = client.post("/api/engine/start", json={"strategy_id": s["id"], "mode": "live", "confirm_text": "LIVE"}, headers=h)
    assert r.status_code == 403 and "disabled" in r.json()["detail"]


def test_paper_session_admin_tick_and_kill(client):
    h = register(client)
    admin = register(client, "admin@devcognix.com")
    assert client.get("/api/auth/me", headers=admin).json()["role"] == "admin"
    assert client.get("/api/admin/stats", headers=h).status_code == 403
    s = client.post("/api/strategies", json={"prompt": "Buy Nifty ATM call at 9:30 AM. Exit at 3:15 PM. Max 1 trade a day."}, headers=h).json()
    st = client.post("/api/engine/start", json={"strategy_id": s["id"], "mode": "paper"}, headers=h)
    assert st.status_code == 200, st.text
    sid = st.json()["session_id"]
    assert client.post("/api/engine/start", json={"strategy_id": s["id"], "mode": "paper"}, headers=h).status_code == 409
    today = datetime.now().date()
    from datetime import timedelta
    d = today
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    for hhmm in ("09:30", "15:15"):
        t = datetime.combine(d, datetime.strptime(hhmm, "%H:%M").time())
        res = client.post("/api/engine/tick", json={"now": t.isoformat()}, headers=admin).json()["results"]
        assert str(sid) in res
    orders = client.get("/api/engine/orders", headers=h).json()["orders"]
    assert len(orders) == 2 and all(o["tag"] == s["tag"] for o in orders)
    journal = client.get("/api/journal", headers=h).json()["entries"]
    assert len(journal) == 1 and journal[0]["strategy_tag"] == s["tag"] and journal[0]["exit_reason"] == "hard_exit"
    summ = client.get("/api/journal/summary", headers=h).json()["by_strategy"]
    assert summ[0]["trades"] == 1
    assert client.get("/api/journal/export.csv", headers=h).text.startswith("id,strategy_tag")
    stats = client.get("/api/admin/stats", headers=admin).json()
    assert stats["running_sessions"] == 1 and stats["orders"] == 2
    assert client.post("/api/admin/kill-all", headers=admin).json()["sessions_killed"] == 1
    assert client.get("/api/engine/status", headers=h).json()["sessions"] == []
    users = client.get("/api/admin/users", headers=admin).json()["users"]
    assert {u["email"] for u in users} == {"trader@example.com", "admin@devcognix.com"}


def test_broker_connect_encrypts_and_skeleton_reports(client):
    h = register(client)
    lst = client.get("/api/brokers").json()["brokers"]
    assert {b["key"] for b in lst} >= {"angelone", "zerodha", "upstox", "dhan", "fyers", "groww", "shoonya"}
    r = client.post("/api/brokers/accounts", json={"broker": "groww", "credentials": {"api_key": "k", "api_secret": "s"}}, headers=h).json()
    assert r["test"]["ok"] is False and r["test"].get("skeleton")
    assert client.post("/api/brokers/accounts", json={"broker": "angelone", "credentials": {"api_key": "k"}, "test": False}, headers=h).status_code == 422
    r = client.post("/api/brokers/accounts", json={"broker": "angelone", "test": False,
                    "credentials": {"api_key": "KEY12345", "client_code": "A1", "pin": "1234", "totp_secret": "JBSWY3DPEHPK3PXP"}}, headers=h).json()
    aid = r["account"]["id"]
    masked = client.get(f"/api/brokers/accounts/{aid}/credentials", headers=h).json()["credentials"]
    # at most a third of a secret is revealed; a 4-digit PIN is fully masked
    assert masked["api_key"].endswith("45") and "KEY1" not in masked["api_key"] and "2345" not in masked["api_key"]
    assert masked["pin"] == "••••"
    from sqlmodel import Session
    from app.db import get_engine
    from app.models import BrokerAccount
    with Session(get_engine()) as db:
        raw = db.get(BrokerAccount, aid).credentials_enc
    assert "KEY12345" not in raw and "1234" not in raw


def test_admin_waitlist_lists_entries_and_requires_admin(client):
    user = register(client, "wl-plain-user@example.com")
    admin = register(client, "admin@devcognix.com")
    client.post("/api/waitlist", json={"email": "wl-admin-test@example.com", "plan": "elite"})
    assert client.get("/api/admin/waitlist", headers=user).status_code == 403
    r = client.get("/api/admin/waitlist", headers=admin)
    assert r.status_code == 200
    d = r.json()
    row = next(e for e in d["entries"] if e["email"] == "wl-admin-test@example.com")
    assert row["plan"] == "elite" and row["has_account"] is False
    assert d["by_plan"]["elite"] >= 1 and d["total"] >= 1


def test_paper_session_survives_restart_and_live_is_halted(client):
    """After a restart, running paper sessions re-attach with their state; a live row is halted for re-confirmation."""
    from app.api.engine import broker_factory
    from app.db import get_engine
    from app.engine.live import engine as live_engine
    from app.models import LiveSession
    from sqlmodel import Session as DBSession

    h = register(client, "restart@example.com")
    s = client.post("/api/strategies", json={"prompt": "Buy Nifty ATM call at 9:30 AM. Exit at 3:15 PM. Max 1 trade a day."}, headers=h).json()
    sid = client.post("/api/engine/start", json={"strategy_id": s["id"], "mode": "paper"}, headers=h).json()["session_id"]
    with DBSession(get_engine()) as db:
        row = db.get(LiveSession, sid)
        fake_live = LiveSession(user_id=row.user_id, strategy_id=row.strategy_id, mode="live", status="running")
        db.add(fake_live)
        db.commit()
        live_id = fake_live.id
    live_engine.sessions.clear()  # what a process restart looks like
    assert client.get("/api/engine/status", headers=h).json()["sessions"] == []
    assert live_engine.restore(broker_factory) == 1
    running = client.get("/api/engine/status", headers=h).json()["sessions"]
    assert [r["session_id"] for r in running] == [sid]
    rows = {r["id"]: r for r in client.get("/api/engine/sessions", headers=h).json()["sessions"]}
    assert rows[sid]["status"] == "running"
    assert rows[live_id]["status"] == "halted" and "re-confirm" in rows[live_id]["halt_reason"]
    live_engine.sessions.clear()
