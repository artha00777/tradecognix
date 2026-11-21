from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from ..backtest.costs import CostConfig
from ..backtest.engine import run_backtest
from ..backtest.sweep import sweep
from ..data.candles import resample
from ..data.synthetic import generate_candles
from ..db import get_session
from ..models import BacktestRun, StrategyRecord, User
from ..strategy.dsl import Strategy
from ..strategy.explain import explain
from ..strategy.parser import parse_strategy
from .brokers import adapter_for, get_account
from .deps import current_user, optional_user
from .strategies import validate_spec

router = APIRouter(prefix="/api/backtests", tags=["backtests"])


class BacktestIn(BaseModel):
    strategy: Optional[dict[str, Any]] = None
    strategy_id: Optional[int] = None
    text: Optional[str] = None
    days: int = Field(60, ge=5, le=750)
    seed: int = 7
    slippage_pct: float = Field(0.5, ge=0, le=10)
    brokerage_per_order: float = Field(20.0, ge=0, le=100)
    iv: Optional[float] = Field(None, gt=0.01, lt=2.0, description="Annualised IV for the option premium proxy")
    broker_account_id: Optional[int] = Field(None, description="Use real candles from this connected broker")
    save: bool = True


class SweepIn(BacktestIn):
    timeframes: list[str] = ["5m", "15m", "30m"]
    modes: list[str] = ["atm_buy", "itm_buy", "atm_sell"]
    entry_starts: list[str] = ["09:30"]


def _resolve_strategy(body: BacktestIn, user: Optional[User], db: Session) -> tuple[Strategy, Optional[int]]:
    if body.strategy_id:
        if not user:
            raise HTTPException(401, "Login required to backtest a saved strategy")
        rec = db.get(StrategyRecord, body.strategy_id)
        if not rec or (rec.user_id != user.id and user.role != "admin"):
            raise HTTPException(404, "Strategy not found")
        return Strategy.model_validate(rec.spec), rec.id
    if body.strategy:
        return validate_spec(body.strategy), None
    if body.text:
        try:
            return parse_strategy(body.text).strategy, None
        except ValueError as e:
            raise HTTPException(422, str(e))
    raise HTTPException(422, "Provide strategy, strategy_id or text")


def _base_candles(body: BacktestIn, s: Strategy, user: Optional[User], db: Session):
    if body.broker_account_id:
        if not user:
            raise HTTPException(401, "Login required for broker data")
        acc = get_account(db, user, body.broker_account_id)
        try:
            ad = adapter_for(db, acc)
            inst = ad.index_instrument(s.underlying) if s.is_index else ad.search_instruments(s.underlying)[0]
            end = datetime.now()
            tf = "5m" if s.timeframe not in ("1m", "3m") else s.timeframe
            df = ad.historical_candles(inst, tf, end - timedelta(days=int(body.days * 1.5)), end)
            return df, "broker"
        except Exception as e:
            raise HTTPException(502, f"Could not load candles from {acc.broker}: {e}")
    return generate_candles(s.underlying, days=body.days, seed=body.seed), "synthetic"


def _downsample(points: list[dict], n: int = 400) -> list[dict]:
    if len(points) <= n:
        return points
    step = len(points) / n
    out = [points[int(i * step)] for i in range(n)]
    out[-1] = points[-1]
    return out


@router.post("/run")
def run(body: BacktestIn, user: Optional[User] = Depends(optional_user), db: Session = Depends(get_session)):
    """Run a backtest. Anonymous runs work on synthetic data (for the public demo) and are not saved."""
    s, sid = _resolve_strategy(body, user, db)
    base, source = _base_candles(body, s, user, db)
    candles = resample(base, s.timeframe)
    cfg = CostConfig(brokerage_per_order=body.brokerage_per_order, slippage_pct=body.slippage_pct)
    try:
        res = run_backtest(s, candles, source, cost_cfg=cfg, iv=body.iv)
    except ValueError as e:
        raise HTTPException(422, str(e))
    payload = {
        "strategy": s.model_dump(), "explanation": explain(s), "metrics": res.metrics, "equity": _downsample(res.equity),
        "trades": res.trades[-500:], "trade_count": len(res.trades), "flags": res.flags, "data_source": res.data_source,
        "params": body.model_dump(exclude={"strategy", "text"}),
    }
    if user and body.save:
        run_row = BacktestRun(user_id=user.id, strategy_id=sid, spec=s.model_dump(), params=payload["params"], metrics=res.metrics,
                              equity=payload["equity"], trades=res.trades, data_source=res.data_source, flags=res.flags)
        db.add(run_row)
        db.commit()
        db.refresh(run_row)
        payload["id"] = run_row.id
    return payload


@router.post("/sweep")
def run_sweep(body: SweepIn, user: Optional[User] = Depends(optional_user), db: Session = Depends(get_session)):
    """Grid of timeframes x option modes x entry-window starts, ranked by net P&L."""
    s, _ = _resolve_strategy(body, user, db)
    if len(body.timeframes) * max(1, len(body.modes)) * len(body.entry_starts) > 60:
        raise HTTPException(422, "Sweep too large (max 60 combinations)")
    base, source = _base_candles(body, s, user, db)
    rows = sweep(s, base, body.timeframes, body.modes, body.entry_starts, CostConfig(brokerage_per_order=body.brokerage_per_order, slippage_pct=body.slippage_pct), source)
    return {"rows": rows, "data_source": source, "best": next((r for r in rows if "error" not in r), None)}


@router.get("")
def list_runs(user: User = Depends(current_user), db: Session = Depends(get_session), limit: int = Query(50, le=200)):
    rows = db.exec(select(BacktestRun).where(BacktestRun.user_id == user.id).order_by(BacktestRun.created_at.desc()).limit(limit)).all()
    return {"runs": [{"id": r.id, "strategy_id": r.strategy_id, "name": r.spec.get("name"), "metrics": {k: r.metrics.get(k) for k in ("trades", "win_rate", "net_pnl", "max_drawdown", "profit_factor")},
                      "data_source": r.data_source, "flags": r.flags, "created_at": r.created_at} for r in rows]}


def _run(db: Session, user: User, rid: int) -> BacktestRun:
    r = db.get(BacktestRun, rid)
    if not r or (r.user_id != user.id and user.role != "admin"):
        raise HTTPException(404, "Backtest not found")
    return r


@router.get("/{rid}")
def get_run(rid: int, user: User = Depends(current_user), db: Session = Depends(get_session)):
    r = _run(db, user, rid)
    return {"id": r.id, "strategy": r.spec, "params": r.params, "metrics": r.metrics, "equity": r.equity, "flags": r.flags, "data_source": r.data_source, "created_at": r.created_at}


@router.get("/{rid}/trades")
def get_trades(rid: int, user: User = Depends(current_user), db: Session = Depends(get_session), offset: int = 0, limit: int = Query(200, le=2000)):
    r = _run(db, user, rid)
    return {"total": len(r.trades), "trades": r.trades[offset: offset + limit]}
