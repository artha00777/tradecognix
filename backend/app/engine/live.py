"""LiveEngine: schedules running strategy sessions (paper or live) on candle close.

- APScheduler BackgroundScheduler fires every minute, Mon-Fri 09:15-15:30 IST.
- Every minute each session runs risk checks (stops, hard exit, loss cap, kill switch).
- When a bar of the session's timeframe has just closed, the strategy is evaluated.
- Orders carry the strategy tag; fills become OrderLog rows; closed trades become JournalEntry rows.
- Live mode requires LIVE_TRADING_ENABLED=true AND the strategy's live confirmation AND the typed
  "LIVE" confirmation (see guardrails.assert_live_allowed). Checked at start AND before each tick.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlmodel import Session, select

from ..brokers.base import BrokerAdapter
from ..brokers.paper import PaperBroker
from ..config import get_settings
from ..data.candles import TF_MINUTES
from ..db import get_engine
from ..models import JournalEntry, LiveSession, OrderLog, StrategyRecord, utcnow
from ..strategy.dsl import Strategy
from .clock import IST, ist_now
from .feeds import BrokerFeed, SyntheticFeed
from .guardrails import GuardrailError
from .runner import StrategyRunner

log = logging.getLogger("tradecognix.engine")
OPEN_MIN, CLOSE_MIN = 9 * 60 + 15, 15 * 60 + 30


def _ist_to_utc(iso: str) -> datetime:
    """Runner timestamps are IST wall-clock strings; store them as UTC like every other column."""
    dt = datetime.fromisoformat(iso)
    return (dt.replace(tzinfo=IST) if dt.tzinfo is None else dt).astimezone(timezone.utc)


@dataclass
class RunningSession:
    session_id: int
    user_id: int
    strategy_id: int
    mode: str
    broker_key: str
    runner: StrategyRunner
    feed: Any


class LiveEngine:
    def __init__(self):
        self.sessions: dict[int, RunningSession] = {}
        self._lock = threading.RLock()  # guards `sessions` and serialises ticks vs stop/kill
        self._scheduler = None
        self.listeners: list = []  # websocket broadcast hooks: fn(user_id, payload)

    # ---------- scheduler ----------
    def start_scheduler(self) -> None:
        if self._scheduler is not None:
            return
        from apscheduler.schedulers.background import BackgroundScheduler

        sch = BackgroundScheduler(timezone=IST)
        sch.add_job(self.tick, "cron", day_of_week="mon-fri", hour="9-15", minute="*", second=5, id="tc-tick",
                    max_instances=1, coalesce=True, misfire_grace_time=30)
        sch.start()
        self._scheduler = sch
        log.info("engine scheduler started")

    def shutdown(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    # ---------- session lifecycle ----------
    def build_runner(self, rec: StrategyRecord, sess: LiveSession, broker: BrokerAdapter | None, broker_key: str) -> RunningSession:
        strategy = Strategy.model_validate(rec.spec)
        settings = get_settings()
        if sess.mode == "paper":
            if broker is not None and broker_key != "paper":
                feed = BrokerFeed(broker, broker.index_instrument(strategy.underlying) if strategy.is_index else broker.search_instruments(strategy.underlying)[0])
                exec_broker: BrokerAdapter = PaperBroker(data_broker=broker, slippage_pct=settings.default_slippage_pct, starting_capital=settings.paper_starting_capital)
            else:
                feed = SyntheticFeed(strategy.underlying)
                exec_broker = PaperBroker(price_source=feed.price, slippage_pct=settings.default_slippage_pct, starting_capital=settings.paper_starting_capital)
        else:
            if broker is None:
                raise GuardrailError("live mode needs a connected broker")
            exec_broker = broker
            feed = BrokerFeed(broker, broker.index_instrument(strategy.underlying) if strategy.is_index else broker.search_instruments(strategy.underlying)[0])
        sid = sess.id
        runner = StrategyRunner(
            strategy, exec_broker, rec.tag or f"TC{rec.id}", sess.mode, state=dict(sess.state or {}),
            on_order=lambda ev: self._persist_order(sid, sess.user_id, broker_key, ev),
            on_trade=lambda tr: self._persist_trade(sid, sess.user_id, rec.id, tr),
            on_event=lambda ev: self._broadcast(sess.user_id, {"type": "event", "session_id": sid, **ev}),
        )
        return RunningSession(sid, sess.user_id, rec.id, sess.mode, broker_key, runner, feed)

    def register(self, rs: RunningSession) -> None:
        with self._lock:
            self.sessions[rs.session_id] = rs

    def stop(self, session_id: int, status: str = "stopped", square_off: bool = True, reason: str = "") -> bool:
        """Remove a session (squaring off first). Returns False if the square-off was incomplete."""
        with self._lock:
            rs = self.sessions.pop(session_id, None)
            ok = True
            if rs and square_off and rs.runner.state.get("position"):
                try:
                    ok = rs.runner.kill(ist_now()) if status == "killed" else rs.runner._close(ist_now(), "manual_stop")
                except Exception as e:
                    ok = False
                    log.exception("square-off failed for session %s", session_id)
                    rs.runner._log("error", f"square-off failed: {e}", ist_now())
            if rs and rs.runner.state.get("position"):
                ok = False
            halt_reason = reason or ("" if ok else "square-off incomplete: close the open position at the broker")
            self._update_session(session_id, status=status, state=rs.runner.state if rs else None, stopped=True, halt_reason=halt_reason)
            return ok

    def kill(self, session_id: int) -> bool:
        with self._lock:
            rs = self.sessions.get(session_id)
            ok = True
            if rs:
                try:
                    ok = rs.runner.kill(ist_now())
                except Exception:
                    ok = False
                    log.exception("kill failed for session %s", session_id)
            return self.stop(session_id, status="killed", square_off=False) and ok

    def kill_user(self, user_id: int) -> int:
        ids = [sid for sid, rs in list(self.sessions.items()) if rs.user_id == user_id]
        for sid in ids:
            self.kill(sid)
        return len(ids)

    def kill_all(self) -> int:
        ids = list(self.sessions)
        for sid in ids:
            self.kill(sid)
        return len(ids)

    # ---------- ticking ----------
    def tick(self, now: Optional[datetime] = None) -> dict[int, Any]:
        """One engine step at IST wall-clock `now` (naive IST; aware datetimes are converted).

        Serialised by a lock so the scheduler and a manual /tick can never process the same bar
        twice (each runner also remembers the last processed bar).
        """
        from .clock import to_ist_naive

        now = to_ist_naive(now) if now else ist_now()
        now = now.replace(second=0, microsecond=0)
        with self._lock:
            return self._tick_locked(now)

    def _tick_locked(self, now: datetime) -> dict[int, Any]:
        results: dict[int, Any] = {}
        settings = get_settings()
        minute_of_day = now.hour * 60 + now.minute
        if not (OPEN_MIN <= minute_of_day <= CLOSE_MIN):
            return {sid: {"action": "none", "reason": "market_closed"} for sid in self.sessions}
        for sid, rs in list(self.sessions.items()):
            try:
                if rs.mode == "live" and not settings.live_trading_enabled:
                    rs.runner.kill(now)
                    self.stop(sid, status="halted", square_off=False, reason="LIVE_TRADING_ENABLED turned off")
                    results[sid] = {"action": "halted", "reason": "LIVE_TRADING_ENABLED turned off"}
                    continue
                mins = TF_MINUTES[rs.runner.s.timeframe]
                minutes_since_open = minute_of_day - OPEN_MIN
                bar_closed = minutes_since_open > 0 and (minutes_since_open % mins == 0 if mins < 1440 else minute_of_day == CLOSE_MIN)
                if bar_closed or (minute_of_day == CLOSE_MIN and mins < 1440):
                    candles = rs.feed.candles(rs.runner.s.timeframe, now)
                    if isinstance(rs.feed, SyntheticFeed):
                        rs.feed.now = now
                    results[sid] = rs.runner.on_candle_close(candles, now)
                else:
                    if isinstance(rs.feed, SyntheticFeed):
                        rs.feed.now = now
                    rs.runner.on_minute(now)
                    results[sid] = {"action": "risk_check"}
                self._update_session(sid, state=rs.runner.state, trades=rs.runner.state["trades_today"], pnl=rs.runner.state["pnl_today"])
                self._broadcast(rs.user_id, {"type": "status", "session_id": sid, **self._public_status(rs)})
            except Exception as e:  # never let one session kill the loop
                log.exception("session %s tick failed", sid)
                results[sid] = {"action": "error", "error": str(e)}
                rs.runner._log("error", str(e), now)
        return results

    # ---------- status ----------
    @staticmethod
    def _public_status(rs: RunningSession) -> dict:
        st = rs.runner.status()
        if st.get("position"):
            st["position"] = {**st["position"], "legs": [{k: v for k, v in l.items() if k != "instrument"} | {"symbol": l["instrument"]["symbol"]} for l in st["position"]["legs"]]}
        return {"mode": rs.mode, "strategy_id": rs.strategy_id, "broker": rs.broker_key, "name": rs.runner.s.name, **st}

    def status(self, user_id: Optional[int] = None) -> list[dict]:
        return [{"session_id": sid, **self._public_status(rs)} for sid, rs in list(self.sessions.items()) if user_id is None or rs.user_id == user_id]

    # ---------- persistence ----------
    def _update_session(self, sid: int, status: str | None = None, state: dict | None = None, trades: int | None = None, pnl: float | None = None,
                        stopped: bool = False, halt_reason: str = "") -> None:
        with Session(get_engine()) as db:
            row = db.get(LiveSession, sid)
            if not row:
                return
            if status:
                row.status = status
            if state is not None:
                row.state = dict(state)
            if trades is not None:
                row.trades_today = trades
            if pnl is not None:
                row.realised_pnl_today = pnl
            if stopped:
                row.stopped_at = utcnow()
            if halt_reason:
                row.halt_reason = halt_reason[:300]
            db.add(row)
            db.commit()

    def _persist_order(self, sid: int, user_id: int, broker_key: str, ev: dict) -> None:
        with Session(get_engine()) as db:
            db.add(OrderLog(session_id=sid, user_id=user_id, broker=broker_key, mode=ev["mode"], symbol=ev["symbol"], side=ev["side"], quantity=ev["qty"],
                            price=ev["price"] or 0.0, broker_order_id=ev["order_id"], tag=ev["tag"], status=ev["status"], message=f"{ev['reason']} {ev.get('message', '')}".strip()))
            db.commit()
        self._broadcast(user_id, {"type": "order", "session_id": sid, **ev})

    def _persist_trade(self, sid: int, user_id: int, strategy_id: int, tr: dict) -> None:
        with Session(get_engine()) as db:
            db.add(JournalEntry(user_id=user_id, strategy_id=strategy_id, session_id=sid, strategy_tag=tr["tag"], mode=tr["mode"], symbol=tr["symbol"],
                                side=tr["side"], quantity=tr["qty"], entry_time=_ist_to_utc(tr["entry_time"]), exit_time=_ist_to_utc(tr["exit_time"]),
                                entry_price=tr["entry_price"], exit_price=tr["exit_price"], gross_pnl=tr["gross_pnl"], costs=tr["costs"], net_pnl=tr["net_pnl"],
                                exit_reason=tr["exit_reason"], tags=[tr["tag"], tr["mode"], tr["direction"]]))
            db.commit()
        self._broadcast(user_id, {"type": "trade", "session_id": sid, **tr})

    def _broadcast(self, user_id: int, payload: dict) -> None:
        for fn in list(self.listeners):
            try:
                fn(user_id, payload)
            except Exception:
                pass

    def restore(self, broker_factory) -> int:
        """Re-attach sessions marked running in the DB after a restart (paper only; live needs re-confirmation)."""
        n = 0
        with Session(get_engine()) as db:
            rows = db.exec(select(LiveSession).where(LiveSession.status == "running")).all()
            for sess in rows:
                if sess.mode == "live":
                    sess.status, sess.halt_reason = "halted", "server restart: re-confirm live trading"
                    db.add(sess)
                    continue
                rec = db.get(StrategyRecord, sess.strategy_id)
                try:
                    if rec is None:
                        raise RuntimeError("strategy was deleted")
                    broker, key = broker_factory(db, sess)
                    self.register(self.build_runner(rec, sess, broker, key))
                    n += 1
                except Exception as e:
                    sess.status, sess.halt_reason = "halted", f"restore failed: {e}"
                    db.add(sess)
            db.commit()
        return n


engine = LiveEngine()
