"""TradeCognix API entrypoint: uvicorn app.main:app --reload"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api import admin, auth, backtests, brokers, engine as engine_api, journal, strategies, ws
from .config import get_settings
from .db import init_db
from .engine.live import engine
from .strategy.llm import llm_available

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tradecognix")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    init_db()
    if s.jwt_secret == "change-me-in-production" and s.environment == "production":
        raise RuntimeError("Set JWT_SECRET in production")
    if s.scheduler_enabled:
        engine.start_scheduler()
        try:
            from sqlmodel import Session  # noqa: F401

            restored = engine.restore(engine_api.broker_factory)
            if restored:
                log.info("restored %d paper session(s)", restored)
        except Exception:
            log.exception("session restore failed")
    log.info("TradeCognix %s up. LIVE_TRADING_ENABLED=%s", __version__, s.live_trading_enabled)
    yield
    engine.shutdown()


app = FastAPI(title="TradeCognix API", version=__version__, lifespan=lifespan,
              description="Plain-English algorithmic trading for Indian brokers. Paper by default; live needs explicit enablement.")

settings = get_settings()
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_list or ["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

for r in (auth.router, brokers.router, strategies.router, backtests.router, engine_api.router, journal.router, admin.router, ws.router):
    app.include_router(r)


@app.get("/health")
def health():
    return {"ok": True, "version": __version__}


@app.get("/api/meta")
def meta():
    s = get_settings()
    return {"name": s.app_name, "version": __version__, "live_trading_enabled": s.live_trading_enabled, "static_ip": s.static_ip,
            "llm": llm_available(), "disclaimer": "Trading in derivatives involves substantial risk. Backtests are not indicative of future results."}
