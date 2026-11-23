"""TradeCognix API entrypoint: uvicorn app.main:app --reload"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api import admin, auth, backtests, billing, brokers, engine as engine_api, journal, strategies, waitlist, ws
from .config import get_settings, production_problems
from .db import init_db
from .engine.live import engine
from .strategy.llm import llm_available

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tradecognix")


def check_startup_settings(s=None) -> None:
    """Refuse to boot in production with unsafe settings; warn loudly elsewhere."""
    s = s or get_settings()
    problems = production_problems(s)
    if s.is_production and problems:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))
    for p in problems:
        log.warning("config (ok for development only): %s", p)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    check_startup_settings(s)
    init_db()
    if s.scheduler_enabled:
        engine.start_scheduler()
        try:
            restored = engine.restore(engine_api.broker_factory)
            if restored:
                log.info("restored %d paper session(s)", restored)
        except Exception:
            log.exception("session restore failed")
    log.info("TradeCognix %s up (%s). LIVE_TRADING_ENABLED=%s", __version__, s.environment, s.live_trading_enabled)
    yield
    engine.shutdown()


settings = get_settings()
app = FastAPI(title="TradeCognix API", version=__version__, lifespan=lifespan,
              description="Plain-English algorithmic trading for Indian brokers. Paper by default; live needs explicit enablement.")

# Wildcard CORS is only for local development; production refuses to start with it (see config).
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_list or ["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "no-referrer")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if not request.url.path.startswith(_DOCS_PATHS):  # Swagger UI needs its CDN scripts
        h.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    if request.url.path.startswith("/api/"):
        h.setdefault("Cache-Control", "no-store")
    if get_settings().is_production:
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


for r in (auth.router, brokers.router, strategies.router, backtests.router, engine_api.router, journal.router, admin.router, billing.router, waitlist.router, ws.router):
    app.include_router(r)


@app.get("/health")
def health():
    return {"ok": True, "version": __version__}


@app.get("/api/meta")
def meta():
    s = get_settings()
    return {"name": s.app_name, "version": __version__, "live_trading_enabled": s.live_trading_enabled, "static_ip": s.static_ip,
            "llm": llm_available(), "disclaimer": "Trading in derivatives involves substantial risk. Backtests are not indicative of future results."}
