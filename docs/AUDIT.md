# TradeCognix audit (Nov 2026)

Scope: the full repo as of `f658dc0` (indicators, strategy DSL and parser, backtester, cost model, metrics, data/resampling, paper/live engine, broker adapters, REST/WebSocket API, auth and secrets), plus the new plans/billing layer and the web front end. Fixes landed in `871d34c` and `6f2e3e6`; each backend fix has a regression test in `backend/tests/test_audit_core.py`, `test_audit_engine.py` or `test_audit_security_billing.py`.

**Tests: 111 passing** (108 from the audit plus 3 waitlist tests) (`cd backend && python -m pytest`). `pyflakes app scripts tests` reports only two intentional table-registration imports (`app/db.py`, `tests/conftest.py`, both marked `noqa: F401`).

## Bugs fixed, by area

### Indicators (`app/indicators.py`)
- RSI and ATR now use Wilder smoothing seeded with the SMA of the first `period` values (was EMA-seeded, so early values drifted from TradingView). A flat market gives RSI 50, not NaN/0.
- Supertrend flips against the *current* bar's band (straight port of TradingView `ta.supertrend`).
- VWAP resets on the IST calendar day; zero-volume bars (index candles) no longer get a fake volume of 1.
- Crossovers return False when either bar is NaN (no phantom signals during warm-up).

### Data and timeframes (`app/data/`)
- Resampling is anchored at the 09:15 open; daily bars are labelled at the session open; upsampling (e.g. 15m to 5m) is refused instead of silently producing bad bars.
- UTC / offset-carrying timestamps are converted to IST wall clock; CSV import validates columns and handles mixed offsets.
- The last hourly bar closes at 15:30 (bar close times are capped at session close). Synthetic data supports 1-minute bars and rejects sizes that don't divide the 375-minute session.

### Strategies, DSL and parser (`app/strategy/`)
- Daily-timeframe strategies are positional; previously the intraday hard exit made them produce zero trades.
- DSL rejects number-vs-number conditions, NaN/inf levels and indicator ids that shadow price fields.
- Parser: "first 15 min" is an opening range, not a timeframe; "daily loss limit 5000" is a loss cap but "stop loss 200 points" is not; inputs over 4,000 characters are rejected. LLM responses are size-capped and must be a JSON object; the API key goes in a header, not the URL.

### Fills, slippage and costs (`app/backtest/engine.py`, `costs.py`)
- Slippage rounds to the tick *against* the trader (buys up, sells down), never less than one tick.
- A bar that gaps through the stop fills at the open, not at the stop price.
- Trailing stops use the peak *before* the current bar and ratchet only after the bar survives (no same-bar hindsight).

### Metrics (`app/backtest/metrics.py`)
- Breakeven trades are neither wins nor losses (were counted as losses, also in loss streaks).
- Profit factor with no losing trades is reported as `∞` via `profit_factor_display` instead of `inf` breaking JSON, and is distinguishable from "no trades".
- Sharpe counts every session day (idle days = 0) on returns over capital; Sortino added. Max drawdown % is measured against peak equity, not starting capital.

### Engine (`app/engine/`)
- A rejected entry leaves no position; a partially filled multi-leg entry is unwound (no naked legs).
- A rejected exit keeps the position and retries next minute; square-off/kill report incomplete exits.
- Live mode confirms fills via the broker order book; an order rejected after submission is not booked as a position; missing fill price raises instead of recording ₹0.
- Stale bars are never traded (first tick of the day, stalled feed); only closed candles are used. Ticks are serialised against stop/kill with a lock; no double processing of a bar; nothing runs when the market is closed. IST clock helpers centralised in `engine/clock.py`.

### Brokers (`app/brokers/`)
- Contract picking no longer falls back to the last (possibly expired) contract; it chooses the nearest unexpired expiry and handles holiday shifts.
- Broker order states are normalised to filled / rejected / cancelled / pending; candle frames are converted to IST naive.
- Upstox expiry dates are computed in IST (were a day off on UTC hosts). Paper broker rejects malformed orders. Secrets are masked and scrubbed from logged/returned error messages.

### Security (`app/security.py`, `app/main.py`, `app/config.py`, `app/api/`)
- Production refuses to start with a default/short `JWT_SECRET`, wildcard CORS, missing `FERNET_KEY` or a non-HMAC JWT algorithm.
- Security headers: `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, COOP, a locked-down CSP (except Swagger docs), `no-store` on `/api/`, HSTS in production.
- In-memory rate limits on login (per IP+email and per IP), register, and anonymous parse/backtest; constant-time login for unknown emails; password policy.
- Garbage/expired tokens return 401 cleanly; WebSocket re-validates the token periodically and disconnects disabled accounts; subprotocol auth added.
- IDOR: users cannot read or modify each other's strategies, backtests, sessions or broker accounts. Admins are read-only on user strategies, may revoke but never confirm live trading, and have no broker-credential access. Deleting a strategy is blocked while a session runs and detaches history instead of breaking FKs.
- DB start-up adds missing columns to older tables (`db.add_missing_columns`).

### Plans and billing (new)
- **Payments disabled during beta.** The site never loads Razorpay; Pro/Elite buttons open a "Free during beta — join the waitlist" form that posts to the new `POST /api/waitlist` (`app/api/waitlist.py`, `WaitlistEntry`; public, rate-limited per IP, idempotent per email, tested in `tests/test_waitlist.py`). In demo mode the email stays in `localStorage` only.
- Free / Pro / Elite enforced server-side (`app/billing/plans.py`): backtests per day (IST reset), saved strategies, running sessions, sweeps and live trading; expired paid plans fall back to Free.
- Razorpay (kept, dormant during beta; not called by the site): `GET /api/billing/plans`, `GET /api/billing/usage`, `POST /api/billing/checkout` (503 until keys are set), `POST /api/billing/verify` (signature check), `POST /api/billing/webhook` (amount check, idempotent activation).

## Known gaps

- **Option premiums are a Black-Scholes proxy** at a constant IV (flagged `premium_proxy_black_scholes`); no historical option candles yet, so skew, IV crush and spreads are missing.
- **6 broker adapters are skeletons** (Groww, ICICI Direct, Kotak Neo, 5paisa, Alice Blue, Shoonya): every method raises `NotImplementedError`. Zerodha, Upstox, Dhan and Fyers are beta and unverified against real accounts.
- **Market orders lack price protection.** `OrderRequest` defaults to `MARKET` and the runner sends no limit band, so a thin or fast market can fill far from the signal price.
- **Candle rate limits** are handled only by a fixed sleep in the Angel One adapter (~3 req/s); there is no shared limiter or 429 backoff across sessions or other brokers.
- **WebSocket token in the URL.** The web client still connects with `?token=<jwt>`, which can land in proxy/access logs; the server already accepts the `Sec-WebSocket-Protocol: tc-auth, <jwt>` form.
- **NSE circular NSE/INSP/77559 (20 Nov 2026)** limits client static IPs to self-coding retail investors. Live trading for plain-English users through a vendor-run static IP is not permitted; it needs exchange empanelment and broker-hosted deployment. Keep `LIVE_TRADING_ENABLED=false` for public users until then (see README compliance notes and `docs/GROWTH.md` §9).
- The GitHub Pages site runs in demo mode, so waitlist emails entered there are not collected anywhere until `TC_API_BASE` points at a deployed backend.
- Plan limits stay enforced while payments are off, so "free during beta" Pro/Elite access currently means an admin grant (`PATCH /api/admin/users/{id}`).
- Rate limits are per process and in memory (fine for the single-worker deployment, not for horizontal scaling).
